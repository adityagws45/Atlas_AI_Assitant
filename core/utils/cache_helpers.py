"""Redis/LocMem cache helpers for finance payloads."""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from django.core.cache import cache

logger = logging.getLogger("atlas.cache")

T = TypeVar("T")


def make_cache_key(namespace: str, *parts: Any) -> str:
    raw = "|".join(str(p).strip().upper() for p in parts if p is not None and str(p) != "")
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"fin:{namespace}:{digest}:{raw[:48]}"


def cache_get(key: str) -> Any | None:
    try:
        return cache.get(key)
    except Exception:  # noqa: BLE001
        logger.warning("event=cache_get_fail key=%s", key[:80])
        return None


def cache_set(key: str, value: Any, ttl: int) -> None:
    if value is None or ttl <= 0:
        return
    try:
        cache.set(key, value, ttl)
    except Exception:  # noqa: BLE001
        logger.warning("event=cache_set_fail key=%s", key[:80])


def get_or_set(key: str, ttl: int, producer: Callable[[], T]) -> tuple[T, bool]:
    """
    Return (value, cached).

    Does not cache None / empty failures — callers decide what is cacheable.
    """
    hit = cache_get(key)
    if hit is not None:
        return hit, True
    value = producer()
    if value is not None:
        cache_set(key, value, ttl)
    return value, False


def dumps_safe(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)
