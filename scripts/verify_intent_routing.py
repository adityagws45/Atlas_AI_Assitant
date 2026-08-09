"""Intent routing tests — preference updates must never call AI/finance."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from accounts.models import User, UserRole  # noqa: E402
from ai.services.clarification_engine import ClarificationEngine  # noqa: E402
from conversation.services.preference_intent import (  # noqa: E402
    apply_preference_update,
    detect_preference_intent,
)
from memory.models import UserPreference, Watchlist  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


def _fresh_user(tid: int) -> tuple[ConversationProcessor, User]:
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    # Complete onboarding quickly
    p.handle_start(telegram_id=tid, first_name="Tester", telegram_message_id=1)
    for i, msg in enumerate(("skip", "skip", "skip", "skip"), start=2):
        p.handle_text(telegram_id=tid, text=msg, telegram_message_id=i)
    user = User.objects.get(telegram_id=tid)
    assert user.onboarding_completed
    return p, user


def test_clarification_ignores_role_statement() -> None:
    eng = ClarificationEngine()
    r = eng.evaluate("I'm an equity research analyst.")
    assert r.needed is False, r
    r2 = eng.evaluate("Tell me about Apple")
    assert r2.needed is True
    assert r2.subject and "Apple" in r2.subject
    print("PASS clarification_role_vs_apple")


def test_role_update_no_ai() -> None:
    p, user = _fresh_user(9600000301)
    with patch.object(p.orchestrator, "process", side_effect=AssertionError("AI must not run")):
        r = p.handle_text(
            telegram_id=9600000301,
            text="I'm an equity research analyst.",
            telegram_message_id=10,
        )
    assert "dig into I" not in r
    assert "analyst" in r.lower()
    assert "sector" in r.lower()
    user.refresh_from_db()
    assert user.role == UserRole.ANALYST
    print("PASS role_update_no_ai")


def test_sector_cover_no_ai() -> None:
    p, user = _fresh_user(9600000302)
    p.handle_text(
        telegram_id=9600000302,
        text="I'm an equity research analyst.",
        telegram_message_id=10,
    )
    with patch.object(p.orchestrator, "process", side_effect=AssertionError("AI must not run")):
        r = p.handle_text(
            telegram_id=9600000302,
            text="I cover banking and fintech.",
            telegram_message_id=11,
        )
    assert "research brain" not in r.lower()
    assert "banking" in r.lower() or "fintech" in r.lower()
    prefs = UserPreference.objects.get(user=user)
    sectors = [s.lower() for s in (prefs.sectors_of_interest or [])]
    assert any("bank" in s or "fintech" in s for s in sectors), sectors
    assert Watchlist.objects.filter(user=user, symbol="JPM").exists() or Watchlist.objects.filter(
        user=user, symbol="SQ"
    ).exists()
    print("PASS sector_cover_no_ai")


def test_briefing_and_watchlist() -> None:
    p, user = _fresh_user(9600000303)
    with patch.object(p.orchestrator, "process", side_effect=AssertionError("AI must not run")):
        r = p.handle_text(
            telegram_id=9600000303,
            text="My briefing should arrive at 7:30 AM.",
            telegram_message_id=10,
        )
        assert "7:30" in r or "07:30" in r or "Locked" in r
        r2 = p.handle_text(
            telegram_id=9600000303,
            text="Add NVDA to my watchlist",
            telegram_message_id=11,
        )
    assert "NVDA" in r2
    assert Watchlist.objects.filter(user=user, symbol="NVDA").exists()
    prefs = UserPreference.objects.get(user=user)
    assert prefs.preferred_briefing_time is not None
    print("PASS briefing_and_watchlist")


def test_follow_semiconductors() -> None:
    p, user = _fresh_user(9600000304)
    with patch.object(p.orchestrator, "process", side_effect=AssertionError("AI must not run")):
        r = p.handle_text(
            telegram_id=9600000304,
            text="I follow semiconductors.",
            telegram_message_id=10,
        )
    assert "NVDA" in r or "semiconductor" in r.lower()
    assert Watchlist.objects.filter(user=user, symbol="NVDA").exists()
    print("PASS follow_semiconductors")


def test_detect_intents() -> None:
    assert detect_preference_intent("I'm an investor.") == "role"
    assert detect_preference_intent("I'm an analyst.") == "role"
    assert detect_preference_intent("I cover banking.") == "sector"
    assert detect_preference_intent("I follow semiconductors.") == "sector"
    assert detect_preference_intent("I'm interested in AI.") == "sector"
    assert detect_preference_intent("My watchlist is NVDA and AMD") == "watchlist"
    assert detect_preference_intent("My briefing should arrive at 7:30.") == "briefing"
    assert detect_preference_intent("Tell me about Nvidia") is None
    assert detect_preference_intent("Why is NVDA moving today?") is None
    assert detect_preference_intent("What is happening with Nvidia today?") is None
    assert detect_preference_intent("Explain P/E ratio like I'm a beginner.") is None
    assert (
        detect_preference_intent(
            "I'm a student. Give me a simple explanation of what the stock market is."
        )
        is None
    )
    assert detect_preference_intent("Compare Nvidia and AMD as AI-chip companies.") is None
    print("PASS detect_intents")


def test_onboarding_interruption_still_works() -> None:
    tid = 9600000305
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    p.handle_start(telegram_id=tid, first_name="Tester", telegram_message_id=1)
    p.handle_text(telegram_id=tid, text="investor", telegram_message_id=2)
    user = User.objects.get(telegram_id=tid)
    user.onboarding_step = "briefing"
    user.onboarding_completed = False
    user.save()

    class FakeOrch:
        formatter = MagicMock()
        formatter.format = lambda x: x

        def process(self, user, conversation, text):
            return {"reply": f"AI:{text}", "metadata": {}}

    p.orchestrator = FakeOrch()
    r = p.handle_text(telegram_id=tid, text="Tell me about Nvidia.", telegram_message_id=3)
    user.refresh_from_db()
    assert user.onboarding_completed
    assert r.startswith("AI:")
    print("PASS onboarding_interruption")


def test_returning_user_role_change() -> None:
    p, user = _fresh_user(9600000306)
    p.handle_text(telegram_id=9600000306, text="I'm an investor.", telegram_message_id=10)
    user.refresh_from_db()
    assert user.role == UserRole.INVESTOR
    p.handle_text(
        telegram_id=9600000306,
        text="I'm an equity research analyst.",
        telegram_message_id=11,
    )
    user.refresh_from_db()
    assert user.role == UserRole.ANALYST
    print("PASS returning_user_role_change")


def test_research_not_stolen_during_focus_onboarding() -> None:
    tid = 9600000307
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    p.handle_start(telegram_id=tid, first_name="Tester", telegram_message_id=1)
    user = User.objects.get(telegram_id=tid)
    user.onboarding_step = "focus"
    user.onboarding_completed = False
    user.save()

    class FakeOrch:
        formatter = MagicMock()
        formatter.format = lambda x: x

        def process(self, user, conversation, text):
            return {"reply": f"AI:{text}", "metadata": {"pipeline": "ai"}}

    p.orchestrator = FakeOrch()
    # Bypass live finance fast-paths so this test isolates onboarding→AI routing.
    with patch(
        "conversation.services.market_fast_path.try_market_move_fast_answer",
        return_value=None,
    ), patch(
        "conversation.services.finance_fast_path.try_finance_fast_answer",
        return_value=None,
    ):
        r = p.handle_text(
            telegram_id=tid,
            text="What is happening with Nvidia today?",
            telegram_message_id=2,
        )
    user.refresh_from_db()
    assert user.onboarding_completed, "research should soft-complete onboarding"
    assert r.startswith("AI:"), r
    assert "focused on semiconductors" not in r.lower()
    print("PASS research_not_stolen_during_focus_onboarding")


def test_student_explain_delegates_to_ai() -> None:
    p, user = _fresh_user(9600000308)

    class FakeOrch:
        formatter = MagicMock()
        formatter.format = lambda x: x

        def process(self, user, conversation, text):
            return {"reply": f"AI:{text}", "metadata": {"pipeline": "ai"}}

    p.orchestrator = FakeOrch()
    r = p.handle_text(
        telegram_id=9600000308,
        text="I'm a student. Give me a simple explanation of what the stock market is.",
        telegram_message_id=20,
    )
    assert r.startswith("AI:"), r
    assert "treat you as a student" not in r.lower()
    print("PASS student_explain_delegates_to_ai")


def main() -> None:
    test_clarification_ignores_role_statement()
    test_detect_intents()
    test_role_update_no_ai()
    test_sector_cover_no_ai()
    test_briefing_and_watchlist()
    test_follow_semiconductors()
    test_onboarding_interruption_still_works()
    test_returning_user_role_change()
    test_research_not_stolen_during_focus_onboarding()
    test_student_explain_delegates_to_ai()
    print("\nALL INTENT ROUTING CHECKS PASSED")


if __name__ == "__main__":
    main()
