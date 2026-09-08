import logging
from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from apps.orders.models import InboundEmailMessage, EmailQueueMessage, PurchaseOrder, OrderCommunication
from apps.accounts.models import AdminNotification
from menard_core.brevo_email import send_departmental_email

logger = logging.getLogger(__name__)

# Exponential backoff schedule in seconds: 1m, 5m, 15m, 30m
BACKOFF_DELAYS = [60, 300, 900, 1800]


def process_outbound_queue(batch_size: int = 25) -> dict:
    """
    Processes pending and retrying outbound emails from EmailQueueMessage.
    Dispatches via Brevo SMTP with exponential backoff and persistent state tracking.
    Uses row-level locking (select_for_update) to guarantee safe concurrent execution.
    """
    now = timezone.now()
    results = {'sent': 0, 'retrying': 0, 'failed': 0}

    # Query candidate queue items
    candidate_ids = list(
        EmailQueueMessage.objects.filter(
            status__in=[EmailQueueMessage.Status.QUEUED, EmailQueueMessage.Status.SENDING],
            next_attempt_at__lte=now
        ).order_by('next_attempt_at', 'created_at')
        .values_list('id', flat=True)[:batch_size]
    )

    if not candidate_ids:
        return results

    for msg_id in candidate_ids:
        try:
            with transaction.atomic():
                msg = EmailQueueMessage.objects.select_for_update(skip_locked=True).filter(id=msg_id).first()
                if not msg or msg.status in [EmailQueueMessage.Status.SENT, EmailQueueMessage.Status.CANCELLED]:
                    continue

                msg.status = EmailQueueMessage.Status.SENDING
                msg.attempts += 1
                msg.save(update_fields=['status', 'attempts', 'updated_at'])

            # Rehydrate context with model instances if available
            dispatch_context = dict(msg.context_data or {})
            if msg.purchase_order and 'po' not in dispatch_context:
                dispatch_context['po'] = msg.purchase_order

            # Send email outside atomic lock to prevent holding DB transactions during network I/O
            success, error_or_msg = send_departmental_email(
                department=msg.department,
                recipient_list=msg.recipient_list,
                subject=msg.subject,
                template_name=msg.template_name,
                context=dispatch_context,
                reply_to=msg.reply_to,
                attachments=msg.attachments_data or None
            )

            with transaction.atomic():
                msg = EmailQueueMessage.objects.select_for_update().filter(id=msg_id).first()
                if not msg:
                    continue

                if success:
                    msg.status = EmailQueueMessage.Status.SENT
                    msg.sent_at = timezone.now()
                    msg.last_error = ""
                    msg.save(update_fields=['status', 'sent_at', 'last_error', 'updated_at'])
                    results['sent'] += 1

                    # Update associated PurchaseOrder if acknowledgment
                    if msg.purchase_order:
                        po = msg.purchase_order
                        po.acknowledgment_sent = True
                        po.acknowledgment_sent_at = msg.sent_at
                        po.save(update_fields=['acknowledgment_sent', 'acknowledgment_sent_at'])

                        # Record in OrderCommunication history
                        OrderCommunication.objects.create(
                            purchase_order=po,
                            sender_department=msg.department,
                            recipient_email=", ".join(msg.recipient_list),
                            subject=msg.subject,
                            message_body=f"Automated email dispatched successfully via Brevo SMTP."
                        )

                    logger.info(f"Worker: Email [{msg.id}] successfully sent to {msg.recipient_list}")

                else:
                    msg.last_error = error_or_msg
                    if msg.attempts < msg.max_attempts:
                        # Schedule next attempt with exponential backoff
                        delay_secs = BACKOFF_DELAYS[min(msg.attempts - 1, len(BACKOFF_DELAYS) - 1)]
                        msg.next_attempt_at = timezone.now() + timezone.timedelta(seconds=delay_secs)
                        msg.status = EmailQueueMessage.Status.QUEUED
                        msg.save(update_fields=['status', 'next_attempt_at', 'last_error', 'updated_at'])
                        results['retrying'] += 1
                        logger.warning(
                            f"Worker: Email [{msg.id}] attempt {msg.attempts}/{msg.max_attempts} failed: {error_or_msg}. "
                            f"Retrying in {delay_secs}s at {msg.next_attempt_at}"
                        )
                    else:
                        # Max retries exhausted -> Permanent failure
                        msg.status = EmailQueueMessage.Status.FAILED
                        msg.save(update_fields=['status', 'last_error', 'updated_at'])
                        results['failed'] += 1
                        logger.error(f"Worker: Email [{msg.id}] permanently failed after {msg.attempts} attempts: {error_or_msg}")

                        # Alert administrators via AdminNotification
                        AdminNotification.objects.create(
                            title=f"Email Delivery Failed: {msg.subject[:50]}",
                            message=f"Outgoing email to {msg.recipient_list} failed after {msg.attempts} attempts. Reason: {error_or_msg}",
                            notification_type=AdminNotification.NotificationType.EMAIL_FAILED,
                            link_url=f"/orders/{msg.purchase_order.id}/" if msg.purchase_order else "/orders/"
                        )

        except Exception as e:
            logger.error(f"Worker error processing queue message {msg_id}: {e}", exc_info=True)

    return results


def process_inbound_queue(batch_size: int = 15) -> dict:
    """
    Asynchronously parses and converts pending InboundEmailMessage records into
    PurchaseOrders, Customers, and Draft Quotations.
    """
    from apps.orders.services import process_inbound_purchase_order

    results = {'processed': 0, 'failed': 0}
    pending_items = list(
        InboundEmailMessage.objects.filter(status=InboundEmailMessage.Status.RECEIVED)
        .order_by('created_at')[:batch_size]
    )

    for item in pending_items:
        try:
            item.status = InboundEmailMessage.Status.PROCESSING
            item.save(update_fields=['status'])

            po = item.purchase_order
            if not po:
                # Create PurchaseOrder record from inbound email
                po_num = f"PO-{item.message_id[:12].upper().replace('@', '-').replace('.', '-')}"
                po = PurchaseOrder.objects.create(
                    po_number=po_num,
                    raw_email_sender=item.sender_email,
                    raw_email_subject=item.subject,
                    raw_email_body=item.body_text or item.body_html,
                    source=PurchaseOrder.Source.EMAIL,
                    status=PurchaseOrder.Status.RECEIVED
                )
                item.purchase_order = po

            # Run document extraction and quotation builder
            quote = process_inbound_purchase_order(po)

            item.status = InboundEmailMessage.Status.PROCESSED
            item.processed_at = timezone.now()
            item.save(update_fields=['status', 'processed_at', 'purchase_order'])
            results['processed'] += 1

            # Create unread notification for admins
            customer_name = po.customer.company_name if po.customer else po.raw_email_sender
            AdminNotification.objects.create(
                title=f"New Purchase Order Received: #{po.po_number}",
                message=f"Inbound order from {customer_name}. Draft Quotation #{quote.quote_number} generated (Total: R{quote.total_amount:,.2f}).",
                notification_type=AdminNotification.NotificationType.NEW_ORDER,
                link_url=f"/orders/{po.id}/"
            )
            logger.info(f"Worker: Inbound email [{item.id}] successfully processed -> PO #{po.po_number}")

        except Exception as e:
            item.status = InboundEmailMessage.Status.FAILED
            item.processing_error = str(e)
            item.save(update_fields=['status', 'processing_error'])
            results['failed'] += 1
            logger.error(f"Worker: Inbound email [{item.id}] processing failed: {e}", exc_info=True)

    return results


def run_worker_cycle() -> dict:
    """
    Executes one complete background worker cycle:
    1. Processes pending inbound email ingestion.
    2. Processes pending outbound email queue with retries.
    """
    inbound_res = process_inbound_queue()
    outbound_res = process_outbound_queue()
    return {
        'inbound': inbound_res,
        'outbound': outbound_res,
    }
