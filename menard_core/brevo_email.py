import re
import email.utils
import logging
from django.conf import settings
from django.utils import timezone
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

LOOPBACK_DISALLOWED_PATTERNS = [
    'orders@menardtrading.com',
    'quotes@menardtrading.com',
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

    if not isinstance(recipient_list, (list, tuple)):
        recipient_list = [recipient_list]

    valid_recipients = [clean_email(r) for r in recipient_list if clean_email(r)]
    if not valid_recipients:
        logger.warning(f"Skipping email queue: No valid recipients for subject '{subject}'")
        return None

    # Filter loopback for no-reply automatic replies
    if department == 'no-reply':
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

    # Convert attachments if any to JSON-safe structure
    safe_attachments = []
    if attachments:
        for att in attachments:
            if isinstance(att, dict):
                safe_attachments.append({
                    'filename': att.get('filename'),
                    'mimetype': att.get('mimetype', 'application/pdf')
                })

    queue_msg = EmailQueueMessage.objects.create(
        department=department,
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
    Dispatches a transactional email via Brevo SMTP using departmental sender profiles.
    
    :param department: Key from EMAIL_CONFIGS ('orders', 'quotes', 'invoicing', 'operations', 'no-reply', 'info')
    :param recipient_list: List of recipient email addresses
    :param subject: Email subject line
    :param template_name: Relative template path (e.g., 'emails/quote_sent.html')
    :param context: Template context dictionary
    :param attachments: List of tuples/dicts [(filename, content_bytes, mimetype)]
    :param reply_to: List of reply-to emails
    :return: (bool success, str message)
    """
    if not isinstance(recipient_list, (list, tuple)):
        recipient_list = [recipient_list]

    clean_recipients = [clean_email(r) for r in recipient_list if clean_email(r)]
    if not clean_recipients:
        return False, "No valid recipient email addresses provided"

    # Filter loopback for no-reply automatic replies
    if department == 'no-reply':
        clean_recipients = [r for r in clean_recipients if not is_loopback_or_invalid_recipient(r)]
        if not clean_recipients:
            return False, "Recipient is a loopback or system email; automated reply suppressed"

    config = settings.EMAIL_CONFIGS.get(department, settings.EMAIL_CONFIGS.get('info', {}))
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

    try:
        # Render HTML and text versions
        html_content = render_to_string(template_name, full_context)
        text_content = strip_tags(html_content)

        # Create explicit Brevo SMTP connection for this department
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
            if department == 'no-reply':
                resolved_reply_to = ['orders@menardtrading.com']
            else:
                resolved_reply_to = [from_email]

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
        if attachments:
            for item in attachments:
                if isinstance(item, (tuple, list)) and len(item) == 3:
                    filename, content, mimetype = item
                    email.attach(filename, content, mimetype)
                elif isinstance(item, dict):
                    email.attach(
                        item.get('filename'),
                        item.get('content'),
                        item.get('mimetype', 'application/pdf')
                    )

        email.send()
        logger.info(f"[{department.upper()}] Email successfully delivered to {clean_recipients} - Subject: {subject}")
        return True, "Email dispatched successfully"

    except Exception as e:
        logger.error(f"[{department.upper()}] Failed to send email to {clean_recipients}: {str(e)}", exc_info=True)
        return False, str(e)
