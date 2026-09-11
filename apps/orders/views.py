import email.utils
import re
import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse
from django.views import View
from django.contrib import messages
from django.conf import settings
from apps.accounts.permissions import PermissionRequiredMixin
from apps.orders.models import PurchaseOrder, OrderCommunication
from apps.customers.models import Customer
from apps.orders.services import process_inbound_purchase_order
from menard_core.brevo_email import send_departmental_email

logger = logging.getLogger(__name__)


def extract_clean_email(raw_address: str) -> str:
    """
    Extracts a clean email address from strings like 'Name <user@example.com>' or 'user@example.com'.
    """
    if not raw_address:
        return ''
    _, addr = email.utils.parseaddr(raw_address.strip())
    if addr and '@' in addr:
        return addr.strip().lower()
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', raw_address)
    if match:
        return match.group(0).strip().lower()
    return raw_address.strip()


class UploadPurchaseOrderView(PermissionRequiredMixin, View):
    """
    Allows staff/operators to upload a real customer PO PDF document.
    Automatically triggers the extraction parser, customer mapping, and quotation generator.
    """
    permission_required = 'orders.upload'
    def post(self, request):
        po_file = request.FILES.get('po_file')
        po_number = request.POST.get('po_number', '').strip()
        customer_id = request.POST.get('customer_id')
        raw_email = extract_clean_email(request.POST.get('sender_email', ''))

        if not po_file:
            messages.error(request, "Please select a PO document (PDF) to upload.")
            return redirect('dashboard_index')

        customer = None
        if customer_id:
            customer = Customer.objects.filter(pk=customer_id).first()

        po = PurchaseOrder.objects.create(
            po_number=po_number or f"PO-{po_file.name.split('.')[0][:20].upper()}",
            customer=customer,
            po_file=po_file,
            raw_email_sender=raw_email or (customer.email if customer else ''),
            raw_email_subject=f"Manual Upload: {po_file.name}",
            source=PurchaseOrder.Source.MANUAL_UPLOAD,
            status=PurchaseOrder.Status.RECEIVED
        )

        try:
            from menard_core.formatters import format_money
            quote = process_inbound_purchase_order(po)
            messages.success(
                request,
                f"Successfully parsed PO #{po.po_number}! Generated Draft Quotation #{quote.quote_number} (Total: {format_money(quote.total_amount, 'R')})."
            )
        except Exception as e:
            logger.error(f"Error parsing uploaded PO: {e}", exc_info=True)
            messages.warning(request, f"PO uploaded as #{po.po_number}, but parser encountered an issue: {e}")

        return redirect('purchase_order_detail', pk=po.id)


class PurchaseOrderDetailView(PermissionRequiredMixin, View):
    """
    Full detailed view of an inbound Purchase Order:
    - Raw email sender, subject, and body text
    - Downloadable and viewable original attachments (PDF)
    - Extracted logistics details & structured JSON data
    - Associated Quotation & Logistics Job status
    - Outgoing communication history & reply composer
    """
    permission_required = 'orders.view'

    def get(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        communications = po.communications.all()
        
        quote = getattr(po, 'quotation', None)
        job = None
        if quote and hasattr(quote, 'job'):
            job = quote.job

        raw_recipient = po.customer.email if (po.customer and po.customer.email) else po.raw_email_sender
        clean_recipient = extract_clean_email(raw_recipient)

        context = {
            'po': po,
            'quote': quote,
            'job': job,
            'communications': communications,
            'queued_emails': po.queued_emails.all().order_by('-created_at'),
            'default_reply_subject': f"Re: {po.raw_email_subject or ('PO #' + po.po_number)} | Menard Trading CC",
            'default_recipient': clean_recipient,
        }
        return render(request, 'orders/po_detail.html', context)


class ReplyPurchaseOrderView(PermissionRequiredMixin, View):
    """
    Allows operations admins to send a reply/clarification email to the customer
    directly from orders@menardtrading.com or quotes@menardtrading.com.
    """
    permission_required = 'orders.email'
    def post(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        raw_recipient_email = request.POST.get('recipient_email', '').strip()
        recipient_email = extract_clean_email(raw_recipient_email)
        department = request.POST.get('department', 'orders')
        subject = request.POST.get('subject', '').strip()
        message_body = request.POST.get('message_body', '').strip()

        if not recipient_email or not message_body:
            messages.error(request, "A valid recipient email address and message body are required.")
            return redirect('purchase_order_detail', pk=pk)

        recipient_name = po.customer.contact_name if (po.customer and po.customer.contact_name) else (po.customer.company_name if po.customer else 'Client')

        try:
            # Send email via Brevo / SMTP
            send_departmental_email(
                department=department,
                recipient_list=[recipient_email],
                subject=subject or f"Regarding Purchase Order #{po.po_number} | Menard Trading CC",
                template_name='emails/po_operator_reply.html',
                context={
                    'po': po,
                    'recipient_name': recipient_name,
                    'message_body': message_body,
                }
            )

            # Log communication in database
            OrderCommunication.objects.create(
                purchase_order=po,
                sender_department=department,
                recipient_email=recipient_email,
                subject=subject,
                message_body=message_body,
            )

            messages.success(request, f"Reply dispatched successfully to {recipient_email} from {department}@menardtrading.com!")
        except Exception as e:
            logger.error(f"Failed to send reply for PO #{po.po_number}: {e}", exc_info=True)
            messages.error(request, f"Error sending reply email: {e}")

        return redirect('purchase_order_detail', pk=pk)


class PurchaseOrderDataAPIView(PermissionRequiredMixin, View):
    """
    Returns full JSON representation of a Purchase Order for interactive merge and edit modals.
    """
    permission_required = 'orders.view'

    def get(self, request, pk):
        po = get_object_or_404(PurchaseOrder, pk=pk)
        return JsonResponse({
            'success': True,
            'id': po.id,
            'po_number': po.po_number,
            'customer_id': po.customer_id,
            'customer_name': po.customer.company_name if po.customer else (po.raw_email_sender or 'Unassigned Client'),
            'customer_email': po.customer.email if po.customer else po.raw_email_sender,
            'cargo_description': po.cargo_description or '',
            'weight_tons': str(po.weight_tons) if po.weight_tons is not None else '',
            'volume_cbm': str(po.volume_cbm) if po.volume_cbm is not None else '',
            'quantity_pallets': po.quantity_pallets or '',
            'pickup_location': po.pickup_location or '',
            'delivery_location': po.delivery_location or '',
            'special_instructions': po.special_instructions or '',
            'source': po.source,
            'status': po.status,
            'status_display': po.get_status_display(),
            'created_at': po.created_at.strftime('%d %b %Y %H:%M'),
            'has_quote': hasattr(po, 'quotation') and po.quotation is not None,
            'quote_number': po.quotation.quote_number if (hasattr(po, 'quotation') and po.quotation) else '',
        })


class MergePurchaseOrdersView(PermissionRequiredMixin, View):
    """
    Merges a secondary Purchase Order into a primary Purchase Order.
    Combines cargo descriptions, aggregates weights/volumes/pallets,
    re-links communications and quotations, and archives the secondary PO.
    """
    permission_required = ('orders.create', 'orders.view')

    def get(self, request):
        orders = PurchaseOrder.objects.exclude(status=PurchaseOrder.Status.CANCELLED).order_by('-created_at')
        primary_id = request.GET.get('primary_po_id', '')
        secondary_id = request.GET.get('secondary_po_id', '')

        return render(request, 'orders/order_merge.html', {
            'orders': orders,
            'primary_id': primary_id,
            'secondary_id': secondary_id,
            'active_tab': 'orders',
        })

    def post(self, request):
        from decimal import Decimal
        from django.utils import timezone
        from apps.accounts.audit import log_audit_event
        from apps.billing.pdf_services import generate_quotation_pdf

        primary_id = request.POST.get('primary_po_id')
        secondary_id = request.POST.get('secondary_po_id')

        if not primary_id or not secondary_id:
            messages.error(request, "Both a Primary PO and a Secondary PO are required for merging.")
            return redirect('orders_list')

        if str(primary_id) == str(secondary_id):
            messages.error(request, "Cannot merge a Purchase Order with itself. Please select two different POs.")
            return redirect('orders_list')

        primary_po = get_object_or_404(PurchaseOrder, pk=primary_id)
        secondary_po = get_object_or_404(PurchaseOrder, pk=secondary_id)

        # Combined PO Number
        combined_po_number = request.POST.get('combined_po_number', '').strip()
        if combined_po_number:
            if combined_po_number != primary_po.po_number and not PurchaseOrder.objects.filter(po_number=combined_po_number).exclude(pk=primary_po.id).exists():
                primary_po.po_number = combined_po_number

        # Combined Cargo Description
        custom_cargo = request.POST.get('cargo_description', '').strip()
        if custom_cargo:
            primary_po.cargo_description = custom_cargo
        else:
            cargo_parts = []
            if primary_po.cargo_description:
                cargo_parts.append(primary_po.cargo_description.strip())
            if secondary_po.cargo_description:
                cargo_parts.append(f"[Merged from PO #{secondary_po.po_number}]: {secondary_po.cargo_description.strip()}")
            primary_po.cargo_description = "\n".join(cargo_parts)

        # Combined Weights & Quantities
        custom_weight = request.POST.get('weight_tons')
        if custom_weight is not None and str(custom_weight).strip() != '':
            try:
                primary_po.weight_tons = Decimal(str(custom_weight).strip())
            except Exception:
                pass
        else:
            w1 = primary_po.weight_tons or Decimal('0.00')
            w2 = secondary_po.weight_tons or Decimal('0.00')
            total_w = w1 + w2
            if total_w > Decimal('0.00'):
                primary_po.weight_tons = total_w

        custom_vol = request.POST.get('volume_cbm')
        if custom_vol is not None and str(custom_vol).strip() != '':
            try:
                primary_po.volume_cbm = Decimal(str(custom_vol).strip())
            except Exception:
                pass
        else:
            v1 = primary_po.volume_cbm or Decimal('0.00')
            v2 = secondary_po.volume_cbm or Decimal('0.00')
            total_v = v1 + v2
            if total_v > Decimal('0.00'):
                primary_po.volume_cbm = total_v

        custom_pallets = request.POST.get('quantity_pallets')
        if custom_pallets is not None and str(custom_pallets).strip() != '':
            try:
                primary_po.quantity_pallets = int(custom_pallets)
            except Exception:
                pass
        else:
            p1 = primary_po.quantity_pallets or 0
            p2 = secondary_po.quantity_pallets or 0
            total_p = p1 + p2
            if total_p > 0:
                primary_po.quantity_pallets = total_p

        # Combined Pickup & Delivery Locations
        if not primary_po.pickup_location and secondary_po.pickup_location:
            primary_po.pickup_location = secondary_po.pickup_location
        if not primary_po.delivery_location and secondary_po.delivery_location:
            primary_po.delivery_location = secondary_po.delivery_location

        # Combined Special Instructions
        custom_instructions = request.POST.get('special_instructions', '').strip()
        if custom_instructions:
            primary_po.special_instructions = custom_instructions
        else:
            instr_parts = []
            if primary_po.special_instructions:
                instr_parts.append(primary_po.special_instructions.strip())
            if secondary_po.special_instructions:
                instr_parts.append(f"[Merged from PO #{secondary_po.po_number}]: {secondary_po.special_instructions.strip()}")
            primary_po.special_instructions = "\n".join(instr_parts)

        # Customer assignment fallback
        if not primary_po.customer and secondary_po.customer:
            primary_po.customer = secondary_po.customer

        primary_po.save()

        # Re-link communications and email messages
        secondary_po.communications.update(purchase_order=primary_po)
        secondary_po.inbound_messages.update(purchase_order=primary_po)

        # Merge Quotations if present
        pri_quote = getattr(primary_po, 'quotation', None)
        sec_quote = getattr(secondary_po, 'quotation', None)

        if pri_quote and sec_quote:
            for item in sec_quote.line_items.all():
                item.quote = pri_quote
                item.save()
            pri_quote.recalculate_totals()
            generate_quotation_pdf(pri_quote)
            sec_quote.delete()
        elif not pri_quote and sec_quote:
            sec_quote.purchase_order = primary_po
            sec_quote.save()

        # Archive secondary PO
        sec_num = secondary_po.po_number
        secondary_po.status = PurchaseOrder.Status.CANCELLED
        note = f"\n[Merged into PO #{primary_po.po_number} by {request.user.username} on {timezone.now().strftime('%Y-%m-%d %H:%M')}]"
        secondary_po.special_instructions = (secondary_po.special_instructions or '') + note
        secondary_po.save()

        log_audit_event(
            request=request,
            user=request.user,
            action='PURCHASE_ORDER_MERGED',
            resource_type='PurchaseOrder',
            resource_id=primary_po.po_number,
            details={
                'primary_po': primary_po.po_number,
                'secondary_po': sec_num,
                'merged_weight': str(primary_po.weight_tons or ''),
                'customer': primary_po.customer.company_name if primary_po.customer else ''
            },
            result='SUCCESS'
        )

        messages.success(
            request,
            f"Successfully merged PO #{sec_num} into PO #{primary_po.po_number}! All cargo items, notes, weights, communications, and quotations have been consolidated."
        )
        return redirect('purchase_order_detail', pk=primary_po.id)


