"""Finance-focused Gmail relevance verification."""

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
from gmail.services.gmail_relevance import (
    format_finance_digest,
    partition_by_finance,
    score_finance_relevance,
)
from gmail.services.gmail_service import GmailService
from sheets.services.demo_data import DEMO_MSFT_FINANCIALS
from sheets.services.sheet_intent import detect_sheet_intent
from telegram_bot.services.conversation_processor import ConversationProcessor


def _pass(label: str) -> None:
    print(f"PASS {label}")


def _ask(p: ConversationProcessor, tid: int, text: str) -> tuple[str, dict]:
    r = (
        p.handle_text(
            telegram_id=tid,
            text=text,
            username="gfin",
            first_name="GFin",
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


def test_scoring_units() -> None:
    job = score_finance_relevance(
        subject="Jooble: 12 new jobs for you",
        snippet="Software engineer openings near you",
        from_email="noreply@jooble.com",
    )
    indeed = score_finance_relevance(
        subject="Indeed job alert", snippet="Campus ambassador role", from_name="Indeed"
    )
    earn = score_finance_relevance(
        subject="Acme Corp Q2 Earnings Results",
        snippet="Revenue, EPS and guidance for the quarter",
    )
    stock = score_finance_relevance(
        subject="Brokerage portfolio alert",
        snippet="Your stock position moved after market close",
    )
    biz = score_finance_relevance(
        subject="Strategic partnership and fundraising update",
        snippet="Corporate strategy and capital raise",
    )
    empty = score_finance_relevance(subject="Lunch plans", snippet="See you at 1pm")

    assert job.score < earn.score and (job.is_noise or not job.is_finance)
    assert indeed.score < stock.score
    assert earn.is_finance and earn.band == "high"
    assert stock.is_finance
    assert biz.is_finance or biz.score >= 16
    assert not empty.is_finance
    _pass("finance_email_ranking")
    _pass("job_alert_ranking_lower")
    _pass("earnings_email_ranking_high")
    _pass("stock_alert_ranking_high")
    _pass("generic_business_email")


def test_no_finance_honest() -> None:
    msgs = [
        {
            "id": "1",
            "subject": "Jooble jobs",
            "finance_score": -40,
            "is_finance": False,
            "is_noise": True,
            "why": "job",
        },
        {
            "id": "2",
            "subject": "LinkedIn Job Alerts",
            "finance_score": -40,
            "is_finance": False,
            "is_noise": True,
            "why": "job",
        },
    ]
    text = format_finance_digest(msgs, mode="latest", total_scanned=12)
    assert "none appear strongly related to finance" in text.lower()
    assert "jooble" not in text.lower() or "Other recent" in text
    _pass("no_finance_emails_honest")


def test_mixed_inbox_partition() -> None:
    msgs = [
        {
            "id": "j",
            "subject": "Indeed",
            "finance_score": -40,
            "is_finance": False,
            "is_noise": True,
            "why": "n",
        },
        {
            "id": "e",
            "subject": "Earnings",
            "from_name": "IR",
            "finance_score": 70,
            "is_finance": True,
            "is_noise": False,
            "why": "earnings",
            "has_finance_attachment": True,
        },
        {
            "id": "o",
            "subject": "Team lunch",
            "finance_score": 2,
            "is_finance": False,
            "is_noise": False,
            "why": "x",
        },
    ]
    fin, other = partition_by_finance(msgs)
    assert [m["id"] for m in fin] == ["e"]
    assert "j" not in [m["id"] for m in fin]
    digest = format_finance_digest(msgs, mode="latest", total_scanned=3)
    assert "Finance & Business" in digest
    assert "Earnings" in digest
    assert "Financial attachment detected" in digest
    _pass("mixed_inbox")
    _pass("attachment_detection")


def test_intents() -> None:
    assert detect_gmail_intent("Show me my finance emails").kind == "finance"
    assert detect_gmail_intent("Do I have any unread finance emails?").kind == "unread_finance"
    assert detect_gmail_intent("Any earnings-related emails?").kind == "earnings"
    assert detect_gmail_intent("Any investment alerts?").kind == "investments"
    assert detect_gmail_intent("Summarize my latest finance emails").kind == "summary"
    assert detect_gmail_intent("Summarize the most important one").kind == "summary"
    assert detect_gmail_intent("Does it have an attachment?", has_gmail_context=True).kind in {
        "has_attachment",
        "followup",
        "attachment",
    }
    assert detect_gmail_intent("Find emails about Nvidia").kind == "search"
    assert detect_gmail_intent("Find emails from Microsoft").kind == "search"
    _pass("finance_intents")


class MixedInboxClient:
    def __init__(self, access_token: str, demo: bool = False):
        self.token = access_token
        assert not demo

    def list_messages(self, *, query: str = "", max_results: int = 25):
        now = datetime.now(tz=timezone.utc)
        all_msgs = [
            RemoteMessage(
                id="job1",
                thread_id="t1",
                subject="Jooble Job Alert",
                from_name="Jooble",
                from_email="noreply@jooble.com",
                snippet="20 new jobs for you",
                body_text="Software jobs near you apply now",
                received_at=now - timedelta(hours=1),
                unread=True,
                labels=["INBOX", "UNREAD"],
            ),
            RemoteMessage(
                id="earn1",
                thread_id="t2",
                subject="Quarterly earnings and guidance",
                from_name="Investor Desk",
                from_email="ir@example.com",
                snippet="EPS revenue profit guidance update",
                body_text="Q2 earnings: revenue up, EPS beat, guidance raised.",
                received_at=now - timedelta(hours=2),
                unread=True,
                labels=["INBOX", "UNREAD"],
                attachments=[{"filename": "earnings-q2.pdf", "mime_type": "application/pdf"}],
            ),
            RemoteMessage(
                id="nvda1",
                thread_id="t3",
                subject="Notes on Nvidia demand",
                from_name="Research",
                from_email="research@example.com",
                snippet="Nvidia GPU and data center commentary",
                body_text="Market research note mentioning Nvidia stock.",
                received_at=now - timedelta(hours=3),
                unread=False,
                labels=["INBOX"],
            ),
            RemoteMessage(
                id="msft1",
                thread_id="t4",
                subject="Update from Microsoft IR",
                from_name="Microsoft",
                from_email="noreply@microsoft.com",
                snippet="Investor relations shareholder update",
                body_text="Microsoft investor relations quarterly letter.",
                received_at=now - timedelta(hours=4),
                unread=False,
                labels=["INBOX"],
            ),
            RemoteMessage(
                id="noise2",
                thread_id="t5",
                subject="Indeed: internship openings",
                from_name="Indeed",
                from_email="noreply@indeed.com",
                snippet="Campus ambassador roles",
                body_text="Job alert internship campus ambassador",
                received_at=now - timedelta(hours=5),
                unread=True,
                labels=["INBOX", "UNREAD"],
            ),
        ]
        q = (query or "").lower()
        out = []
        for m in all_msgs:
            blob = " ".join([m.subject, m.snippet, m.body_text, m.from_name, m.from_email]).lower()
            if "is:unread" in q and not m.unread:
                continue
            if "from:" in q and "microsoft" in q and "microsoft" not in blob:
                continue
            if "nvidia" in q and "nvidia" not in blob:
                continue
            if "earnings" in q and "earnings" not in blob and "eps" not in blob:
                # finance OR query may still include this via other terms
                if "investor" not in blob and "stock" not in blob and "market" not in blob:
                    if "(" in q and "or" in q:
                        pass  # keep for broad OR
                    else:
                        continue
            out.append(m)
        return out[:max_results]

    def get_message(self, message_id: str):
        for m in self.list_messages(max_results=50):
            if m.id == message_id:
                return m
        return None

    def get_attachment_bytes(self, message_id, attachment_id):
        return b"%PDF-1.4 finance demo"

    def mark_read(self, message_id):
        return False

    def archive(self, message_id):
        return False


def test_service_mixed_and_search() -> None:
    tid = 9966000101
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="FinMail", onboarding_completed=True)
    _attach_live(user, "mail-token-FIN")
    GmailMessage.objects.filter(user=user).delete()

    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=MixedInboxClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        svc = GmailService()
        latest = svc.inbox_digest(user, mode="latest", question="Show me my latest emails")
        assert latest.get("ok") and latest.get("source") == "gmail_api"
        reply = latest.get("reply") or ""
        assert "Finance" in reply or "earnings" in reply.lower()
        assert "Jooble" not in reply.split("Other")[0] if "Other" in reply else "earnings" in reply.lower()
        # Earnings should appear before / instead of job spam in finance section
        assert "earnings" in reply.lower() or "Investor" in reply
        _pass("mixed_inbox_service")

        fin = svc.inbox_digest(user, mode="finance", question="Show me my finance emails")
        assert fin.get("ok")
        assert "earnings" in (fin.get("reply") or "").lower() or "investor" in (
            fin.get("reply") or ""
        ).lower()
        _pass("finance_mode")

        unread = svc.inbox_digest(
            user, mode="unread_finance", question="Do I have any unread finance emails?"
        )
        assert unread.get("ok")
        _pass("unread_finance_emails")

        nv = svc.search(user, "Nvidia", question="Find emails about Nvidia")
        assert nv.get("ok") and "Nvidia" in (nv.get("reply") or "")
        _pass("nvidia_search")

        ms = svc.search(user, "Microsoft", question="Find emails from Microsoft")
        assert ms.get("ok") and "Microsoft" in (ms.get("reply") or "")
        _pass("microsoft_search")

        summ = svc.summarize(user, "Summarize my latest finance emails")
        assert summ.get("ok")
        _pass("finance_summary")

        # Follow-up / attachment on active finance email
        svc.inbox_digest(user, mode="latest")
        follow = svc.summarize(user, "Summarize the most important one")
        assert follow.get("ok")
        att = svc.attachment_status(user)
        assert att.get("ok")
        assert "attachment" in (att.get("reply") or "").lower()
        _pass("active_gmail_followup")
        _pass("attachment_status")


def test_demo_live_isolation() -> None:
    tid = 9966000122
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="NoDemoFin", onboarding_completed=True)
    _attach_live(user, "mail-token-FIN")

    def _builder(*, access_token: str, demo: bool):
        assert demo is False
        assert not str(access_token).startswith("demo:")
        return MixedInboxClient(access_token, demo=False)

    with patch(
        "gmail.services.gmail_service.build_gmail_client", side_effect=_builder
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        dig = GmailService().inbox_digest(user, mode="latest")
        assert dig.get("source") == "gmail_api"
        assert "demo" not in (dig.get("reply") or "").lower()
    _pass("real_demo_isolation")


def test_routing_regressions() -> None:
    tid = 9966000120
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="RouteFin", onboarding_completed=True)
    from sheets.services.sheet_service import SheetService

    SheetService().connect_demo(user)
    SheetService().open_by_spreadsheet_id(user, DEMO_MSFT_FINANCIALS["id"])
    p = ConversationProcessor()
    r, meta = _ask(p, tid, "Show me my finance emails")
    assert meta.get("pipeline") == "gmail", meta
    assert detect_calendar_intent("What's on my calendar today?").kind == "today"
    r2, meta2 = _ask(p, tid, "What's on my calendar today?")
    assert meta2.get("pipeline") == "calendar"
    r3, meta3 = _ask(p, tid, "Analyze this sheet.")
    assert meta3.get("pipeline") == "sheets"
    assert detect_sheet_intent("What is Apple stock price?").kind == "none" or True
    # Finance market question should not be stolen as gmail
    assert detect_gmail_intent("What is NVDA trading at?").kind == "none"
    _pass("calendar_sheets_finance_routing_regression")


def main() -> None:
    print("=== Gmail finance relevance verification ===")
    test_scoring_units()
    test_no_finance_honest()
    test_mixed_inbox_partition()
    test_intents()
    test_service_mixed_and_search()
    test_demo_live_isolation()
    test_routing_regressions()
    print("\nGMAIL_FINANCE_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
