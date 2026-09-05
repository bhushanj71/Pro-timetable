"""
Response headers that constrain what a page is allowed to do.

Defence in depth behind output escaping: if a stored payload ever does reach
the DOM, the CSP decides whether it can execute or phone home.
"""
import logging

from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# The app inlines small scripts in its templates (the pre-paint theme switch,
# for one), so 'unsafe-inline' is required for scripts today. Everything else
# is locked to same-origin, and the directives that actually stop data leaving
# -- connect-src, form-action, frame-ancestors, base-uri -- are strict.
#
# Google AdSense is the exception, and it is a real one rather than a
# formality. An ad network needs to fetch scripts, open frames, load creatives
# and report back, so admitting it means widening four directives at once. The
# tag in base.html does nothing without these, and a blocked third-party
# script fails silently -- so if ads ever stop appearing, this list is the
# first place to look and not the last.
#
# What is deliberately NOT widened: form-action stays 'self', so no injected
# form can post a password anywhere; base-uri stays 'self'; frame-ancestors
# stays none; and object-src stays none. Those are the directives that decide
# whether credentials and page control can leave, and advertising does not
# need any of them.
_ADS_SCRIPT = (
    "https://pagead2.googlesyndication.com "
    "https://partner.googleadservices.com "
    "https://tpc.googlesyndication.com "
    "https://adservice.google.com "
    "https://www.googletagservices.com "
    "https://ep2.adtrafficquality.google"
)
_ADS_FRAME = (
    "https://googleads.g.doubleclick.net "
    "https://tpc.googlesyndication.com "
    "https://www.google.com "
    "https://ep2.adtrafficquality.google"
)
_ADS_IMG = (
    "https://pagead2.googlesyndication.com "
    "https://*.g.doubleclick.net "
    "https://*.googlesyndication.com "
    "https://*.googleusercontent.com "
    "https://www.google.com "
    "https://ep1.adtrafficquality.google"
)
_ADS_CONNECT = (
    "https://pagead2.googlesyndication.com "
    "https://*.g.doubleclick.net "
    "https://*.googlesyndication.com "
    "https://www.google.com "
    "https://ep1.adtrafficquality.google"
)

CSP = "; ".join(
    [
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline' {_ADS_SCRIPT}",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com data:",
        f"img-src 'self' data: blob: {_ADS_IMG}",
        f"connect-src 'self' {_ADS_CONNECT}",
        # Ads render in frames. Without this they inherit default-src and every
        # one of them is blocked, which looks exactly like having no ads.
        f"frame-src {_ADS_FRAME}",
        "manifest-src 'self'",
        "worker-src 'self'",
        "frame-ancestors 'none'",   # no clickjacking
        "form-action 'self'",       # a stored form cannot post credentials away
        "base-uri 'self'",          # no <base> hijack of every relative URL
        "object-src 'none'",
    ]
)

HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # An empty allowlist means *no* origin, this one included. `microphone=()`
    # therefore switched off voice input site-wide: the browser refused the
    # microphone before any page script ran, so the mic button reported
    # "not-allowed" no matter what permission the professor had granted.
    # Everything the app genuinely never uses stays locked shut.
    "Permissions-Policy": "geolocation=(), microphone=(self), camera=(), payment=(), usb=()",
    # Push subscriptions and the service worker only exist on HTTPS anyway;
    # this stops a downgrade from ever being attempted.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        for name, value in HEADERS.items():
            # HSTS is meaningless (and misleading) over plain HTTP in local dev.
            if name == "Strict-Transport-Security" and request.url.scheme != "https":
                continue
            response.headers.setdefault(name, value)
        return response
