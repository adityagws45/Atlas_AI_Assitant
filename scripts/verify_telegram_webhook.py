"""Telegram webhook reliability verification (fast ACK + persistent loop).

Mocks external Telegram/Gemini/Groq where needed. Does not prove live APIs.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from django.core.cache import cache
from django.test import Client, override_settings

from gcalendar.services.calendar_intent import detect_calendar_intent
from gmail.services.gmail_intent import detect_gmail_intent
from sheets.services.sheet_intent import detect_sheet_intent
from telegram_bot.adapters.telegram_adapter import (
    extract_google_oauth_url,
    prepare_telegram_markdown,
)
from telegram_bot.management.commands.set_telegram_webhook import Command as SetWebhookCmd
from telegram_bot.services.conversation_processor import ConversationProcessor
from telegram_bot.services.webhook_dedupe import claim_update, release_update
from telegram_bot.bot import build_application
from telegram_bot import webhook_views
from telegram_bot.webhook_views import WorkerState


def _pass(label: str) -> None:
    print(f"PASS {label}")


def _fail(label: str, detail: str = "") -> None:
    raise AssertionError(f"FAIL {label}: {detail}")


def _fake_application(*, on_process=None, init_gate=None, fail_init_times: int = 0):
    """Minimal PTB Application stand-in for worker-loop tests (no network)."""

    class FakeBot:
        pass

    class FakeApp:
        def __init__(self):
            self.bot = FakeBot()
            self.running = False
            self.init_loop_id = None
            self.process_loop_ids: list[int] = []
            self.processed_ids: list[int | None] = []
            self.errors: list[BaseException] = []
            self.init_attempts = 0
            self._fail_remaining = fail_init_times
            self._lock = threading.Lock()

        async def initialize(self):
            if init_gate is not None:
                # Controlled cold-start / concurrent-startup delay (not wall-clock flake).
                while not init_gate.is_set():
                    if webhook_views._stop_flag.is_set():
                        raise RuntimeError("stopped during init gate")
                    await asyncio.sleep(0.01)

            with self._lock:
                self.init_attempts += 1
                attempt = self.init_attempts
                should_fail = self._fail_remaining > 0
                if should_fail:
                    self._fail_remaining -= 1

            if should_fail:
                raise RuntimeError(f"simulated initialize failure attempt={attempt}")

            loop = asyncio.get_running_loop()
            if loop.is_closed():
                raise RuntimeError("Event loop is closed")
            self.init_loop_id = id(loop)

        async def process_update(self, update):
            loop = asyncio.get_running_loop()
            if loop.is_closed():
                err = RuntimeError("Event loop is closed")
                self.errors.append(err)
                raise err
            self.process_loop_ids.append(id(loop))
            self.processed_ids.append(getattr(update, "update_id", None))
            # Mimic send_typing / HTTPX await on the same loop.
            await asyncio.sleep(0)
            if on_process is not None:
                await on_process(update, loop)

        async def shutdown(self):
            return None

    return FakeApp()


def _make_payload(uid: int, text: str = "hi") -> dict:
    return {
        "update_id": uid,
        "message": {
            "message_id": uid,
            "date": 1,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "T"},
            "text": text,
        },
    }


def _post_webhook(client: Client, payload: dict, secret: str = "sec"):
    return client.post(
        "/api/telegram/webhook/",
        data=json.dumps(payload),
        content_type="application/json",
        HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=secret,
    )


def test_webhook_route_and_security() -> None:
    client = Client()
    r = client.get("/api/telegram/webhook/")
    assert r.status_code == 200
    body = r.json()
    assert body.get("mode") == "webhook"
    assert body.get("ack") == "fast"
    _pass("webhook_route_exists")

    with override_settings(TELEGRAM_WEBHOOK_SECRET="test-secret-abc", DEBUG=False):
        bad = client.post(
            "/api/telegram/webhook/",
            data=b"{}",
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong",
        )
        assert bad.status_code == 403
        _pass("invalid_secret_rejected")

        good_headers = {"HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN": "test-secret-abc"}
        malformed = client.post(
            "/api/telegram/webhook/",
            data=b"not-json",
            content_type="application/json",
            **good_headers,
        )
        assert malformed.status_code == 400
        _pass("malformed_update_handled")


def test_duplicate_update_id() -> None:
    release_update(424242)
    assert claim_update(424242) is True
    assert claim_update(424242) is False
    release_update(424242)
    assert claim_update(424242) is True
    release_update(424242)
    _pass("duplicate_update_id_once")


def test_fast_ack_does_not_wait_for_handlers() -> None:
    """HTTP 200 must return before slow process_update finishes."""
    webhook_views.reset_webhook_worker_for_tests()
    client = Client()
    started = {"bg": False}
    gate = {"release": False}
    fake = _fake_application()

    async def slow_on_process(update, loop):
        started["bg"] = True
        for _ in range(200):
            if gate["release"]:
                break
            await asyncio.sleep(0.01)

    fake = _fake_application(on_process=slow_on_process)

    uid = 900001
    release_update(uid)
    payload = _make_payload(uid, "Why is Nvidia moving today?")

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views.build_application",
            return_value=fake,
        ):
            t0 = time.perf_counter()
            resp = _post_webhook(client, payload)
            elapsed = time.perf_counter() - t0
            assert resp.status_code == 200
            assert elapsed < 1.0, f"ACK too slow: {elapsed:.3f}s"
            webhook_views.wait_until_worker_ready_for_tests(timeout=3.0)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and not started["bg"]:
                time.sleep(0.01)
            assert started["bg"] is True
            gate["release"] = True
            webhook_views.wait_for_queue_idle_for_tests(timeout=3.0)

            resp2 = _post_webhook(client, payload)
            assert resp2.status_code == 200

    webhook_views.reset_webhook_worker_for_tests()
    release_update(uid)
    _pass("fast_ack_before_handlers")
    _pass("duplicate_delivery_acked")


def test_persistent_loop_two_sequential_updates() -> None:
    """A: Two updates share one persistent loop and one Application."""
    webhook_views.reset_webhook_worker_for_tests()
    client = Client()
    fake = _fake_application()
    uids = [910001, 910002]
    for uid in uids:
        release_update(uid)

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views.build_application",
            return_value=fake,
        ):
            for uid in uids:
                resp = _post_webhook(client, _make_payload(uid, f"msg-{uid}"))
                assert resp.status_code == 200

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(fake.processed_ids) < 2:
                time.sleep(0.02)
            webhook_views.wait_for_queue_idle_for_tests(timeout=3.0)

            assert fake.errors == [], f"unexpected errors: {fake.errors}"
            assert fake.processed_ids == uids, fake.processed_ids
            assert fake.init_loop_id is not None
            assert len(fake.process_loop_ids) == 2
            assert fake.process_loop_ids[0] == fake.init_loop_id
            assert fake.process_loop_ids[1] == fake.init_loop_id
            assert webhook_views.get_application() is fake
            loop = webhook_views.get_worker_loop()
            assert loop is not None
            assert not loop.is_closed()
            assert id(loop) == fake.init_loop_id
            assert webhook_views._worker_thread is not None
            assert webhook_views._worker_thread.is_alive()
            assert webhook_views.get_worker_state() is WorkerState.READY

    webhook_views.reset_webhook_worker_for_tests()
    for uid in uids:
        release_update(uid)
    _pass("persistent_loop_two_sequential_updates")
    _pass("no_event_loop_is_closed")


def test_concurrent_webhook_submissions() -> None:
    """B: Concurrent POSTs enqueue safely; all updates process on one loop."""
    webhook_views.reset_webhook_worker_for_tests()
    fake = _fake_application()
    uids = list(range(920001, 920009))
    for uid in uids:
        release_update(uid)

    results: list[int] = []
    lock = threading.Lock()

    def post_one(uid: int) -> None:
        c = Client()
        with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
            resp = _post_webhook(c, _make_payload(uid, f"c-{uid}"))
            with lock:
                results.append(resp.status_code)

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views.build_application",
            return_value=fake,
        ):
            webhook_views._ensure_worker_started()
            threads = [
                threading.Thread(target=post_one, args=(uid,)) for uid in uids
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(fake.processed_ids) < len(uids):
                time.sleep(0.02)
            webhook_views.wait_for_queue_idle_for_tests(timeout=5.0)

            assert results == [200] * len(uids), results
            assert fake.errors == [], fake.errors
            assert sorted(fake.processed_ids) == uids
            assert len(set(fake.process_loop_ids)) == 1
            assert fake.process_loop_ids[0] == fake.init_loop_id
            assert webhook_views.get_application() is fake

    webhook_views.reset_webhook_worker_for_tests()
    for uid in uids:
        release_update(uid)
    _pass("concurrent_webhook_submissions")


def test_concurrent_startup_while_init_pending() -> None:
    """C: Concurrent requests during STARTING; one thread; no false READY."""
    webhook_views.reset_webhook_worker_for_tests()
    init_gate = threading.Event()
    fake = _fake_application(init_gate=init_gate)
    uids = list(range(930001, 930009))
    for uid in uids:
        release_update(uid)

    observed_states: list[WorkerState] = []
    results: list[int] = []
    lock = threading.Lock()

    def post_one(uid: int) -> None:
        c = Client()
        with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
            resp = _post_webhook(c, _make_payload(uid, f"s-{uid}"))
            state = webhook_views.get_worker_state()
            with lock:
                results.append(resp.status_code)
                observed_states.append(state)

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views.build_application",
            return_value=fake,
        ):
            threads = [
                threading.Thread(target=post_one, args=(uid,)) for uid in uids
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            assert results == [200] * len(uids), results
            # While gate held, worker must not be READY.
            assert webhook_views.get_worker_state() in (
                WorkerState.STARTING,
                WorkerState.FAILED,
            )
            assert WorkerState.READY not in observed_states
            assert webhook_views._worker_thread is not None
            alive_name = webhook_views._worker_thread.name
            assert alive_name == "tg-webhook-loop"
            # Exactly one worker thread object for this process lifetime segment.
            thread_id = webhook_views._worker_thread.ident
            assert fake.processed_ids == []

            init_gate.set()
            webhook_views.wait_until_worker_ready_for_tests(timeout=5.0)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(fake.processed_ids) < len(uids):
                time.sleep(0.02)
            webhook_views.wait_for_queue_idle_for_tests(timeout=5.0)

            assert webhook_views.get_worker_state() is WorkerState.READY
            assert webhook_views._worker_thread.ident == thread_id
            assert sorted(fake.processed_ids) == uids
            assert len(set(fake.process_loop_ids)) == 1

    webhook_views.reset_webhook_worker_for_tests()
    for uid in uids:
        release_update(uid)
    _pass("concurrent_startup_while_init_pending")


def test_initialization_failure_and_recovery() -> None:
    """D: Init failures do not strand forever; recover and process; claims OK."""
    webhook_views.reset_webhook_worker_for_tests()
    # Fail twice, then succeed. Short retry for tests.
    fake = _fake_application(fail_init_times=2)
    uid = 940001
    release_update(uid)
    client = Client()

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views.build_application",
            return_value=fake,
        ):
            with patch.object(webhook_views, "_INIT_RETRY_SECONDS", 0.05):
                t0 = time.perf_counter()
                resp = _post_webhook(client, _make_payload(uid, "recover-me"))
                elapsed = time.perf_counter() - t0
                # ACK must not wait for successful initialize.
                assert resp.status_code == 200
                assert elapsed < 0.5, f"ACK blocked on init retries: {elapsed:.3f}s"

                # Not processed while still failing init.
                time.sleep(0.08)
                assert fake.processed_ids == []
                assert fake.init_attempts >= 1

                webhook_views.wait_until_worker_ready_for_tests(timeout=5.0)
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not fake.processed_ids:
                    time.sleep(0.02)
                webhook_views.wait_for_queue_idle_for_tests(timeout=3.0)

                assert fake.init_attempts >= 3
                assert fake.processed_ids == [uid]
                assert webhook_views.get_worker_state() is WorkerState.READY
                # Claim still held after successful accept+process (not released).
                assert claim_update(uid) is False

    webhook_views.reset_webhook_worker_for_tests()
    release_update(uid)
    _pass("initialization_failure_and_recovery")


def test_cold_start_ack_does_not_wait_for_initialize() -> None:
    """E: Cold-start ACK returns before Application.initialize completes."""
    webhook_views.reset_webhook_worker_for_tests()
    init_gate = threading.Event()
    fake = _fake_application(init_gate=init_gate)
    uid = 950001
    release_update(uid)
    client = Client()

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views.build_application",
            return_value=fake,
        ):
            t0 = time.perf_counter()
            resp = _post_webhook(client, _make_payload(uid, "cold"))
            elapsed = time.perf_counter() - t0
            assert resp.status_code == 200
            assert elapsed < 0.5, f"cold-start ACK too slow: {elapsed:.3f}s"
            assert webhook_views.get_worker_state() is WorkerState.STARTING
            assert fake.processed_ids == []
            assert fake.init_loop_id is None

            init_gate.set()
            webhook_views.wait_until_worker_ready_for_tests(timeout=5.0)
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not fake.processed_ids:
                time.sleep(0.02)
            assert fake.processed_ids == [uid]

    webhook_views.reset_webhook_worker_for_tests()
    release_update(uid)
    _pass("cold_start_ack_does_not_wait_for_initialize")


def test_processing_exception_does_not_kill_worker() -> None:
    """F: First update raises; worker stays alive; second update processes."""
    webhook_views.reset_webhook_worker_for_tests()
    boom_uid = 960001
    ok_uid = 960002
    release_update(boom_uid)
    release_update(ok_uid)

    async def on_process(update, loop):
        if getattr(update, "update_id", None) == boom_uid:
            raise RuntimeError("simulated handler failure")

    fake = _fake_application(on_process=on_process)
    client = Client()

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views.build_application",
            return_value=fake,
        ):
            assert _post_webhook(client, _make_payload(boom_uid, "boom")).status_code == 200
            assert _post_webhook(client, _make_payload(ok_uid, "ok")).status_code == 200

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and ok_uid not in fake.processed_ids:
                time.sleep(0.02)
            webhook_views.wait_for_queue_idle_for_tests(timeout=3.0)

            assert boom_uid in fake.processed_ids
            assert ok_uid in fake.processed_ids
            assert webhook_views.get_worker_state() is WorkerState.READY
            assert webhook_views._worker_thread is not None
            assert webhook_views._worker_thread.is_alive()
            loop = webhook_views.get_worker_loop()
            assert loop is not None and not loop.is_closed()
            # Failed update released claim for Telegram retry.
            assert claim_update(boom_uid) is True
            release_update(boom_uid)
            # Successful update claim remains.
            assert claim_update(ok_uid) is False

    webhook_views.reset_webhook_worker_for_tests()
    release_update(ok_uid)
    _pass("processing_exception_does_not_kill_worker")


def test_enqueue_failure_releases_claim() -> None:
    """Enqueue/start failure must release claim and return 500."""
    webhook_views.reset_webhook_worker_for_tests()
    uid = 970001
    release_update(uid)
    client = Client()

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views._ensure_worker_started",
            side_effect=RuntimeError("cannot start worker"),
        ):
            resp = _post_webhook(client, _make_payload(uid, "nope"))
            assert resp.status_code == 500
            # Claim released so Telegram can retry.
            assert claim_update(uid) is True
            release_update(uid)

    webhook_views.reset_webhook_worker_for_tests()
    _pass("enqueue_failure_releases_claim")


def test_intents_and_processor_contract() -> None:
    """Shared brain still routes finance/sheets/calendar/gmail (not webhook-specific)."""
    assert detect_sheet_intent("https://docs.google.com/spreadsheets/d/abc/edit")
    _pass("sheets_url_intent")
    assert detect_calendar_intent("What's on my calendar today?")
    _pass("calendar_intent")
    assert detect_gmail_intent("Show me my latest finance emails.")
    _pass("gmail_intent")

    assert hasattr(ConversationProcessor, "handle_text")
    assert hasattr(ConversationProcessor, "handle_document")
    _pass("conversation_processor_contract")

    sample = (
        "I need Google Calendar access.\n"
        "[Connect Google](https://accounts.google.com/o/oauth2/v2/auth?client_id=x)"
    )
    url = extract_google_oauth_url(sample)
    assert url and url.startswith("https://accounts.google.com/")
    assert "*" in prepare_telegram_markdown("**Summary**")
    _pass("oauth_url_button_generation")
    _pass("telegram_adapter_formatting")


def test_voice_and_pdf_handlers_registered() -> None:
    src = Path(BASE / "telegram_bot" / "bot.py").read_text(encoding="utf-8")
    assert "handle_voice_message" in src
    assert "handle_photo_or_document" in src
    assert "filters.VOICE" in src
    wh = Path(BASE / "telegram_bot" / "webhook_views.py").read_text(encoding="utf-8")
    assert "_schedule_background" in wh
    assert "queue.Queue" in wh
    assert "tg-webhook-loop" in wh
    assert "asyncio.new_event_loop" in wh
    assert "from asgiref.sync import async_to_sync" not in wh
    assert "async_to_sync(" not in wh
    assert "_schedule_background(payload, update_id)" in wh
    assert "daemon=True" in wh
    _pass("voice_path_compatible_with_fast_ack")
    _pass("pdf_path_compatible_with_fast_ack")
    _pass("persistent_worker_architecture")


def test_send_typing_still_present() -> None:
    adapter = Path(
        BASE / "telegram_bot" / "adapters" / "telegram_adapter.py"
    ).read_text(encoding="utf-8")
    assert "async def send_typing" in adapter
    assert "send_chat_action" in adapter or "chat.send_action" in adapter
    _pass("send_typing_preserved")


def test_set_webhook_command_dry_run() -> None:
    with override_settings(
        TELEGRAM_BOT_TOKEN="123456:ABCDEF-fake-token-for-test",
        TELEGRAM_WEBHOOK_URL="https://example.onrender.com/api/telegram/webhook/",
        TELEGRAM_WEBHOOK_SECRET="webhook-secret-value",
        DEBUG=False,
    ):
        cmd = SetWebhookCmd()
        out = MagicMock()
        cmd.stdout = out
        cmd.style = MagicMock()
        cmd.style.SUCCESS = lambda s: s
        cmd.style.WARNING = lambda s: s
        cmd.handle(dry_run=True, delete=False, info=False, drop_pending=False)
        written = " ".join(
            str(c.args[0]) if c.args else "" for c in out.write.call_args_list
        )
        assert "webhook-secret-value" not in written
        assert "ABCDEF-fake-token-for-test" not in written
        assert "dry_run=True" in written or "dry_run" in written.lower()
    _pass("set_webhook_dry_run")
    _pass("no_secret_leakage_in_command")


def test_polling_still_available() -> None:
    run_bot = Path(BASE / "telegram_bot" / "management" / "commands" / "run_bot.py")
    assert run_bot.exists()
    text = run_bot.read_text(encoding="utf-8")
    assert "run_polling" in text
    assert callable(build_application)
    _pass("polling_run_bot_still_exists")


def test_render_yaml_free_no_worker() -> None:
    text = (BASE / "render.yaml").read_text(encoding="utf-8")
    assert "atlas-ai-bot" not in text
    assert "type: worker" not in text
    assert "startCommand: python manage.py run_bot" not in text
    assert "plan: free" in text
    assert "atlas-ai-web" in text
    assert "TELEGRAM_WEBHOOK_URL" in text
    assert "gunicorn config.wsgi:application --bind 0.0.0.0:$PORT" in text
    assert "healthCheckPath: /health/" in text
    for bad in ("sk-", "AIza", "ghp_"):
        assert bad not in text
    _pass("render_yaml_free_no_paid_worker")


def test_oauth_paths_unchanged() -> None:
    from accounts import urls as acc_urls

    patterns = [getattr(p, "name", None) for p in acc_urls.urlpatterns]
    assert "google_oauth_callback" in patterns
    _pass("oauth_callback_route_unchanged")


def main() -> None:
    print("=== Telegram webhook reliability verification ===")
    try:
        cache.clear()
    except Exception:  # noqa: BLE001
        pass
    webhook_views.reset_webhook_worker_for_tests()

    test_webhook_route_and_security()
    test_duplicate_update_id()
    test_fast_ack_does_not_wait_for_handlers()
    test_persistent_loop_two_sequential_updates()
    test_concurrent_webhook_submissions()
    test_concurrent_startup_while_init_pending()
    test_initialization_failure_and_recovery()
    test_cold_start_ack_does_not_wait_for_initialize()
    test_processing_exception_does_not_kill_worker()
    test_enqueue_failure_releases_claim()
    test_intents_and_processor_contract()
    test_voice_and_pdf_handlers_registered()
    test_send_typing_still_present()
    test_set_webhook_command_dry_run()
    test_polling_still_available()
    test_render_yaml_free_no_worker()
    test_oauth_paths_unchanged()

    webhook_views.reset_webhook_worker_for_tests()
    print("\nWEBHOOK_VERIFICATION: PASS")
    print(
        "Architecture: Telegram -> HTTPS webhook -> fast ACK -> queue -> "
        "one persistent asyncio loop thread -> Application.process_update "
        "(no paid worker)."
    )
    print(
        "Limitation: in-process queue/thread are not durable across Render free "
        "spin-downs/restarts."
    )


if __name__ == "__main__":
    main()
