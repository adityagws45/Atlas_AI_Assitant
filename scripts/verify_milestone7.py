"""Milestone 7 verification — Google Sheets as portfolio intelligence (demo mode)."""

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
from sheets.models import SheetWorkbook  # noqa: E402
from sheets.services.sheet_detect import detect_workbook  # noqa: E402
from sheets.services.sheet_intent import detect_sheet_intent  # noqa: E402
from sheets.services.sheet_service import SheetService  # noqa: E402
from sheets.services.demo_data import DEMO_PORTFOLIO  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402
from tools.definitions import list_implemented_tool_names  # noqa: E402


def _ok(label: str) -> None:
    print(f"PASS {label}")


def test_tools_registered() -> None:
    names = list_implemented_tool_names()
    for t in (
        "sheet_lookup",
        "sheet_search",
        "sheet_open",
        "sheet_summary",
        "sheet_portfolio",
        "sheet_trends",
    ):
        assert t in names, t
    _ok("sheet_tools_registered")


def test_intents() -> None:
    assert detect_sheet_intent("Connect my Sheets").kind == "connect"
    assert detect_sheet_intent("Show my spreadsheets").kind == "list"
    assert detect_sheet_intent("Open my portfolio").kind == "open"
    assert detect_sheet_intent("What stands out?").kind == "analyze"
    assert detect_sheet_intent("What about Microsoft?").mode == "ticker"
    assert detect_sheet_intent("Apple stock price").kind == "none"
    _ok("sheet_intents")


def test_detection_and_service() -> None:
    tid = 9910000701
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="SheetHero", onboarding_completed=True)
    detected = detect_workbook(DEMO_PORTFOLIO["values"])
    assert detected["primary_kind"] == "portfolio"
    svc = SheetService()
    svc.connect_demo(user)
    sync = svc.sync_catalog(user)
    assert sync["ok"]
    assert SheetWorkbook.objects.filter(user=user).count() >= 1
    opened = svc.open_sheet(user, "portfolio")
    assert opened["ok"], opened
    assert "Summary" in (opened.get("reply") or "")
    follow = svc.analyze_active(user, question="What changed?", mode="trends")
    assert follow["ok"]
    about = svc.analyze_active(user, question="What about MSFT?", mode="ticker", ticker="MSFT")
    assert about["ok"] and "MSFT" in (about.get("reply") or "")
    # No ID leaks
    blob = (opened.get("reply") or "") + (follow.get("reply") or "")
    assert "demo_portfolio" not in blob.lower()
    assert "spreadsheet_id" not in blob.lower()
    _ok("detection_service_memory")


def test_telegram_journey() -> None:
    tid = 9910000702
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="SheetDemo", onboarding_completed=True)
    # Use demo catalog so the journey doesn't require interactive OAuth
    SheetService().connect_demo(user)
    SheetService().sync_catalog(user)
    p = ConversationProcessor()

    def ask(label: str, text: str) -> str:
        r = p.handle_text(
            telegram_id=tid,
            text=text,
            telegram_message_id=int(time.time() * 1000) % 10_000_000,
        )
        low = (r or "").lower()
        for leak in ("spreadsheet_id", "range!", "a1:", "sheet_id"):
            assert leak not in low, f"leak {leak} in {label}"
        if "googleapis.com/drive" in low or "spreadsheets/v4" in low:
            assert False, f"api leak in {label}"
        safe = (r or "")[:110].replace("\n", " | ").encode("ascii", "replace").decode("ascii")
        print(f"OK {label}: {safe}")
        return r or ""

    list_r = ask("list", "Show my spreadsheets.")
    assert "demo portfolio" in list_r.lower() or "watchlist" in list_r.lower()
    open_r = ask("open", "Open my portfolio.")
    assert "portfolio" in open_r.lower()
    assert "full sheet analysis is next up" not in open_r.lower()
    assert "summary" in open_r.lower() or "key findings" in open_r.lower()
    ask("summarize", "Summarize my portfolio.")
    ask("allocation", "Which sectors am I overweight in?")
    ask("best", "Which holdings are performing best?")
    ask("worst", "Which holdings concern you?")
    ask("risks", "What are the biggest risks?")
    ask("recs", "Recommend portfolio improvements.")
    ask("changed", "What changed?")
    msft = ask("msft", "What about Microsoft?")
    assert "msft" in msft.lower() or "microsoft" in msft.lower()
    _ok("telegram_sheets_journey")


def main() -> None:
    print("=== Milestone 7 verification ===")
    test_tools_registered()
    test_intents()
    test_detection_and_service()
    test_telegram_journey()
    print("ALL MILESTONE 7 CHECKS PASSED")


if __name__ == "__main__":
    main()
