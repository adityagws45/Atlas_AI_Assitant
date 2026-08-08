"""Document tools — document_qa / document_compare via DocumentQAService."""

from __future__ import annotations

import logging
from typing import Any

from ai.types import ToolName, ToolRequest

logger = logging.getLogger("atlas.tools.document")


class DocumentToolExecutor:
    def __init__(self, qa=None) -> None:
        self._qa = qa

    @property
    def qa(self):
        if self._qa is None:
            from documents.services.document_qa_service import DocumentQAService

            self._qa = DocumentQAService()
        return self._qa

    def execute(self, request: ToolRequest, *, user=None) -> dict[str, Any]:
        name = request.name
        args = request.arguments or {}
        question = (
            str(args.get("question") or args.get("query") or request.reason or "")
        ).strip()
        doc_id = args.get("document_id") or args.get("document")
        doc_ids = args.get("document_ids") or args.get("documents") or []
        if isinstance(doc_ids, str):
            doc_ids = [doc_ids]
        if doc_id:
            doc_ids = [str(doc_id)] + [str(x) for x in doc_ids]
        doc_ids = [str(x) for x in doc_ids if x]

        if user is None:
            return {
                "ok": False,
                "error": "Document analysis needs an active user session.",
                "error_code": "no_user",
                "tool": name,
            }
        if not question:
            return {
                "ok": False,
                "error": "What would you like to know about the report?",
                "error_code": "no_question",
                "tool": name,
            }

        compare = name == ToolName.DOCUMENT_COMPARE.value or bool(args.get("compare"))
        logger.info(
            "event=document_tool_exec name=%s compare=%s docs=%s",
            name,
            compare,
            len(doc_ids),
        )
        result = self.qa.answer(
            user,
            question,
            document_ids=doc_ids or None,
            compare=compare,
        )
        return {
            "ok": bool(result.get("ok")),
            "error": (result.get("error") or result.get("reply")) if not result.get("ok") else None,
            "error_code": result.get("error_code"),
            "tool": name,
            "data": {
                "reply": result.get("reply"),
                "sources": result.get("sources") or [],
                "document_ids": result.get("document_ids") or [],
            },
            "source": "documents",
            "cached": False,
            "pre_synthesized_reply": result.get("reply"),
        }
