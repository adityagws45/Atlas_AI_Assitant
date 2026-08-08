"""Telegram webhook reliability verification (fast ACK + idempotency).

Mocks external Telegram/Gemini/Groq where needed. Does not prove live APIs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from django.conf import settings
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


def _pass(label: str) -> None:
    print(f"PASS {label}")


def _fail(label: str, detail: str = "") -> None:
    raise AssertionError(f"FAIL {label}: {detail}")


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
    client = Client()
    started = {"bg": False}
    gate = {"release": False}

    def slow_process(payload, update_id):
        started["bg"] = True
        # Block until main thread has already observed 200
        for _ in range(200):
            if gate["release"]:
                break
            time.sleep(0.01)

    uid = 900001
    release_update(uid)
    payload = {
        "update_id": uid,
        "message": {
            "message_id": 1,
            "date": 1,
            "chat": {"id": 1, "type": "private"},
            "from": {"id": 1, "is_bot": False, "first_name": "T"},
            "text": "Why is Nvidia moving today?",
        },
    }

    with override_settings(TELEGRAM_WEBHOOK_SECRET="sec", DEBUG=False):
        with patch(
            "telegram_bot.webhook_views._process_update_payload",
            side_effect=slow_process,
        ):
            t0 = time.perf_counter()
            resp = client.post(
                "/api/telegram/webhook/",
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="sec",
            )
            elapsed = time.perf_counter() - t0
            assert resp.status_code == 200
            assert elapsed < 1.0, f"ACK too slow: {elapsed:.3f}s"
            # Allow thread to start
            time.sleep(0.05)
            assert started["bg"] is True
            gate["release"] = True
            time.sleep(0.05)

            # Duplicate should ACK without scheduling again
            resp2 = client.post(
                "/api/telegram/webhook/",
                data=json.dumps(payload),
                content_type="application/json",
                HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="sec",
            )
            assert resp2.status_code == 200

    release_update(uid)
    _pass("fast_ack_before_handlers")
    _pass("duplicate_delivery_acked")


def test_intents_and_processor_contract() -> None:
    """Shared brain still routes finance/sheets/calendar/gmail (not webhook-specific)."""
    assert detect_sheet_intent("https://docs.google.com/spreadsheets/d/abc/edit")
    _pass("sheets_url_intent")
    assert detect_calendar_intent("What's on my calendar today?")
    _pass("calendar_intent")
    assert detect_gmail_intent("Show me my latest finance emails.")
    _pass("gmail_intent")

    # ConversationProcessor still the brain
    assert hasattr(ConversationProcessor, "handle_text")
    assert hasattr(ConversationProcessor, "handle_document")
    _pass("conversation_processor_contract")

    # OAuth button extraction unchanged
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
    assert "return HttpResponse(status=200)" in wh
    assert "async_to_sync(_process)()" not in wh.split("def telegram_webhook")[1].split(
        "return HttpResponse(status=200)"
    )[0]
    # Ensure the view itself does not call process_update synchronously before ACK
    view_src = Path(BASE / "telegram_bot" / "webhook_views.py").read_text(encoding="utf-8")
    assert "_schedule_background(payload, update_id)" in view_src
    assert "daemon=True" in view_src
    _pass("voice_path_compatible_with_fast_ack")
    _pass("pdf_path_compatible_with_fast_ack")


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
        # Ensure secrets not printed
        written = " ".join(
            str(c.args[0]) if c.args else "" for c in out.write.call_args_list
        )
        assert "webhook-secret-value" not in written
        assert "ABCDEF-fake-token-for-test" not in written
        assert "dry_run=True" in written or "dry_run" in written.lower()
    _pass("set_webhook_dry_run")
    _pass("no_secret_leakage_in_command")


def test_polling_still_available() -> None:
    from pathlib import Path as P

    run_bot = P(BASE / "telegram_bot" / "management" / "commands" / "run_bot.py")
    assert run_bot.exists()
    text = run_bot.read_text(encoding="utf-8")
    assert "run_polling" in text
    # build_application still shared
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
    # No secret values
    for bad in ("sk-", "AIza", "ghp_"):
        assert bad not in text
    _pass("render_yaml_free_no_paid_worker")


def test_oauth_paths_unchanged() -> None:
    from django.urls import reverse

    # accounts urls include callback
    from accounts import urls as acc_urls

    patterns = [getattr(p, "name", None) for p in acc_urls.urlpatterns]
    assert "google_oauth_callback" in patterns
    _pass("oauth_callback_route_unchanged")


def main() -> None:
    print("=== Telegram webhook reliability verification ===")
    # Isolate cache keys between runs
    try:
        cache.clear()
    except Exception:  # noqa: BLE001
        pass

    test_webhook_route_and_security()
    test_duplicate_update_id()
    test_fast_ack_does_not_wait_for_handlers()
    test_intents_and_processor_contract()
    test_voice_and_pdf_handlers_registered()
    test_set_webhook_command_dry_run()
    test_polling_still_available()
    test_render_yaml_free_no_worker()
    test_oauth_paths_unchanged()

    print("\nWEBHOOK_VERIFICATION: PASS")
    print(
        "Architecture: Telegram → HTTPS webhook → fast ACK → background thread "
        "→ same handlers → ConversationProcessor (no paid worker)."
    )
    print(
        "Limitation: in-process threads are not durable across Render free "
        "spin-downs/restarts."
    )


if __name__ == "__main__":
    main()
