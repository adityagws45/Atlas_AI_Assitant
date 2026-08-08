"""
Milestone 2 product verification — adaptive onboarding + skip + persistence.
"""

from __future__ import annotations

import os
import sys

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from accounts.models import User  # noqa: E402
from conversation.models import Message, MessageRole  # noqa: E402
from core.logging_filters import RedactSecretsFilter  # noqa: E402
from memory.models import UserPreference, Watchlist  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


def _assert_human(text: str) -> None:
    banned = [
        "what is your role",
        "please select",
        "how may i help you today",
        "fill out",
        "registration",
    ]
    lower = text.lower()
    for phrase in banned:
        assert phrase not in lower, f"Robotic phrase found: {phrase} in {text}"


def run_path(telegram_id: int, answers: list[str], label: str) -> None:
    User.objects.filter(telegram_id=telegram_id).delete()
    p = ConversationProcessor()
    start = p.handle_start(
        telegram_id=telegram_id,
        username="reviewer",
        first_name="Ava",
        telegram_message_id=1,
    )
    _assert_human(start)
    assert "Atlas" in start

    for i, ans in enumerate(answers, start=2):
        reply = p.handle_text(telegram_id=telegram_id, text=ans, telegram_message_id=i)
        _assert_human(reply)

    user = User.objects.get(telegram_id=telegram_id)
    assert user.onboarding_completed is True, label

    # Post-onboarding: Milestone 3 AI path (or friendly fallback if no API key)
    followup = p.handle_text(
        telegram_id=telegram_id, text="What moved markets today?", telegram_message_id=99
    )
    assert followup and len(followup.strip()) > 10
    assert "something glitched" not in followup.lower()

    msgs = Message.objects.filter(conversation__user=user)
    assert msgs.filter(role=MessageRole.USER).count() >= len(answers) + 1
    assert msgs.filter(role=MessageRole.ASSISTANT).count() >= len(answers) + 1
    print(f"PASS {label} role={user.role!r} messages={msgs.count()}")


def main() -> None:
    # Adaptive paths
    run_path(
        9300000001,
        ["investor", "NVDA semiconductors", "growth", "US", "8am UTC"],
        "INVESTOR",
    )
    run_path(
        9300000002,
        ["founder", "fintech", "SQ", "Series B", "yes", "skip"],
        "FOUNDER",
    )
    run_path(
        9300000003,
        ["student", "valuation", "AAPL MSFT", "US", "skip"],
        "STUDENT",
    )
    run_path(
        9300000004,
        ["finance professional", "meeting prep", "earnings", "JPM", "yes", "9:30"],
        "FINANCE_PRO",
    )
    run_path(
        9300000005,
        ["skip", "skip", "skip", "skip", "skip"],
        "SKIP_ALL",
    )

    # Restart phrase
    User.objects.filter(telegram_id=9300000006).delete()
    p = ConversationProcessor()
    p.handle_start(telegram_id=9300000006, first_name="Sam", telegram_message_id=1)
    p.handle_text(telegram_id=9300000006, text="analyst", telegram_message_id=2)
    again = p.handle_text(telegram_id=9300000006, text="restart", telegram_message_id=3)
    assert "Atlas" in again
    user = User.objects.get(telegram_id=9300000006)
    assert user.onboarding_completed is False
    assert user.onboarding_step == "role"
    print("PASS RESTART")

    # Empty input
    empty = p.handle_text(telegram_id=9300000006, text="   ", telegram_message_id=4)
    assert "catch" in empty.lower() or "again" in empty.lower()
    print("PASS EMPTY")

    # Secret redaction
    filt = RedactSecretsFilter()
    record = type("R", (), {})()
    import logging

    rec = logging.LogRecord(
        "test", logging.INFO, __file__, 1, "POST https://api.telegram.org/bot123:SECRETTOKEN/getMe", (), None
    )
    assert filt.filter(rec)
    assert "SECRETTOKEN" not in rec.getMessage()
    assert "REDACTED" in rec.getMessage()
    print("PASS SECRET_REDACTION")

    # Persistence sample
    prefs = UserPreference.objects.get(user__telegram_id=9300000001)
    assert prefs.sectors_of_interest
    assert Watchlist.objects.filter(user__telegram_id=9300000001, symbol="NVDA").exists()
    print("PASS PERSISTENCE")
    print("MILESTONE_2_PRODUCT_REVIEW: PASS")


if __name__ == "__main__":
    main()
