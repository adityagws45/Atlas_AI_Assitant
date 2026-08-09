"""Conversation orchestrator — AI + finance tool pipeline."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from django.db import close_old_connections

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

# Cap concurrent background memory jobs per process (Gunicorn worker).
# Non-blocking acquire → skip when saturated so threads cannot pile up.
_MEMORY_EXTRACT_SLOTS = threading.BoundedSemaphore(2)


class ConversationOrchestrator:
    """
    Central pipeline:

    Preference routing happens BEFORE this class (ConversationProcessor).

    Clarification (deterministic) → optional AIService → ToolRouter → synthesis

    Memory extraction is best-effort AFTER the user-facing reply is ready so it
    does not block Telegram delivery.
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
        t0 = time.perf_counter()
        clar = self.clarification.evaluate(user_message)
        t_intent_ms = int((time.perf_counter() - t0) * 1000)

        # Deterministic clarification — no Gemini / tools required
        if clar.needed and clar.suggested_question:
            formatted = self.formatter.format(clar.suggested_question)
            logger.info(
                "event=clarification_short_circuit subject=%s telegram_id=%s "
                "latency_ms=%s",
                clar.subject,
                user.telegram_id,
                int((time.perf_counter() - t0) * 1000),
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
                    "timing_ms": {
                        "intent": t_intent_ms,
                        "total": int((time.perf_counter() - t0) * 1000),
                    },
                },
            }

        try:
            t_ctx = time.perf_counter()
            context = self.context_builder.build(user, conversation, user_message)
            t_context_ms = int((time.perf_counter() - t_ctx) * 1000)
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
            t_ai = time.perf_counter()
            # Memory extract is deferred — do not block the Telegram reply.
            result = self.ai_service.generate_turn(
                context,
                user=user,
                extract_memory=False,
            )
            t_ai_ms = int((time.perf_counter() - t_ai) * 1000)
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
        total_ms = int((time.perf_counter() - t0) * 1000)
        metadata = {
            "pipeline": "milestone4",
            "used_clarification": result.used_clarification,
            "needs_tool": bool(result.tool_request),
            "tool": result.tool_request.to_dict() if result.tool_request else None,
            "memories_saved": result.memories_saved,
            "provider_model": result.provider_model,
            "timing_ms": {
                "intent": t_intent_ms,
                "context": t_context_ms,
                "gemini_and_tools": t_ai_ms,
                "total": total_ms,
            },
            **(result.metadata or {}),
        }
        logger.info(
            "event=orchestrator_ok telegram_id=%s clarification=%s tool=%s "
            "memories=%s timing_ms=%s",
            user.telegram_id,
            result.used_clarification,
            metadata.get("tool"),
            result.memories_saved,
            metadata["timing_ms"],
        )

        if answer and user is not None:
            self._schedule_memory_extract(
                user_id=user.pk,
                telegram_id=int(user.telegram_id),
                user_message=user_message,
                assistant_message=answer,
            )

        return {"reply": formatted, "metadata": metadata}

    def _schedule_memory_extract(
        self,
        *,
        user_id,
        telegram_id: int,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Best-effort memory extract after the reply is ready (bounded, non-blocking)."""

        if not _MEMORY_EXTRACT_SLOTS.acquire(blocking=False):
            logger.info(
                "event=memory_extract_async_skipped_busy telegram_id=%s",
                telegram_id,
            )
            return

        def _run() -> None:
            close_old_connections()
            started = time.perf_counter()
            try:
                from accounts.models import User as UserModel

                user = UserModel.objects.filter(pk=user_id).first()
                if user is None:
                    return
                saved = self.memory_extractor.extract_and_save(
                    user,
                    user_message=user_message,
                    assistant_message=assistant_message,
                )
                logger.info(
                    "event=memory_extract_async_ok telegram_id=%s keys=%s "
                    "latency_ms=%s",
                    telegram_id,
                    saved,
                    int((time.perf_counter() - started) * 1000),
                )
            except Exception:
                logger.exception(
                    "event=memory_extract_async_failed telegram_id=%s",
                    telegram_id,
                )
            finally:
                close_old_connections()
                _MEMORY_EXTRACT_SLOTS.release()

        try:
            threading.Thread(
                target=_run,
                name=f"atlas-memory-{telegram_id}",
                daemon=True,
            ).start()
        except Exception:
            _MEMORY_EXTRACT_SLOTS.release()
            logger.exception(
                "event=memory_extract_async_schedule_failed telegram_id=%s",
                telegram_id,
            )
