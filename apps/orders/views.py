import email.utils
import re
import logging
from django.shortcuts import render, get_object_or_404, redirect
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
            quote = process_inbound_purchase_order(po)
            messages.success(
                request,
                f"Successfully parsed PO #{po.po_number}! Generated Draft Quotation #{quote.quote_number} (Total: R{quote.total_amount})."
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

        recipient_name = po.customer.contact_name if (po.customer and po.customer.contact_name) else (po.customer.company_name if po.customer else 'Customer')

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

