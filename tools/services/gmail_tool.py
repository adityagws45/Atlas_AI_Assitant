"""Gmail tools — inbox intelligence via GmailService."""

from __future__ import annotations

import logging
from typing import Any

from ai.types import ToolName, ToolRequest

logger = logging.getLogger("atlas.tools.gmail")


class GmailToolExecutor:
    def __init__(self, service=None) -> None:
        self._service = service

    @property
    def service(self):
        if self._service is None:
            from gmail.services.gmail_service import GmailService

            self._service = GmailService()
        return self._service

    def execute(self, request: ToolRequest, *, user=None) -> dict[str, Any]:
        if user is None:
            return {
                "ok": False,
                "error": "Email tools need an active user session.",
                "error_code": "no_user",
                "tool": request.name,
            }
        args = request.arguments or {}
        query = str(
            args.get("query") or args.get("question") or args.get("q") or request.reason or ""
        ).strip()
        tone = str(args.get("tone") or "polite").strip()

        name = request.name
        if name == ToolName.GMAIL_SEARCH.value:
            result = self.service.search(user, query)
        elif name == ToolName.GMAIL_SUMMARY.value:
            result = self.service.inbox_digest(user, mode="summary")
        elif name == ToolName.GMAIL_UNREAD.value:
            result = self.service.inbox_digest(user, mode="unread")
        elif name == ToolName.GMAIL_PRIORITY.value:
            result = self.service.inbox_digest(user, mode="priority")
        elif name == ToolName.GMAIL_THREAD.value:
            result = self.service.open_thread(user, query)
        elif name == ToolName.GMAIL_ATTACHMENT.value:
            result = self.service.summarize_attachment(user)
        elif name == ToolName.GMAIL_DRAFT.value:
            result = self.service.draft_reply(user, instruction=query, tone=tone)
        elif name == ToolName.GMAIL_REPLY.value:
            result = self.service.draft_reply(user, instruction=query or "reply", tone=tone)
        elif name == ToolName.GMAIL_ARCHIVE.value:
            result = self.service.archive_active(user)
        elif name == ToolName.GMAIL_MARK_READ.value:
            result = self.service.mark_active_read(user)
        else:
            result = self.service.inbox_digest(user, mode="check")

        reply = result.get("reply") or result.get("error") or "Done."
        return {
            "ok": bool(result.get("ok")),
            "error": None if result.get("ok") else reply,
            "error_code": result.get("error_code"),
            "tool": request.name,
            "data": {"reply": reply},
            "source": "gmail",
            "cached": False,
            "pre_synthesized_reply": reply,
        }
