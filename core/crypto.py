"""Field-level encryption helpers for OAuth tokens."""

from __future__ import annotations

import base64
import hashlib
import logging

from django.conf import settings

logger = logging.getLogger("atlas.core.crypto")


def _fernet():
    from cryptography.fernet import Fernet

    raw = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    if not raw:
        # Deterministic fallback from SECRET_KEY for local/dev only — never log the key
        digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
        raw = base64.urlsafe_b64encode(digest).decode("ascii")
        logger.warning("event=crypto_using_secret_key_derived_fernet")
    return Fernet(raw.encode("utf-8") if isinstance(raw, str) else raw)


def encrypt_text(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_text(token: str) -> str:
    if not token:
        return ""
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.warning("event=crypto_decrypt_failed")
        return ""
