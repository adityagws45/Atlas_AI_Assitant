"""Generic Google Sheets URL + active-sheet verification (demo mode)."""

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
from accounts.services.google_oauth_service import GoogleOAuthService  # noqa: E402
from conversation.models import Message  # noqa: E402
from sheets.services.demo_data import (  # noqa: E402
    DEMO_AMZN_FINANCIALS,
    DEMO_GENERIC_HOLDINGS,
    DEMO_MSFT_FINANCIALS,
    DEMO_WATCHLIST,
)
from sheets.services.sheet_intent import detect_sheet_intent, extract_spreadsheet_id  # noqa: E402
from sheets.services.sheet_memory import SheetMemory  # noqa: E402
from sheets.services.sheet_service import SheetService  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


def _url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid=0"


def _ask(p: ConversationProcessor, tid: int, text: str) -> tuple[str, dict]:
    r = p.handle_text(
        telegram_id=tid,
        text=text,
        username="sheetsgen",
        first_name="SheetsGen",
        telegram_message_id=int(time.time() * 1000) % 10_000_000,
    ) or ""
    m = (
        Message.objects.filter(conversation__user__telegram_id=tid, role="assistant")
        .order_by("-created_at")
        .first()
    )
    return r, (m.metadata or {}) if m else {}


def main() -> None:
    # URL extraction
    sid = extract_spreadsheet_id(_url("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"))
    assert sid and len(sid) > 20
    assert detect_sheet_intent(_url(sid)).kind == "open_url"
    assert detect_sheet_intent("Happy to dig into Google").kind == "none"
    print("PASS url_detection")

    oauth_configured = GoogleOAuthService().is_configured()
    print(f"INFO live_oauth_configured={oauth_configured}")

    # Public Google sample — must open WITHOUT OAuth / without Watchlist
    tid0 = 9920000800
    User.objects.filter(telegram_id=tid0).delete()
    User.objects.create(telegram_id=tid0, first_name="SheetOAuth", onboarding_completed=True)
    p = ConversationProcessor()
    unknown = _url("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms")
    r, meta = _ask(p, tid0, unknown)
    low = r.lower()
    assert meta.get("pipeline") == "sheets"
    assert "watchlist" not in low
    assert "ai watchlist" not in low
    assert "company overview" not in low
    assert "happy to dig into google" not in low
    assert "connect google" not in low
    assert meta.get("ok") is True or "i can access" in low or "google sheet detected" in low
    print("PASS public_url_no_oauth_no_watchlist")
    print("  ", r[:180].replace("\n", " | ").encode("ascii", "replace").decode("ascii"))

    # Truly unknown / inaccessible ID must not become Watchlist
    tid_priv = 9920000805
    User.objects.filter(telegram_id=tid_priv).delete()
    User.objects.create(telegram_id=tid_priv, first_name="SheetPriv", onboarding_completed=True)
    r, meta = _ask(p, tid_priv, _url("1ArbitraryPrivateSheetIdXXXXXX99999"))
    low = r.lower()
    assert meta.get("pipeline") == "sheets"
    assert "watchlist" not in low
    assert (
        "connect google" in low
        or "couldn't find" in low
        or "authorization" in low
        or "oauth" in low
        or "permission" in low
        or "temporary" in low
        or "couldn't read" in low
        or "can't" in low
    )
    print("PASS inaccessible_url_no_watchlist_fallback")
    print("  ", r[:180].replace("\n", " | ").encode("ascii", "replace").decode("ascii"))

    def run_company_sheet(label: str, tid: int, demo: dict, revenue_token: str) -> None:
        User.objects.filter(telegram_id=tid).delete()
        User.objects.create(telegram_id=tid, first_name=label, onboarding_completed=True)
        cp = ConversationProcessor()
        url = _url(demo["id"])
        r1, m1 = _ask(cp, tid, url)
        assert m1.get("pipeline") == "sheets" and m1.get("ok") is True
        assert "google sheet detected" in r1.lower()
        assert "ready to analyze" in r1.lower()
        assert "ai watchlist" not in r1.lower()
        mem = SheetMemory()
        user = User.objects.get(telegram_id=tid)
        assert mem.active_workbook_id(user)
        print(f"PASS {label}_url_active_sheet")

        r2, m2 = _ask(cp, tid, "Analyze this sheet.")
        assert m2.get("pipeline") == "sheets"
        assert demo["title"].split()[0].lower() in r2.lower() or "key findings" in r2.lower() or "summary" in r2.lower()
        assert "ai watchlist" not in r2.lower()
        print(f"PASS {label}_analyze")

        r3, m3 = _ask(cp, tid, "What was the revenue in 2025?")
        assert m3.get("pipeline") == "sheets"
        assert revenue_token in r3.replace(",", "")
        assert "ai watchlist" not in r3.lower()
        print(f"PASS {label}_revenue_2025")
        print("  ", r3[:160].replace("\n", " | "))

        r4, m4 = _ask(cp, tid, "Which metric improved the most?")
        assert m4.get("pipeline") == "sheets"
        assert "ai watchlist" not in r4.lower()
        print(f"PASS {label}_metric_improved")

        r5, m5 = _ask(cp, tid, "What are the biggest risks?")
        assert m5.get("pipeline") == "sheets"
        assert "ai watchlist" not in r5.lower()
        print(f"PASS {label}_risks_followup")

    run_company_sheet("msft", 9920000801, DEMO_MSFT_FINANCIALS, "268000")
    run_company_sheet("amzn", 9920000802, DEMO_AMZN_FINANCIALS, "700000")

    # Generic holdings sheet
    tid3 = 9920000803
    User.objects.filter(telegram_id=tid3).delete()
    User.objects.create(telegram_id=tid3, first_name="GenericSheet", onboarding_completed=True)
    cp = ConversationProcessor()
    r1, m1 = _ask(cp, tid3, _url(DEMO_GENERIC_HOLDINGS["id"]))
    assert m1.get("pipeline") == "sheets" and "google sheet detected" in r1.lower()
    r2, m2 = _ask(cp, tid3, "Analyze this sheet.")
    assert m2.get("pipeline") == "sheets"
    assert "tsla" in r2.lower() or "voo" in r2.lower() or "holdings" in r2.lower() or "summary" in r2.lower()
    assert DEMO_WATCHLIST["title"].lower() not in r2.lower()
    print("PASS generic_holdings_sheet")

    # Regression: finance/doc routing not broken — stock question without active sheet
    tid4 = 9920000804
    User.objects.filter(telegram_id=tid4).delete()
    User.objects.create(telegram_id=tid4, first_name="NoSheet", onboarding_completed=True)
    cp = ConversationProcessor()
    r, m = _ask(cp, tid4, "What are the biggest risks?")
    # Without active sheet / doc this may be sheets-none → orchestrator, or sheets asking to paste URL
    assert "ai watchlist" not in (r or "").lower() or m.get("pipeline") != "sheets"
    # Explicit: analyze without active sheet must not open watchlist
    r2, m2 = _ask(cp, tid4, "What stands out?")
    if m2.get("pipeline") == "sheets":
        assert "ai watchlist" not in (r2 or "").lower()
        assert (
            "paste" in (r2 or "").lower()
            or "active" in (r2 or "").lower()
            or "url" in (r2 or "").lower()
            or "spreadsheet" in (r2 or "").lower()
        )
        # Must not have silently analyzed demo portfolio/watchlist holdings
        assert "nvda" not in (r2 or "").lower()
        assert "$99" not in (r2 or "").lower()
    print("PASS no_silent_watchlist_without_active")

    print("\nGENERIC_SHEETS_VERIFICATION: PASS")
    print(f"live_oauth_configured={oauth_configured}")


if __name__ == "__main__":
    main()
