"""Remember active / recent spreadsheets for natural follow-ups."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from accounts.models import User
from memory.models import AssistantMemory, MemorySource, MemoryType
from sheets.models import SheetWorkbook

logger = logging.getLogger("atlas.sheets.memory")

ACTIVE_KEY = "active_spreadsheet"
RECENT_KEY = "recent_spreadsheets"
PENDING_KEY = "pending_spreadsheet"


class SheetMemory:
    def remember_open(self, user: User, workbook: SheetWorkbook) -> None:
        entry = {
            "id": str(workbook.id),
            "title": workbook.title,
            "spreadsheet_id": workbook.spreadsheet_id,
            "kind": (workbook.detected or {}).get("primary_kind") or "",
            "updated_at": timezone.now().isoformat(),
        }
        self._set(user, ACTIVE_KEY, {"workbook": entry}, memory_type=MemoryType.CONTEXT)
        recent = self._get(user, RECENT_KEY) or {"items": []}
        items = [x for x in recent.get("items", []) if x.get("id") != entry["id"]]
        items.insert(0, entry)
        self._set(user, RECENT_KEY, {"items": items[:8]}, memory_type=MemoryType.CONTEXT)
        # Clear pending once opened
        self.clear_pending(user)
        logger.info(
            "event=sheet_memory_open telegram_id=%s workbook=%s",
            user.telegram_id,
            workbook.id,
        )

    def remember_pending(self, user: User, spreadsheet_id: str) -> None:
        sid = (spreadsheet_id or "").strip()
        if not sid:
            return
        self._set(
            user,
            PENDING_KEY,
            {"spreadsheet_id": sid, "updated_at": timezone.now().isoformat()},
            memory_type=MemoryType.CONTEXT,
        )

    def pending_spreadsheet_id(self, user: User) -> str | None:
        pending = self._get(user, PENDING_KEY) or {}
        sid = str(pending.get("spreadsheet_id") or "").strip()
        return sid or None

    def clear_pending(self, user: User) -> None:
        existing = (
            AssistantMemory.objects.filter(user=user, key=PENDING_KEY)
            .order_by("-updated_at")
            .first()
        )
        if existing:
            existing.value = {}
            existing.save(update_fields=["value", "updated_at"])

    def active_workbook_id(self, user: User) -> str | None:
        active = self._get(user, ACTIVE_KEY) or {}
        wb = active.get("workbook") or {}
        return str(wb["id"]) if wb.get("id") else None

    def active_spreadsheet_id(self, user: User) -> str | None:
        active = self._get(user, ACTIVE_KEY) or {}
        wb = active.get("workbook") or {}
        sid = str(wb.get("spreadsheet_id") or "").strip()
        return sid or None

    def active_title(self, user: User) -> str:
        active = self._get(user, ACTIVE_KEY) or {}
        wb = active.get("workbook") or {}
        return str(wb.get("title") or "")

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
