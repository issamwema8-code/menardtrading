from django.shortcuts import redirect
from django.contrib.auth import logout
from django.urls import resolve, Resolver404


class TwoFactorEnforcementMiddleware:
    """
    Middleware ensuring that no user receives authenticated access without
    completing the secondary verification step (Email OTP or 2FA PIN).
    """

    EXEMPT_URL_NAMES = {
        'login',
        'logout',
        'verify_2fa',
        'resend_2fa_otp',
        'forgot_password',
        'forgot_password_done',
        'reset_password_confirm',
        'reset_password_complete',
        'access_denied',
        'quote_portal',
        'quote_approve',
        'brevo_inbound_webhook',
    }

    EXEMPT_PREFIXES = (
        '/static/',
        '/media/',
        '/portal/',
        '/api/webhooks/',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info

        # Allow exempt static/media/portal/webhook paths
        for prefix in self.EXEMPT_PREFIXES:
            if path.startswith(prefix):
                return self.get_response(request)

        try:
            resolved = resolve(path)
            url_name = resolved.url_name
        except Resolver404:
            url_name = None

        if url_name in self.EXEMPT_URL_NAMES:
            return self.get_response(request)

        # If user has only passed Step 1 (password verified) but has not completed 2FA:
        if request.session.get('pre_auth_user_id'):
            return redirect('verify_2fa')

        # If user is authenticated but 2FA flag is explicitly invalid
        if request.user.is_authenticated and request.session.get('is_2fa_verified') is False:
            logout(request)
            return redirect('login')

        return self.get_response(request)
