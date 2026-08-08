"""Remember active thread / pending drafts for natural follow-ups."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from accounts.models import User
from memory.models import AssistantMemory, MemorySource, MemoryType

logger = logging.getLogger("atlas.gmail.memory")

ACTIVE_KEY = "active_email"
RECENT_KEY = "recent_emails"
RESULT_KEY = "active_gmail_results"
PENDING_Q_KEY = "pending_gmail_question"
DRAFT_KEY = "pending_email_draft"
PENDING_SEND_KEY = "pending_email_send"


class GmailMemory:
    def remember_open(self, user: User, message_meta: dict[str, Any]) -> None:
        entry = {
            **message_meta,
            "updated_at": timezone.now().isoformat(),
        }
        # Never persist full body in long-term memory — keep snippet only
        entry.pop("body_text", None)
        self._set(user, ACTIVE_KEY, {"message": entry}, memory_type=MemoryType.CONTEXT)
        recent = self._get(user, RECENT_KEY) or {"items": []}
        items = [x for x in recent.get("items", []) if x.get("id") != entry.get("id")]
        items.insert(0, {k: entry.get(k) for k in ("id", "subject", "from_name", "companies", "tickers")})
        self._set(user, RECENT_KEY, {"items": items[:10]}, memory_type=MemoryType.CONTEXT)
        logger.info(
            "event=gmail_memory_open telegram_id=%s msg=%s",
            user.telegram_id,
            entry.get("id"),
        )

    def remember_results(
        self,
        user: User,
        *,
        messages: list[dict[str, Any]],
        label: str,
        gmail_query: str = "",
    ) -> None:
        slim = []
        for m in messages[:12]:
            slim.append(
                {
                    k: m.get(k)
                    for k in (
                        "id",
                        "message_id",
                        "subject",
                        "from_name",
                        "from_email",
                        "snippet",
                        "is_unread",
                        "priority_score",
                        "why",
                    )
                }
            )
        self._set(
            user,
            RESULT_KEY,
            {
                "label": label,
                "gmail_query": gmail_query,
                "messages": slim,
                "updated_at": timezone.now().isoformat(),
            },
            memory_type=MemoryType.CONTEXT,
        )
        if slim:
            self.remember_open(user, messages[0])

    def get_results(self, user: User) -> dict[str, Any]:
        return dict(self._get(user, RESULT_KEY) or {})

    def has_recent_context(self, user: User) -> bool:
        ctx = self.get_results(user)
        if ctx.get("messages"):
            return True
        return bool(self.active_message_id(user))

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

    def active_message_id(self, user: User) -> str | None:
        active = self._get(user, ACTIVE_KEY) or {}
        msg = active.get("message") or {}
        return str(msg["id"]) if msg.get("id") else None

    def active_message(self, user: User) -> dict[str, Any]:
        active = self._get(user, ACTIVE_KEY) or {}
        return dict(active.get("message") or {})

    def save_draft(self, user: User, draft: dict[str, Any]) -> None:
        self._set(user, DRAFT_KEY, {"draft": draft}, memory_type=MemoryType.CONTEXT)
        self.clear_pending_send(user)

    def get_draft(self, user: User) -> dict[str, Any] | None:
        data = self._get(user, DRAFT_KEY) or {}
        draft = data.get("draft")
        return dict(draft) if isinstance(draft, dict) else None

    def clear_draft(self, user: User) -> None:
        AssistantMemory.objects.filter(user=user, key=DRAFT_KEY).delete()
        self.clear_pending_send(user)

    def mark_pending_send(self, user: User) -> None:
        self._set(user, PENDING_SEND_KEY, {"pending": True}, memory_type=MemoryType.CONTEXT)

    def is_pending_send(self, user: User) -> bool:
        data = self._get(user, PENDING_SEND_KEY) or {}
        return bool(data.get("pending"))

    def clear_pending_send(self, user: User) -> None:
        AssistantMemory.objects.filter(user=user, key=PENDING_SEND_KEY).delete()

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
