"""Sheets tools — portfolio / spreadsheet intelligence via SheetService."""

from __future__ import annotations

import logging
from typing import Any

from ai.types import ToolName, ToolRequest

logger = logging.getLogger("atlas.tools.sheet")

MODE_BY_TOOL = {
    ToolName.SHEET_LOOKUP.value: "summary",
    ToolName.SHEET_SUMMARY.value: "summary",
    ToolName.SHEET_ANALYSIS.value: "analysis",
    ToolName.SHEET_PORTFOLIO.value: "portfolio",
    ToolName.SHEET_STATISTICS.value: "statistics",
    ToolName.SHEET_FIND_OUTLIERS.value: "outliers",
    ToolName.SHEET_TRENDS.value: "trends",
    ToolName.SHEET_COMPARE.value: "trends",
}


class SheetToolExecutor:
    def __init__(self, service=None) -> None:
        self._service = service

    @property
    def service(self):
        if self._service is None:
            from sheets.services.sheet_service import SheetService

            self._service = SheetService()
        return self._service

    def execute(self, request: ToolRequest, *, user=None) -> dict[str, Any]:
        if user is None:
            return {
                "ok": False,
                "error": "Spreadsheet analysis needs an active user session.",
                "error_code": "no_user",
                "tool": request.name,
            }
        args = request.arguments or {}
        query = str(
            args.get("query") or args.get("question") or args.get("name") or request.reason or ""
        ).strip()
        mode = str(args.get("mode") or MODE_BY_TOOL.get(request.name) or "summary").strip()

        if request.name == ToolName.SHEET_SEARCH.value:
            result = self.service.list_sheets(user, query)
        elif request.name == ToolName.SHEET_OPEN.value:
            result = self.service.open_sheet(user, query or "portfolio")
        else:
            result = self.service.analyze_active(user, question=query or "summarize", mode=mode)

        reply = result.get("reply") or result.get("error") or "Done."
        return {
            "ok": bool(result.get("ok")),
            "error": None if result.get("ok") else reply,
            "error_code": result.get("error_code"),
            "tool": request.name,
            "data": {"reply": reply},
            "source": "sheets",
            "cached": False,
            "pre_synthesized_reply": reply,
        }
