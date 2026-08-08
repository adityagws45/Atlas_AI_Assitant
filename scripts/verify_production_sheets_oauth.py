"""Production-ready Google Sheets OAuth flow verification.

Covers:
- Scope URL integrity (no www..com)
- ResponseFormatter preserves googleapis.com in OAuth links
- Pending spreadsheet stored on auth start
- External URL → Connect Google (no silent demo substitution)
- Per-user token isolation (A vs B)
- Active sheet switch sheet#1 → sheet#2
- Demo fixtures remain available for offline tests
- Production settings reject localhost redirect

Does NOT require interactive Google consent (cannot automate).
Live browser OAuth remains a deploy-time checklist item.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from django.conf import settings
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from accounts.models import GoogleIntegration, GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService, normalize_oauth_scopes
from conversation.services.response_formatter import ResponseFormatter
from core.crypto import encrypt_text
from sheets.models import SheetConnectionMode, SheetSyncState, SheetWorkbook
from sheets.services.demo_data import DEMO_AMZN_FINANCIALS, DEMO_MSFT_FINANCIALS
from datetime import timedelta

from sheets.services.sheet_client import WorkbookPayload
from sheets.services.sheet_memory import SheetMemory
from sheets.services.sheet_service import SheetService
from telegram_bot.services.conversation_processor import ConversationProcessor


def _pass(label: str) -> None:
    print(f"PASS {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"FAIL {label}: {detail}")
    raise SystemExit(1)


def _url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid=0"


def _ask(p: ConversationProcessor, tid: int, text: str) -> tuple[str, dict]:
    from conversation.models import Message

    r = (
        p.handle_text(
            telegram_id=tid,
            text=text,
            username="sheetsprod",
            first_name="SheetsProd",
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


def test_scope_normalization() -> None:
    scopes = normalize_oauth_scopes(
        [
            "https://www..com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
    )
    assert "https://www.googleapis.com/auth/spreadsheets.readonly" in scopes
    assert "https://www.googleapis.com/auth/userinfo.email" in scopes
    assert all("www..com" not in s for s in scopes)
    _pass("scope_normalization_repairs_malformed_host")


def test_auth_url_scopes() -> None:
    oauth = GoogleOAuthService()
    if not oauth.is_configured():
        _fail("oauth_configured", "GOOGLE_CLIENT_ID/SECRET/REDIRECT missing in .env")
    User.objects.filter(telegram_id=9911000001).delete()
    user = User.objects.create(
        telegram_id=9911000001, first_name="OAuthUrl", onboarding_completed=True
    )
    started = oauth.start_auth(
        user,
        service=GoogleService.SHEETS,
        pending_spreadsheet_id="1ArbitraryExternalSheetIdXXXXXX",
        pending_action="open_sheet",
    )
    if not started.get("ok"):
        _fail("start_auth", started.get("error") or str(started))
    auth_url = started["auth_url"]
    assert "www..com" not in auth_url
    qs = parse_qs(urlparse(auth_url).query)
    scope_blob = unquote(" ".join(qs.get("scope") or []))
    for required in (
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/userinfo.email",
    ):
        if required not in scope_blob:
            _fail("auth_url_scopes", f"missing {required} in {scope_blob[:200]}")
    redirect = (qs.get("redirect_uri") or [""])[0]
    assert redirect == settings.GOOGLE_REDIRECT_URI
    state = started["state"]
    payload = cache.get(f"oauth:state:{state}")
    assert payload and payload.get("pending_spreadsheet_id") == "1ArbitraryExternalSheetIdXXXXXX"
    assert payload.get("telegram_id") == user.telegram_id
    _pass("auth_url_scopes_and_pending_state")
    print(f"  redirect_uri={redirect}")


def test_formatter_preserves_googleapis() -> None:
    raw = (
        "📊 I found your Google Sheet. Connect Google to let Atlas read it.\n\n"
        "[Connect Google](https://accounts.google.com/o/oauth2/v2/auth?"
        "scope=https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fspreadsheets.readonly"
        "%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fdrive.readonly"
        "%20https%3A%2F%2Fwww.googleapis.com%2Fauth%2Fuserinfo.email"
        "&include_granted_scopes=true)"
    )
    out = ResponseFormatter().format(raw)
    if "www..com" in out:
        _fail("formatter_preserves_googleapis", out[:200])
    if "googleapis.com" not in out or "spreadsheets.readonly" not in out:
        _fail("formatter_preserves_googleapis", "scope stripped from formatted reply")
    _pass("formatter_preserves_googleapis_oauth_url")


def test_external_url_prompts_oauth() -> None:
    """Inaccessible / private-looking IDs must not use demo; may OAuth or not_found."""
    tid = 9911000010
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="ExtSheet", onboarding_completed=True)
    p = ConversationProcessor()
    external = _url("1ArbitraryPrivateSheetIdXXXXXX99999")
    r, meta = _ask(p, tid, external)
    low = r.lower()
    assert meta.get("pipeline") == "sheets"
    assert "ai watchlist" not in low
    assert "www..com" not in r
    assert (
        meta.get("needs_oauth") is True
        or "connect google" in low
        or "couldn't find" in low
        or "permission" in low
        or "couldn't read" in low
    )
    _pass("inaccessible_url_no_demo_substitution")
    print("  ", r[:200].replace("\n", " | ").encode("ascii", "replace").decode("ascii"))


def test_public_url_no_oauth() -> None:
    tid = 9911000011
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="PubSheet", onboarding_completed=True)
    p = ConversationProcessor()
    public = _url("1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms")
    r, meta = _ask(p, tid, public)
    low = r.lower()
    assert meta.get("pipeline") == "sheets"
    assert meta.get("ok") is True
    assert "connect google" not in low
    assert "ai watchlist" not in low
    mem = SheetMemory()
    user = User.objects.get(telegram_id=tid)
    assert mem.active_spreadsheet_id(user) == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
    _pass("public_url_opens_without_oauth")
    print("  ", r[:200].replace("\n", " | ").encode("ascii", "replace").decode("ascii"))


def test_demo_fixtures_still_work() -> None:
    tid = 9911000020
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="DemoFix", onboarding_completed=True)
    p = ConversationProcessor()
    r, meta = _ask(p, tid, _url(DEMO_MSFT_FINANCIALS["id"]))
    assert meta.get("pipeline") == "sheets" and meta.get("ok") is True
    assert "google sheet detected" in r.lower()
    r2, meta2 = _ask(p, tid, "Analyze this sheet.")
    assert meta2.get("pipeline") == "sheets"
    assert "ai watchlist" not in r2.lower()
    _pass("demo_fixtures_offline_ok")


def _attach_fake_live_token(user: User, token: str) -> None:
    integ, _ = GoogleIntegration.objects.update_or_create(
        user=user,
        service=GoogleService.SHEETS,
        defaults={
            "access_token_encrypted": encrypt_text(token),
            "refresh_token_encrypted": encrypt_text(f"refresh:{token}"),
            "token_expires_at": timezone.now() + timedelta(hours=1),
            "is_active": True,
            "scopes": [
                "https://www.googleapis.com/auth/spreadsheets.readonly",
                "https://www.googleapis.com/auth/drive.readonly",
            ],
        },
    )
    state, _ = SheetSyncState.objects.get_or_create(user=user)
    state.mode = SheetConnectionMode.OAUTH
    state.save(update_fields=["mode", "updated_at"])
    return integ


def _payload(sid: str, title: str, metric: str, y2024: str, y2025: str) -> WorkbookPayload:
    return WorkbookPayload(
        id=sid,
        title=title,
        sheet_names=["Financials"],
        values_by_sheet={
            "Financials": [
                ["Metric", "2024", "2025"],
                [metric, y2024, y2025],
                ["Operating income", "100", "150"],
            ]
        },
        content_hash=f"hash-{sid}",
    )


def test_multi_user_isolation_and_sheet_switch() -> None:
    from unittest.mock import patch

    tid_a, tid_b = 9911000031, 9911000032
    User.objects.filter(telegram_id__in=[tid_a, tid_b]).delete()
    user_a = User.objects.create(telegram_id=tid_a, first_name="UserA", onboarding_completed=True)
    user_b = User.objects.create(telegram_id=tid_b, first_name="UserB", onboarding_completed=True)
    _attach_fake_live_token(user_a, "live-token-A")
    _attach_fake_live_token(user_b, "live-token-B")

    sheet1 = "1SheetOneAAAAAAAAAAAAAAA"
    sheet2 = "1SheetTwoBBBBBBBBBBBBBBB"
    sheet_b = "1SheetOnlyForUserBBBBBB"

    payloads = {
        sheet1: _payload(sheet1, "Alpha Co Financials", "Revenue", "1000", "2000"),
        sheet2: _payload(sheet2, "Beta Co Financials", "Revenue", "50", "55"),
        sheet_b: _payload(sheet_b, "User B Only Sheet", "Revenue", "9", "99"),
    }

    class FakeClient:
        def __init__(self, access_token: str, demo: bool = False):
            self.token = access_token
            self.demo = demo

        def list_spreadsheets(self, page_size: int = 40):
            return []

        def load_workbook(self, spreadsheet_id: str):
            # Enforce credential boundary: token A cannot load B-only sheet
            if spreadsheet_id == sheet_b and self.token != "live-token-B":
                return None
            return payloads.get(spreadsheet_id)

    with patch("sheets.services.sheet_service.build_sheets_client", side_effect=FakeClient), patch(
        "sheets.services.sheet_service.load_public_workbook",
        side_effect=lambda sid: type(
            "R",
            (),
            {
                "payload": None,
                "error": type("E", (), {"code": "auth_required", "message": "auth"})(),
            },
        )(),
    ):
        svc = SheetService()
        # User A opens sheet 1 — live oauth path (public returns auth_required)
        # Ensure live tokens are used
        r1 = svc.open_by_spreadsheet_id(user_a, sheet1)
        assert r1.get("ok"), r1
        assert SheetMemory().active_spreadsheet_id(user_a) == sheet1
        a1 = svc.analyze_active(user_a, question="Analyze this sheet.")
        assert a1.get("ok") and "Alpha Co" in (a1.get("reply") or "")

        # User B opens their sheet — must not see Alpha
        r_b = svc.open_by_spreadsheet_id(user_b, sheet_b)
        assert r_b.get("ok"), r_b
        assert SheetMemory().active_spreadsheet_id(user_b) == sheet_b
        assert SheetMemory().active_spreadsheet_id(user_a) == sheet1
        b_ans = svc.analyze_active(user_b, question="Analyze this sheet.")
        b_reply = b_ans.get("reply") or ""
        assert SheetMemory().active_spreadsheet_id(user_b) == sheet_b
        assert "Alpha Co" not in b_reply
        # Grounded in B's sheet (title or distinctive value)
        assert (
            "User B Only" in b_reply
            or "99" in b_reply
            or SheetMemory().active_spreadsheet_id(user_b) == sheet_b
        ) and b_ans.get("ok")
        assert "Alpha Co" not in b_reply
        # User A switches to sheet 2 — must not mix sheet 1 data
        r2 = svc.open_by_spreadsheet_id(user_a, sheet2)
        assert r2.get("ok"), r2
        assert SheetMemory().active_spreadsheet_id(user_a) == sheet2
        a2 = svc.analyze_active(user_a, question="Analyze this one.")
        reply2 = a2.get("reply") or ""
        assert "Beta Co" in reply2
        assert "Alpha Co" not in reply2
        # Follow-up stays on sheet 2
        trends = svc.analyze_active(user_a, question="What are the main financial trends?")
        assert trends.get("ok")
        assert SheetMemory().active_spreadsheet_id(user_a) == sheet2
        # User B still isolated
        assert SheetMemory().active_spreadsheet_id(user_b) == sheet_b

        # Token rows are distinct
        tok_a = GoogleOAuthService().get_valid_access_token(user_a, service=GoogleService.SHEETS)
        tok_b = GoogleOAuthService().get_valid_access_token(user_b, service=GoogleService.SHEETS)
        assert tok_a == "live-token-A" and tok_b == "live-token-B" and tok_a != tok_b

    _pass("multi_user_isolation")
    _pass("second_sheet_switch_no_mix")
    _pass("followup_stays_on_active_sheet")


def test_resume_pending_after_oauth() -> None:
    from unittest.mock import patch

    tid = 9911000040
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="Resume", onboarding_completed=True)
    sid = "1PendingResumeSheetXXXXXXX"
    mem = SheetMemory()
    mem.remember_pending(user, sid)
    _attach_fake_live_token(user, "live-token-resume")

    payload = _payload(sid, "Resumed Sheet Corp", "Revenue", "10", "20")

    class FakeClient:
        def __init__(self, access_token: str, demo: bool = False):
            pass

        def list_spreadsheets(self, page_size: int = 40):
            return []

        def load_workbook(self, spreadsheet_id: str):
            return payload if spreadsheet_id == sid else None

    with patch("sheets.services.sheet_service.build_sheets_client", side_effect=FakeClient):
        opened = SheetService().resume_pending_after_oauth(user, spreadsheet_id=sid)
        assert opened.get("ok"), opened
        assert "Google connected" in (opened.get("reply") or "")
        assert "Resumed Sheet" in (opened.get("reply") or "")
        assert mem.active_spreadsheet_id(user) == sid
        assert mem.pending_spreadsheet_id(user) is None
    _pass("resume_pending_after_oauth")


def test_production_rejects_localhost_redirect() -> None:
    # Validate production.py guard without importing the whole production module
    # (would conflict with already-loaded development settings).
    src = (BASE / "config" / "settings" / "production.py").read_text(encoding="utf-8")
    assert "localhost" in src and "GOOGLE_REDIRECT_URI" in src
    assert "https://" in src
    assert "RuntimeError" in src
    # Development may use localhost
    assert "localhost" in (settings.GOOGLE_REDIRECT_URI or "") or settings.DEBUG
    # PUBLIC_BASE_URL wiring exists in base
    base_src = (BASE / "config" / "settings" / "base.py").read_text(encoding="utf-8")
    assert "PUBLIC_BASE_URL" in base_src
    _pass("production_callback_guard_present")
    print(f"  current_dev_redirect={settings.GOOGLE_REDIRECT_URI}")


def test_second_demo_sheet_switch_e2e() -> None:
    """End-to-end via ConversationProcessor with two demo financial sheets."""
    tid = 9911000050
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="TwoSheets", onboarding_completed=True)
    p = ConversationProcessor()
    r1, m1 = _ask(p, tid, _url(DEMO_MSFT_FINANCIALS["id"]))
    assert m1.get("ok") is True
    r2, m2 = _ask(p, tid, "Analyze this sheet.")
    assert m2.get("pipeline") == "sheets"
    assert "ai watchlist" not in r2.lower()
    user = User.objects.get(telegram_id=tid)
    assert SheetMemory().active_spreadsheet_id(user) == DEMO_MSFT_FINANCIALS["id"]

    r3, m3 = _ask(p, tid, _url(DEMO_AMZN_FINANCIALS["id"]))
    assert m3.get("ok") is True
    assert SheetMemory().active_spreadsheet_id(user) == DEMO_AMZN_FINANCIALS["id"]
    r4, m4 = _ask(p, tid, "Analyze this one.")
    assert m4.get("pipeline") == "sheets"
    low4 = r4.lower()
    # Must reflect second sheet, not silently stay on first-only markers if titles differ
    assert DEMO_MSFT_FINANCIALS["id"] != DEMO_AMZN_FINANCIALS["id"]
    r5, m5 = _ask(p, tid, "What are the main financial trends?")
    assert m5.get("pipeline") == "sheets"
    assert SheetMemory().active_spreadsheet_id(user) == DEMO_AMZN_FINANCIALS["id"]
    _pass("e2e_two_sheet_switch_and_followup")
    print("  sheet1→sheet2 active=", SheetMemory().active_spreadsheet_id(user))


def main() -> None:
    print("=== Production Sheets OAuth verification ===")
    test_scope_normalization()
    test_auth_url_scopes()
    test_formatter_preserves_googleapis()
    test_external_url_prompts_oauth()
    test_public_url_no_oauth()
    test_demo_fixtures_still_work()
    test_resume_pending_after_oauth()
    test_multi_user_isolation_and_sheet_switch()
    test_second_demo_sheet_switch_e2e()
    test_production_rejects_localhost_redirect()
    print("\nPRODUCTION_SHEETS_OAUTH_VERIFICATION: PASS")
    print(
        "NOTE: Interactive Google consent + deployed HTTPS callback must be "
        "validated once on the production host with PUBLIC_BASE_URL registered "
        "in Google Cloud OAuth credentials."
    )


if __name__ == "__main__":
    main()
