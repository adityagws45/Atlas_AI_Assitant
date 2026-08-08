"""Webhook endpoint for Render / production Telegram delivery."""

from __future__ import annotations

import json
import logging
import threading

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from telegram import Update

from telegram_bot.bot import build_application

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


@csrf_exempt
@require_http_methods(["POST", "GET"])
def telegram_webhook(request: HttpRequest):
    """
    Production entrypoint.
    GET returns a simple health probe for the webhook path.
    POST processes Telegram updates.
    """
    if request.method == "GET":
        return JsonResponse({"status": "ok", "mode": "webhook"})

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
        logger.exception("Webhook received invalid JSON")
        return HttpResponse(status=400)

    app = get_application()
    update = Update.de_json(payload, app.bot)
    if update is None:
        logger.warning("Webhook could not parse Update")
        return HttpResponse(status=400)

    logger.info(
        "Webhook update_id=%s chat=%s",
        update.update_id,
        getattr(update.effective_chat, "id", None),
    )

    async def _process():
        # Ensure app is initialized for webhook mode
        if not app.running:
            await app.initialize()
        await app.process_update(update)

    try:
        async_to_sync(_process)()
    except Exception:
        logger.exception("Webhook processing failed update_id=%s", update.update_id)
        return HttpResponse(status=500)

    return HttpResponse(status=200)
