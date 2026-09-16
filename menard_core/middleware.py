class OpenLiteSpeedProxyMiddleware:
    """
    Middleware ensuring reverse-proxy requests from OpenLiteSpeed / CyberPanel / Nginx
    are accurately normalized for Django's CSRF, Origin, and SSL detection.
    Fixes OpenLiteSpeed duplicated comma-separated Origin and Host headers (e.g. 'https://site,https://site').
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1. Clean duplicate comma-separated Origin header from proxy chaining
        origin = request.META.get('HTTP_ORIGIN', '')
        if origin and ',' in origin:
            first_origin = origin.split(',')[0].strip()
            request.META['HTTP_ORIGIN'] = first_origin
            origin = first_origin

        # 2. Clean duplicate comma-separated Referer header
        referer = request.META.get('HTTP_REFERER', '')
        if referer and ',' in referer:
            first_referer = referer.split(',')[0].strip()
            request.META['HTTP_REFERER'] = first_referer
            referer = first_referer

        # 3. Clean duplicate comma-separated Host headers
        host = request.META.get('HTTP_HOST', '')
        if host and ',' in host:
            request.META['HTTP_HOST'] = host.split(',')[0].strip()

        fwd_host = request.META.get('HTTP_X_FORWARDED_HOST', '')
        if fwd_host and ',' in fwd_host:
            request.META['HTTP_X_FORWARDED_HOST'] = fwd_host.split(',')[0].strip()

        # 4. Detect and enforce HTTPS scheme
        proto = request.META.get('HTTP_X_FORWARDED_PROTO', '')
        ssl_flag = request.META.get('HTTP_X_FORWARDED_SSL', '')
        server_port = request.META.get('HTTP_X_FORWARDED_PORT', '')

        if (
            proto.lower() == 'https' or
            ssl_flag.lower() in ('on', '1', 'true') or
            server_port == '443' or
            (origin and origin.startswith('https://')) or
            (referer and referer.startswith('https://'))
        ):
            request.META['HTTP_X_FORWARDED_PROTO'] = 'https'
            request.META['wsgi.url_scheme'] = 'https'

        return self.get_response(request)
