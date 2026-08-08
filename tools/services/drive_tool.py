"""Drive tools — search / import via DriveSyncService."""

from __future__ import annotations

import logging
from typing import Any

from ai.types import ToolName, ToolRequest

logger = logging.getLogger("atlas.tools.drive")


class DriveToolExecutor:
    def __init__(self, service=None) -> None:
        self._service = service

    @property
    def service(self):
        if self._service is None:
            from drive.services.drive_service import DriveService

            self._service = DriveService()
        return self._service

    def execute(self, request: ToolRequest, *, user=None) -> dict[str, Any]:
        if user is None:
            return {
                "ok": False,
                "error": "File access needs an active user session.",
                "error_code": "no_user",
                "tool": request.name,
            }
        args = request.arguments or {}
        query = str(args.get("query") or args.get("name") or args.get("q") or "").strip()
        if request.name == ToolName.DRIVE_SEARCH.value:
            result = self.service.search(user, query)
            return {
                "ok": bool(result.get("ok")),
                "error": None if result.get("ok") else result.get("reply"),
                "error_code": result.get("error_code"),
                "tool": request.name,
                "data": {"reply": result.get("reply")},
                "source": "drive",
                "cached": False,
                "pre_synthesized_reply": result.get("reply"),
            }
        if request.name == ToolName.DRIVE_IMPORT.value:
            result = self.service.import_and_ready(user, query)
            return {
                "ok": bool(result.get("ok")),
                "error": None if result.get("ok") else result.get("reply"),
                "error_code": result.get("error_code"),
                "tool": request.name,
                "data": {"reply": result.get("reply")},
                "source": "drive",
                "cached": False,
                "pre_synthesized_reply": result.get("reply"),
            }
        return {
            "ok": False,
            "error": "Unknown Drive tool.",
            "error_code": "unknown",
            "tool": request.name,
        }
