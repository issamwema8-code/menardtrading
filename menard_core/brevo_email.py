import re
import email.utils
import logging
import mimetypes
from pathlib import Path
from django.conf import settings
from django.utils import timezone
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class AttachmentValidationError(ValueError):
    """Raised when an email attachment cannot be safely sent."""


def _attachment_bytes(item):
    if isinstance(item, (tuple, list)) and len(item) == 3:
        filename, content, mimetype = item
    elif isinstance(item, dict):
        filename = item.get('filename') or item.get('name')
        content = item.get('content')
        if content is None and item.get('path'):
            path = item['path']
            try:
                from django.core.files.storage import default_storage
                if default_storage.exists(path):
                    with default_storage.open(path, 'rb') as stored_file:
                        content = stored_file.read()
                else:
                    content = Path(path).read_bytes()
            except (OSError, IOError) as exc:
                raise AttachmentValidationError(f'Attachment {filename or path} could not be read: {exc}') from exc
        mimetype = item.get('mimetype') or mimetypes.guess_type(filename or '')[0]
    else:
        raise AttachmentValidationError('Attachment must be a (filename, bytes, mimetype) tuple or a file descriptor.')

    if not filename or content is None:
        raise AttachmentValidationError(f'Attachment {filename or "<unnamed>"} has no readable content.')
    if hasattr(content, 'read'):
        content = content.read()
    if isinstance(content, str):
        content = content.encode()
    if not isinstance(content, bytes) or not content:
        raise AttachmentValidationError(f'Attachment {filename} is empty or unreadable.')

    mimetype = mimetype or 'application/octet-stream'
    if str(filename).lower().endswith('.pdf'):
        if mimetype != 'application/pdf':
            raise AttachmentValidationError(f'PDF attachment {filename} must use MIME type application/pdf.')
        if not content.startswith(b'%PDF-'):
            raise AttachmentValidationError(f'Attachment {filename} is not a valid PDF.')
    return str(filename), content, mimetype


def validate_attachments(attachments):
    validated = []
    for item in attachments or []:
        validated.append(_attachment_bytes(item))
    return validated


def _document_context(context):
    for key, document_type in (
        ('invoice', 'Invoice'), ('quote', 'Quotation'), ('receipt', 'Receipt'), ('po', 'PurchaseOrder')
    ):
        document = (context or {}).get(key)
        if document is not None:
            identifier = getattr(document, 'invoice_number', None) or getattr(document, 'quote_number', None) or getattr(document, 'receipt_number', None) or getattr(document, 'po_number', None)
            return document_type, str(identifier or getattr(document, 'pk', ''))
    return '', ''

LOOPBACK_DISALLOWED_PATTERNS = [
    'orders@menardtrading.com',
    'quotes@menardtrading.com',
    'billing@menardtrading.com',
    'accounts@menardtrading.com',
    'logistics@menardtrading.com',
    'support@menardtrading.com',
    'info@menardtrading.com',
    'no-reply@menardtrading.com',
    'mailer-daemon',
    'postmaster',
    'no-reply',
    'noreply',
    'bounce',
    'donotreply',
    'notifications@',
]


def clean_email(raw_address: str) -> str:
    """
    Extracts a normalized, lowercase email address from strings like 'Name <user@example.com>' or 'user@example.com'.
    """
    if not raw_address:
        return ''
    _, addr = email.utils.parseaddr(raw_address.strip())
    if addr and '@' in addr:
        return addr.strip().lower()
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', raw_address)
    if match:
        return match.group(0).strip().lower()
    return raw_address.strip().lower()


def is_loopback_or_invalid_recipient(recipient: str) -> bool:
    """
    Guards against email self-reply loops and bounce loops.
    Disallows replying directly to orders@menardtrading.com, postmaster, or daemon addresses.
    """
    cleaned = clean_email(recipient)
    if not cleaned or '@' not in cleaned:
        return True
    
    # Check disallowed local and system patterns
    for pat in LOOPBACK_DISALLOWED_PATTERNS:
        if pat in cleaned:
            return True

    return False


DEPARTMENT_PURPOSE_MAP = {
    # Financial, Billing, Invoicing, Quotes & Receipts -> accounts@menardtrading.com
    'accounts': 'accounts',
    'billing': 'accounts',
    'invoicing': 'accounts',
    'invoice': 'accounts',
    'receipt': 'accounts',
    'receipts': 'accounts',
    'statement': 'accounts',
    'statements': 'accounts',
    'finance': 'accounts',
    'financial': 'accounts',
    'payment': 'accounts',
    'quotes': 'quotes',
    'quotation': 'quotes',
    'quotations': 'quotes',

    # Support -> support@menardtrading.com
    'support': 'support',
    'help': 'support',
    'assistance': 'support',

    # Info & General Enquiries -> info@menardtrading.com
    'info': 'info',
    'general': 'info',
    'enquiry': 'info',
    'enquiries': 'info',
    'otp': 'info',
    '2fa': 'info',
    'auth': 'info',
    'password_reset': 'info',

    # Orders -> orders@menardtrading.com
    'orders': 'orders',
    'order': 'orders',
    'po': 'orders',
    'inbound_po': 'orders',

    # Logistics & Operations -> logistics@menardtrading.com
    'operations': 'operations',
    'logistics': 'operations',
    'dispatch': 'operations',
    'pod': 'operations',

    # No-Reply Automatic System Notifications
    'no-reply': 'no-reply',
    'noreply': 'no-reply',
    'system': 'no-reply',
}


def resolve_department_key(department_or_purpose: str) -> str:
    """
    Centralized resolver that maps a business purpose or department key to an official EMAIL_CONFIGS channel.
    """
    key = str(department_or_purpose or '').strip().lower()
    return DEPARTMENT_PURPOSE_MAP.get(key, key if key in getattr(settings, 'EMAIL_CONFIGS', {}) else 'info')


def queue_departmental_email(
    department: str,
    recipient_list: list,
    subject: str,
    template_name: str,
    context: dict,
    reply_to: str = None,
    attachments: list = None,
    purchase_order=None,
    inbound_email=None,
):
    """
    Persists an email into EmailQueueMessage for reliable, asynchronous background delivery.
    Returns the created EmailQueueMessage instance or None if invalid recipients.
    """
    from apps.orders.models import EmailQueueMessage

    dept_key = resolve_department_key(department)

    if not isinstance(recipient_list, (list, tuple)):
        recipient_list = [recipient_list]

    valid_recipients = [clean_email(r) for r in recipient_list if clean_email(r)]
    if not valid_recipients:
        logger.warning(f"Skipping email queue: No valid recipients for subject '{subject}'")
        return None

    # Filter loopback for no-reply automatic replies
    if dept_key == 'no-reply':
        valid_recipients = [r for r in valid_recipients if not is_loopback_or_invalid_recipient(r)]
        if not valid_recipients:
            logger.warning(f"Skipping auto-reply to prevent loopback: {recipient_list}")
            return None

    # Sanitize context dictionary to JSON-safe primitives
    safe_context = {}
    for k, v in (context or {}).items():
        if hasattr(v, 'id') and hasattr(v, '__class__') and not isinstance(v, (str, int, float, bool, list, dict)):
            safe_context[f"{k}_id"] = v.id
            if hasattr(v, 'po_number'):
                safe_context['po_number'] = str(v.po_number)
            if hasattr(v, 'quote_number'):
                safe_context['quote_number'] = str(v.quote_number)
        else:
            safe_context[k] = v

    # Queue only durable attachment descriptors. Raw bytes must not be silently discarded.
    safe_attachments = []
    if attachments:
        for att in attachments:
            if isinstance(att, dict):
                descriptor = {'filename': att.get('filename'), 'mimetype': att.get('mimetype', 'application/pdf')}
                if att.get('path'):
                    descriptor['path'] = att['path']
                elif att.get('content') is not None:
                    raise AttachmentValidationError('Queued attachments require a durable path; raw bytes cannot be queued safely.')
                else:
                    raise AttachmentValidationError(f"Queued attachment {descriptor['filename']} has no durable content reference.")
                safe_attachments.append(descriptor)
            else:
                raise AttachmentValidationError('Queued attachments require a filename and durable path.')

    queue_msg = EmailQueueMessage.objects.create(
        department=dept_key,
        recipient_list=valid_recipients,
        reply_to=clean_email(reply_to) if reply_to else '',
        subject=subject,
        template_name=template_name,
        context_data=safe_context,
        attachments_data=safe_attachments,
        status=EmailQueueMessage.Status.QUEUED,
        next_attempt_at=timezone.now(),
        purchase_order=purchase_order,
        inbound_email=inbound_email,
    )
    logger.info(f"Queued email [{queue_msg.id}] to {valid_recipients} - Subject: '{subject}'")
    return queue_msg


def send_departmental_email(
    department: str,
    recipient_list: list,
    subject: str,
    template_name: str,
    context: dict,
    attachments: list = None,
    reply_to: list = None,
    cc: list = None,
    bcc: list = None,
):
    """
    Dispatches a transactional email using departmental sender profiles.
    
    :param department: Purpose or channel key ('accounts', 'support', 'info', 'orders', 'quotes', 'invoicing', 'operations', 'no-reply')
    :param recipient_list: List of recipient email addresses
    :param subject: Email subject line
    :param template_name: Relative template path (e.g., 'emails/invoice_sent.html')
    :param context: Template context dictionary
    :param attachments: List of tuples/dicts [(filename, content_bytes, mimetype)]
    :param reply_to: List of reply-to emails
    :return: (bool success, str message)
    """
    dept_key = resolve_department_key(department)

    if not isinstance(recipient_list, (list, tuple)):
        recipient_list = [recipient_list]

    clean_recipients = [clean_email(r) for r in recipient_list if clean_email(r)]
    if not clean_recipients:
        return False, "No valid recipient email addresses provided"

    # Filter loopback for no-reply automatic replies
    if dept_key == 'no-reply':
        clean_recipients = [r for r in clean_recipients if not is_loopback_or_invalid_recipient(r)]
        if not clean_recipients:
            return False, "Recipient is a loopback or system email; automated reply suppressed"

    config = settings.EMAIL_CONFIGS.get(dept_key, settings.EMAIL_CONFIGS.get('info', {}))
    from_email = config.get('DEFAULT_FROM_EMAIL', settings.DEFAULT_FROM_EMAIL)

    try:
        from apps.accounts.models import CompanySettings
        company_branding = CompanySettings.get_settings().as_branding_dict()
    except Exception:
        company_branding = getattr(settings, 'EMAIL_BRANDING', {})

    # Inject dynamic company branding into template context
    full_context = {
        'branding': company_branding,
        'company': company_branding,
        'base_url': getattr(settings, 'BASE_URL', 'https://menardtrading.com'),
        **context,
    }

    delivery = None
    document_type, document_id = _document_context(context)
    try:
        from apps.orders.models import DocumentEmailDelivery
        if document_type and clean_recipients:
            sender_address = config.get('DEFAULT_FROM_EMAIL', settings.DEFAULT_FROM_EMAIL)
            delivery = DocumentEmailDelivery.objects.create(
                document_type=document_type,
                document_id=document_id,
                recipient=clean_recipients[0],
                sender=sender_address,
                subject=subject,
                status=DocumentEmailDelivery.Status.GENERATING,
            )
    except Exception:
        logger.exception('Could not create document email delivery record')

    try:
        validated_attachments = validate_attachments(attachments)
        claims_attachment = any(token in (html_content := render_to_string(template_name, full_context)).lower() for token in ('attached', 'attachment'))
        if claims_attachment and not validated_attachments:
            raise AttachmentValidationError('Email template claims a document is attached, but no attachment was supplied.')

        # Render HTML and text versions
        text_content = strip_tags(html_content)

        # Create explicit SMTP connection for this department
        connection = get_connection(
            backend=settings.EMAIL_BACKEND,
            host=config.get('EMAIL_HOST'),
            port=config.get('EMAIL_PORT'),
            username=config.get('EMAIL_HOST_USER'),
            password=config.get('EMAIL_HOST_PASSWORD'),
            use_tls=config.get('EMAIL_USE_TLS', True),
            use_ssl=config.get('EMAIL_USE_SSL', False),
            fail_silently=False,
        )

        resolved_reply_to = []
        if reply_to:
            if isinstance(reply_to, str):
                resolved_reply_to = [clean_email(reply_to)]
            else:
                resolved_reply_to = [clean_email(r) for r in reply_to if clean_email(r)]
        
        if not resolved_reply_to:
            # Default reply-to for no-reply is orders@menardtrading.com so customer replies go to operations
            if dept_key == 'no-reply':
                resolved_reply_to = ['orders@menardtrading.com']
            else:
                # Clean address from from_email (e.g. accounts@menardtrading.com, support@menardtrading.com, info@menardtrading.com)
                clean_from = clean_email(from_email)
                resolved_reply_to = [clean_from] if clean_from else [from_email]

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=clean_recipients,
            reply_to=resolved_reply_to,
            cc=cc,
            bcc=bcc,
            connection=connection,
        )
        email.attach_alternative(html_content, "text/html")

        # Process attachments
        for filename, content, mimetype in validated_attachments:
            email.attach(filename, content, mimetype)

        attached_names = {attachment[0] for attachment in email.attachments}
        expected_names = {filename for filename, _, _ in validated_attachments}
        if not expected_names.issubset(attached_names):
            raise AttachmentValidationError('One or more validated attachments were not added to the outgoing email.')

        if delivery:
            delivery.status = DocumentEmailDelivery.Status.SENDING
            delivery.attachment_manifest = [
                {'filename': filename, 'mimetype': mimetype, 'size': len(content)}
                for filename, content, mimetype in validated_attachments
            ]
            delivery.save(update_fields=['status', 'attachment_manifest'])

        email.send()
        if delivery:
            delivery.status = DocumentEmailDelivery.Status.SENT
            delivery.sent_at = timezone.now()
            delivery.save(update_fields=['status', 'sent_at'])
        logger.info(f"[{dept_key.upper()}] Email successfully delivered to {clean_recipients} - Subject: {subject}")
        return True, "Email dispatched successfully"

    except Exception as e:
        if delivery:
            from apps.orders.models import DocumentEmailDelivery
            delivery.status = DocumentEmailDelivery.Status.ATTACHMENT_FAILED if isinstance(e, AttachmentValidationError) else DocumentEmailDelivery.Status.FAILED
            delivery.error_message = str(e)
            delivery.save(update_fields=['status', 'error_message'])
        logger.error(f"[{dept_key.upper()}] Failed to send email to {clean_recipients}: {str(e)}", exc_info=True)
        return False, str(e)
