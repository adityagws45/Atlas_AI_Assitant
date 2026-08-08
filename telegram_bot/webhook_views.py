"""Webhook endpoint for Render / production Telegram delivery.

Fast ACK design (required for Telegram + free Render web):
  1. Validate secret
  2. Parse JSON / update_id
  3. Claim update_id (idempotency)
  4. Spawn background thread for ConversationProcessor path
  5. Return HTTP 200 immediately

Limitations (honest):
  - Background work is in-process threads on the web dyno.
  - Jobs die if Render restarts / spins down the free web service mid-flight.
  - Not a durable queue. Acceptable for hackathon $0 demos; not production-grade
    background processing.
"""

from __future__ import annotations

import json
import logging
import threading

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from telegram import Update

from telegram_bot.bot import build_application
from telegram_bot.services.webhook_dedupe import claim_update, release_update

logger = logging.getLogger("atlas.telegram.webhook")

_application = None
_application_lock = threading.Lock()


def get_application():
    global _application
    if _application is None:
        # Serialize lazy init so concurrent webhook requests cannot build two
        # PTB Applications (duplicate polling/webhook connections).
        with _application_lock:
            if _application is None:
                _application = build_application()
    return _application


def _process_update_payload(payload: dict, update_id: int | None) -> None:
    """Run the shared PTB handlers (same as polling) off the request thread."""
    close_old_connections()
    try:
        app = get_application()
        update = Update.de_json(payload, app.bot)
        if update is None:
            logger.warning(
                "event=webhook_bg_parse_failed update_id=%s",
                update_id,
            )
            release_update(update_id)
            return

        async def _process():
            if not app.running:
                await app.initialize()
            await app.process_update(update)

        async_to_sync(_process)()
        logger.info("event=webhook_bg_ok update_id=%s", update_id)
    except Exception:
        logger.exception("event=webhook_bg_failed update_id=%s", update_id)
        # Allow Telegram retry to re-deliver after a hard failure.
        release_update(update_id)
    finally:
        close_old_connections()


def _schedule_background(payload: dict, update_id: int | None) -> None:
    thread = threading.Thread(
        target=_process_update_payload,
        args=(payload, update_id),
        name=f"tg-webhook-{update_id}",
        daemon=True,
    )
    thread.start()
    logger.info("event=webhook_queued update_id=%s", update_id)


@csrf_exempt
@require_http_methods(["POST", "GET"])
def telegram_webhook(request: HttpRequest):
    """
    Production entrypoint.
    GET returns a simple health probe for the webhook path.
    POST validates, dedupes, queues processing, and ACKs immediately.
    """
    if request.method == "GET":
        return JsonResponse({"status": "ok", "mode": "webhook", "ack": "fast"})

    # Production should always set TELEGRAM_WEBHOOK_SECRET.
    # When set, reject requests without a matching header.
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    if secret:
        incoming = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if incoming != secret:
            logger.warning("event=webhook_rejected reason=invalid_secret")
            return HttpResponseForbidden("Invalid secret token")
    elif not settings.DEBUG:
        logger.error("event=webhook_rejected reason=secret_not_configured")
        return HttpResponseForbidden("Webhook secret not configured")

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("event=webhook_rejected reason=invalid_json")
        return HttpResponse(status=400)

    if not isinstance(payload, dict):
        logger.warning("event=webhook_rejected reason=payload_not_object")
        return HttpResponse(status=400)

    raw_id = payload.get("update_id")
    try:
        update_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        logger.warning("event=webhook_rejected reason=bad_update_id")
        return HttpResponse(status=400)

    # Cheap structural check — full Update.de_json happens in the worker thread
    # so we never block ACK on bot init / network.
    if update_id is None and not any(
        k in payload for k in ("message", "edited_message", "channel_post")
    ):
        logger.warning("event=webhook_rejected reason=unrecognized_update")
        return HttpResponse(status=400)

    if not claim_update(update_id):
        # Duplicate delivery — ACK without reprocessing.
        logger.info("event=webhook_duplicate_ack update_id=%s", update_id)
        return HttpResponse(status=200)

    logger.info(
        "event=webhook_accepted update_id=%s chat=%s",
        update_id,
        (payload.get("message") or {}).get("chat", {}).get("id"),
    )
    _schedule_background(payload, update_id)
    return HttpResponse(status=200)
