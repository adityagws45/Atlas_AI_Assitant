"""Remember active / recent documents for natural follow-ups."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from accounts.models import User
from documents.models import FinancialDocument, ProcessingStatus
from memory.models import AssistantMemory, MemorySource, MemoryType

logger = logging.getLogger("atlas.documents.memory")

ACTIVE_KEY = "active_documents"
RECENT_KEY = "recent_documents"


class DocumentMemory:
    """Track which filings the user is currently discussing."""

    def remember_upload(self, user: User, document: FinancialDocument) -> None:
        active = self._get(user, ACTIVE_KEY) or {"documents": []}
        docs = [d for d in active.get("documents", []) if d.get("id") != str(document.id)]
        entry = {
            "id": str(document.id),
            "title": document.title,
            "company": document.company,
            "kind": document.document_kind,
            "fiscal_period": document.fiscal_period,
            "updated_at": timezone.now().isoformat(),
        }
        docs.insert(0, entry)
        active = {"documents": docs[:5], "focus_id": str(document.id)}
        self._set(user, ACTIVE_KEY, active, memory_type=MemoryType.CONTEXT)

        recent = self._get(user, RECENT_KEY) or {"documents": []}
        rdocs = [d for d in recent.get("documents", []) if d.get("id") != str(document.id)]
        rdocs.insert(0, entry)
        self._set(user, RECENT_KEY, {"documents": rdocs[:12]}, memory_type=MemoryType.CONTEXT)
        logger.info(
            "event=doc_memory_upload telegram_id=%s doc_id=%s",
            user.telegram_id,
            document.id,
        )

    def active_document_ids(self, user: User) -> list[str]:
        active = self._get(user, ACTIVE_KEY) or {}
        docs = active.get("documents") or []
        ids = [str(d.get("id")) for d in docs if d.get("id")]
        focus = active.get("focus_id")
        if focus and focus in ids:
            ids = [focus] + [i for i in ids if i != focus]
        # Validate still ready
        ready = set(
            str(x)
            for x in FinancialDocument.objects.filter(
                user=user,
                id__in=ids,
                processing_status=ProcessingStatus.READY,
            ).values_list("id", flat=True)
        )
        return [i for i in ids if i in ready]

    def active_summaries(self, user: User) -> list[dict[str, Any]]:
        ids = self.active_document_ids(user)
        if not ids:
            return []
        docs = FinancialDocument.objects.filter(user=user, id__in=ids)
        by_id = {str(d.id): d for d in docs}
        out = []
        for i in ids:
            d = by_id.get(i)
            if not d:
                continue
            out.append(
                {
                    "id": str(d.id),
                    "title": d.title,
                    "company": d.company,
                    "kind": d.document_kind,
                    "fiscal_period": d.fiscal_period,
                    "pages": d.page_count,
                }
            )
        return out

    def clear_focus(self, user: User) -> None:
        AssistantMemory.objects.filter(user=user, key=ACTIVE_KEY).delete()

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
