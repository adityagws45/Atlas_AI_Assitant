"""Lightweight self-ping to reduce Render free-tier sleep while the process is up."""

from __future__ import annotations

import logging
import threading
import time

from django.conf import settings

logger = logging.getLogger("atlas.core.keepalive")

_started = False
_INTERVAL_SECONDS = 240  # 4 minutes


def start_keepalive_thread() -> None:
    global _started
    if _started:
        return
    # Avoid double-start under Django autoreload parent process.
    import os

    if os.environ.get("RUN_MAIN") == "false":
        return
    _started = True
    threading.Thread(target=_loop, name="atlas-keepalive", daemon=True).start()


def _loop() -> None:
    while True:
        time.sleep(_INTERVAL_SECONDS)
        try:
            _ping_once()
        except Exception:  # noqa: BLE001
            logger.debug("event=keepalive_ping_failed", exc_info=True)


def _ping_once() -> None:
    import httpx

    base = (getattr(settings, "PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if not base.startswith("https://"):
        redirect = (getattr(settings, "GOOGLE_REDIRECT_URI", "") or "").strip()
        if "onrender.com" in redirect:
            base = "https://atlas-ai-assitant.onrender.com"
        else:
            return
    httpx.get(f"{base}/health/", timeout=10)
    logger.info("event=keepalive_ok host=%s", base)
