"""Lightweight conversation entity context for pronoun follow-ups."""

from __future__ import annotations

import logging
import re
from typing import Any

from accounts.models import User
from memory.models import AssistantMemory, MemorySource, MemoryType

logger = logging.getLogger("atlas.conversation.entity")

ENTITY_KEY = "conversation_entity"

_PRONOUN = re.compile(
    r"\b(its|it'?s|their|they|the company|the stock|this one|that one)\b",
    re.IGNORECASE,
)
_AMBIGUOUS_PAIR = re.compile(
    r"\b(which one|both|either|nvidia or amd|a or b)\b",
    re.IGNORECASE,
)


class EntityContext:
    """Remember the active company / ticker / doc / sheet for follow-ups."""

    def get(self, user: User) -> dict[str, Any]:
        mem = (
            AssistantMemory.objects.filter(user=user, key=ENTITY_KEY)
            .order_by("-updated_at")
            .first()
        )
        if not mem or not isinstance(mem.value, dict):
            return {}
        return dict(mem.value)

    def remember(
        self,
        user: User,
        *,
        symbol: str | None = None,
        company: str | None = None,
        topic: str | None = None,
        document_id: str | None = None,
        sheet_id: str | None = None,
        alt_symbols: list[str] | None = None,
    ) -> None:
        data = self.get(user)
        if symbol:
            data["current_ticker"] = str(symbol).upper()[:16]
        if company:
            data["current_company"] = str(company)[:120]
        if topic:
            data["current_topic"] = str(topic)[:80]
        if document_id:
            data["current_document"] = str(document_id)
        if sheet_id:
            data["current_sheet"] = str(sheet_id)
        if alt_symbols is not None:
            data["alt_tickers"] = [str(s).upper()[:16] for s in alt_symbols[:4]]
        existing = (
            AssistantMemory.objects.filter(user=user, key=ENTITY_KEY)
            .order_by("-updated_at")
            .first()
        )
        if existing:
            existing.value = data
            existing.confidence = 1.0
            existing.source = MemorySource.CONVERSATION
            existing.save(update_fields=["value", "confidence", "source", "updated_at"])
        else:
            AssistantMemory.objects.create(
                user=user,
                memory_type=MemoryType.CONTEXT,
                key=ENTITY_KEY,
                value=data,
                source=MemorySource.CONVERSATION,
                confidence=1.0,
            )
        logger.info(
            "event=entity_remember telegram_id=%s ticker=%s",
            user.telegram_id,
            data.get("current_ticker"),
        )

    def resolve_symbol(self, user: User, text: str) -> str | None:
        """If the message uses a pronoun and we have a single ticker, return it."""
        q = (text or "").strip()
        if not q or not _PRONOUN.search(q):
            return None
        data = self.get(user)
        ticker = (data.get("current_ticker") or "").strip().upper()
        alts = [str(a).upper() for a in (data.get("alt_tickers") or []) if a]
        # After a compare, "its" is ambiguous — force a clarify prompt.
        if ticker and alts:
            return None
        return ticker or None

    def ambiguity_prompt(self, user: User) -> str | None:
        data = self.get(user)
        alts = [str(a).upper() for a in (data.get("alt_tickers") or []) if a]
        primary = (data.get("current_ticker") or "").strip().upper()
        names = [x for x in [primary, *alts] if x]
        names = list(dict.fromkeys(names))
        if len(names) >= 2:
            return f"Do you mean *{names[0]}* or *{names[1]}*?"
        return None
