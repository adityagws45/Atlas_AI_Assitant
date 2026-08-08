"""UX polish verification for progressive, listening onboarding."""

from __future__ import annotations

import os
import sys

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from accounts.models import User  # noqa: E402
from memory.models import Watchlist  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


def main() -> None:
    p = ConversationProcessor()
    tid = 9400000100
    User.objects.filter(telegram_id=tid).delete()

    # Listening: companies imply sector
    r = p.handle_start(telegram_id=tid, first_name="Maya", telegram_message_id=1)
    assert "Atlas" in r
    r = p.handle_text(telegram_id=tid, text="investor", telegram_message_id=2)
    r = p.handle_text(
        telegram_id=tid,
        text="I mainly follow Nvidia and TSMC",
        telegram_message_id=3,
    )
    assert "semiconductor" in r.lower(), r
    assert "NVDA" in r or "nvidia" in r.lower() or "TSM" in r or "other companies" in r.lower()
    assert Watchlist.objects.filter(user__telegram_id=tid, symbol="NVDA").exists()
    assert Watchlist.objects.filter(user__telegram_id=tid, symbol="TSM").exists()
    print("PASS listening_inference")

    r = p.handle_text(telegram_id=tid, text="skip", telegram_message_id=4)
    # Should move toward briefing (progressive — skip depth if we used more_names)
    r = p.handle_text(telegram_id=tid, text="8am", telegram_message_id=5)
    user = User.objects.get(telegram_id=tid)
    # Might still be on depth if path differed — complete if needed
    if not user.onboarding_completed:
        r = p.handle_text(telegram_id=tid, text="skip", telegram_message_id=6)
        user.refresh_from_db()
    assert user.onboarding_completed, (user.onboarding_step, r)
    print("PASS progressive_complete")

    # Welcome back
    r = p.handle_start(telegram_id=tid, first_name="Maya", telegram_message_id=7)
    assert "welcome back" in r.lower(), r
    assert "semiconductor" in r.lower() or "NVDA" in r or "TSM" in r
    print("PASS welcome_back")

    # Change watchlist
    r = p.handle_text(telegram_id=tid, text="add AAPL to my watchlist", telegram_message_id=8)
    assert Watchlist.objects.filter(user__telegram_id=tid, symbol="AAPL").exists()
    assert "AAPL" in r
    print("PASS change_watchlist")

    # Emoji
    r = p.handle_text(telegram_id=tid, text="🔥🚀", telegram_message_id=9)
    assert "sentence" in r.lower() or "help" in r.lower()
    print("PASS emoji")

    # Long message
    r = p.handle_text(telegram_id=tid, text=("markets " * 500), telegram_message_id=10)
    assert r
    print("PASS long_message")

    # Restart
    r = p.handle_text(telegram_id=tid, text="restart", telegram_message_id=11)
    assert "Atlas" in r
    user.refresh_from_db()
    assert user.onboarding_completed is False
    print("PASS restart")

    # Banned robotic phrases across a fresh short path
    tid2 = 9400000101
    User.objects.filter(telegram_id=tid2).delete()
    replies = [
        p.handle_start(telegram_id=tid2, first_name="Leo", telegram_message_id=1),
        p.handle_text(telegram_id=tid2, text="founder", telegram_message_id=2),
        p.handle_text(telegram_id=tid2, text="fintech", telegram_message_id=3),
        p.handle_text(telegram_id=tid2, text="skip", telegram_message_id=4),
        p.handle_text(telegram_id=tid2, text="skip", telegram_message_id=5),
    ]
    blob = " ".join(replies).lower()
    for bad in ("thanks.", " okay.", "noted.", "how may i help", "what is your role"):
        assert bad not in blob, bad
    print("PASS wording")

    # Suppression service importable
    from notifications.services.suppression_service import SuppressionService

    assert SuppressionService is not None
    print("PASS suppression_design")
    print("UX_POLISH_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
