"""Conversation orchestrator — AI + finance tool pipeline."""

from __future__ import annotations

import logging
from typing import Any

from accounts.models import User
from ai.providers.base import BaseAIProvider
from ai.providers.gemini_provider import GeminiProvider
from ai.services.ai_service import AIService, FRIENDLY_AI_UNAVAILABLE
from ai.services.clarification_engine import ClarificationEngine
from ai.types import ProviderError
from conversation.models import Conversation
from conversation.services.context_builder import ContextBuilder
from conversation.services.response_formatter import ResponseFormatter
from conversation.services.summary_service import ConversationSummaryService
from memory.services.memory_extractor import MemoryExtractor
from memory.services.memory_retriever import MemoryRetriever
from tools.router import ToolRouter

logger = logging.getLogger("atlas.conversation.orchestrator")


class ConversationOrchestrator:
    """
    Central pipeline:

    Preference routing happens BEFORE this class (ConversationProcessor).

    Clarification (deterministic) → optional AIService → ToolRouter → synthesis
    """

    def __init__(
        self,
        provider: BaseAIProvider | None = None,
        *,
        ai_service: AIService | None = None,
        context_builder: ContextBuilder | None = None,
        formatter: ResponseFormatter | None = None,
    ) -> None:
        self.provider = provider or GeminiProvider()
        self.tool_router = ToolRouter()
        self.memory_retriever = MemoryRetriever()
        self.summary_service = ConversationSummaryService(self.provider)
        self.clarification = ClarificationEngine()
        self.memory_extractor = MemoryExtractor(self.provider)

        self.ai_service = ai_service or AIService(
            self.provider,
            tool_router=self.tool_router,
            memory_extractor=self.memory_extractor,
        )
        self.context_builder = context_builder or ContextBuilder(
            summary_service=self.summary_service,
            memory_retriever=self.memory_retriever,
            clarification=self.clarification,
            tool_router=self.tool_router,
        )
        self.formatter = formatter or ResponseFormatter()

    def process(
        self,
        user: User,
        conversation: Conversation,
        user_message: str,
    ) -> dict[str, Any]:
        clar = self.clarification.evaluate(user_message)

        # Deterministic clarification — no Gemini / tools required
        if clar.needed and clar.suggested_question:
            formatted = self.formatter.format(clar.suggested_question)
            logger.info(
                "event=clarification_short_circuit subject=%s telegram_id=%s",
                clar.subject,
                user.telegram_id,
            )
            return {
                "reply": formatted,
                "metadata": {
                    "pipeline": "clarification",
                    "used_clarification": True,
                    "needs_tool": False,
                    "tool": None,
                    "memories_saved": [],
                    "provider_model": None,
                },
            }

        try:
            context = self.context_builder.build(user, conversation, user_message)
        except Exception:
            logger.exception(
                "event=context_build_failed telegram_id=%s — recovering",
                user.telegram_id,
            )
            return {
                "reply": self.formatter.format(FRIENDLY_AI_UNAVAILABLE),
                "metadata": {"pipeline": "recovery", "error": "context_build"},
            }

        try:
            result = self.ai_service.generate_turn(
                context,
                user=user,
                extract_memory=True,
            )
        except ProviderError as exc:
            logger.warning(
                "event=orchestrator_provider_recover err=%s telegram_id=%s",
                type(exc).__name__,
                user.telegram_id,
            )
            return {
                "reply": self.formatter.format(FRIENDLY_AI_UNAVAILABLE),
                "metadata": {
                    "pipeline": "recovery",
                    "error": type(exc).__name__,
                    "used_clarification": False,
                    "needs_tool": False,
                },
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "event=orchestrator_unexpected_recover err=%s telegram_id=%s",
                type(exc).__name__,
                user.telegram_id,
            )
            return {
                "reply": self.formatter.format(FRIENDLY_AI_UNAVAILABLE),
                "metadata": {
                    "pipeline": "recovery",
                    "error": type(exc).__name__,
                },
            }

        answer = result.answer
        formatted = self.formatter.format(answer)
        metadata = {
            "pipeline": "milestone4",
            "used_clarification": result.used_clarification,
            "needs_tool": bool(result.tool_request),
            "tool": result.tool_request.to_dict() if result.tool_request else None,
            "memories_saved": result.memories_saved,
            "provider_model": result.provider_model,
            **(result.metadata or {}),
        }
        logger.info(
            "event=orchestrator_ok telegram_id=%s clarification=%s tool=%s memories=%s",
            user.telegram_id,
            result.used_clarification,
            metadata.get("tool"),
            result.memories_saved,
        )
        return {"reply": formatted, "metadata": metadata}
