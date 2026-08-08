"""Telegram webhook update_id deduplication (Redis cache preferred).

Uses Django's cache backend:
- Redis via django-redis when REDIS_URL is available
- LocMem fallback (per-process only — not shared across Gunicorn workers)

Not durable across process restarts. Acceptable for hackathon / free-tier demos;
not a substitute for a persistent job queue.
"""

from __future__ import annotations

import logging
import threading

from django.core.cache import cache

logger = logging.getLogger("atlas.telegram.webhook")

# Keep claimed update_ids long enough to absorb Telegram retries (hours).
_CLAIM_TTL_SECONDS = 60 * 60 * 24  # 24h
_KEY_PREFIX = "atlas:tg:update:"

# Process-local fallback if cache.add is unavailable / misconfigured
_local_claimed: set[int] = set()
_local_lock = threading.Lock()
_LOCAL_MAX = 5000


def _key(update_id: int) -> str:
    return f"{_KEY_PREFIX}{int(update_id)}"


def claim_update(update_id: int | None) -> bool:
    """
    Try to claim an update_id for processing.

    Returns True if this process should process it (first claim).
    Returns False if it was already claimed (duplicate delivery).
    """
    if update_id is None:
        # No id — process once; cannot dedupe
        return True

    uid = int(update_id)
    key = _key(uid)
    try:
        # cache.add is SET-if-not-exists on Redis; LocMem also supports it.
        claimed = cache.add(key, "1", timeout=_CLAIM_TTL_SECONDS)
        if claimed:
            logger.info("event=webhook_claim_ok update_id=%s backend=cache", uid)
            return True
        logger.info("event=webhook_claim_duplicate update_id=%s backend=cache", uid)
        return False
    except Exception:  # noqa: BLE001
        logger.warning(
            "event=webhook_claim_cache_unavailable update_id=%s using=local_set",
            uid,
        )
        with _local_lock:
            if uid in _local_claimed:
                return False
            _local_claimed.add(uid)
            if len(_local_claimed) > _LOCAL_MAX:
                # Drop arbitrary oldest-ish entries
                for _ in range(1000):
                    _local_claimed.pop()
            return True


def release_update(update_id: int | None) -> None:
    """Release claim so Telegram can retry after a hard processing failure."""
    if update_id is None:
        return
    uid = int(update_id)
    try:
        cache.delete(_key(uid))
    except Exception:  # noqa: BLE001
        pass
    with _local_lock:
        _local_claimed.discard(uid)
    logger.info("event=webhook_claim_released update_id=%s", uid)
