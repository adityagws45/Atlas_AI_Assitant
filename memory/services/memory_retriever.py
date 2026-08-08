"""Retrieve relevant AssistantMemory for a turn."""

from __future__ import annotations

import logging
import re
from typing import Any

from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from memory.models import AssistantMemory

logger = logging.getLogger("atlas.memory.retriever")

# Always-include preference-like keys when present
PRIORITY_KEYS = {
    "preferred_sectors",
    "favorite_companies",
    "briefing_time",
    "communication_style",
    "research_interests",
    "researched_companies",
    "investment_style",
    "meeting_preferences",
    "workflow",
    "response_style",
    "markets_of_interest",
    "active_documents",
    "recent_documents",
}


class MemoryRetriever:
    """Select only memories relevant to the current user message."""

    def __init__(self, *, limit: int = 12) -> None:
        self.limit = limit

    def retrieve(self, user: User, user_message: str = "") -> list[dict[str, Any]]:
        now = timezone.now()
        qs = (
            AssistantMemory.objects.filter(user=user)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .order_by("-confidence", "-updated_at")
        )

        tokens = self._tokens(user_message)
        scored: list[tuple[float, AssistantMemory]] = []
        for mem in qs[:80]:
            score = self._score(mem, tokens)
            if score > 0:
                scored.append((score, mem))

        scored.sort(key=lambda x: (-x[0], -float(x[1].confidence or 0)))
        selected = [self._serialize(m) for _, m in scored[: self.limit]]

        logger.info(
            "event=memory_retrieve telegram_id=%s candidates=%s selected=%s",
            user.telegram_id,
            len(scored),
            len(selected),
        )
        return selected

    def existing_keys(self, user: User) -> list[str]:
        return list(
            AssistantMemory.objects.filter(user=user)
            .order_by("key")
            .values_list("key", flat=True)
            .distinct()
        )

    def _score(self, mem: AssistantMemory, tokens: set[str]) -> float:
        key = (mem.key or "").lower()
        score = 0.0
        if key in PRIORITY_KEYS or any(k in key for k in PRIORITY_KEYS):
            score += 2.0
        if mem.memory_type == "preference":
            score += 1.0
        blob = f"{key} {mem.value}".lower()
        hits = sum(1 for t in tokens if t in blob)
        score += hits * 1.5
        # Keep high-confidence prefs even without token overlap
        if score == 0 and mem.memory_type == "preference" and (mem.confidence or 0) >= 0.7:
            score = 0.5
        return score

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t}

    @staticmethod
    def _serialize(mem: AssistantMemory) -> dict[str, Any]:
        return {
            "id": str(mem.id),
            "memory_type": mem.memory_type,
            "key": mem.key,
            "value": mem.value,
            "source": mem.source,
            "confidence": mem.confidence,
        }
