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

# Production OAuth callback must be HTTPS on the deployed host — never localhost.
_redirect = (GOOGLE_REDIRECT_URI or "").strip()  # noqa: F405
if not _redirect or "localhost" in _redirect or "127.0.0.1" in _redirect:
    _pub = (PUBLIC_BASE_URL or "").strip()  # noqa: F405
    if _pub.startswith("https://"):
        GOOGLE_REDIRECT_URI = f"{_pub.rstrip('/')}/api/oauth/google/callback/"
    else:
        # Known production host fallback (must also be registered in Google Cloud).
        _hosts = ALLOWED_HOSTS if isinstance(ALLOWED_HOSTS, (list, tuple)) else []  # noqa: F405
        if any("atlas-ai-assitant.onrender.com" in str(h) for h in _hosts):
            GOOGLE_REDIRECT_URI = (
                "https://atlas-ai-assitant.onrender.com/api/oauth/google/callback/"
            )
        else:
            raise RuntimeError(
                "Production requires GOOGLE_REDIRECT_URI or PUBLIC_BASE_URL as HTTPS "
                "(not localhost). Register the same URI in Google Cloud OAuth credentials."
            )
