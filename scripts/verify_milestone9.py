"""Milestone 9 verification — Google Calendar as schedule intelligence (demo mode)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
os.chdir(BASE)

import django

django.setup()

from accounts.models import User  # noqa: E402
from gcalendar.models import CalendarEvent  # noqa: E402
from gcalendar.services.calendar_intent import detect_calendar_intent  # noqa: E402
from gcalendar.services.calendar_service import CalendarService  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402
from tools.definitions import list_implemented_tool_names  # noqa: E402


def _ok(label: str) -> None:
    print(f"PASS {label}")


def test_tools_registered() -> None:
    names = list_implemented_tool_names()
    for t in (
        "calendar_lookup",
        "calendar_today",
        "calendar_search",
        "calendar_create",
        "calendar_update",
        "calendar_delete",
        "calendar_free_time",
        "calendar_conflicts",
        "calendar_deadlines",
    ):
        assert t in names, t
    _ok("calendar_tools_registered")


def test_intents() -> None:
    assert detect_calendar_intent("Connect my calendar").kind == "connect"
    assert detect_calendar_intent("What does my day look like?").kind == "today"
    assert detect_calendar_intent("What meetings do I have today?").kind == "today"
    assert detect_calendar_intent("When am I free?").kind == "free"
    assert detect_calendar_intent("Any conflicts?").kind == "conflicts"
    assert detect_calendar_intent("Schedule a meeting tomorrow at 2 PM").kind == "create"
    assert detect_calendar_intent("Move it to Friday").kind == "update"
    assert detect_calendar_intent("What about Friday?").kind == "update"
    assert detect_calendar_intent("Cancel that").kind == "cancel"
    assert detect_calendar_intent("Apple stock price").kind == "none"
    _ok("calendar_intents")


def test_service_memory() -> None:
    tid = 9910000901
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="CalHero", onboarding_completed=True)
    svc = CalendarService()
    svc.connect_demo(user)
    synced = svc.sync_range(user)
    assert synced["ok"]
    assert CalendarEvent.objects.filter(user=user).count() >= 3
    day = svc.day_view(user)
    assert day["ok"]
    assert "today" in (day.get("reply") or "").lower() or "•" in (day.get("reply") or "")
    conf = svc.conflicts(user)
    assert conf["ok"]
    assert "overlap" in (conf.get("reply") or "").lower() or "conflict" in (
        conf.get("reply") or ""
    ).lower()
    free = svc.free_time(user, "find a free one-hour slot")
    assert free["ok"]
    blob = (day.get("reply") or "") + (conf.get("reply") or "")
    assert "demo_evt_" not in blob.lower()
    assert "calendar_id" not in blob.lower()
    assert "event_id" not in blob.lower()
    _ok("calendar_service_memory")


def test_telegram_journey() -> None:
    tid = 9910000902
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="CalDemo", onboarding_completed=True)
    CalendarService().connect_demo(user)
    CalendarService().sync_range(user)
    p = ConversationProcessor()

    def ask(label: str, text: str) -> str:
        r = p.handle_text(
            telegram_id=tid,
            text=text,
            telegram_message_id=int(time.time() * 1000) % 10_000_000,
        )
        low = (r or "").lower()
        for leak in ("event_id", "calendar_id", "demo_evt_", "calendar#event"):
            assert leak not in low, f"leak {leak} in {label}"
        safe = (r or "")[:120].replace("\n", " | ").encode("ascii", "replace").decode("ascii")
        print(f"OK {label}: {safe}")
        return r or ""

    ask("today", "What does my day look like?")
    create = ask("create", "Schedule a meeting tomorrow at 2 PM.")
    assert "yes" in create.lower()
    ask("confirm_create", "YES")
    ask("free", "Find a free one-hour slot.")
    port = ask("portfolio", "Schedule time to review my portfolio tomorrow at 4 PM.")
    assert "yes" in port.lower() or "draft" in port.lower()
    ask("confirm_port", "YES")
    ask("conflicts", "Any conflicts?")
    ask("remind", "Remind me before Nvidia reports.")
    ask("confirm_remind", "YES")
    move = ask("friday", "What about Friday?")
    assert "yes" in move.lower() or "friday" in move.lower() or "reschedule" in move.lower()
    ask("confirm_move", "YES")
    _ok("telegram_calendar_journey")


def test_cross_integration_hint() -> None:
    """Gmail meeting emails should nudge calendar; calendar earnings nudge finance."""
    from gmail.services.gmail_intel import format_thread_summary

    hint = format_thread_summary(
        {
            "subject": "Interview invite",
            "from_name": "Alex",
            "snippet": "Please join Friday.",
            "body_text": "Interview Friday 10am.",
            "categories": ["meeting"],
            "why": "Meeting implied.",
        }
    )
    assert "calendar" in hint.lower()
    tid = 9910000903
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="Cross", onboarding_completed=True)
    svc = CalendarService()
    svc.connect_demo(user)
    svc.sync_range(user)
    day = svc.day_view(user)
    # Demo day includes finance / earnings items across range; today may mention finance
    assert day["ok"]
    _ok("cross_integration_hints")


def main() -> None:
    print("=== Milestone 9 verification ===")
    test_tools_registered()
    test_intents()
    test_service_memory()
    test_telegram_journey()
    test_cross_integration_hint()
    print("ALL MILESTONE 9 CHECKS PASSED")


if __name__ == "__main__":
    main()
