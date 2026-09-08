import os
import email
import imaplib
import logging
from decimal import Decimal
from django.utils import timezone
from django.conf import settings
from django.core.files.base import ContentFile
from apps.orders.models import PurchaseOrder, InboundEmailMessage, EmailQueueMessage
from apps.orders.services import process_inbound_purchase_order
from apps.orders.email_worker import process_inbound_queue, process_outbound_queue
from menard_core.brevo_email import queue_departmental_email, clean_email

logger = logging.getLogger(__name__)


def get_unique_po_number(base_ref: str) -> str:
    """
    Ensures PO numbers are always unique and prevents database integrity constraint collisions.
    """
    candidate = base_ref.strip().replace(' ', '-')
    if not candidate:
        candidate = f"PO-{timezone.now().strftime('%Y%m%d%H%M%S')}"
    
    if not PurchaseOrder.objects.filter(po_number__iexact=candidate).exists():
        return candidate
    
    counter = 1
    while PurchaseOrder.objects.filter(po_number__iexact=f"{candidate}-{counter}").exists():
        counter += 1
    return f"{candidate}-{counter}"


def sync_orders_mailbox():
    """
    Autonomous IMAP Ingestion Service:
    - Connects to the orders@menardtrading.com mailbox via IMAP (SSL).
    - Ingests incoming emails into InboundEmailMessage with deduplication.
    - Queues optimistic auto-acknowledgments and creates PurchaseOrders.
    - Can be executed independently in the background via cron or management command.
    """
    host = getattr(settings, 'IMAP_HOST', 'mail.menardtrading.com')
    port = getattr(settings, 'IMAP_PORT', 993)
    user = getattr(settings, 'IMAP_USER', 'orders@menardtrading.com')
    password = getattr(settings, 'EMAIL_PASSWORD', '')

    if not password:
        logger.warning("IMAP sync skipped: EMAIL_PASSWORD is not configured in settings/environment.")
        return {'status': 'skipped', 'message': 'EMAIL_PASSWORD not set', 'synced_count': 0, 'orders': []}

    created_orders = []

    try:
        mail = imaplib.IMAP4_SSL(host, port=port, timeout=15)
        mail.login(user, password)
        mail.select('INBOX')

        status, messages = mail.search(None, 'ALL')
        if status != 'OK' or not messages or not messages[0]:
            mail.logout()
            return {'status': 'success', 'synced_count': 0, 'orders': []}

        msg_ids = messages[0].split()

        for msg_id in msg_ids:
            try:
                res, data = mail.fetch(msg_id, '(RFC822)')
                if res != 'OK':
                    continue

                for response_part in data:
                    if not isinstance(response_part, tuple):
                        continue

                    raw_msg = response_part[1]
                    msg = email.message_from_bytes(raw_msg)

                    subject = msg.get('Subject', 'PO Email')
                    # Decode subject if encoded
                    from email.header import decode_header
                    decoded_pieces = decode_header(subject)
                    subject_str = ""
                    for text, encoding in decoded_pieces:
                        if isinstance(text, bytes):
                            subject_str += text.decode(encoding or 'utf-8', errors='ignore')
                        else:
                            subject_str += str(text)

                    sender_raw = msg.get('From', '')
                    sender_name, sender_email = email.utils.parseaddr(sender_raw)
                    sender_email = clean_email(sender_email or sender_raw)

                    # Extract Reply-To
                    reply_to_raw = msg.get('Reply-To', '')
                    _, reply_to_email = email.utils.parseaddr(reply_to_raw)
                    reply_to_email = clean_email(reply_to_email) or sender_email

                    # RFC Message-ID for strict idempotency
                    rfc_msg_id = (msg.get('Message-ID') or msg.get('Message-Id') or f"IMAP-{msg_id.decode()}-{sender_email}").strip()

                    # Ignore automated bounce / delivery notification emails
                    lower_sender = sender_raw.lower()
                    lower_subject = subject_str.lower()
                    if any(bad in lower_sender for bad in ['mailer-daemon', 'postmaster', 'no-reply', 'bounce', 'donotreply']) or \
                       any(bad in lower_subject for bad in ['undelivered mail', 'delivery status notification', 'failure notice', 'returned to sender', 'message blocked', 'message rejected']):
                        continue

                    # Atomic Idempotency Check
                    inbound_record, created = InboundEmailMessage.objects.get_or_create(
                        message_id=rfc_msg_id,
                        defaults={
                            'sender_email': sender_email,
                            'sender_name': sender_name,
                            'recipient_email': user,
                            'reply_to': reply_to_email,
                            'subject': subject_str,
                            'status': InboundEmailMessage.Status.RECEIVED,
                        }
                    )

                    if not created:
                        continue  # Already ingested

                    # Extract body text and PDF attachments
                    body_text = ""
                    pdf_filename = None
                    pdf_bytes = None

                    for part in msg.walk():
                        content_type = part.get_content_type()
                        disposition = str(part.get('Content-Disposition'))

                        if content_type == 'text/plain' and 'attachment' not in disposition:
                            try:
                                body_text += part.get_payload(decode=True).decode('utf-8', errors='ignore') + "\n"
                            except Exception:
                                pass
                        elif content_type == 'text/html' and not body_text and 'attachment' not in disposition:
                            try:
                                body_text += part.get_payload(decode=True).decode('utf-8', errors='ignore') + "\n"
                            except Exception:
                                pass

                        filename = part.get_filename()
                        if filename:
                            if filename.lower().endswith('.pdf') or 'pdf' in content_type:
                                pdf_filename = filename
                                pdf_bytes = part.get_payload(decode=True)

                    inbound_record.body_text = body_text.strip()
                    inbound_record.save(update_fields=['body_text'])

                    unique_po_ref = get_unique_po_number(f"PO-{msg_id.decode()}")

                    po = PurchaseOrder.objects.create(
                        po_number=unique_po_ref,
                        raw_email_sender=sender_email,
                        raw_email_subject=subject_str,
                        raw_email_body=body_text.strip(),
                        source=PurchaseOrder.Source.EMAIL,
                        status=PurchaseOrder.Status.RECEIVED
                    )
                    inbound_record.purchase_order = po
                    inbound_record.save(update_fields=['purchase_order'])

                    if pdf_bytes and pdf_filename:
                        po.po_file.save(pdf_filename, ContentFile(pdf_bytes), save=True)

                    # Queue optimistic auto-acknowledgement
                    if reply_to_email or sender_email:
                        ack_target = reply_to_email or sender_email
                        queue_departmental_email(
                            department='no-reply',
                            recipient_list=[ack_target],
                            subject=f"Purchase Order Received – #{po.po_number} | Menard Trading CC",
                            template_name='emails/po_received_noreply.html',
                            context={
                                'po': po,
                                'customer_name': sender_name or "Procurement / Logistics"
                            },
                            reply_to='orders@menardtrading.com',
                            purchase_order=po,
                            inbound_email=inbound_record
                        )

                    # Trigger extraction and quote creation
                    process_inbound_purchase_order(po)
                    created_orders.append(po.po_number)

            except Exception as item_err:
                logger.error(f"Error processing IMAP message {msg_id}: {item_err}", exc_info=True)
                continue

        try:
            mail.logout()
        except Exception:
            pass

        # Trigger quick worker cycle
        try:
            process_outbound_queue(batch_size=10)
        except Exception as q_err:
            logger.warning(f"IMAP sync outbound queue flush notice: {q_err}")

        return {
            'status': 'success',
            'synced_count': len(created_orders),
            'orders': created_orders
        }

    except Exception as e:
        logger.error(f"IMAP sync failed: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'synced_count': 0,
            'orders': []
        }
