import logging
from django.conf import settings
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


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
    
    :param department: Key from EMAIL_CONFIGS ('orders', 'quotes', 'invoicing', 'operations', 'info')
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

    config = settings.EMAIL_CONFIGS.get(department, settings.EMAIL_CONFIGS['info'])
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

        email = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=from_email,
            to=recipient_list,
            reply_to=reply_to or [from_email],
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
        logger.info(f"[{department.upper()}] Email sent to {recipient_list} - Subject: {subject}")
        return True, "Email dispatched successfully"

    except Exception as e:
        logger.error(f"[{department.upper()}] Failed to send email to {recipient_list}: {str(e)}", exc_info=True)
        return False, str(e)
