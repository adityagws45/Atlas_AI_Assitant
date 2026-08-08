"""Shared settings for Atlas AI Financial Assistant."""

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY", default="change-me-in-production")
DEBUG = env.bool("DEBUG", default=False)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    # Atlas apps
    "core",
    "accounts",
    "conversation",
    "memory",
    "finance",
    "documents",
    "gmail",
    "gcalendar",  # named to avoid clash with Python stdlib `calendar`
    "drive",
    "sheets",
    "notifications",
    "scheduler",
    "telegram_bot",
    "tools",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

AUTH_USER_MODEL = "accounts.User"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgres://atlas:atlas@localhost:5433/atlas_ai",
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
}

# ---------------------------------------------------------------------------
# Cache (Redis preferred; LocMem fallback for local without Redis)
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "atlas-default",
    }
}

# Prefer Redis when available (production / docker-compose).
try:
    import django_redis  # noqa: F401

    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
            "KEY_PREFIX": "atlas",
        }
    }
except ImportError:
    pass

CACHE_TTL_STOCK = env.int("CACHE_TTL_STOCK", default=300)  # 5 min
CACHE_TTL_NEWS = env.int("CACHE_TTL_NEWS", default=600)  # 10 min
CACHE_TTL_PROFILE = env.int("CACHE_TTL_PROFILE", default=600)
CACHE_TTL_SEC = env.int("CACHE_TTL_SEC", default=600)
CACHE_TTL_METRICS = env.int("CACHE_TTL_METRICS", default=600)
CACHE_TTL_DOCUMENT = env.int("CACHE_TTL_DOCUMENT", default=3600)
CACHE_TTL_SHEET_VALUES = env.int("CACHE_TTL_SHEET_VALUES", default=180)
CACHE_TTL_SHEET_META = env.int("CACHE_TTL_SHEET_META", default=300)
CACHE_TTL_SHEET_ANALYSIS = env.int("CACHE_TTL_SHEET_ANALYSIS", default=300)
CACHE_TTL_GMAIL_META = env.int("CACHE_TTL_GMAIL_META", default=120)
CACHE_TTL_GMAIL_BODY = env.int("CACHE_TTL_GMAIL_BODY", default=180)
CACHE_TTL_CALENDAR_EVENTS = env.int("CACHE_TTL_CALENDAR_EVENTS", default=120)

# ---------------------------------------------------------------------------
# Documents (Milestone 5)
# ---------------------------------------------------------------------------
DOCUMENT_MAX_UPLOAD_MB = env.int("DOCUMENT_MAX_UPLOAD_MB", default=25)
DOCUMENT_CHUNK_SIZE = env.int("DOCUMENT_CHUNK_SIZE", default=1100)
DOCUMENT_CHUNK_OVERLAP = env.int("DOCUMENT_CHUNK_OVERLAP", default=160)
DOCUMENT_TOP_K = env.int("DOCUMENT_TOP_K", default=6)
DOCUMENT_ALLOWED_EXTENSIONS = (".pdf", ".txt", ".md", ".markdown", ".docx", ".pptx")
GEMINI_EMBEDDING_MODEL = (
    env("GEMINI_EMBEDDING_MODEL", default="gemini-embedding-001") or ""
).strip()
DOCUMENT_EMBEDDING_FORCE_LOCAL = env.bool("DOCUMENT_EMBEDDING_FORCE_LOCAL", default=False)
DOCUMENT_EMBEDDING_BATCH_SIZE = env.int("DOCUMENT_EMBEDDING_BATCH_SIZE", default=8)
CACHE_TTL_DRIVE_LIST = env.int("CACHE_TTL_DRIVE_LIST", default=120)
CACHE_TTL_DRIVE_META = env.int("CACHE_TTL_DRIVE_META", default=300)

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = env("TELEGRAM_BOT_TOKEN", default="").strip()
TELEGRAM_WEBHOOK_URL = env("TELEGRAM_WEBHOOK_URL", default="").strip()
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="").strip()

# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------
GEMINI_API_KEY = (env("GEMINI_API_KEY", default="") or "").strip()
GEMINI_MODEL = (env("GEMINI_MODEL", default="gemini-3.5-flash") or "").strip()
GEMINI_PRO_MODEL = (env("GEMINI_PRO_MODEL", default="gemini-3.5-flash") or "").strip()
GEMINI_LIGHT_MODEL = (
    env("GEMINI_LIGHT_MODEL", default=GEMINI_MODEL) or GEMINI_MODEL
).strip()
GEMINI_TIMEOUT_SECONDS = env.float("GEMINI_TIMEOUT_SECONDS", default=30)
GEMINI_MAX_RETRIES = env.int("GEMINI_MAX_RETRIES", default=3)
GEMINI_TEMPERATURE = env.float("GEMINI_TEMPERATURE", default=0.4)
GEMINI_MAX_OUTPUT_TOKENS = env.int("GEMINI_MAX_OUTPUT_TOKENS", default=2048)

# Groq — speech-to-text ONLY (does not replace Gemini for reasoning)
GROQ_API_KEY = (env("GROQ_API_KEY", default="") or "").strip()
GROQ_WHISPER_MODEL = (
    env("GROQ_WHISPER_MODEL", default="whisper-large-v3-turbo") or "whisper-large-v3-turbo"
).strip()
GROQ_WHISPER_TIMEOUT_SECONDS = env.float("GROQ_WHISPER_TIMEOUT_SECONDS", default=60)
MAX_VOICE_DURATION_SECONDS = env.int("MAX_VOICE_DURATION_SECONDS", default=120)

# Conversation context limits
MAX_RECENT_MESSAGES = env.int("MAX_RECENT_MESSAGES", default=20)

# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", default="")
# Public HTTPS origin for the deployed Atlas backend (no trailing slash).
# Production example: https://api.your-atlas-domain.com
PUBLIC_BASE_URL = (env("PUBLIC_BASE_URL", default="") or "").strip().rstrip("/")
_default_redirect = (
    f"{PUBLIC_BASE_URL}/api/oauth/google/callback/"
    if PUBLIC_BASE_URL
    else "http://localhost:8000/api/oauth/google/callback/"
)
GOOGLE_REDIRECT_URI = (env("GOOGLE_REDIRECT_URI", default=_default_redirect) or _default_redirect).strip()
GOOGLE_OAUTH_SCOPES = {
    "gmail": [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
    ],
    "calendar": [
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/userinfo.email",
    ],
    "drive": [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
    ],
    "sheets": [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
    ],
}

# ---------------------------------------------------------------------------
# Finance APIs
# ---------------------------------------------------------------------------
FINNHUB_API_KEY = env("FINNHUB_API_KEY", default="")
FINANCE_PRIMARY_PROVIDER = env("FINANCE_PRIMARY_PROVIDER", default="finnhub")
FINANCE_FALLBACK_PROVIDER = env("FINANCE_FALLBACK_PROVIDER", default="yahoo")

# ---------------------------------------------------------------------------
# Notifications (anti-spam)
# ---------------------------------------------------------------------------
MAX_NOTIFICATIONS_PER_DAY = env.int("MAX_NOTIFICATIONS_PER_DAY", default=5)
DEFAULT_QUIET_HOURS_START = env("DEFAULT_QUIET_HOURS_START", default="22:00")
DEFAULT_QUIET_HOURS_END = env("DEFAULT_QUIET_HOURS_END", default="07:00")
MIN_MINUTES_BETWEEN_PROACTIVE = env.int("MIN_MINUTES_BETWEEN_PROACTIVE", default=30)

# ---------------------------------------------------------------------------
# Encryption
# ---------------------------------------------------------------------------
FIELD_ENCRYPTION_KEY = env("FIELD_ENCRYPTION_KEY", default="")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact_secrets": {
            "()": "core.logging_filters.RedactSecretsFilter",
        },
    },
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
            "filters": ["redact_secrets"],
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": LOG_DIR / "app.log",
            "maxBytes": 10 * 1024 * 1024,
            "backupCount": 5,
            "formatter": "verbose",
            "filters": ["redact_secrets"],
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
        "atlas": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
        "httpx": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
        "httpcore": {
            "handlers": ["console", "file"],
            "level": "WARNING",
            "propagate": False,
        },
        "telegram": {
            "handlers": ["console", "file"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
