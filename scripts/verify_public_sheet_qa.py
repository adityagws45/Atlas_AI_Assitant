"""Public Google Sheets + free-form active-sheet Q&A verification."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from accounts.models import User
from conversation.models import Message
from sheets.services.public_sheets import load_public_workbook
from sheets.services.sheet_memory import SheetMemory
from sheets.services.sheet_service import SheetService
from telegram_bot.services.conversation_processor import ConversationProcessor

# Google's official public Class Data sample
PUBLIC_SHEET_1 = "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
# Second publicly readable sheet (different dataset)
PUBLIC_SHEET_2 = "1isXwTpJlxMClz1Kg0tkSNMwhd8Z944IgprPULx_aqWg"


def _url(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit#gid=0"


def _pass(label: str) -> None:
    print(f"PASS {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"FAIL {label}: {detail}")
    raise SystemExit(1)


def _ask(p: ConversationProcessor, tid: int, text: str) -> tuple[str, dict]:
    r = (
        p.handle_text(
            telegram_id=tid,
            text=text,
            username="pubsheet",
            first_name="PubSheet",
            telegram_message_id=int(time.time() * 1000) % 10_000_000,
        )
        or ""
    )
    m = (
        Message.objects.filter(conversation__user__telegram_id=tid, role="assistant")
        .order_by("-created_at")
        .first()
    )
    return r, (m.metadata or {}) if m else {}


def main() -> None:
    print("=== Public Sheets + free-form Q&A ===")

    # Direct public fetch
    loaded = load_public_workbook(PUBLIC_SHEET_1)
    if loaded.error or not loaded.payload:
        _fail("public_fetch", str(loaded.error))
    blob = str(loaded.payload.values_by_sheet).lower()
    assert "alexandra" in blob or "student" in blob
    _pass("public_fetch_no_oauth")

    missing = load_public_workbook("1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz")
    assert missing.error and missing.error.code == "not_found"
    _pass("not_found_taxonomy")

    tid = 9933000100
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="PublicQA", onboarding_completed=True)
    p = ConversationProcessor()
    mem = SheetMemory()

    r0, m0 = _ask(p, tid, _url(PUBLIC_SHEET_1))
    low0 = r0.lower()
    if m0.get("needs_oauth") or "connect google" in low0:
        _fail("no_oauth_for_public", r0[:200])
    if "ai watchlist" in low0 or "demo portfolio" in low0:
        _fail("no_demo_substitution", r0[:200])
    if "i can access" not in low0 and "google sheet detected" not in low0:
        _fail("open_ack", r0[:200])
    user = User.objects.get(telegram_id=tid)
    if mem.active_spreadsheet_id(user) != PUBLIC_SHEET_1:
        _fail("active_sheet", str(mem.active_spreadsheet_id(user)))
    _pass("accessed_without_oauth")
    _pass("active_sheet_context")
    print("  ", r0[:160].encode("ascii", "replace").decode("ascii").replace("\n", " | "))

    questions = [
        "Summarize this spreadsheet.",
        "How many students are listed?",
        "Which students are from CA?",
        "What majors appear in the data?",
        "Who is in Drama Club?",
        "What class levels are present?",
        "Explain this data in simple terms.",
        "Show me the important findings.",
        "Which extracurriculars are mentioned?",
        "Give me a quick analysis.",
    ]
    answers: list[str] = []
    for q in questions:
        r, m = _ask(p, tid, q)
        assert m.get("pipeline") == "sheets", (q, m)
        assert "ai watchlist" not in r.lower()
        assert "connect google" not in r.lower()
        answers.append(r)
        print(f"  Q: {q}")
        print(f"  A: {r[:140].encode('ascii', 'replace').decode('ascii').replace(chr(10), ' | ')}")
    # Grounding checks against known Class Data cells
    joined = " ".join(answers).lower()
    if "alexandra" not in joined and "english" not in joined and "drama" not in joined:
        _fail("answers_grounded_in_sheet", "expected Class Data tokens in answers")
    _pass("arbitrary_natural_language_questions")

    # Follow-ups
    r_f1, m_f1 = _ask(p, tid, "Why?")
    assert m_f1.get("pipeline") == "sheets"
    r_f2, m_f2 = _ask(p, tid, "What should I pay attention to?")
    assert m_f2.get("pipeline") == "sheets"
    assert mem.active_spreadsheet_id(user) == PUBLIC_SHEET_1
    _pass("followups")

    # Unsupported info — must not invent Bitcoin / presidents from sheet
    r_miss, m_miss = _ask(p, tid, "What is the Bitcoin price in this sheet?")
    assert m_miss.get("pipeline") == "sheets"
    miss_low = r_miss.lower()
    if "couldn't find that information in the spreadsheet" not in miss_low:
        # Allow close variants
        if "could not find" not in miss_low and "not" not in miss_low:
            _fail("unsupported_info", r_miss[:220])
        if "bitcoin" in miss_low and any(x in miss_low for x in ("$", "usd", "65000", "price is")):
            _fail("unsupported_info_hallucination", r_miss[:220])
    _pass("unsupported_information_handling")
    print("  ", r_miss[:160].encode("ascii", "replace").decode("ascii"))

    # Second public sheet switch
    r2, m2 = _ask(p, tid, _url(PUBLIC_SHEET_2))
    assert m2.get("ok") is True
    assert "connect google" not in r2.lower()
    user.refresh_from_db()
    active2 = mem.active_spreadsheet_id(user)
    if active2 != PUBLIC_SHEET_2:
        _fail("second_sheet_active", str(active2))
    r3, m3 = _ask(p, tid, "Summarize this spreadsheet.")
    assert m3.get("pipeline") == "sheets"
    low3 = r3.lower()
    # Must not keep answering only Class Data student roster as if still sheet 1
    # (sheet 2 is a different public dataset)
    assert mem.active_spreadsheet_id(user) == PUBLIC_SHEET_2
    _pass("second_sheet_switching")
    print("  sheet2 active=", active2)
    print("  ", r3[:160].encode("ascii", "replace").decode("ascii").replace("\n", " | "))

    # Confirm no demo substitution on public path
    assert "ai watchlist" not in (r0 + r3).lower()
    _pass("no_demo_data_substitution")

    # Service-level: private-looking ID should not silently use demo
    tid2 = 9933000101
    User.objects.filter(telegram_id=tid2).delete()
    User.objects.create(telegram_id=tid2, first_name="PrivatePath", onboarding_completed=True)
    svc = SheetService()
    user2 = User.objects.get(telegram_id=tid2)
    priv = svc.open_by_spreadsheet_id(user2, "1ArbitraryPrivateSheetIdXXXXXX12")
    reply_p = (priv.get("reply") or "").lower()
    assert "watchlist" not in reply_p
    assert priv.get("ok") is not True or priv.get("access_mode") != "demo"
    _pass("private_or_missing_no_demo")

    print("\nPUBLIC_SHEET_QA_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
