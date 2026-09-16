import logging
from django.contrib.auth.models import User
from .models import AuditLog

logger = logging.getLogger('accounts.audit')


def get_client_ip(request):
    if not request:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def log_audit_event(
    request=None,
    action: str = 'ACTION',
    resource_type: str = '',
    resource_id: str = '',
    details: dict = None,
    result: str = AuditLog.Result.SUCCESS,
    user=None,
    user_email: str = ''
):
    """
    Central helper for logging security and business events to the database and structured logger.
    """
    try:
        active_user = user
        if active_user is None and request and hasattr(request, 'user') and request.user.is_authenticated:
            active_user = request.user

        email_str = user_email
        if not email_str and active_user:
            email_str = active_user.email or active_user.username

        ip_addr = get_client_ip(request)
        ua_str = request.META.get('HTTP_USER_AGENT', '') if request else ''

        log_entry = AuditLog.objects.create(
            user=active_user if isinstance(active_user, User) else None,
            user_email=email_str,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id else '',
            ip_address=ip_addr,
            user_agent=ua_str[:500],
            details=details or {},
            result=result
        )
        return log_entry
    except Exception as e:
        logger.error(f"Failed to record audit event [{action}]: {e}", exc_info=True)
        return None
