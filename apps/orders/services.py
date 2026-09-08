import re
import os
import logging
from decimal import Decimal
from django.utils import timezone
from django.conf import settings
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder, OrderCommunication
from apps.quotes.models import Quotation, QuoteLineItem
from menard_core.brevo_email import send_departmental_email

logger = logging.getLogger(__name__)


def extract_text_from_pdf(file_path):
    """
    Extracts text from a PDF file using pdfplumber with pypdf fallback.
    """
    text = ""
    try:
        import pdfplumber
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        logger.warning(f"pdfplumber failed, trying pypdf: {e}")
        try:
            import pypdf
            reader = pypdf.PdfReader(file_path)
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
        except Exception as e2:
            logger.error(f"Failed to extract PDF text: {e2}")
    return text


def parse_po_text(text: str) -> dict:
    """
    Heuristic regex parsing for typical transport PO documents.
    """
    data = {
        'po_number': None,
        'pickup_location': '',
        'delivery_location': '',
        'cargo_description': '',
        'weight_tons': None,
        'quantity_pallets': None,
        'company_name': '',
        'contact_name': '',
        'contact_email': '',
        'contact_phone': '',
    }

    # Extract PO Number
    po_patterns = [
        r'Purchase\s*Order\s*#?([A-Z0-9\-_/]{3,30})',
        r'(?:PO|Purchase\s*Order|P\.O\.?)\s*(?:Number|No\.?|#)?[:\s]+([A-Z0-9\-_/]{3,30})',
        r'#?PO[/_-]?([0-9]{4,15})',
        r'(?:Order\s*Ref|Ref\s*No\.?|Shipment)[:\s]+([A-Z0-9\-_/]{3,30})',
    ]
    for pattern in po_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip()
            if candidate and candidate.upper() not in ['EMAIL', 'INBOUND', 'NEW']:
                data['po_number'] = candidate
                break

    # Extract Weight
    weight_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:Tons?|Tonnes?|t)\b', text, re.IGNORECASE)
    if weight_match:
        try:
            data['weight_tons'] = Decimal(weight_match.group(1))
        except Exception:
            pass
    else:
        kg_match = re.search(r'(\d+(?:[\s,]\d+)*(?:\.\d+)?)\s*(?:kg|kilograms?)\b', text, re.IGNORECASE)
        if kg_match:
            try:
                kg_val = float(kg_match.group(1).replace(' ', '').replace(',', ''))
                if kg_val > 0:
                    data['weight_tons'] = Decimal(str(round(kg_val / 1000.0, 2)))
            except Exception:
                pass

    # Extract Pallets
    pallet_match = re.search(r'(\d+)\s*(?:Pallets?|plt|EA)\b', text, re.IGNORECASE)
    if pallet_match:
        try:
            data['quantity_pallets'] = int(pallet_match.group(1))
        except Exception:
            pass

    # Extract Pickup / Origin
    pickup_patterns = [
        r'(?:Collection|Pickup|Origin|From|Loading\s*Address)[:\s]+([^\n\r]+)',
        r'(?:Walvis\s*Bay|Johannesburg|Durban|Cape\s*Town|Windhoek|Gaborone|Harare|Lusaka)',
    ]
    for pattern in pickup_patterns:
        p_match = re.search(pattern, text, re.IGNORECASE)
        if p_match:
            data['pickup_location'] = (p_match.group(1) if p_match.groups() else p_match.group(0)).strip()
            break

    # Extract Delivery / Destination
    delivery_patterns = [
        r'(?:Delivery|Destination|To|Offloading\s*Address|Drop\s*off|Shipping\s*address)[:\s]+([^\n\r]+)',
        r'(?:Rundu|Katima\s*Mulilo|Oshikango|Lubumbashi|Gaborone|Ndola|Kitwe|Lusaka|Maputo|Beira)',
    ]
    for pattern in delivery_patterns:
        d_match = re.search(pattern, text, re.IGNORECASE)
        if d_match:
            data['delivery_location'] = (d_match.group(1) if d_match.groups() else d_match.group(0)).strip()
            break

    # Extract Buyer / Contact Name
    buyer_match = re.search(r'(?:Buyer|Attention|Attn|Contact)[:\s]+([^\n\r]+)', text, re.IGNORECASE)
    if buyer_match:
        # Clean buyer name
        candidate_buyer = buyer_match.group(1).split('Order Date')[0].strip()
        data['contact_name'] = candidate_buyer

    # Extract Cargo Description
    cargo_match = re.search(r'(?:DESCRIPTION|Commodity|Cargo|Goods\s*Description|Product)[:\s]+([^\n\r]+)', text, re.IGNORECASE)
    if cargo_match:
        data['cargo_description'] = cargo_match.group(1).strip()

    # Extract Company / Contact
    company_match = re.search(r'([A-Z][a-zA-Z0-9\s&,.\-]{3,40}(?:CC|Pty\s*Ltd|\(Pty\)\s*Ltd|Logistics|Trading|Enterprises|Limited|Ltd))', text)
    if company_match:
        data['company_name'] = company_match.group(1).strip()

    email_match = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', text)
    if email_match:
        data['contact_email'] = email_match.group(1).lower()

    phone_match = re.search(r'(?:\+?264|\+?27|0)\s*\d{1,4}\s*\d{2,4}\s*\d{3,4}', text)
    if phone_match:
        data['contact_phone'] = phone_match.group(0).strip()

    return data


def process_inbound_purchase_order(po: PurchaseOrder) -> Quotation:
    """
    Executes parsing, customer auto-matching, and draft quotation generation.
    """
    po.status = PurchaseOrder.Status.PARSING
    po.save(update_fields=['status'])

    extracted = {}
    if po.po_file and os.path.exists(po.po_file.path):
        raw_text = extract_text_from_pdf(po.po_file.path)
        if raw_text:
            extracted = parse_po_text(raw_text)
    
    if not extracted and po.raw_email_body:
        extracted = parse_po_text(po.raw_email_body)

    # Populate extracted data
    if extracted.get('pickup_location') and not po.pickup_location:
        po.pickup_location = extracted['pickup_location']
    if extracted.get('delivery_location') and not po.delivery_location:
        po.delivery_location = extracted['delivery_location']
    if extracted.get('cargo_description') and not po.cargo_description:
        po.cargo_description = extracted['cargo_description']
    if extracted.get('weight_tons') and not po.weight_tons:
        po.weight_tons = extracted['weight_tons']
    if extracted.get('quantity_pallets') and not po.quantity_pallets:
        po.quantity_pallets = extracted['quantity_pallets']

    po.extracted_json = {k: str(v) if isinstance(v, Decimal) else v for k, v in extracted.items()}

    # Match or Create Customer (Guaranteed non-null)
    if not po.customer:
        customer_email = (extracted.get('contact_email') or po.raw_email_sender or '').strip()
        if not customer_email:
            safe_ref = re.sub(r'[^a-zA-Z0-9]', '', po.po_number).lower() or 'order'
            customer_email = f"client_{safe_ref}@menardtrading.com"

        customer = Customer.objects.filter(email__iexact=customer_email).first()
        if not customer:
            company = extracted.get('company_name') or f"Client ({po.po_number})"
            contact = extracted.get('contact_name') or "Procurement / Logistics"
            customer = Customer.objects.create(
                company_name=company,
                contact_name=contact,
                email=customer_email,
                phone=extracted.get('contact_phone', 'N/A'),
                physical_address=po.pickup_location or 'N/A',
            )
        po.customer = customer

    po.status = PurchaseOrder.Status.PARSED
    po.save()

    # Generate Draft Quotation
    if not hasattr(po, 'quotation') or not po.quotation:
        quote = Quotation.objects.create(
            purchase_order=po,
            customer=po.customer,
            valid_until=timezone.now().date() + timezone.timedelta(days=14),
            status=Quotation.Status.DRAFT,
            notes=f"Quotation based on PO #{po.po_number}. Cargo: {po.cargo_description or 'General Freight'}."
        )

        # Base Line Item: Freight Rate calculation
        weight = po.weight_tons or Decimal('34.00')
        origin = po.pickup_location or 'Origin Point'
        dest = po.delivery_location or 'Destination Point'
        
        # Estimate base rate: e.g. R450 per ton or default R15,500 trip rate
        estimated_unit_price = Decimal('450.00')
        QuoteLineItem.objects.create(
            quote=quote,
            item_type=QuoteLineItem.ItemType.FREIGHT,
            description=f"Long-haul Freight: {origin} to {dest} ({weight} Tons)",
            quantity=weight,
            unit_price=estimated_unit_price,
        )

        po.status = PurchaseOrder.Status.QUOTED
        po.save(update_fields=['status'])
    else:
        quote = po.quotation

    # Send confirmation email to customer (strictly once)
    if po.customer and po.customer.email and not po.acknowledgment_sent:
        try:
            success, msg = send_departmental_email(
                department='no-reply',
                recipient_list=[po.customer.email],
                subject=f"Purchase Order Received – #{po.po_number} | Menard Trading CC",
                template_name='emails/po_received_noreply.html',
                context={'po': po, 'customer_name': po.customer.contact_name or po.customer.company_name}
            )
            if success:
                po.acknowledgment_sent = True
                po.acknowledgment_sent_at = timezone.now()
                po.save(update_fields=['acknowledgment_sent', 'acknowledgment_sent_at'])

                OrderCommunication.objects.create(
                    purchase_order=po,
                    sender_department='no-reply',
                    recipient_email=po.customer.email,
                    subject=f"Purchase Order Received – #{po.po_number} | Menard Trading CC",
                    message_body="Automated acknowledgment notice sent upon purchase order receipt."
                )
        except Exception as email_err:
            logger.error(f"Failed to send acknowledgment email for PO #{po.po_number}: {email_err}")

    return quote
