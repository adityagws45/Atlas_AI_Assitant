"""Response style + deferred memory + latency metadata checks (no live Gemini required)."""

from __future__ import annotations

import os
import sys
import time
import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from accounts.models import User  # noqa: E402
from ai.services.ai_service import FRIENDLY_AI_UNAVAILABLE  # noqa: E402
from ai.types import AITurnResult, StructuredAIDecision  # noqa: E402
from conversation.services.response_formatter import ResponseFormatter  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


BANNED_FILLER = (
    "absolutely!",
    "great question",
    "let's dive in",
    "the bottom line",
    "financial snapshot",
    "the student lens",
    "market position",
    "would you like me to",
    "research brain",
)


def _complete_user(tid: int) -> ConversationProcessor:
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    p.handle_start(telegram_id=tid, first_name="Style", telegram_message_id=1)
    for i, msg in enumerate(("skip", "skip", "skip", "skip"), start=2):
        p.handle_text(telegram_id=tid, text=msg, telegram_message_id=i)
    assert User.objects.get(telegram_id=tid).onboarding_completed
    return p


def test_formatter_strips_chatgpt_filler() -> None:
    fmt = ResponseFormatter()
    raw = (
        "Absolutely! Great question!\n\n"
        "*The Bottom Line*\n"
        "NVDA is up on AI demand.\n\n"
        "*The Student Lens*\n"
        "Because you're a student, remember P/E.\n\n"
        "Would you like me to dig deeper?"
    )
    out = fmt.format(raw).lower()
    for bad in BANNED_FILLER:
        assert bad not in out, f"still contains {bad!r}: {out}"
    assert "nvda is up on ai demand" in out
    print("PASS formatter_strips_chatgpt_filler")


def test_friendly_error_has_no_provider_language() -> None:
    low = FRIENDLY_AI_UNAVAILABLE.lower()
    assert "research brain" not in low
    assert "gemini" not in low
    assert "provider" not in low
    assert "trouble pulling" in low or "try again" in low
    print("PASS friendly_error_language")


def test_memory_failure_does_not_block_reply() -> None:
    tid = 9700000601
    p = _complete_user(tid)
    called = {"memory": 0, "extract_memory_flag": None}

    def slow_failing_memory(*a, **k):
        called["memory"] += 1
        time.sleep(0.35)
        raise RuntimeError("memory boom")

    def fake_turn(*a, **k):
        called["extract_memory_flag"] = k.get("extract_memory", True)
        return AITurnResult(
            answer="P/E is price divided by earnings per share.",
            decision=StructuredAIDecision(answer="P/E is price divided by earnings per share."),
            memories_saved=[],
            used_clarification=False,
            provider_model="test",
            metadata={},
        )

    p.orchestrator.ai_service.generate_turn = fake_turn  # type: ignore[method-assign]
    p.orchestrator.memory_extractor.extract_and_save = slow_failing_memory  # type: ignore[method-assign]

    t0 = time.perf_counter()
    r = p.handle_text(
        telegram_id=tid,
        text="Explain P/E ratio like I'm a beginner.",
        telegram_message_id=10,
    )
    elapsed = time.perf_counter() - t0
    assert "P/E" in r or "earnings" in r.lower()
    assert called["extract_memory_flag"] is False
    # Reply must return before the slow async memory finishes
    assert elapsed < 0.25, f"reply blocked on memory: {elapsed:.3f}s"
    time.sleep(0.5)
    assert called["memory"] >= 1
    print("PASS memory_failure_does_not_block_reply")


def test_student_compound_routes_to_ai_not_preference() -> None:
    tid = 9700000602
    p = _complete_user(tid)

    class FakeOrch:
        formatter = ResponseFormatter()

        def process(self, user, conversation, text):
            return {
                "reply": "P/E shows how much investors pay per unit of earnings.",
                "metadata": {"pipeline": "ai", "timing_ms": {"total": 1}},
            }

    p.orchestrator = FakeOrch()  # type: ignore[assignment]
    r = p.handle_text(
        telegram_id=tid,
        text="I'm a student. Give me a simple explanation of what the stock market is.",
        telegram_message_id=20,
    )
    assert "treat you as a student" not in r.lower()
    assert "stock" in r.lower() or "earnings" in r.lower() or "investors" in r.lower()
    print("PASS student_compound_routes_to_ai")


def test_orchestrator_timing_metadata() -> None:
    tid = 9700000603
    p = _complete_user(tid)

    def fake_turn(*a, **k):
        return AITurnResult(
            answer="NVDA is higher on AI-chip demand.",
            decision=StructuredAIDecision(answer="NVDA is higher on AI-chip demand."),
            memories_saved=[],
            used_clarification=False,
            provider_model="test",
            metadata={},
        )

    # Persist for the deferred memory thread (do not use patch context that ends early).
    p.orchestrator.memory_extractor.extract_and_save = lambda *a, **k: []  # type: ignore[method-assign]
    p.orchestrator.ai_service.generate_turn = fake_turn  # type: ignore[method-assign]
    user = User.objects.get(telegram_id=tid)
    from conversation.services.message_service import MessageService

    conv = MessageService.get_or_create_active_conversation(user)
    out = p.orchestrator.process(user, conv, "What is happening with Nvidia today?")
    assert "NVDA" in out["reply"] or "AI" in out["reply"]
    timing = (out.get("metadata") or {}).get("timing_ms") or {}
    assert "total" in timing
    assert "gemini_and_tools" in timing
    time.sleep(0.15)  # let deferred memory release its slot
    print("PASS orchestrator_timing_metadata")


def test_prompts_forbid_essay_headings() -> None:
    from ai.prompts.system_prompt import SYSTEM_PERSONA, build_system_prompt
    from ai.prompts.finance_prompt import SYNTHESIS_SYSTEM

    blob = (SYSTEM_PERSONA + SYNTHESIS_SYSTEM + build_system_prompt()).lower()
    assert "bottom line" in blob  # banned by instruction
    assert "never" in blob or "banned" in blob or "do not" in blob
    assert "chat gpt" in blob.replace("-", "") or "chatgpt" in blob
    assert "deep dive" in blob
    assert "never invent" in blob or "do not invent" in blob or "never invent" in SYSTEM_PERSONA.lower()
    print("PASS prompts_forbid_essay_headings")


def test_formatter_preserves_detailed_body() -> None:
    fmt = ResponseFormatter()
    body = "*NVDA*\n\n" + ("AI demand remains strong. " * 40) + "\n\nWatch: earnings."
    out = fmt.format(body)
    assert "AI demand remains strong" in out
    assert "Watch: earnings" in out
    assert len(out) > 500
    # Under soft-trim threshold — must not be gutted
    assert abs(len(out) - len(body.strip())) < 40
    print("PASS formatter_preserves_detailed_body")


def test_memory_slots_bounded() -> None:
    import threading

    from conversation.services import orchestrator as orch_mod

    sem = orch_mod._MEMORY_EXTRACT_SLOTS
    assert isinstance(sem, threading.BoundedSemaphore)

    # Wait for any deferred jobs from earlier tests to release slots.
    acquired = 0
    deadline = time.perf_counter() + 3.0
    while acquired < 2 and time.perf_counter() < deadline:
        if sem.acquire(blocking=False):
            acquired += 1
        else:
            time.sleep(0.05)
    assert acquired == 2, f"expected 2 free memory slots, got {acquired}"
    assert not sem.acquire(blocking=False), "third slot must not be available"
    sem.release()
    sem.release()
    print("PASS memory_slots_bounded")


def main() -> None:
    print("=== Response style / latency verification ===")
    test_formatter_strips_chatgpt_filler()
    test_friendly_error_has_no_provider_language()
    test_memory_failure_does_not_block_reply()
    test_student_compound_routes_to_ai_not_preference()
    test_orchestrator_timing_metadata()
    test_prompts_forbid_essay_headings()
    test_formatter_preserves_detailed_body()
    test_memory_slots_bounded()
    print("\nRESPONSE_STYLE_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
