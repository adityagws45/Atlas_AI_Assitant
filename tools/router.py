"""Tool router — decision + execution for implemented tools."""

from __future__ import annotations

import logging
from typing import Any

from accounts.models import User
from ai.types import StructuredAIDecision, ToolName, ToolRequest
from tools.definitions import get_tool_definition, list_implemented_tool_names, list_tool_names
from tools.services.calendar_tool import CalendarToolExecutor
from tools.services.document_tool import DocumentToolExecutor
from tools.services.drive_tool import DriveToolExecutor
from tools.services.gmail_tool import GmailToolExecutor
from tools.services.sheet_tool import SheetToolExecutor
from tools.services.stock_tool import FinanceToolExecutor

logger = logging.getLogger("atlas.tools.router")

DOCUMENT_TOOLS = {ToolName.DOCUMENT_QA.value, ToolName.DOCUMENT_COMPARE.value}
DRIVE_TOOLS = {ToolName.DRIVE_SEARCH.value, ToolName.DRIVE_IMPORT.value}
SHEET_TOOLS = {
    ToolName.SHEET_LOOKUP.value,
    ToolName.SHEET_SEARCH.value,
    ToolName.SHEET_OPEN.value,
    ToolName.SHEET_SUMMARY.value,
    ToolName.SHEET_ANALYSIS.value,
    ToolName.SHEET_COMPARE.value,
    ToolName.SHEET_PORTFOLIO.value,
    ToolName.SHEET_STATISTICS.value,
    ToolName.SHEET_FIND_OUTLIERS.value,
    ToolName.SHEET_TRENDS.value,
}
GMAIL_TOOLS = {
    ToolName.GMAIL_SEARCH.value,
    ToolName.GMAIL_SUMMARY.value,
    ToolName.GMAIL_UNREAD.value,
    ToolName.GMAIL_PRIORITY.value,
    ToolName.GMAIL_THREAD.value,
    ToolName.GMAIL_ATTACHMENT.value,
    ToolName.GMAIL_DRAFT.value,
    ToolName.GMAIL_REPLY.value,
    ToolName.GMAIL_ARCHIVE.value,
    ToolName.GMAIL_MARK_READ.value,
}
CALENDAR_TOOLS = {
    ToolName.CALENDAR_LOOKUP.value,
    ToolName.CALENDAR_TODAY.value,
    ToolName.CALENDAR_SEARCH.value,
    ToolName.CALENDAR_CREATE.value,
    ToolName.CALENDAR_UPDATE.value,
    ToolName.CALENDAR_DELETE.value,
    ToolName.CALENDAR_FREE_TIME.value,
    ToolName.CALENDAR_CONFLICTS.value,
    ToolName.CALENDAR_DEADLINES.value,
}


class ToolRouter:
    """
    Decision + execution layer.

    Gemini decides which tool; this router validates and executes finance + document + Drive + Sheets tools.
    """

    def __init__(
        self,
        *,
        allow_unimplemented: bool = True,
        finance_executor: FinanceToolExecutor | None = None,
        document_executor: DocumentToolExecutor | None = None,
        drive_executor: DriveToolExecutor | None = None,
        sheet_executor: SheetToolExecutor | None = None,
        gmail_executor: GmailToolExecutor | None = None,
        calendar_executor: CalendarToolExecutor | None = None,
    ) -> None:
        self.allow_unimplemented = allow_unimplemented
        self.available = list_tool_names()
        self.finance_executor = finance_executor or FinanceToolExecutor()
        self.document_executor = document_executor or DocumentToolExecutor()
        self.drive_executor = drive_executor or DriveToolExecutor()
        self.sheet_executor = sheet_executor or SheetToolExecutor()
        self.gmail_executor = gmail_executor or GmailToolExecutor()
        self.calendar_executor = calendar_executor or CalendarToolExecutor()

    def available_tools(self) -> list[str]:
        implemented = list_implemented_tool_names()
        return implemented or list(self.available)

    def parse_decision(self, payload: dict[str, Any]) -> StructuredAIDecision:
        needs_clarification = bool(payload.get("needs_clarification"))
        clarification = (payload.get("clarification_question") or "").strip()
        needs_tool = bool(payload.get("needs_tool")) and not needs_clarification
        answer = (payload.get("answer") or "").strip()
        if needs_clarification and clarification and not answer:
            answer = clarification

        tool_request = None
        if needs_tool:
            tool_request = self._parse_tool(payload.get("tool") or payload.get("tool_request"))
            if tool_request is None:
                needs_tool = False
                logger.info("event=tool_decision_invalid_dropped")

        confidence = payload.get("confidence")
        try:
            confidence_f = float(confidence) if confidence is not None else 1.0
        except (TypeError, ValueError):
            confidence_f = 1.0

        decision = StructuredAIDecision(
            answer=answer,
            needs_clarification=needs_clarification,
            clarification_question=clarification,
            needs_tool=needs_tool,
            tool_request=tool_request,
            confidence=max(0.0, min(1.0, confidence_f)),
            raw_json=payload,
        )
        logger.info(
            "event=tool_decision clarification=%s needs_tool=%s tool=%s",
            decision.needs_clarification,
            decision.needs_tool,
            decision.tool_request.name if decision.tool_request else None,
        )
        return decision

    def execute(self, request: ToolRequest, *, user: User | None = None) -> dict[str, Any]:
        definition = get_tool_definition(request.name)
        if definition is None:
            return {
                "ok": False,
                "error": f"Unknown tool `{request.name}`.",
                "error_code": "unknown",
                "tool": request.name,
            }
        if not definition.get("implemented"):
            return {
                "ok": False,
                "error": f"`{request.name}` isn't wired yet.",
                "error_code": "unimplemented",
                "tool": request.name,
            }
        try:
            if request.name in DOCUMENT_TOOLS:
                result = self.document_executor.execute(request, user=user)
            elif request.name in DRIVE_TOOLS:
                result = self.drive_executor.execute(request, user=user)
            elif request.name in SHEET_TOOLS:
                result = self.sheet_executor.execute(request, user=user)
            elif request.name in GMAIL_TOOLS:
                result = self.gmail_executor.execute(request, user=user)
            elif request.name in CALENDAR_TOOLS:
                result = self.calendar_executor.execute(request, user=user)
            else:
                result = self.finance_executor.execute(request)
            logger.info(
                "event=tool_executed name=%s ok=%s source=%s cached=%s",
                request.name,
                result.get("ok"),
                result.get("source"),
                result.get("cached"),
            )
            return result
        except Exception:  # noqa: BLE001
            logger.exception("event=tool_execute_error name=%s", request.name)
            return {
                "ok": False,
                "error": "I hit a snag on that. Try again in a moment.",
                "error_code": "provider",
                "tool": request.name,
            }

    def _parse_tool(self, raw: Any) -> ToolRequest | None:
        if not isinstance(raw, dict):
            return None
        name = (raw.get("name") or "").strip()
        if not name:
            return None
        definition = get_tool_definition(name)
        if definition is None:
            logger.warning("event=unknown_tool name=%s", name)
            return None
        if not definition.get("implemented", False) and not self.allow_unimplemented:
            return None
        args = raw.get("arguments") or raw.get("args") or {}
        if not isinstance(args, dict):
            args = {"value": args}
        return ToolRequest(
            name=name,
            arguments=args,
            reason=(raw.get("reason") or "").strip(),
        )

    def describe_pending(self, request: ToolRequest) -> str:
        reason = request.reason or "I need the right context for a precise answer."
        return (
            f"{reason}\n\n"
            "Rephrase with a bit more detail if this didn't resolve cleanly."
        )
