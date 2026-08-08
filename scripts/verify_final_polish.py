"""Final polish verification — cross-tool continuity (demo paths, no new features)."""

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
from conversation.services.response_formatter import ResponseFormatter  # noqa: E402
from gcalendar.services.calendar_service import CalendarService  # noqa: E402
from gmail.services.gmail_service import GmailService  # noqa: E402
from sheets.services.sheet_service import SheetService  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


def _ok(label: str) -> None:
    print(f"PASS {label}")


def test_formatter_polish() -> None:
    fmt = ResponseFormatter()
    out = fmt.format("Certainly! Finnhub says AAPL is fine. message_id 123")
    low = out.lower()
    assert not low.startswith("certainly")
    assert "finnhub" not in low
    assert "message_id" not in low
    _ok("formatter_bans_leaks")


def test_cross_tool_journey() -> None:
    tid = 9910000999
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(
        telegram_id=tid, first_name="Polish", onboarding_completed=True
    )
    SheetService().connect_demo(user)
    SheetService().sync_catalog(user)
    GmailService().connect_demo(user)
    GmailService().sync_inbox(user)
    CalendarService().connect_demo(user)
    CalendarService().sync_range(user)

    p = ConversationProcessor()
    mid = 900000

    def ask(text: str) -> str:
        nonlocal mid
        mid += 1
        return p.handle_text(telegram_id=tid, text=text, telegram_message_id=mid) or ""

    sheet = ask("Open my portfolio.")
    assert "portfolio" in sheet.lower()
    assert "spreadsheet_id" not in sheet.lower()

    mail = ask("What needs my attention?")
    assert len(mail) > 20
    assert "message_id" not in mail.lower()

    msft = ask("Find emails about Microsoft.")
    assert "microsoft" in msft.lower()

    day = ask("What does my day look like?")
    assert "today" in day.lower() or "standup" in day.lower() or "portfolio" in day.lower()

    # Scheduling should not be stolen by Sheets
    sched = ask("Schedule time to review Nvidia earnings tomorrow at 3 PM.")
    assert "yes" in sched.lower()
    assert "draft event" in sched.lower() or "nvidia" in sched.lower()

    # Sheets follow-up still works after calendar/mail
    about = ask("Open my portfolio.")
    tick = ask("What about Microsoft?")
    assert "msft" in tick.lower() or "microsoft" in tick.lower()

    _ok("cross_tool_continuity")


def test_confirm_gates() -> None:
    tid = 9910000998
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(
        telegram_id=tid, first_name="Gate", onboarding_completed=True
    )
    CalendarService().connect_demo(user)
    CalendarService().sync_range(user)
    p = ConversationProcessor()
    r1 = p.handle_text(telegram_id=tid, text="Cancel that", telegram_message_id=1) or ""
    assert "yes" in r1.lower()
    # Without YES, event should still exist as confirmed for highest-importance
    from gcalendar.models import CalendarEvent

    before = CalendarEvent.objects.filter(user=user, status="confirmed").count()
    assert before >= 1
    _ok("calendar_confirm_gate")


def main() -> None:
    print("=== Final polish verification ===")
    test_formatter_polish()
    test_cross_tool_journey()
    test_confirm_gates()
    print("ALL FINAL POLISH CHECKS PASSED")


if __name__ == "__main__":
    main()
