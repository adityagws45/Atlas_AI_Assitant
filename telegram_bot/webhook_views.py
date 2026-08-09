"""Webhook endpoint for Render / production Telegram delivery.

Fast ACK design (required for Telegram + free Render web):
  1. Validate secret
  2. Parse JSON / update_id
  3. Claim update_id (idempotency)
  4. Ensure one process-local worker exists (do NOT wait for PTB init)
  5. Enqueue payload on a thread-safe queue
  6. Return HTTP 200 immediately

Why a persistent event loop exists
---------------------------------
python-telegram-bot's Bot uses an HTTPX AsyncClient bound to the asyncio
loop that called ``Application.initialize()``. Creating a new loop per
update via ``async_to_sync`` closes the previous loop and causes:

    RuntimeError: Event loop is closed

on later ``send_typing`` / network calls. Fix: ONE daemon thread per
Gunicorn process owns ONE loop for its lifetime, initializes the
Application once on that loop, and runs ``process_update`` sequentially.

Worker states (thread-safe): STOPPED | STARTING | READY | FAILED

- STARTING/FAILED: HTTP may still enqueue; worker retries initialize.
- READY: processing queue on the persistent loop.
- If the thread dies, the next enqueue path restarts exactly one worker.

Limitations (honest):
  - In-process queue/thread dies if Render restarts / spins down the free web service.
  - Not a durable external queue. Acceptable for hackathon $0 demos.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import queue
import threading
import time
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import close_old_connections
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from telegram import Update

from telegram_bot.bot import build_application
from telegram_bot.services.webhook_dedupe import claim_update, release_update

logger = logging.getLogger("atlas.telegram.webhook")


class WorkerState(enum.Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    FAILED = "failed"


# Process-local queue + single worker (one Gunicorn worker process = one loop).
_STOP = object()
_update_queue: queue.Queue[Any] = queue.Queue()
_worker_thread: threading.Thread | None = None
_worker_loop: asyncio.AbstractEventLoop | None = None
_application = None
_worker_lock = threading.RLock()
_worker_state = WorkerState.STOPPED
_worker_ready = threading.Event()  # set only while READY
_stop_flag = threading.Event()

# Retry backoff when Application.initialize() fails (overridable in tests).
_INIT_RETRY_SECONDS = 1.0


def get_application():
    """Return the Application owned by the webhook worker (may be None before ready)."""
    return _application


def get_worker_loop():
    """Return the persistent asyncio loop (or None if worker not running)."""
    return _worker_loop


def get_worker_state() -> WorkerState:
    with _worker_lock:
        return _worker_state


def _set_worker_state(state: WorkerState) -> None:
    global _worker_state
    with _worker_lock:
        _worker_state = state
        if state is WorkerState.READY:
            _worker_ready.set()
        else:
            _worker_ready.clear()


def _ensure_worker_started() -> None:
    """Idempotently start the dedicated webhook loop thread.

    Does NOT wait for Application.initialize() — HTTP ACK must stay fast.
    Concurrent callers share the same STARTING/READY/FAILED worker; they
    must never assume READY just because the thread is alive.
    """
    global _worker_thread

    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            # Thread owns recovery (init retries). Enqueue is safe.
            return

        # Previous worker died or never started — create exactly one replacement.
        _stop_flag.clear()
        _set_worker_state(WorkerState.STARTING)
        thread = threading.Thread(
            target=_worker_main,
            name="tg-webhook-loop",
            daemon=True,
        )
        _worker_thread = thread
        thread.start()
        logger.info(
            "event=webhook_worker_thread_started state=%s",
            WorkerState.STARTING.value,
        )


def _worker_main() -> None:
    """Create one event loop for this process and process the queue forever."""
    global _worker_loop, _application

    _application = None
    _set_worker_state(WorkerState.STARTING)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _worker_loop = loop
    logger.info("event=webhook_worker_loop_created")

    async def _bootstrap_and_run() -> None:
        global _application
        app = await _initialize_application_with_retries()
        _application = app
        running = asyncio.get_running_loop()
        while not _stop_flag.is_set():
            item = await running.run_in_executor(None, _queue_get_or_stop)
            if item is _STOP or (isinstance(item, tuple) and item[0] is _STOP):
                logger.info("event=webhook_worker_stop_requested")
                break
            payload, update_id = item
            await _process_one_update(app, payload, update_id)

    try:
        loop.run_until_complete(_bootstrap_and_run())
    except Exception:
        logger.exception("event=webhook_worker_crashed")
        _set_worker_state(WorkerState.FAILED)
    finally:
        try:
            if _application is not None:
                loop.run_until_complete(_application.shutdown())
        except Exception:
            logger.exception("event=webhook_worker_shutdown_failed")
        try:
            if not loop.is_closed():
                loop.close()
        except Exception:
            pass
        _worker_loop = None
        _application = None
        _set_worker_state(WorkerState.STOPPED)
        logger.info("event=webhook_worker_stopped")


def _queue_get_or_stop() -> Any:
    """Block for queue items but wake periodically to honor stop."""
    while True:
        if _stop_flag.is_set():
            return (_STOP, None)
        try:
            return _update_queue.get(timeout=0.2)
        except queue.Empty:
            continue


async def _initialize_application_with_retries() -> Any:
    """Initialize PTB Application on this loop; retry on failure (queue keeps items)."""
    attempt = 0
    while not _stop_flag.is_set():
        attempt += 1
        _set_worker_state(WorkerState.STARTING)
        try:
            app = build_application()
            await app.initialize()
            _set_worker_state(WorkerState.READY)
            logger.info(
                "event=webhook_worker_initialized attempt=%s",
                attempt,
            )
            return app
        except Exception:
            _set_worker_state(WorkerState.FAILED)
            logger.exception(
                "event=webhook_worker_init_failed attempt=%s; will_retry=true",
                attempt,
            )
            # Keep queued updates; retry until success or stop.
            await asyncio.sleep(_INIT_RETRY_SECONDS)

    raise RuntimeError("Telegram webhook worker stopped during initialization")


async def _process_one_update(
    app: Any,
    payload: dict[str, Any],
    update_id: int | None,
) -> None:
    """Handle a single Telegram update on the persistent worker loop."""
    await sync_to_async(close_old_connections)()
    try:
        update = Update.de_json(payload, app.bot)
        if update is None:
            logger.warning(
                "event=webhook_bg_parse_failed update_id=%s",
                update_id,
            )
            await sync_to_async(release_update)(update_id)
            return

        await app.process_update(update)
        logger.info("event=webhook_bg_ok update_id=%s", update_id)
    except Exception:
        logger.exception("event=webhook_bg_failed update_id=%s", update_id)
        # Allow Telegram retry to re-deliver after a hard failure.
        await sync_to_async(release_update)(update_id)
    finally:
        await sync_to_async(close_old_connections)()


def _schedule_background(payload: dict[str, Any], update_id: int | None) -> None:
    """Start worker if needed and enqueue. Never waits for PTB initialize()."""
    _ensure_worker_started()

    with _worker_lock:
        thread = _worker_thread
        state = _worker_state

    if thread is None or not thread.is_alive():
        # Extremely narrow race: thread died between ensure and check.
        # Retry start once; if still dead, fail the enqueue (release claim → 500).
        _ensure_worker_started()
        with _worker_lock:
            thread = _worker_thread
        if thread is None or not thread.is_alive():
            raise RuntimeError("Telegram webhook worker thread failed to start")

    # STARTING / FAILED / READY are all acceptable: the live worker will
    # retry init if needed and then drain the queue. Never assume READY here.
    _update_queue.put((payload, update_id))
    logger.info(
        "event=webhook_queued update_id=%s qsize=%s worker_state=%s",
        update_id,
        _update_queue.qsize(),
        state.value,
    )


def reset_webhook_worker_for_tests(*, timeout: float = 5.0) -> None:
    """Test helper — stop the worker thread and drain the queue."""
    global _worker_thread, _application, _worker_loop

    _stop_flag.set()
    with _worker_lock:
        thread = _worker_thread
        if thread is not None and thread.is_alive():
            _update_queue.put((_STOP, None))
        else:
            thread = None

    if thread is not None:
        thread.join(timeout=timeout)

    with _worker_lock:
        _worker_thread = None
        _application = None
        _worker_loop = None
    _set_worker_state(WorkerState.STOPPED)
    _stop_flag.clear()
    try:
        while True:
            _update_queue.get_nowait()
    except queue.Empty:
        pass


def wait_until_worker_ready_for_tests(*, timeout: float = 5.0) -> None:
    """Test helper — block until worker state is READY."""
    if not _worker_ready.wait(timeout=timeout):
        raise TimeoutError(
            f"webhook worker not READY (state={get_worker_state().value})"
        )


def wait_for_queue_idle_for_tests(*, timeout: float = 5.0) -> None:
    """Test helper — wait until the in-process queue is empty."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _update_queue.empty():
            time.sleep(0.05)
            if _update_queue.empty():
                return
        time.sleep(0.01)
    raise TimeoutError("webhook update queue did not drain in time")


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
    try:
        _schedule_background(payload, update_id)
    except Exception:
        logger.exception("event=webhook_enqueue_failed update_id=%s", update_id)
        release_update(update_id)
        return HttpResponse(status=500)
    return HttpResponse(status=200)
