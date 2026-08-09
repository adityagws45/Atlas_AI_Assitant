"""State recovery + live conversation regression (intent priority)."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from accounts.models import User  # noqa: E402
from ai.services.ai_service import FRIENDLY_AI_UNAVAILABLE  # noqa: E402
from ai.types import ProviderRetryExhausted  # noqa: E402
from conversation.services.preference_intent import detect_preference_intent  # noqa: E402
from memory.models import UserPreference, Watchlist  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


def _complete_user(tid: int) -> ConversationProcessor:
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    p.handle_start(telegram_id=tid, first_name="Hero", telegram_message_id=1)
    for i, msg in enumerate(("skip", "skip", "skip", "skip"), start=2):
        p.handle_text(telegram_id=tid, text=msg, telegram_message_id=i)
    user = User.objects.get(telegram_id=tid)
    assert user.onboarding_completed
    return p


def test_intent_matrix() -> None:
    assert detect_preference_intent("I invest for the long term.") == "style"
    assert detect_preference_intent("8:00 AM") == "briefing"
    assert detect_preference_intent("8:00 AM.") == "briefing"
    assert detect_preference_intent("I mainly follow AI, semiconductors and cloud computing.") == "sector"
    assert detect_preference_intent("Tell me about Nvidia.") is None
    print("PASS intent_matrix")


def test_live_script_no_ai_on_prefs() -> None:
    """Exact failing live conversation — prefs must not call orchestrator.process."""
    tid = 9800000501
    p = _complete_user(tid)
    calls = {"n": 0}
    real_process = p.orchestrator.process

    def counting_process(*a, **k):
        calls["n"] += 1
        return real_process(*a, **k)

    p.orchestrator.process = counting_process  # type: ignore[method-assign]

    r1 = p.handle_text(
        telegram_id=tid,
        text="I mainly follow AI, semiconductors and cloud computing.",
        telegram_message_id=10,
    )
    assert "research brain" not in r1.lower() and "glitched" not in r1.lower()
    assert "AI" in r1 or "semiconductor" in r1.lower() or "cloud" in r1.lower()
    assert calls["n"] == 0, "sector update invoked AI"

    r2 = p.handle_text(
        telegram_id=tid,
        text="I invest for the long term.",
        telegram_message_id=11,
    )
    assert "research brain" not in r2.lower() and "glitched" not in r2.lower()
    assert "long-term" in r2.lower() or "long" in r2.lower()
    assert calls["n"] == 0, "style update invoked AI"

    r3 = p.handle_text(telegram_id=tid, text="8:00 AM", telegram_message_id=12)
    assert "research brain" not in r3.lower() and "glitched" not in r3.lower()
    assert "08:00" in r3 or "8:00" in r3 or "Locked" in r3
    assert calls["n"] == 0, "briefing update invoked AI"
    prefs = UserPreference.objects.get(user__telegram_id=tid)
    assert prefs.preferred_briefing_time is not None
    assert prefs.preferred_briefing_time.hour == 8

    # Research: clarification short-circuit — still no Gemini needed
    r4 = p.handle_text(telegram_id=tid, text="Tell me about Nvidia.", telegram_message_id=13)
    assert "research brain" not in r4.lower() and "glitched" not in r4.lower()
    assert "Nvidia" in r4 or "NVDA" in r4 or "angle" in r4.lower()
    assert calls["n"] == 1  # orchestrator called, but clarification path
    print("PASS live_script_prefs")


def test_ai_exception_recovery() -> None:
    tid = 9800000502
    p = _complete_user(tid)

    def boom(*a, **k):
        raise ProviderRetryExhausted("simulated")

    p.orchestrator.ai_service.generate_turn = boom  # type: ignore[method-assign]
    # Force past clarification; disable fast paths so we hit the AI recovery path
    with patch(
        "conversation.services.market_fast_path.try_market_move_fast_answer",
        return_value=None,
    ), patch(
        "conversation.services.finance_fast_path.try_finance_fast_answer",
        return_value=None,
    ):
        r = p.handle_text(
            telegram_id=tid,
            text="Why is Nvidia moving today?",
            telegram_message_id=20,
        )
    assert (
        "trouble pulling" in r.lower()
        or "try again" in r.lower()
        or "moment" in r.lower()
    )
    assert "research brain" not in r.lower()
    assert "gemini" not in r.lower()

    # Next preference must still work (state not poisoned)
    r2 = p.handle_text(
        telegram_id=tid,
        text="I follow semiconductors.",
        telegram_message_id=21,
    )
    assert "research brain" not in r2.lower()
    assert "NVDA" in r2 or "semiconductor" in r2.lower()
    print("PASS ai_exception_recovery")


def test_finance_exception_recovery() -> None:
    tid = 9800000503
    p = _complete_user(tid)

    class FakeOrch:
        formatter = p.orchestrator.formatter

        def process(self, user, conversation, text):
            raise RuntimeError("FinanceService exploded")

    p.orchestrator = FakeOrch()  # type: ignore[assignment]
    with patch(
        "conversation.services.market_fast_path.try_market_move_fast_answer",
        return_value=None,
    ), patch(
        "conversation.services.finance_fast_path.try_finance_fast_answer",
        return_value=None,
    ):
        r = p.handle_text(
            telegram_id=tid, text="Why is NVDA moving?", telegram_message_id=30
        )
    # Processor catches and returns FRIENDLY_ERROR
    assert "trouble" in r.lower() or "moment" in r.lower() or "try again" in r.lower()
    assert "research brain" not in r.lower()
    assert "glitched" not in r.lower()

    # Restore real orchestrator via new processor path for prefs
    p2 = ConversationProcessor()
    # Same user
    r2 = p2.handle_text(
        telegram_id=tid,
        text="Add AAPL to my watchlist",
        telegram_message_id=31,
    )
    assert "AAPL" in r2
    assert Watchlist.objects.filter(user__telegram_id=tid, symbol="AAPL").exists()
    print("PASS finance_exception_recovery")


def test_multiple_preference_updates() -> None:
    tid = 9800000504
    p = _complete_user(tid)
    with patch.object(p.orchestrator, "process", side_effect=AssertionError("no AI")):
        p.handle_text(telegram_id=tid, text="I'm an investor.", telegram_message_id=40)
        p.handle_text(telegram_id=tid, text="I invest for the long term.", telegram_message_id=41)
        p.handle_text(telegram_id=tid, text="I cover banking and fintech.", telegram_message_id=42)
        p.handle_text(telegram_id=tid, text="My briefing should arrive at 7:30 AM.", telegram_message_id=43)
        p.handle_text(telegram_id=tid, text="Add NVDA to my watchlist", telegram_message_id=44)
    user = User.objects.get(telegram_id=tid)
    assert user.role == "investor"
    prefs = UserPreference.objects.get(user=user)
    assert prefs.preferred_briefing_time is not None
    assert Watchlist.objects.filter(user=user, symbol="NVDA").exists()
    print("PASS multiple_preference_updates")


def test_watch_today_after_prefs() -> None:
    tid = 9800000505
    p = _complete_user(tid)
    with patch.object(p.orchestrator, "process", side_effect=AssertionError("no AI")):
        p.handle_text(
            telegram_id=tid,
            text="I mainly follow AI, semiconductors and cloud computing.",
            telegram_message_id=50,
        )
        r = p.handle_text(telegram_id=tid, text="What should I watch today?", telegram_message_id=51)
    assert "NVDA" in r or "eye" in r.lower()
    assert "research brain" not in r.lower()
    print("PASS watch_today_after_prefs")


def main() -> None:
    test_intent_matrix()
    test_live_script_no_ai_on_prefs()
    test_ai_exception_recovery()
    test_finance_exception_recovery()
    test_multiple_preference_updates()
    test_watch_today_after_prefs()
    print("\nALL STATE RECOVERY CHECKS PASSED")


if __name__ == "__main__":
    main()
