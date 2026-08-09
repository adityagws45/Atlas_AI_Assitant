from .base import *  # noqa: F403

DEBUG = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)  # noqa: F405
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Redis/Key Value is optional on free Render. Prefer Redis when reachable;
# otherwise fall back to LocMem so webhook/cache features still work.
CACHES = {  # noqa: F405
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "atlas-prod",
    }
}
_redis_url = (REDIS_URL or "").strip()  # noqa: F405
if _redis_url:
    try:
        import redis

        redis.from_url(_redis_url).ping()
        CACHES = {
            "default": {
                "BACKEND": "django_redis.cache.RedisCache",
                "LOCATION": _redis_url,
                "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
                "KEY_PREFIX": "atlas",
            }
        }
    except Exception:
        # Keep LocMem — do not fail production boot when Key Value is absent.
        pass

# ---------------------------------------------------------------------------
# Google OAuth redirect — MUST be HTTPS on the public host.
# Render env often still has localhost from .env.example; that causes
# Error 400: redirect_uri_mismatch. Always force a production URI here.
# ---------------------------------------------------------------------------
_PROD_OAUTH_CALLBACK = (
    "https://atlas-ai-assitant.onrender.com/api/oauth/google/callback/"
)
_redirect = (GOOGLE_REDIRECT_URI or "").strip()  # noqa: F405
_pub = (PUBLIC_BASE_URL or "").strip().rstrip("/")  # noqa: F405

if _pub.startswith("https://"):
    GOOGLE_REDIRECT_URI = f"{_pub}/api/oauth/google/callback/"
elif (
    not _redirect
    or "localhost" in _redirect
    or "127.0.0.1" in _redirect
    or not _redirect.startswith("https://")
):
    GOOGLE_REDIRECT_URI = _PROD_OAUTH_CALLBACK
else:
    # Explicit HTTPS redirect from env — keep it, normalize trailing slash.
    GOOGLE_REDIRECT_URI = _redirect if _redirect.endswith("/") else f"{_redirect}/"

# Keep PUBLIC_BASE_URL aligned so auth URL builders / absolute links stay consistent.
if not _pub.startswith("https://"):
    PUBLIC_BASE_URL = "https://atlas-ai-assitant.onrender.com"
