"""Request-level security helpers: security headers and the client's IP."""

from django.conf import settings


def client_ip(request):
    """The client's IP address. ``X-Forwarded-For`` is used only when the
    deployment says a trusted proxy overwrites it (right-most entry = the
    address that proxy saw), because clients can forge the header otherwise."""
    if settings.TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()[:45] or None
    return request.META.get("REMOTE_ADDR") or None


def user_agent(request):
    return request.META.get("HTTP_USER_AGENT", "")[:255]


class SecurityHeadersMiddleware:
    """Adds Content-Security-Policy (when configured) and Permissions-Policy.
    Django's SecurityMiddleware covers HSTS, nosniff, Referrer-Policy and COOP;
    XFrameOptionsMiddleware covers X-Frame-Options."""

    PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        policy = settings.CONTENT_SECURITY_POLICY
        if policy and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = policy
        response.setdefault("Permissions-Policy", self.PERMISSIONS_POLICY)
        return response
