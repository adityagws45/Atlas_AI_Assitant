"""
Verify live Gemini config with the current .env key + auto model resolution.
Does not implement Milestone 4.
"""

from __future__ import annotations

import os
import sys
import time

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from django.conf import settings  # noqa: E402

from accounts.models import User  # noqa: E402
from ai.prompts.prompt_manager import PromptManager  # noqa: E402
from ai.providers.gemini_provider import GeminiProvider  # noqa: E402
from ai.providers.model_resolve import (  # noqa: E402
    list_generate_content_models,
    model_supported,
)
from ai.services.clarification_engine import ClarificationEngine  # noqa: E402
from conversation.services.context_builder import ContextBuilder  # noqa: E402
from conversation.services.message_service import MessageService  # noqa: E402
from conversation.services.orchestrator import ConversationOrchestrator  # noqa: E402
from conversation.services.summary_service import ConversationSummaryService  # noqa: E402
from memory.models import AssistantMemory, UserPreference, Watchlist  # noqa: E402
from memory.services.memory_extractor import MemoryExtractor  # noqa: E402
from memory.services.memory_retriever import MemoryRetriever  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402

PASSED = 0
FAILED = 0
NOTES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))


def note(msg: str) -> None:
    NOTES.append(msg)
    print(f"NOTE  {msg}")


def main() -> None:
    print("=== Live Gemini configuration verification ===")
    key = (settings.GEMINI_API_KEY or "").strip()
    configured = (settings.GEMINI_MODEL or "").strip()
    configured_light = (settings.GEMINI_LIGHT_MODEL or "").strip()
    check("api_key_loaded", bool(key), "GEMINI_API_KEY missing")
    check("api_key_no_edge_whitespace", key == (settings.GEMINI_API_KEY or "").strip())
    note(f"key_len={len(key)} prefix={key[:6]}...")
    note(f"configured_model={configured!r} light={configured_light!r}")

    provider = GeminiProvider()
    # Force resolve via a live call path
    try:
        resp = provider.generate_text(
            system="You are a ping service.",
            user='Reply JSON: {"pong": true}',
            response_json=True,
            temperature=0,
            max_output_tokens=64,
        )
        check("simple_live_gemini_request", resp.ok, (resp.text or "")[:120])
        note(f"live_model={resp.model} latency_ms={resp.latency_ms} chars={len(resp.text)}")
    except Exception as exc:  # noqa: BLE001
        check("simple_live_gemini_request", False, f"{type(exc).__name__}: {exc}")
        print(f"RESULT: {PASSED} passed, {FAILED} failed")
        sys.exit(1)

    health = provider.health_check()
    note(f"resolved_model={health.get('model')!r} light={health.get('light_model')!r}")
    if health.get("model_switch_reason"):
        note(f"model_switch={health.get('model_switch_reason')}")

    # Confirm resolved model is in SDK catalog
    import google.generativeai as genai

    genai.configure(api_key=key)
    available = list_generate_content_models(genai)
    check(
        "resolved_model_supported_by_sdk",
        model_supported(provider.model, available),
        provider.model,
    )
    check(
        "configured_or_auto_switched",
        provider.model in available,
        f"model={provider.model}",
    )

    # Component checks
    pm = PromptManager()
    system, user_prompt = pm.compose_from_dict(
        {
            "telegram_id": 1,
            "preferences": {"response_style": "concise", "sectors_of_interest": ["semiconductors"]},
            "watchlist": [{"symbol": "NVDA"}],
            "memories": [{"key": "favorite_companies", "value": ["NVDA"]}],
            "current_user_message": "Tell me about Apple",
            "available_tools": ["stock_quote"],
        }
    )
    check("prompt_manager", "Atlas" in system and "needs_clarification" in user_prompt)

    eng = ClarificationEngine()
    check("clarification_engine", eng.evaluate("Tell me about Apple.").needed is True)

    tid = 9600000301
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(
        telegram_id=tid,
        first_name="Live",
        role="investor",
        onboarding_completed=True,
        onboarding_step="done",
    )
    UserPreference.objects.create(user=user, sectors_of_interest=["semiconductors"])
    Watchlist.objects.create(user=user, symbol="NVDA")
    AssistantMemory.objects.create(
        user=user,
        memory_type="preference",
        key="favorite_companies",
        value=["NVDA"],
        source="onboarding",
        confidence=0.95,
    )
    conv = MessageService.get_or_create_active_conversation(user)
    MessageService.save_user_message(conv, "Focus on chip supply chains")
    MessageService.save_assistant_message(conv, "Noted.")

    builder = ContextBuilder(summary_service=ConversationSummaryService(provider))
    ctx = builder.build(user, conv, "Tell me about Apple")
    check("context_builder_profile", ctx.user_profile.get("role") == "investor")
    check("context_builder_memories", any(m["key"] == "favorite_companies" for m in ctx.memories))
    check("context_builder_recent", len(ctx.recent_messages) >= 2)

    retriever = MemoryRetriever()
    found = retriever.retrieve(user, "NVDA semiconductors")
    check("memory_retrieval", any(m["key"] == "favorite_companies" for m in found))

    orch = ConversationOrchestrator(provider=provider)
    time.sleep(0.8)
    ai_out = orch.ai_service.generate_turn(ctx, user=user, extract_memory=False)
    check(
        "aiservice_live",
        bool(ai_out.answer)
        and "trouble reaching" not in ai_out.answer.lower()
        and "?" in ai_out.answer,
        ai_out.answer[:160],
    )
    note(f"AIService reply: {ai_out.answer[:180]}")

    extractor = MemoryExtractor(provider)
    saved = extractor.extract_and_save(
        user,
        user_message="Going forward I always prefer concise Telegram updates.",
        assistant_message="Understood — I'll keep replies tight.",
    )
    check("memory_extraction", len(saved) >= 1, str(saved))

    # End-to-end conversation via processor
    tid2 = 9600000302
    User.objects.filter(telegram_id=tid2).delete()
    processor = ConversationProcessor(orchestrator=ConversationOrchestrator(provider=provider))
    start = processor.handle_start(telegram_id=tid2, first_name="Riley", telegram_message_id=1)
    check("e2e_start", "Atlas" in start)
    for i, text in enumerate(["investor", "NVDA semiconductors", "growth", "skip"], start=2):
        processor.handle_text(telegram_id=tid2, text=text, telegram_message_id=i)
    user2 = User.objects.get(telegram_id=tid2)
    check("e2e_onboarding_done", user2.onboarding_completed is True)
    clar = processor.handle_text(telegram_id=tid2, text="Tell me about Apple", telegram_message_id=10)
    check("e2e_clarification", "?" in clar and "Apple" in clar, clar[:180])
    note(f"E2E clarification: {clar[:200]}")
    follow = processor.handle_text(
        telegram_id=tid2,
        text="Company overview, keep it concise.",
        telegram_message_id=11,
    )
    check(
        "e2e_followup",
        len(follow) > 40 and "trouble reaching" not in follow.lower(),
        follow[:180],
    )
    note(f"E2E follow-up: {follow[:200]}")

    print(f"\nRESULT: {PASSED} passed, {FAILED} failed")
    if NOTES:
        print("Notes:")
        for n in NOTES:
            print(f"  - {n}")
    if FAILED:
        print("LIVE_GEMINI_CONFIG_VERIFICATION: FAIL")
        sys.exit(1)
    print("LIVE_GEMINI_CONFIG_VERIFICATION: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
