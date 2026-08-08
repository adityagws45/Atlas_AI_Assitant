from .base import *  # noqa: F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Production OAuth callback must be HTTPS on the deployed host — never localhost.
_redirect = (GOOGLE_REDIRECT_URI or "").strip()  # noqa: F405
if not _redirect or "localhost" in _redirect or "127.0.0.1" in _redirect:
    _pub = (PUBLIC_BASE_URL or "").strip()  # noqa: F405
    if _pub.startswith("https://"):
        GOOGLE_REDIRECT_URI = f"{_pub.rstrip('/')}/api/oauth/google/callback/"
    else:
        raise RuntimeError(
            "Production requires GOOGLE_REDIRECT_URI or PUBLIC_BASE_URL as HTTPS "
            "(not localhost). Register the same URI in Google Cloud OAuth credentials."
        )
