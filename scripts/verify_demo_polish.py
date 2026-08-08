"""Demo polish verification — JSON leak, onboarding UX, personalization, split."""

from __future__ import annotations

import os
import sys

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from accounts.models import User  # noqa: E402
from ai.services.ai_service import AIService  # noqa: E402
from ai.services.json_guard import (  # noqa: E402
    extract_json_object,
    looks_like_orchestration_json,
    public_answer_from_payload,
)
from ai.types import ConversationContext, StructuredAIDecision  # noqa: E402
from conversation.services.onboarding_service import OnboardingService  # noqa: E402
from conversation.services.personalization import (  # noqa: E402
    build_sector_follow_reply,
    build_tell_me_everything_reply,
    build_watch_today_reply,
    seed_tickers_for_sectors,
)
from conversation.services.response_formatter import ResponseFormatter  # noqa: E402
from memory.models import UserPreference, Watchlist  # noqa: E402
from telegram_bot.adapters.telegram_adapter import split_message  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


LEAKED = """{
  "needs_clarification": false,
  "clarification_question": "",
  "needs_tool": true,
  "tool": {
    "name": "market_overview",
    "arguments": {},
    "reason": "To fetch today's key macro events"
  },
  "answer": "Let me pull up today's key market-moving events.",
  "confidence": 0.95
}}"""


def assert_no_leak(text: str, label: str) -> None:
    assert text and text.strip(), label
    assert not looks_like_orchestration_json(text), f"{label}: still looks like JSON\n{text}"
    assert '"needs_tool"' not in text, f"{label}: needs_tool leaked\n{text}"
    assert '"confidence"' not in text, f"{label}: confidence leaked\n{text}"
    print(f"PASS {label}")


def test_json_guard() -> None:
    data = extract_json_object(LEAKED)
    assert data is not None, "failed to parse leaky JSON with extra brace"
    assert data.get("needs_tool") is True
    public = public_answer_from_payload(data)
    assert "market-moving" in public.lower() or "pull" in public.lower()
    fmt = ResponseFormatter().format(LEAKED)
    assert_no_leak(fmt, "formatter_blocks_orch_json")

    # ensure_public_answer path
    svc = AIService.__new__(AIService)
    decision = StructuredAIDecision(answer=LEAKED, needs_tool=True, raw_json=data or {})
    cleaned = AIService._ensure_public_answer(svc, LEAKED, decision, None)
    assert_no_leak(cleaned, "ai_service_ensure_public")


def test_split() -> None:
    long = ("Paragraph one about markets.\n\n" * 80) + ("Sentence two. " * 200)
    chunks = split_message(long, limit=800)
    assert len(chunks) > 1
    assert all(len(c) <= 800 for c in chunks)
    assert "...(trimmed)" not in "".join(chunks)
    print(f"PASS split_message chunks={len(chunks)}")


def test_onboarding_cross_question_and_sector() -> None:
    tid = 9500000201
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    p.handle_start(telegram_id=tid, first_name="Hero", telegram_message_id=1)
    p.handle_text(telegram_id=tid, text="I'm an investor.", telegram_message_id=2)
    r = p.handle_text(
        telegram_id=tid,
        text="I'm mostly interested in AI, semiconductors and cloud computing.",
        telegram_message_id=3,
    )
    assert "semiconductor" in r.lower() or "AI" in r or "prioritize" in r.lower(), r
    user = User.objects.get(telegram_id=tid)
    prefs = UserPreference.objects.get(user=user)
    assert any("semi" in (s or "").lower() for s in (prefs.sectors_of_interest or [])), prefs.sectors_of_interest
    assert Watchlist.objects.filter(user=user, symbol="NVDA").exists()
    print("PASS sector_seed_onboarding")

    # Depth step: answer with briefing time instead of style
    user.refresh_from_db()
    if user.onboarding_step != "depth" and user.onboarding_step != "style_catchup":
        # may be on more_names or depth depending on path
        if user.onboarding_step == "more_names":
            p.handle_text(telegram_id=tid, text="skip", telegram_message_id=4)
    user.refresh_from_db()
    # Force depth if somehow completed
    if user.onboarding_completed:
        user.onboarding_completed = False
        user.onboarding_step = "depth"
        user.save()
    r = p.handle_text(
        telegram_id=tid,
        text="I prefer a morning briefing around 8 AM.",
        telegram_message_id=5,
    )
    assert "8" in r or "briefing" in r.lower() or "investing style" in r.lower(), r
    assert "investing style" in r.lower() or "growth" in r.lower(), r
    user.refresh_from_db()
    prefs.refresh_from_db()
    assert prefs.preferred_briefing_time is not None
    print("PASS cross_question_briefing_during_style")

    r = p.handle_text(telegram_id=tid, text="growth, long-term", telegram_message_id=6)
    user.refresh_from_db()
    assert user.onboarding_completed, (user.onboarding_step, r)
    print("PASS style_catchup_complete")


def test_personalization_paths() -> None:
    tid = 9500000202
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    # Fast-complete a user with sectors
    p.handle_start(telegram_id=tid, first_name="Hero", telegram_message_id=1)
    p.handle_text(telegram_id=tid, text="skip", telegram_message_id=2)
    p.handle_text(telegram_id=tid, text="skip", telegram_message_id=3)
    p.handle_text(telegram_id=tid, text="skip", telegram_message_id=4)
    p.handle_text(telegram_id=tid, text="skip", telegram_message_id=5)
    user = User.objects.get(telegram_id=tid)
    assert user.onboarding_completed

    r = p.handle_text(
        telegram_id=tid,
        text="I mostly follow semiconductor companies.",
        telegram_message_id=6,
    )
    assert "NVDA" in r or "semiconductor" in r.lower(), r
    assert "which companies should I add" not in r.lower(), r
    assert Watchlist.objects.filter(user=user, symbol="NVDA").exists()
    print("PASS sector_follow_inference")

    r = p.handle_text(telegram_id=tid, text="What should I watch today?", telegram_message_id=7)
    assert "which companies should I add" not in r.lower(), r
    assert "NVDA" in r or "eye" in r.lower(), r
    print("PASS watch_today")

    r = p.handle_text(telegram_id=tid, text="Tell me everything.", telegram_message_id=8)
    assert "?" not in r.split("\n")[0] or "briefing" in r.lower()
    assert "Names on radar" in r or "Focus areas" in r or "radar" in r.lower(), r
    print("PASS tell_me_everything")


def test_mid_onboarding_research_handoff() -> None:
    tid = 9500000203
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    p.handle_start(telegram_id=tid, first_name="Hero", telegram_message_id=1)
    p.handle_text(telegram_id=tid, text="investor", telegram_message_id=2)
    p.handle_text(telegram_id=tid, text="AI and semiconductors", telegram_message_id=3)
    user = User.objects.get(telegram_id=tid)
    # Jump to briefing step
    user.onboarding_step = "briefing"
    user.onboarding_completed = False
    user.save()

    # Mock orchestrator to avoid live Gemini
    class FakeOrch:
        formatter = ResponseFormatter()

        def process(self, user, conversation, text):
            return {
                "reply": f"Research handoff for: {text}",
                "metadata": {"pipeline": "test"},
            }

    p.orchestrator = FakeOrch()
    r = p.handle_text(telegram_id=tid, text="Tell me about Nvidia.", telegram_message_id=4)
    user.refresh_from_db()
    assert user.onboarding_completed
    assert "Research handoff" in r
    assert "Perfect — I've got enough" not in r
    print("PASS mid_onboarding_research_handoff")


def main() -> None:
    test_json_guard()
    test_split()
    test_onboarding_cross_question_and_sector()
    test_personalization_paths()
    test_mid_onboarding_research_handoff()
    print("\nALL DEMO POLISH CHECKS PASSED")


if __name__ == "__main__":
    main()
