"""Track repeatedly researched companies into AssistantMemory."""

from __future__ import annotations

import logging
from typing import Any

from accounts.models import User
from memory.models import AssistantMemory, MemorySource, MemoryType

logger = logging.getLogger("atlas.memory.research")

MAX_COMPANIES = 20


class ResearchInterestTracker:
    """Persist researched tickers without re-asking the user."""

    def remember_symbols(self, user: User, symbols: list[str]) -> list[str]:
        cleaned = []
        for s in symbols:
            sym = (s or "").strip().upper()
            if sym and sym not in cleaned:
                cleaned.append(sym)
        if not cleaned:
            return []

        existing = (
            AssistantMemory.objects.filter(user=user, key="researched_companies")
            .order_by("-updated_at")
            .first()
        )
        current: list[str] = []
        if existing and isinstance(existing.value, list):
            current = [str(x).upper() for x in existing.value if x]
        elif existing and isinstance(existing.value, dict):
            current = [str(x).upper() for x in (existing.value.get("symbols") or []) if x]

        # Most-recent first
        merged = list(dict.fromkeys(cleaned + current))[:MAX_COMPANIES]
        if existing:
            existing.value = merged
            existing.memory_type = MemoryType.PREFERENCE
            existing.source = MemorySource.CONVERSATION
            existing.confidence = 0.9
            existing.save(update_fields=["value", "memory_type", "source", "confidence", "updated_at"])
        else:
            AssistantMemory.objects.create(
                user=user,
                key="researched_companies",
                memory_type=MemoryType.PREFERENCE,
                value=merged,
                source=MemorySource.CONVERSATION,
                confidence=0.9,
            )
        logger.info(
            "event=research_memory_saved telegram_id=%s symbols=%s",
            user.telegram_id,
            merged[:8],
        )
        return merged

    @staticmethod
    def symbols_from_tool(tool_name: str, request_args: dict[str, Any], result: dict[str, Any]) -> list[str]:
        symbols: list[str] = []
        for key in ("symbol", "ticker"):
            if request_args.get(key):
                symbols.append(str(request_args[key]))
        for s in request_args.get("symbols") or []:
            symbols.append(str(s))
        data = result.get("data")
        if isinstance(data, dict):
            if data.get("symbol"):
                symbols.append(str(data["symbol"]))
            for row in data.get("companies") or []:
                if isinstance(row, dict) and row.get("symbol"):
                    symbols.append(str(row["symbol"]))
        return symbols
