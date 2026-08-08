from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "*"]

# Prefer LocMem in local development unless Redis is reachable.
CACHES = {  # noqa: F405
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "atlas-dev",
    }
}

try:
    import redis

    client = redis.from_url(REDIS_URL)  # noqa: F405
    client.ping()
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,  # noqa: F405
            "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
            "KEY_PREFIX": "atlas",
        }
    }
except Exception:
    pass
