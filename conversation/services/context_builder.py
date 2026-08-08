"""Gather relevant context before every AI turn."""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache

from accounts.models import User
from ai.services.clarification_engine import ClarificationEngine
from ai.types import ConversationContext
from conversation.models import Conversation
from conversation.services.summary_service import ConversationSummaryService
from memory.models import UserPreference, Watchlist
from memory.services.memory_retriever import MemoryRetriever
from tools.router import ToolRouter

logger = logging.getLogger("atlas.conversation.context")


class ContextBuilder:
    """
    Assemble user profile, preferences, watchlist, memories, summary,
    recent messages, and onboarding state — then keep only what matters.
    """

    def __init__(
        self,
        *,
        summary_service: ConversationSummaryService | None = None,
        memory_retriever: MemoryRetriever | None = None,
        clarification: ClarificationEngine | None = None,
        tool_router: ToolRouter | None = None,
    ) -> None:
        self.summary_service = summary_service or ConversationSummaryService()
        self.memory_retriever = memory_retriever or MemoryRetriever()
        self.clarification = clarification or ClarificationEngine()
        self.tool_router = tool_router or ToolRouter()

    def build(
        self,
        user: User,
        conversation: Conversation,
        user_message: str,
    ) -> ConversationContext:
        # Ensure summary is current before reading recent window
        self.summary_service.maybe_roll_summary(conversation)
        conversation.refresh_from_db(fields=["context_summary"])

        prefs = self._preferences(user)
        watchlist = self._watchlist(user)
        memories = self.memory_retriever.retrieve(user, user_message)
        clar = self.clarification.evaluate(user_message)

        active_docs: list[dict[str, Any]] = []
        try:
            from documents.services.document_memory import DocumentMemory

            active_docs = DocumentMemory().active_summaries(user)
        except Exception:  # noqa: BLE001
            active_docs = []

        # Prefer document Q&A over company clarification when a filing is loaded
        if active_docs and clar.needed:
            clar.needed = False
            clar.hint = None
            clar.suggested_question = None

        context = ConversationContext(
            user_id=str(user.id),
            telegram_id=int(user.telegram_id),
            user_profile=self._profile(user),
            preferences=prefs,
            watchlist=watchlist,
            memories=memories,
            onboarding_state={
                "completed": bool(user.onboarding_completed),
                "step": user.onboarding_step or "",
                "role": user.role or "",
            },
            conversation_id=str(conversation.id),
            conversation_summary=self.summary_service.get_summary(conversation),
            recent_messages=self.summary_service.recent_messages(conversation),
            current_user_message=user_message,
            clarification_hint=clar.hint,
            available_tools=self.tool_router.available_tools(),
            extras={
                "clarification_needed": clar.needed,
                "clarification_subject": clar.subject,
                "suggested_clarification": clar.suggested_question,
                "active_documents": active_docs,
            },
        )

        logger.info(
            "event=context_built telegram_id=%s memories=%s watchlist=%s recent=%s "
            "summary_chars=%s clarification=%s docs=%s",
            user.telegram_id,
            len(memories),
            len(watchlist),
            len(context.recent_messages),
            len(context.conversation_summary or ""),
            clar.needed,
            len(active_docs),
        )
        return context

    def _profile(self, user: User) -> dict[str, Any]:
        cache_key = f"ctx:profile:{user.id}"
        ttl = int(getattr(settings, "CACHE_TTL_PROFILE", 600))
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        data = {
            "first_name": user.first_name or "",
            "telegram_username": user.telegram_username or "",
            "role": user.role or "",
            "onboarding_completed": bool(user.onboarding_completed),
        }
        cache.set(cache_key, data, ttl)
        return data

    def _preferences(self, user: User) -> dict[str, Any]:
        prefs = UserPreference.objects.filter(user=user).first()
        if not prefs:
            return {"response_style": "concise"}
        return {
            "response_style": prefs.response_style,
            "preferred_briefing_time": str(prefs.preferred_briefing_time)
            if prefs.preferred_briefing_time
            else None,
            "briefing_timezone": prefs.briefing_timezone,
            "insight_types": prefs.insight_types or [],
            "sectors_of_interest": prefs.sectors_of_interest or [],
            "markets_of_interest": prefs.markets_of_interest or [],
            "additional_verticals": prefs.additional_verticals or [],
            "language": prefs.language,
        }

    def _watchlist(self, user: User) -> list[dict[str, Any]]:
        items = Watchlist.objects.filter(user=user).order_by("symbol")[:30]
        return [
            {
                "symbol": w.symbol,
                "company_name": w.company_name,
                "notes": w.notes,
            }
            for w in items
        ]
