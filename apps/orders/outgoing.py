import logging
import os
from decimal import Decimal, InvalidOperation

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from apps.orders.models import OrderCommunication, PurchaseOrder
from apps.billing.pdf_services import render_html_to_pdf_bytes

logger = logging.getLogger(__name__)


def generate_outgoing_po_number():
    year = timezone.localdate().year
    prefix = f"PO-OUT-{year}-"
    last = PurchaseOrder.objects.filter(
        direction=PurchaseOrder.Direction.OUTGOING,
        po_number__startswith=prefix,
    ).order_by('-id').first()
    sequence = 1
    if last:
        try:
            sequence = int(last.po_number.rsplit('-', 1)[-1]) + 1
        except (TypeError, ValueError):
            sequence = PurchaseOrder.objects.filter(direction=PurchaseOrder.Direction.OUTGOING).count() + 1
    return f"{prefix}{sequence:04d}"


def generate_outgoing_po_pdf(po):
    pdf_bytes = render_html_to_pdf_bytes('pdfs/outgoing_purchase_order.html', {'po': po})
    if pdf_bytes:
        po.po_file.save(f"{po.po_number}.pdf", ContentFile(pdf_bytes), save=True)
    return pdf_bytes


def parse_decimal(value, field_name):
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, AttributeError):
        raise ValueError(f"{field_name} must be a valid number.")
    if amount < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return amount


def send_outgoing_po(po, user, request=None):
    """Generate and send an outgoing PO once, recording the send atomically."""
    from apps.accounts.audit import log_audit_event
    from menard_core.brevo_email import send_departmental_email

    with transaction.atomic():
        locked_po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
        if locked_po.direction != PurchaseOrder.Direction.OUTGOING:
            raise ValueError('Only outgoing purchase orders can be sent.')
        if locked_po.status in {
            PurchaseOrder.Status.SENT,
            PurchaseOrder.Status.VIEWED,
            PurchaseOrder.Status.ACCEPTED,
            PurchaseOrder.Status.COMPLETED,
        } or locked_po.sent_at:
            return locked_po, False, 'This purchase order has already been sent.'
        if not locked_po.recipient_email:
            raise ValueError('A recipient email address is required before sending.')

        pdf_bytes = generate_outgoing_po_pdf(locked_po)
        if not pdf_bytes:
            raise ValueError('The purchase order PDF could not be generated.')

        attachments = [(f'{locked_po.po_number}.pdf', pdf_bytes, 'application/pdf')]
        if locked_po.supporting_attachment and os.path.exists(locked_po.supporting_attachment.path):
            with locked_po.supporting_attachment.open('rb') as supporting_file:
                attachments.append((
                    os.path.basename(locked_po.supporting_attachment.name),
                    supporting_file.read(),
                    'application/octet-stream',
                ))

        success, message = send_departmental_email(
            department='orders',
            recipient_list=[locked_po.recipient_email],
            subject=f"Purchase Order #{locked_po.po_number} | Menard Trading CC",
            template_name='emails/outgoing_po_sent.html',
            context={'po': locked_po, 'recipient_name': locked_po.supplier.contact_name if locked_po.supplier else 'Service Provider'},
            attachments=attachments,
            reply_to=['orders@menardtrading.com'],
        )
        if not success:
            locked_po.email_send_status = 'FAILED'
            locked_po.save(update_fields=['email_send_status', 'updated_at'])
            raise ValueError(message)

        now = timezone.now()
        locked_po.status = PurchaseOrder.Status.SENT
        locked_po.sent_at = now
        locked_po.sent_by = user
        locked_po.email_send_status = 'SENT'
        locked_po.save(update_fields=['status', 'sent_at', 'sent_by', 'email_send_status', 'updated_at'])
        OrderCommunication.objects.create(
            purchase_order=locked_po,
            sender_department='orders',
            recipient_email=locked_po.recipient_email,
            subject=f"Purchase Order #{locked_po.po_number} | Menard Trading CC",
            message_body='Outgoing purchase order PDF dispatched to the supplier/service provider.',
        )
        log_audit_event(
            request=request,
            user=user,
            action='OUTGOING_PURCHASE_ORDER_SENT',
            resource_type='PurchaseOrder',
            resource_id=locked_po.po_number,
            details={'recipient': locked_po.recipient_email, 'status': locked_po.status},
        )
        return locked_po, True, 'Purchase order sent successfully.'
