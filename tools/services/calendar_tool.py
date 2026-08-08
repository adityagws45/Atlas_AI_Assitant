"""Calendar tools — schedule intelligence via CalendarService."""

from __future__ import annotations

import logging
from typing import Any

from ai.types import ToolName, ToolRequest

logger = logging.getLogger("atlas.tools.calendar")


class CalendarToolExecutor:
    def __init__(self, service=None) -> None:
        self._service = service

    @property
    def service(self):
        if self._service is None:
            from gcalendar.services.calendar_service import CalendarService

            self._service = CalendarService()
        return self._service

    def execute(self, request: ToolRequest, *, user=None) -> dict[str, Any]:
        if user is None:
            return {
                "ok": False,
                "error": "Calendar tools need an active user session.",
                "error_code": "no_user",
                "tool": request.name,
            }
        args = request.arguments or {}
        query = str(
            args.get("query") or args.get("question") or args.get("text") or request.reason or ""
        ).strip()

        name = request.name
        if name in {ToolName.CALENDAR_TODAY.value, ToolName.CALENDAR_LOOKUP.value}:
            result = self.service.day_view(user, offset_days=0)
        elif name == ToolName.CALENDAR_SEARCH.value:
            result = self.service.search(user, query or "meeting")
        elif name == ToolName.CALENDAR_CREATE.value:
            result = self.service.propose_create(user, query or "schedule a meeting tomorrow at 2 PM")
        elif name == ToolName.CALENDAR_UPDATE.value:
            result = self.service.propose_update(user, query or "move it to Friday")
        elif name == ToolName.CALENDAR_DELETE.value:
            result = self.service.propose_cancel(user, query or "cancel that")
        elif name == ToolName.CALENDAR_FREE_TIME.value:
            result = self.service.free_time(user, query or "when am I free")
        elif name == ToolName.CALENDAR_CONFLICTS.value:
            result = self.service.conflicts(user)
        elif name == ToolName.CALENDAR_DEADLINES.value:
            result = self.service.deadlines(user)
        else:
            result = self.service.day_view(user)

        reply = result.get("reply") or result.get("error") or "Done."
        return {
            "ok": bool(result.get("ok")),
            "error": None if result.get("ok") else reply,
            "error_code": result.get("error_code"),
            "tool": request.name,
            "data": {"reply": reply},
            "source": "calendar",
            "cached": False,
            "pre_synthesized_reply": reply,
        }
