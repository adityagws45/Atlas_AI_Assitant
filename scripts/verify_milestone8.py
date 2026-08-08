"""Milestone 8 verification — Gmail as inbox intelligence (demo mode)."""

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
from gmail.models import GmailMessage  # noqa: E402
from gmail.services.gmail_intent import detect_gmail_intent  # noqa: E402
from gmail.services.gmail_service import GmailService  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402
from tools.definitions import list_implemented_tool_names  # noqa: E402


def _ok(label: str) -> None:
    print(f"PASS {label}")


def test_tools_registered() -> None:
    names = list_implemented_tool_names()
    for t in (
        "gmail_search",
        "gmail_summary",
        "gmail_unread",
        "gmail_priority",
        "gmail_thread",
        "gmail_attachment",
        "gmail_draft",
        "gmail_archive",
        "gmail_mark_read",
    ):
        assert t in names, t
    _ok("gmail_tools_registered")


def test_intents() -> None:
    assert detect_gmail_intent("Connect my email").kind == "connect"
    assert detect_gmail_intent("Check my email").kind == "check"
    assert detect_gmail_intent("Show me my latest emails").kind == "latest"
    assert detect_gmail_intent("What needs my attention?").kind == "priority"
    assert detect_gmail_intent("Show unread emails").kind == "unread"
    assert detect_gmail_intent("Find emails about Microsoft").kind == "search"
    assert detect_gmail_intent("Draft a reply").kind == "draft"
    assert detect_gmail_intent("Rewrite politely").kind == "draft"
    assert detect_gmail_intent("Summarize the attachment").kind == "attachment"
    assert detect_gmail_intent("Archive this").kind == "archive"
    assert detect_gmail_intent("What did Microsoft say?").kind == "search"
    assert detect_gmail_intent("What did Microsoft say?").query.lower() == "microsoft"
    assert detect_gmail_intent("Apple stock price").kind == "none"
    assert detect_gmail_intent("Are any of these important?", has_gmail_context=True).kind in {
        "priority",
        "followup",
    }
    _ok("gmail_intents")


def test_service_memory() -> None:
    tid = 9910000801
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="MailHero", onboarding_completed=True)
    svc = GmailService()
    svc.connect_demo(user)
    synced = svc.sync_inbox(user)
    assert synced["ok"]
    assert GmailMessage.objects.filter(user=user).count() >= 3
    dig = svc.inbox_digest(user, mode="priority")
    assert dig["ok"]
    reply = (dig.get("reply") or "").lower()
    assert (
        "attention" in reply
        or "finance" in reply
        or "●" in (dig.get("reply") or "")
        or "why it matters" in reply
        or "earnings" in reply
        or "inbox" in reply
        or "email" in reply
    )
    found = svc.search(user, "Microsoft")
    assert found["ok"]
    assert "microsoft" in (found.get("reply") or "").lower()
    draft = svc.draft_reply(user, instruction="reply politely", tone="polite")
    assert draft["ok"]
    assert "haven't sent" in (draft.get("reply") or "").lower() or "draft" in (
        draft.get("reply") or ""
    ).lower()
    blob = (dig.get("reply") or "") + (found.get("reply") or "")
    assert "demo_msg_" not in blob.lower()
    assert "message_id" not in blob.lower()
    _ok("gmail_service_memory")


def test_telegram_journey() -> None:
    tid = 9910000802
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="MailDemo", onboarding_completed=True)
    GmailService().connect_demo(user)
    GmailService().sync_inbox(user)
    p = ConversationProcessor()

    def ask(label: str, text: str) -> str:
        r = p.handle_text(
            telegram_id=tid,
            text=text,
            telegram_message_id=int(time.time() * 1000) % 10_000_000,
        )
        low = (r or "").lower()
        for leak in ("message_id", "thread_id", "demo_msg_", "users/me/messages"):
            assert leak not in low, f"leak {leak} in {label}"
        safe = (r or "")[:120].replace("\n", " | ").encode("ascii", "replace").decode("ascii")
        print(f"OK {label}: {safe}")
        return r or ""

    ask("check", "Check my email.")
    ask("summary", "Summarize today's emails.")
    ask("unread", "Show unread emails.")
    msft = ask("msft", "Find emails about Microsoft.")
    assert "microsoft" in msft.lower()
    ask("attachment", "Summarize the attachment.")
    draft = ask("draft", "Draft a reply.")
    assert "send" in draft.lower() or "draft" in draft.lower()
    ask("rewrite", "Rewrite politely.")
    mem = ask("memory", "What did Microsoft say?")
    assert "don't see emails matching" not in mem.lower()
    assert "microsoft" in mem.lower()
    assert (
        "azure" in mem.lower()
        or "q2" in mem.lower()
        or "cloud" in mem.lower()
        or "investor" in mem.lower()
    )
    ask("archive", "Archive this.")
    _ok("telegram_gmail_journey")


def main() -> None:
    print("=== Milestone 8 verification ===")
    test_tools_registered()
    test_intents()
    test_service_memory()
    test_telegram_journey()
    print("ALL MILESTONE 8 CHECKS PASSED")


if __name__ == "__main__":
    main()
