"""Central AI Service — every model interaction goes through here."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from accounts.models import User
from ai.prompts.finance_prompt import SYNTHESIS_SYSTEM, build_synthesis_prompt
from ai.prompts.prompt_manager import PromptManager
from ai.providers.base import BaseAIProvider
from ai.providers.gemini_provider import GeminiProvider
from ai.types import (
    AITurnResult,
    ConversationContext,
    ProviderConfigError,
    ProviderError,
    ProviderMessage,
    StructuredAIDecision,
)
from ai.services.json_guard import (
    extract_json_object,
    is_orchestration_payload,
    looks_like_orchestration_json,
    public_answer_from_payload,
)
from finance.services.payload_sanitize import sanitize_tool_payload
from finance.utils.ticker_resolve import resolve_symbols
from memory.services.memory_extractor import MemoryExtractor
from memory.services.research_tracker import ResearchInterestTracker
from tools.router import ToolRouter

logger = logging.getLogger("atlas.ai.service")

FRIENDLY_AI_UNAVAILABLE = (
    "I'm having trouble pulling that data right now. Try again in a moment."
)

FRIENDLY_PLAN_FALLBACK = (
    "Let me pull the latest on that — rephrase with a ticker (e.g. NVDA) if this stalls."
)


class AIService:
    """
    Single entry for AI turns:

    build prompt → call provider → parse decision → execute tools → synthesize → memory
    """

    def __init__(
        self,
        provider: BaseAIProvider | None = None,
        *,
        prompts: PromptManager | None = None,
        tool_router: ToolRouter | None = None,
        memory_extractor: MemoryExtractor | None = None,
    ) -> None:
        self.provider = provider or GeminiProvider()
        self.prompts = prompts or PromptManager()
        self.tool_router = tool_router or ToolRouter()
        self.memory_extractor = memory_extractor or MemoryExtractor(
            self.provider, prompts=self.prompts
        )
        self.research_tracker = ResearchInterestTracker()

    def generate_turn(
        self,
        context: ConversationContext,
        *,
        user: User | None = None,
        extract_memory: bool = True,
    ) -> AITurnResult:
        system, user_prompt = self.prompts.compose_turn(context)
        messages = self._history_messages(context, user_prompt)

        try:
            response = self.provider.generate(
                system=system,
                messages=messages,
                response_json=True,
                temperature=None,
            )
        except ProviderConfigError:
            logger.warning("event=ai_config_missing")
            return AITurnResult(
                answer=FRIENDLY_AI_UNAVAILABLE,
                metadata={"error": "provider_config"},
            )
        except ProviderError as exc:
            logger.warning("event=ai_provider_error err=%s", type(exc).__name__)
            return AITurnResult(
                answer=FRIENDLY_AI_UNAVAILABLE,
                metadata={"error": "provider_error", "detail": str(exc)[:200]},
            )

        payload = extract_json_object(response.text)
        if payload is None and looks_like_orchestration_json(response.text or ""):
            # Last-chance salvage for truncated / extra-brace JSON
            payload = extract_json_object((response.text or "").rstrip("}` \n"))

        if payload is None:
            raw = (response.text or "").strip()
            if looks_like_orchestration_json(raw):
                logger.warning("event=orch_json_unparsed_blocked")
                decision = StructuredAIDecision(
                    answer="",
                    needs_tool=False,
                    raw_json={},
                )
                return AITurnResult(
                    answer=FRIENDLY_PLAN_FALLBACK,
                    decision=decision,
                    metadata={"error": "orch_json_unparsed"},
                )
            decision = StructuredAIDecision(answer=raw)
        else:
            decision = self.tool_router.parse_decision(payload)

        # Never allow a planning payload to become the user-facing answer
        if looks_like_orchestration_json(decision.answer):
            salvaged = public_answer_from_payload(payload or {})
            decision.answer = salvaged

        tool_payload: dict[str, Any] | None = None
        answer = ""
        research_saved: list[str] = []
        if decision.needs_clarification:
            answer = self._finalize_answer(decision, context)
        elif decision.needs_tool and decision.tool_request:
            tool_payload = self.tool_router.execute(decision.tool_request, user=user)
            # Document tools already synthesize an analyst reply
            pre = (tool_payload or {}).get("pre_synthesized_reply")
            if pre:
                answer = str(pre).strip()
            else:
                sanitized = sanitize_tool_payload(decision.tool_request.name, tool_payload)
                answer = self._synthesize_with_tool_data(
                    context=context,
                    tool_name=decision.tool_request.name,
                    tool_reason=decision.tool_request.reason,
                    tool_result=sanitized,
                )
                if not answer:
                    answer = self._fallback_tool_answer(tool_payload, decision)
                if looks_like_orchestration_json(answer):
                    answer = self._fallback_tool_answer(tool_payload, decision)
            if user is not None and tool_payload.get("ok"):
                syms = ResearchInterestTracker.symbols_from_tool(
                    decision.tool_request.name,
                    decision.tool_request.arguments or {},
                    tool_payload,
                )
                syms.extend(resolve_symbols(context.current_user_message))
                research_saved = self.research_tracker.remember_symbols(user, syms)
        else:
            answer = self._finalize_answer(decision, context)
            if user is not None:
                syms = resolve_symbols(context.current_user_message)
                if syms and any(
                    w in context.current_user_message.lower()
                    for w in ("research", "compare", "analyze", "earnings", "news about")
                ):
                    research_saved = self.research_tracker.remember_symbols(user, syms)

        answer = self._ensure_public_answer(answer, decision, tool_payload)

        memories_saved: list[str] = []
        if extract_memory and user is not None and answer:
            memories_saved = self.memory_extractor.extract_and_save(
                user,
                user_message=context.current_user_message,
                assistant_message=answer,
            )
        if research_saved and "researched_companies" not in memories_saved:
            memories_saved = list(memories_saved) + ["researched_companies"]

        return AITurnResult(
            answer=answer,
            decision=decision,
            tool_request=decision.tool_request,
            memories_saved=memories_saved,
            used_clarification=bool(decision.needs_clarification),
            provider_model=response.model,
            metadata={
                "latency_ms": response.latency_ms,
                "usage": response.usage,
                "needs_tool": decision.needs_tool,
                "confidence": decision.confidence,
                "tool_result_ok": None if tool_payload is None else bool(tool_payload.get("ok")),
                "tool_source": None if tool_payload is None else tool_payload.get("source"),
                "tool_cached": None if tool_payload is None else tool_payload.get("cached"),
            },
        )

    def generate_raw_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float = 0.2,
    ) -> dict[str, Any] | None:
        response = self.provider.generate_text(
            system=system,
            user=user,
            temperature=temperature,
            response_json=True,
            model=model,
        )
        return self._parse_json(response.text)

    def _ensure_public_answer(
        self,
        answer: str,
        decision: StructuredAIDecision,
        tool_payload: dict[str, Any] | None,
    ) -> str:
        text = (answer or "").strip()
        if looks_like_orchestration_json(text) or is_orchestration_payload(
            extract_json_object(text)
        ):
            logger.warning("event=orch_json_stripped_from_answer")
            if tool_payload is not None:
                return self._fallback_tool_answer(tool_payload, decision)
            salvaged = public_answer_from_payload(decision.raw_json or {})
            return salvaged or FRIENDLY_PLAN_FALLBACK
        return text or FRIENDLY_PLAN_FALLBACK

    def _synthesize_with_tool_data(
        self,
        *,
        context: ConversationContext,
        tool_name: str,
        tool_reason: str,
        tool_result: dict[str, Any],
    ) -> str:
        prompt = build_synthesis_prompt(
            user_message=context.current_user_message,
            tool_name=tool_name,
            tool_reason=tool_reason,
            tool_result=tool_result,
            context=context.to_prompt_dict(),
        )
        try:
            response = self.provider.generate_text(
                system=SYNTHESIS_SYSTEM,
                user=prompt,
                temperature=0.35,
                response_json=False,
                max_output_tokens=1024,
            )
            text = (response.text or "").strip()
            if text:
                return text
        except ProviderError as exc:
            logger.warning("event=synthesis_provider_error err=%s", type(exc).__name__)
        except Exception:
            logger.exception("event=synthesis_error")
        return ""

    def _fallback_tool_answer(
        self, tool_payload: dict[str, Any], decision: StructuredAIDecision
    ) -> str:
        if not tool_payload.get("ok"):
            return (
                tool_payload.get("error")
                or "I couldn't pull that market data cleanly just now. Try again shortly."
            )
        data = tool_payload.get("data")
        # Minimal readable fallback
        if isinstance(data, dict) and "price" in data:
            sym = data.get("symbol") or ""
            price = data.get("price")
            pct = data.get("change_percent")
            return (
                f"{sym} is around {price}"
                + (f" ({pct:+.2f}%)" if isinstance(pct, (int, float)) else "")
                + ". That's the live read — ask if you want the catalyst or valuation angle."
            )
        return (
            decision.answer
            or "I've pulled the latest figures. Ask me which angle to dig into next."
        )

    def _finalize_answer(
        self, decision: StructuredAIDecision, context: ConversationContext
    ) -> str:
        if decision.needs_clarification:
            q = decision.clarification_question or decision.answer
            if q:
                return q.strip()
            suggested = (context.extras or {}).get("suggested_clarification")
            if suggested:
                return suggested

        answer = (decision.answer or "").strip()
        if not answer:
            answer = (
                "I want to make sure I hit the right angle — "
                "what would help most right now?"
            )
        return answer

    @staticmethod
    def _history_messages(
        context: ConversationContext, current_user_prompt: str
    ) -> list[ProviderMessage]:
        messages: list[ProviderMessage] = []
        recent = list(context.recent_messages or [])
        if recent and recent[-1].get("role") == "user":
            if (recent[-1].get("content") or "").strip() == (
                context.current_user_message or ""
            ).strip():
                recent = recent[:-1]

        for row in recent[-10:]:
            role = row.get("role") or "user"
            content = (row.get("content") or "").strip()
            if not content:
                continue
            if role == "assistant":
                messages.append(ProviderMessage(role="assistant", content=content))
            elif role == "user":
                messages.append(ProviderMessage(role="user", content=content))

        messages.append(ProviderMessage(role="user", content=current_user_prompt))
        return messages

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        return extract_json_object(text)
