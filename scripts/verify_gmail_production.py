"""Gmail production verification — OAuth gate, search, isolation, routing regressions."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from accounts.models import GoogleIntegration, GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService
from conversation.models import Message
from core.crypto import encrypt_text
from gcalendar.services.calendar_intent import detect_calendar_intent
from gmail.models import GmailConnectionMode, GmailMessage, GmailSyncState
from gmail.services.gmail_client import RemoteMessage
from gmail.services.gmail_intent import detect_gmail_intent
from gmail.services.gmail_memory import GmailMemory
from gmail.services.gmail_query import build_gmail_query
from gmail.services.gmail_service import GmailService
from sheets.services.demo_data import DEMO_MSFT_FINANCIALS
from sheets.services.sheet_intent import detect_sheet_intent
from telegram_bot.services.conversation_processor import ConversationProcessor


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
            username="gmailprod",
            first_name="GmailProd",
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


def _attach_live(user: User, token: str) -> None:
    GoogleIntegration.objects.update_or_create(
        user=user,
        service=GoogleService.GMAIL,
        defaults={
            "access_token_encrypted": encrypt_text(token),
            "refresh_token_encrypted": encrypt_text(f"refresh:{token}"),
            "token_expires_at": datetime.now(tz=timezone.utc) + timedelta(hours=2),
            "is_active": True,
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
        },
    )
    state, _ = GmailSyncState.objects.get_or_create(user=user)
    state.mode = GmailConnectionMode.OAUTH
    state.save(update_fields=["mode", "updated_at"])


_ORIG_RESOLVE = GoogleOAuthService._resolve_actual_scopes


def _fake_resolve(self, tokens):  # noqa: ANN001
    access = (tokens.get("access_token") or "").strip()
    if access.startswith("mail-token") or access.startswith("tok-"):
        claimed = [
            s for s in str(tokens.get("scope") or "").replace(",", " ").split() if s
        ]
        return claimed
    return _ORIG_RESOLVE(self, tokens)


GoogleOAuthService._resolve_actual_scopes = _fake_resolve  # type: ignore[method-assign]


def _msgs_for(token: str) -> list[RemoteMessage]:
    now = datetime.now(tz=timezone.utc)
    if token == "mail-token-A":
        return [
            RemoteMessage(
                id="a1",
                thread_id="ta1",
                subject="Microsoft Partner Update",
                from_name="Microsoft",
                from_email="noreply@microsoft.com",
                snippet="Quarterly partner brief",
                body_text="Microsoft partner update body with action items.",
                received_at=now - timedelta(hours=1),
                unread=True,
                labels=["INBOX", "UNREAD"],
            ),
            RemoteMessage(
                id="a2",
                thread_id="ta2",
                subject="Nvidia earnings notes",
                from_name="Research Desk",
                from_email="desk@example.com",
                snippet="Notes on Nvidia GPU demand",
                body_text="Nvidia earnings and GPU demand summary.",
                received_at=now - timedelta(hours=3),
                unread=False,
                labels=["INBOX"],
            ),
            RemoteMessage(
                id="a3",
                thread_id="ta3",
                subject="Invoice reminder",
                from_name="Billing",
                from_email="billing@vendor.com",
                snippet="Please pay invoice #99",
                body_text="Urgent invoice payment needed.",
                received_at=now - timedelta(days=2),
                unread=True,
                labels=["INBOX", "UNREAD", "IMPORTANT"],
            ),
        ]
    return [
        RemoteMessage(
            id="b1",
            thread_id="tb1",
            subject="Beta Only Secret Mail",
            from_name="Beta Boss",
            from_email="boss@beta.test",
            snippet="Private beta note",
            body_text="User B private email content.",
            received_at=now - timedelta(hours=2),
            unread=True,
            labels=["INBOX", "UNREAD"],
        ),
    ]


class FakeGmailClient:
    def __init__(self, access_token: str, demo: bool = False):
        self.token = access_token
        self.demo = demo

    def list_messages(self, *, query: str = "", max_results: int = 25) -> list[RemoteMessage]:
        if self.demo or (self.token or "").startswith("demo:"):
            raise AssertionError("OAuth path must not use demo client")
        msgs = list(_msgs_for(self.token))
        q = (query or "").strip().lower()
        if not q or q.startswith("in:inbox"):
            # latest/inbox style — return all for this token
            if "is:unread" in q:
                msgs = [m for m in msgs if m.unread]
            return msgs[:max_results]
        out: list[RemoteMessage] = []
        for m in msgs:
            blob = " ".join(
                [m.subject, m.snippet, m.body_text, m.from_name, m.from_email]
            ).lower()
            if "is:unread" in q and not m.unread:
                continue
            if "from:" in q:
                needle = q.split("from:", 1)[1]
                needle = needle.split()[0].strip("()\"'")
                if needle not in m.from_email.lower() and needle not in m.from_name.lower():
                    continue
            elif "nvidia" in q and "nvidia" not in blob:
                continue
            elif "zzzz" in q:
                continue
            elif "from:" not in q and "is:unread" not in q and "is:important" not in q:
                tokens = [
                    t
                    for t in q.replace('"', " ").split()
                    if t
                    and not t.startswith(("newer_than:", "older_than:", "in:", "is:", "subject:"))
                    and t not in {"or", "and"}
                ]
                if tokens and not all(t in blob for t in tokens):
                    continue
            out.append(m)
        return out[:max_results]

    def get_message(self, message_id: str) -> RemoteMessage | None:
        for m in _msgs_for(self.token):
            if m.id == message_id:
                return m
        return None

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes | None:
        return None

    def mark_read(self, message_id: str) -> bool:
        return False

    def archive(self, message_id: str) -> bool:
        return False


def test_intents() -> None:
    assert detect_gmail_intent("Show me my latest emails").kind == "latest"
    assert detect_gmail_intent("Do I have any unread emails?").kind == "unread"
    assert detect_gmail_intent("Find emails from Microsoft").kind == "search"
    assert detect_gmail_intent("Find emails about Nvidia").kind == "search"
    assert detect_gmail_intent("Search emails from last week").kind == "search"
    assert detect_gmail_intent("Summarize my latest emails").kind == "summary"
    assert detect_gmail_intent("Do I have anything important?").kind == "priority"
    assert detect_gmail_intent("Are any of these important?", has_gmail_context=True).kind in {
        "priority",
        "followup",
    }
    assert detect_gmail_intent("Summarize the latest 5", has_gmail_context=True).kind == "summary"
    assert detect_gmail_intent("What is this email about?", has_gmail_context=True).kind == "thread"
    assert detect_gmail_intent("Apple stock price").kind == "none"
    assert detect_gmail_intent("What's on my calendar today?").kind == "none"
    _pass("gmail_intent_detection")


def test_query_builder() -> None:
    assert "from:" in build_gmail_query("Find emails from Microsoft")
    assert "nvidia" in build_gmail_query("Find emails about Nvidia").lower()
    assert build_gmail_query("unread", kind="unread") == "is:unread newer_than:30d"
    assert "newer_than:7d" in build_gmail_query("emails from last week")
    assert "from:acme" in build_gmail_query("emails from acme.io") or "from:" in build_gmail_query(
        "emails from acme.io"
    )
    _pass("gmail_nl_query_builder")


def test_oauth_rejects_missing_gmail_scopes() -> None:
    tid = 9955000104
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="ScopeFailG", onboarding_completed=True)
    oauth = GoogleOAuthService()
    from django.core.cache import cache

    state = "test-gmail-scope-state"
    cache.set(
        f"oauth:state:{state}",
        {
            "user_id": str(user.id),
            "service": GoogleService.GMAIL,
            "telegram_id": user.telegram_id,
            "code_verifier": "",
            "scopes": ["https://www.googleapis.com/auth/gmail.readonly", "openid"],
            "pending_spreadsheet_id": "",
            "pending_action": "",
        },
        600,
    )
    with patch.object(
        GoogleOAuthService,
        "_exchange_code",
        return_value={
            "access_token": "tok-sheets-only",
            "refresh_token": "ref",
            "expires_in": 3600,
            "scope": (
                "https://www.googleapis.com/auth/spreadsheets.readonly "
                "https://www.googleapis.com/auth/drive.readonly openid"
            ),
        },
    ), patch.object(
        GoogleOAuthService,
        "_resolve_actual_scopes",
        return_value=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
            "openid",
        ],
    ):
        result = oauth.handle_callback(code="fake", state=state)
    assert result.get("ok") is False
    assert result.get("error_code") == "insufficient_scopes"
    assert not GoogleIntegration.objects.filter(
        user=user, service=GoogleService.GMAIL, is_active=True
    ).exists()
    _pass("oauth_scope_detection_rejects_missing_gmail")


def test_auth_required_not_empty() -> None:
    tid = 9955000100
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="FreshMail", onboarding_completed=True)
    p = ConversationProcessor()
    r, meta = _ask(p, tid, "Show me my latest emails")
    low = r.lower()
    assert meta.get("pipeline") == "gmail"
    assert "no emails found" not in low
    if GoogleOAuthService().is_configured():
        assert meta.get("needs_oauth") is True or "connect google" in low or "gmail" in low
        assert "accounts.google.com" in r or "connect google" in low
    _pass("gmail_authorization_required")


def test_permission_failure_not_empty() -> None:
    tid = 9955000102
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="PermFailG", onboarding_completed=True)
    _attach_live(user, "mail-token-bad")
    GmailMessage.objects.filter(user=user).delete()

    class DenyClient(FakeGmailClient):
        def list_messages(self, *, query: str = "", max_results: int = 25):
            raise PermissionError("permission_denied")

    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=DenyClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        dig = GmailService().inbox_digest(user, mode="latest", question="Show me my latest emails")
        reply = (dig.get("reply") or "").lower()
        assert dig.get("ok") is False or dig.get("needs_oauth") or dig.get("error_code")
        assert "no emails found" not in reply
        assert "permission" in reply or "reconnect" in reply or "connect google" in reply
    _pass("gmail_api_permission_failure")


def test_api_success_and_empty() -> None:
    tid = 9955000101
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="LiveMail", onboarding_completed=True)
    _attach_live(user, "mail-token-A")
    GmailMessage.objects.filter(user=user).delete()

    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=FakeGmailClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        svc = GmailService()
        latest = svc.inbox_digest(user, mode="latest", question="Show me my latest emails")
        assert latest.get("ok") and latest.get("source") == "gmail_api"
        assert "Microsoft" in (latest.get("reply") or "")
        assert "Nvidia" in (latest.get("reply") or "")
        _pass("gmail_api_success_latest")

        unread = svc.inbox_digest(user, mode="unread", question="Do I have any unread emails?")
        assert unread.get("ok")
        assert "Microsoft" in (unread.get("reply") or "") or "Invoice" in (unread.get("reply") or "")
        _pass("gmail_unread")

        search = svc.search(user, "Microsoft", question="Find emails from Microsoft")
        assert search.get("ok")
        assert "Microsoft" in (search.get("reply") or "")
        _pass("gmail_search")

        about = svc.search(user, "Nvidia", question="Find emails about Nvidia")
        assert about.get("ok")
        assert "Nvidia" in (about.get("reply") or "")
        _pass("gmail_search_about")

        summary = svc.summarize(user, "Summarize the latest 5")
        assert summary.get("ok")
        assert "Summary" in (summary.get("reply") or "") or "Microsoft" in (
            summary.get("reply") or ""
        )
        _pass("gmail_summary")

        follow = svc.followup(user, "Are any of these important?")
        assert follow.get("ok")
        assert follow.get("reply")
        _pass("gmail_followup_context")

        class EmptyClient(FakeGmailClient):
            def list_messages(self, *, query: str = "", max_results: int = 25):
                return []

        with patch(
            "gmail.services.gmail_service.build_gmail_client", side_effect=EmptyClient
        ):
            GmailMessage.objects.filter(user=user).delete()
            empty = GmailService().search(user, "zzzz-no-match", question="Find emails zzzz")
            assert empty.get("ok") is True
            assert "no emails found" in (empty.get("reply") or "").lower()
            assert empty.get("error_code") == "empty_results"
            _pass("gmail_empty_search_result")


def test_resume_requires_verify() -> None:
    tid = 9955000103
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="ResumeG", onboarding_completed=True)
    _attach_live(user, "mail-token-A")
    GmailMemory().remember_pending_question(user, "Show me my latest emails")

    class DenyClient(FakeGmailClient):
        def list_messages(self, *, query: str = "", max_results: int = 25):
            raise PermissionError("permission_denied")

    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=DenyClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        resumed = GmailService().resume_after_oauth(user)
        reply = resumed.get("reply") or ""
        assert resumed.get("ok") is False
        assert "Gmail connected" not in reply or "permission" in reply.lower()
        assert "permission" in reply.lower() or "reconnect" in reply.lower()
    _pass("gmail_resume_verify_before_claim")


def test_api_disabled() -> None:
    tid = 9955000105
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="ApiOffG", onboarding_completed=True)
    _attach_live(user, "mail-token-A")

    class ApiOffClient(FakeGmailClient):
        def list_messages(self, *, query: str = "", max_results: int = 25):
            raise RuntimeError("api_disabled")

    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=ApiOffClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        verified = GmailService().verify_gmail_access(user)
        assert verified.get("ok") is False
        assert verified.get("error_code") == "api_disabled"
        assert "Gmail API" in (verified.get("error") or "")
        dig = GmailService().inbox_digest(user, mode="latest")
        assert "no emails found" not in (dig.get("reply") or "").lower()
        assert dig.get("error_code") == "api_disabled"
    _pass("gmail_api_disabled_distinct")


def test_multi_user_isolation() -> None:
    tid_a, tid_b = 9955000110, 9955000111
    User.objects.filter(telegram_id__in=[tid_a, tid_b]).delete()
    user_a = User.objects.create(telegram_id=tid_a, first_name="MailA", onboarding_completed=True)
    user_b = User.objects.create(telegram_id=tid_b, first_name="MailB", onboarding_completed=True)
    _attach_live(user_a, "mail-token-A")
    _attach_live(user_b, "mail-token-B")
    GmailMessage.objects.filter(user__in=[user_a, user_b]).delete()

    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=FakeGmailClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        a = GmailService().inbox_digest(user_a, mode="latest")
        b = GmailService().inbox_digest(user_b, mode="latest")
        assert "Microsoft" in (a.get("reply") or "")
        assert "Beta Only Secret Mail" in (b.get("reply") or "")
        assert "Beta Only Secret Mail" not in (a.get("reply") or "")
        assert "Microsoft" not in (b.get("reply") or "")
        assert GmailMemory().has_recent_context(user_a)
        assert GmailMemory().has_recent_context(user_b)
        # Memory keys are per-user — A must not see B's active set
        a_ids = {m.get("message_id") for m in (GmailMemory().get_results(user_a).get("messages") or [])}
        b_ids = {m.get("message_id") for m in (GmailMemory().get_results(user_b).get("messages") or [])}
        assert a_ids.isdisjoint(b_ids)
    _pass("multi_user_isolation")


def test_demo_live_isolation() -> None:
    tid = 9955000122
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="NoDemo", onboarding_completed=True)
    _attach_live(user, "mail-token-A")

    def _builder(*, access_token: str, demo: bool):
        assert demo is False
        assert not str(access_token).startswith("demo:")
        return FakeGmailClient(access_token, demo=False)

    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=_builder
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        dig = GmailService().inbox_digest(user, mode="latest")
        assert dig.get("ok") and dig.get("source") == "gmail_api"
        # Force oauth mode with no token → auth required, never silent demo
        GoogleIntegration.objects.filter(user=user, service=GoogleService.GMAIL).update(
            is_active=False
        )
        state = GmailSyncState.objects.get(user=user)
        state.mode = GmailConnectionMode.OAUTH
        state.save(update_fields=["mode", "updated_at"])
        gate = GmailService().inbox_digest(user, mode="latest", question="Show me my latest emails")
        assert gate.get("needs_oauth") or gate.get("error_code") == "auth_required" or not gate.get(
            "ok"
        )
        assert gate.get("source") != "demo"
        assert "no emails found" not in (gate.get("reply") or "").lower()
    _pass("demo_live_isolation")


def test_routing_regressions() -> None:
    tid = 9955000120
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="RouteMail", onboarding_completed=True)
    from sheets.services.sheet_service import SheetService

    SheetService().connect_demo(user)
    SheetService().open_by_spreadsheet_id(user, DEMO_MSFT_FINANCIALS["id"])
    p = ConversationProcessor()

    r, meta = _ask(p, tid, "Show me my latest emails")
    assert meta.get("pipeline") == "gmail", meta
    assert meta.get("pipeline") != "sheets"

    # Without gmail connection, still gmail pipeline (oauth prompt), not sheets
    assert "watchlist" not in r.lower()

    r_cal, meta_cal = _ask(p, tid, "What's on my calendar today?")
    assert meta_cal.get("pipeline") == "calendar"
    assert detect_calendar_intent("What's on my calendar today?").kind == "today"

    r_sheet, meta_sheet = _ask(p, tid, "Analyze this sheet.")
    assert meta_sheet.get("pipeline") == "sheets"

    r_fin, meta_fin = _ask(p, tid, "What is my revenue?")
    assert meta_fin.get("pipeline") == "sheets"
    assert detect_sheet_intent("What is my revenue?", has_active_sheet=True).kind != "none"

    # With active gmail results + sheet, email follow-up stays gmail
    _attach_live(user, "mail-token-A")
    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=FakeGmailClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        GmailService().inbox_digest(user, mode="latest", question="Show me my latest emails")
    r_f, meta_f = _ask(p, tid, "Are any of these important?")
    # May need patch during processor call too
    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=FakeGmailClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        r_f, meta_f = _ask(p, tid, "Are any of these important?")
    assert meta_f.get("pipeline") == "gmail", meta_f
    assert meta_f.get("pipeline") != "sheets"
    _pass("routing_regressions_sheets_calendar_finance")


def main() -> None:
    print("=== Gmail production verification ===")
    test_intents()
    test_query_builder()
    test_oauth_rejects_missing_gmail_scopes()
    test_auth_required_not_empty()
    test_permission_failure_not_empty()
    test_resume_requires_verify()
    test_api_disabled()
    test_api_success_and_empty()
    test_multi_user_isolation()
    test_demo_live_isolation()
    test_routing_regressions()
    print("\nGMAIL_PRODUCTION_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
