import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views import View
from django.contrib import messages
from apps.accounts.permissions import PermissionRequiredMixin
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, InvoiceLineItem
from apps.billing.pdf_services import generate_quotation_pdf, generate_invoice_pdf
from menard_core.brevo_email import send_departmental_email

logger = logging.getLogger(__name__)


class CustomerQuotePortalView(View):
    """
    Public customer-facing portal to view and review a quotation via a secure token.
    """
    def get(self, request, token):
        quote = get_object_or_404(Quotation, approval_token=token)
        return render(request, 'portal/quote_view.html', {
            'quote': quote,
            'active_tab': 'quotes'
        })



class CustomerApproveQuoteView(View):
    """
    1-Click Quote Approval by the Customer.
    Automatically creates a LogisticsJob and initiates the first-stage invoice.
    """
    def post(self, request, token):
        quote = get_object_or_404(Quotation, approval_token=token)
        
        if quote.status == Quotation.Status.APPROVED:
            messages.info(request, "This quotation has already been approved.")
            return redirect('quote_portal', token=token)

        quote.status = Quotation.Status.APPROVED
        quote.approved_at = timezone.now()
        quote.save(update_fields=['status', 'approved_at'])

        # 1. Automatically create Logistics Job
        po = quote.purchase_order
        job = LogisticsJob.objects.create(
            quote=quote,
            customer=quote.customer,
            status=LogisticsJob.Status.BOOKED,
            pickup_address=po.pickup_location if po else quote.customer.physical_address,
            delivery_address=po.delivery_location if po else 'As specified',
            cargo_summary=po.cargo_description if po else 'General Freight',
            weight_tons=po.weight_tons if po else None,
            scheduled_date=timezone.now().date(),
            notes=f"Auto-generated from approved Quote #{quote.quote_number} (PO #{po.po_number if po else 'N/A'})"
        )

        # 2. Automatically generate first invoice according to Customer Payment Terms
        terms = quote.customer.payment_terms
        due_date = timezone.now().date() + timezone.timedelta(days=7)

        if terms == '100_UPFRONT':
            inv = Invoice.objects.create(
                job=job,
                quote=quote,
                customer=quote.customer,
                invoice_type=Invoice.InvoiceType.FULL,
                due_date=due_date,
                notes="100% Upfront payment before truck dispatch."
            )
            for item in quote.line_items.all():
                InvoiceLineItem.objects.create(
                    invoice=inv,
                    description=item.description,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                )
            inv.recalculate_totals()
            generate_invoice_pdf(inv)

        elif terms in ['50_DEPOSIT_50_POD', '30_DEPOSIT_70_POD']:
            pct = 50 if terms == '50_DEPOSIT_50_POD' else 30
            inv = Invoice.objects.create(
                job=job,
                quote=quote,
                customer=quote.customer,
                invoice_type=Invoice.InvoiceType.PARTIAL_DEPOSIT,
                due_date=due_date,
                notes=f"{pct}% Mobilization deposit required prior to loading."
            )
            deposit_subtotal = (quote.subtotal * (pct / 100))
            InvoiceLineItem.objects.create(
                invoice=inv,
                description=f"{pct}% Mobilization Deposit for Job #{job.job_number} ({quote.quote_number})",
                quantity=1,
                unit_price=deposit_subtotal,
            )
            inv.recalculate_totals()
            generate_invoice_pdf(inv)

        # Send notification email with the new invoice to the customer
        if 'inv' in locals() and quote.customer.email:
            send_departmental_email(
                department='invoicing',
                recipient_list=[quote.customer.email],
                subject=f"Tax Invoice #{inv.invoice_number} – Menard Trading CC",
                template_name='emails/invoice_sent.html',
                context={'invoice': inv},
                attachments=[(f"{inv.invoice_number}.pdf", inv.invoice_pdf.read(), 'application/pdf')] if inv.invoice_pdf else None
            )

        messages.success(request, f"Quotation #{quote.quote_number} approved! Job #{job.job_number} created.")
        return redirect('quote_portal', token=token)


class SendQuoteEmailView(PermissionRequiredMixin, View):
    """
    Staff action: Sends quotation PDF with interactive 1-click approval link to customer via Brevo.
    """
    permission_required = 'quotations.send'

    def post(self, request, pk):
        quote = get_object_or_404(Quotation, pk=pk)
        
        # Ensure totals and PDF exist
        quote.recalculate_totals()
        pdf_bytes = generate_quotation_pdf(quote)

        approval_url = request.build_absolute_uri(f"/portal/quotes/{quote.approval_token}/")

        success, msg = send_departmental_email(
            department='quotes',
            recipient_list=[quote.customer.email],
            subject=f"Quotation #{quote.quote_number} – Menard Trading CC",
            template_name='emails/quote_sent.html',
            context={
                'quote': quote,
                'approval_url': approval_url
            },
            attachments=[(f"{quote.quote_number}.pdf", pdf_bytes, 'application/pdf')] if pdf_bytes else None
        )

        if success:
            quote.status = Quotation.Status.SENT
            quote.sent_at = timezone.now()
            quote.save(update_fields=['status', 'sent_at'])
            messages.success(request, f"Quotation #{quote.quote_number} emailed to {quote.customer.email}.")
        else:
            messages.error(request, f"Failed to send email: {msg}")

        return redirect(request.META.get('HTTP_REFERER', 'quotes_list'))


class CreateQuotationView(PermissionRequiredMixin, View):
    """
    Manually create a real quotation for a customer with custom line items.
    """
    permission_required = 'quotations.create'
    def post(self, request):
        from apps.customers.models import Customer
        from apps.quotes.models import QuoteLineItem
        from decimal import Decimal

        customer_id = request.POST.get('customer_id') or request.POST.get('customer')
        customer = get_object_or_404(Customer, pk=customer_id)
        
        notes = request.POST.get('notes', 'Standard freight transport terms apply.')
        valid_days = int(request.POST.get('validity_days') or request.POST.get('valid_days') or 14)

        quote = Quotation.objects.create(
            customer=customer,
            valid_until=timezone.now().date() + timezone.timedelta(days=valid_days),
            notes=notes,
            status=Quotation.Status.DRAFT
        )

        descriptions = request.POST.getlist('descriptions[]') or request.POST.getlist('description')
        quantities = request.POST.getlist('quantities[]') or request.POST.getlist('quantity')
        unit_prices = request.POST.getlist('unit_prices[]') or request.POST.getlist('unit_price')
        item_types = request.POST.getlist('item_types[]') or request.POST.getlist('item_type')

        items_created = 0
        if descriptions:
            for idx, desc in enumerate(descriptions):
                desc_str = str(desc).strip()
                if not desc_str:
                    continue
                qty_raw = quantities[idx] if idx < len(quantities) else '1.00'
                price_raw = unit_prices[idx] if idx < len(unit_prices) else '0.00'
                itype = item_types[idx] if idx < len(item_types) else QuoteLineItem.ItemType.OTHER
                try:
                    qty = Decimal(str(qty_raw).strip() or '1.00')
                except Exception:
                    qty = Decimal('1.00')
                try:
                    price = Decimal(str(price_raw).strip() or '0.00')
                except Exception:
                    price = Decimal('0.00')

                QuoteLineItem.objects.create(
                    quote=quote,
                    description=desc_str,
                    quantity=qty,
                    unit_price=price,
                    item_type=itype if itype in QuoteLineItem.ItemType.values else QuoteLineItem.ItemType.OTHER
                )
                items_created += 1

        if items_created == 0:
            QuoteLineItem.objects.create(
                quote=quote,
                description=request.POST.get('description', 'General Supply & Logistics Service') or 'General Supply & Logistics Service',
                quantity=Decimal(request.POST.get('quantity', '1.00') or '1.00'),
                unit_price=Decimal(request.POST.get('unit_price', '0.00') or '0.00'),
                item_type=QuoteLineItem.ItemType.OTHER
            )

        quote.recalculate_totals()
        generate_quotation_pdf(quote)

        messages.success(request, f"Created Quotation #{quote.quote_number} for {customer.company_name} (Total: N$ {quote.total_amount}).")
        return redirect('quotes_list')


class QuotationPreviewView(PermissionRequiredMixin, View):
    permission_required = 'quotations.view'

    def get(self, request, pk):
        quote = get_object_or_404(Quotation, pk=pk)
        return render(request, 'documents/quotation_preview.html', {
            'quote': quote,
            'active_tab': 'quotes'
        })

