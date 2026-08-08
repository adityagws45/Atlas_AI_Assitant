"""Remember active event / recent window / pending mutations for follow-ups."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from accounts.models import User
from memory.models import AssistantMemory, MemorySource, MemoryType

logger = logging.getLogger("atlas.calendar.memory")

ACTIVE_KEY = "active_calendar_event"
RECENT_KEY = "recent_calendar_events"
PENDING_KEY = "pending_calendar_action"
CONTEXT_KEY = "active_calendar_context"
PENDING_Q_KEY = "pending_calendar_question"


class CalendarMemory:
    def remember_event(self, user: User, event_meta: dict[str, Any]) -> None:
        entry = self._serialize_event(event_meta)
        entry["updated_at"] = timezone.now().isoformat()
        self._set(user, ACTIVE_KEY, {"event": entry}, memory_type=MemoryType.CONTEXT)
        recent = self._get(user, RECENT_KEY) or {"items": []}
        items = [x for x in recent.get("items", []) if x.get("id") != entry.get("id")]
        items.insert(0, entry)
        self._set(user, RECENT_KEY, {"items": items[:8]}, memory_type=MemoryType.CONTEXT)
        logger.info(
            "event=calendar_memory_open telegram_id=%s event=%s",
            user.telegram_id,
            entry.get("id"),
        )

    def remember_context(
        self,
        user: User,
        *,
        events: list[dict[str, Any]],
        label: str,
        offset_days: int = 0,
    ) -> None:
        serialized = [self._serialize_event(e) for e in events[:20]]
        self._set(
            user,
            CONTEXT_KEY,
            {
                "label": label,
                "offset_days": offset_days,
                "events": serialized,
                "updated_at": timezone.now().isoformat(),
            },
            memory_type=MemoryType.CONTEXT,
        )
        if serialized:
            self.remember_event(user, events[0])

    def get_context(self, user: User) -> dict[str, Any]:
        return dict(self._get(user, CONTEXT_KEY) or {})

    def has_recent_context(self, user: User) -> bool:
        ctx = self.get_context(user)
        return bool(ctx.get("events") is not None or ctx.get("label"))

    def remember_pending_question(self, user: User, text: str) -> None:
        q = (text or "").strip()
        if not q:
            return
        self._set(
            user,
            PENDING_Q_KEY,
            {"question": q, "updated_at": timezone.now().isoformat()},
            memory_type=MemoryType.CONTEXT,
        )

    def pop_pending_question(self, user: User) -> str:
        data = self._get(user, PENDING_Q_KEY) or {}
        q = str(data.get("question") or "").strip()
        AssistantMemory.objects.filter(user=user, key=PENDING_Q_KEY).delete()
        return q

    def active_event_id(self, user: User) -> str | None:
        active = self._get(user, ACTIVE_KEY) or {}
        ev = active.get("event") or {}
        return str(ev["id"]) if ev.get("id") else None

    def active_event(self, user: User) -> dict[str, Any]:
        active = self._get(user, ACTIVE_KEY) or {}
        return dict(active.get("event") or {})

    def set_pending(self, user: User, action: dict[str, Any]) -> None:
        self._set(user, PENDING_KEY, {"action": action}, memory_type=MemoryType.CONTEXT)

    def get_pending(self, user: User) -> dict[str, Any] | None:
        data = self._get(user, PENDING_KEY) or {}
        action = data.get("action")
        return dict(action) if isinstance(action, dict) else None

    def clear_pending(self, user: User) -> None:
        AssistantMemory.objects.filter(user=user, key=PENDING_KEY).delete()

    def has_pending(self, user: User) -> bool:
        return self.get_pending(user) is not None

    @staticmethod
    def _serialize_event(event_meta: dict[str, Any]) -> dict[str, Any]:
        entry = {
            k: event_meta.get(k)
            for k in (
                "id",
                "event_id",
                "title",
                "start_at",
                "end_at",
                "categories",
                "companies",
                "tickers",
                "importance",
            )
        }
        for key in ("start_at", "end_at"):
            val = entry.get(key)
            if hasattr(val, "isoformat"):
                entry[key] = val.isoformat()
        return entry

    def _get(self, user: User, key: str) -> dict | None:
        mem = (
            AssistantMemory.objects.filter(user=user, key=key)
            .order_by("-updated_at")
            .first()
        )
        if not mem or not isinstance(mem.value, dict):
            return None
        return mem.value

    def _set(self, user: User, key: str, value: dict, *, memory_type: str) -> None:
        existing = (
            AssistantMemory.objects.filter(user=user, key=key)
            .order_by("-updated_at")
            .first()
        )
        if existing:
            existing.value = value
            existing.confidence = 1.0
            existing.source = MemorySource.CONVERSATION
            existing.save(update_fields=["value", "confidence", "source", "updated_at"])
        else:
            AssistantMemory.objects.create(
                user=user,
                memory_type=memory_type,
                key=key,
                value=value,
                source=MemorySource.CONVERSATION,
                confidence=1.0,
            )
