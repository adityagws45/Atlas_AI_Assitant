"""Free-form Q&A over the active spreadsheet — answers only from sheet data."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ai.types import ProviderError, ProviderMessage

logger = logging.getLogger("atlas.sheets.qa")

SHEET_QA_SYSTEM = """
You are Atlas answering questions about the user's ACTIVE Google Spreadsheet.

Rules (strict):
- Use ONLY the spreadsheet data provided in the user message.
- Answer the user's natural-language question directly and clearly.
- You may calculate totals, averages, rankings, growth %, comparisons, and
  simple statistics when the numbers exist in the sheet.
- If the sheet does not contain enough information to answer, reply with EXACTLY:
  I couldn't find that information in the spreadsheet.
- Do NOT invent companies, metrics, years, or figures that are not in the data.
- Do NOT use general market knowledge, demo portfolios, or AI Watchlist data.
- Do NOT mention embeddings, tools, OAuth, or internal systems.
- Keep the reply Telegram-friendly: concise, readable, light structure.
- Prefer short paragraphs or bullets when listing multiple items.
""".strip()

_MISSING = "I couldn't find that information in the spreadsheet."


class SheetQAService:
    def __init__(self, *, provider=None) -> None:
        if provider is None:
            from ai.providers.gemini_provider import GeminiProvider

            provider = GeminiProvider()
        self.provider = provider

    def answer(
        self,
        *,
        question: str,
        title: str,
        values_by_sheet: dict[str, list[list[Any]]],
        findings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        q = (question or "").strip()
        if not q:
            return {"ok": False, "reply": "What would you like to know about the spreadsheet?"}

        context = self._build_context(title=title, values_by_sheet=values_by_sheet, findings=findings)
        prompt = (
            f"Spreadsheet title: {title}\n\n"
            f"Spreadsheet data:\n{context}\n\n"
            f"User question: {q}\n\n"
            "Answer using only the spreadsheet data above."
        )
        try:
            response = self.provider.generate(
                system=SHEET_QA_SYSTEM,
                messages=[ProviderMessage(role="user", content=prompt)],
                response_json=False,
                temperature=0.2,
                max_output_tokens=1024,
            )
            text = (response.text or "").strip()
            if not text:
                return {"ok": True, "reply": _MISSING, "source": "empty_model"}
            text = self._normalize_missing(text)
            return {"ok": True, "reply": text, "source": "gemini_sheet_qa"}
        except (ProviderError, Exception) as exc:  # noqa: BLE001
            logger.warning("event=sheet_qa_gemini_failed err=%s", type(exc).__name__)
            fallback = self._deterministic_fallback(
                question=q, title=title, values_by_sheet=values_by_sheet, findings=findings
            )
            return {"ok": True, "reply": fallback, "source": "deterministic_fallback"}

    def _build_context(
        self,
        *,
        title: str,
        values_by_sheet: dict[str, list[list[Any]]],
        findings: dict[str, Any] | None,
    ) -> str:
        parts: list[str] = []
        char_budget = 14000
        used = 0
        for name, rows in (values_by_sheet or {}).items():
            block_lines = [f"### Sheet: {name}"]
            for row in rows[:200]:
                cells = [self._cell(c) for c in (row or [])[:26]]
                # Drop trailing empties
                while cells and cells[-1] == "":
                    cells.pop()
                if not cells:
                    continue
                block_lines.append(" | ".join(cells))
            block = "\n".join(block_lines)
            if used + len(block) > char_budget:
                remain = max(0, char_budget - used)
                if remain > 200:
                    parts.append(block[:remain] + "\n… (truncated)")
                break
            parts.append(block)
            used += len(block)

        if findings:
            compact = {
                "kind": findings.get("kind"),
                "metrics": findings.get("metrics"),
                "holdings": findings.get("holdings"),
                "metric_improvements": findings.get("metric_improvements"),
                "total_value": findings.get("total_value"),
                "sectors": findings.get("sectors"),
                "best": findings.get("best"),
                "worst": findings.get("worst"),
                "risks": findings.get("risks"),
            }
            try:
                extra = json.dumps(compact, ensure_ascii=False, default=str)
                if len(extra) < 4000:
                    parts.append("### Structured findings\n" + extra)
            except Exception:  # noqa: BLE001
                pass
        return "\n\n".join(parts) if parts else "(empty spreadsheet)"

    @staticmethod
    def _cell(value: Any) -> str:
        if value is None:
            return ""
        s = str(value).replace("\n", " ").strip()
        return s[:120]

    @staticmethod
    def _normalize_missing(text: str) -> str:
        low = text.lower().strip()
        markers = (
            "i couldn't find that information in the spreadsheet",
            "i could not find that information in the spreadsheet",
            "not found in the spreadsheet",
            "not present in the spreadsheet",
            "doesn't contain",
            "does not contain",
            "no information in the spreadsheet",
            "isn't in the spreadsheet",
            "is not in the spreadsheet",
        )
        if any(m in low for m in markers) and len(text) < 280:
            return _MISSING
        return text

    def _deterministic_fallback(
        self,
        *,
        question: str,
        title: str,
        values_by_sheet: dict[str, list[list[Any]]],
        findings: dict[str, Any] | None,
    ) -> str:
        """Offline / Gemini-down path — still grounded in sheet cells only."""
        from sheets.services.sheet_analyze import SheetAnalyzer

        q = question.lower()
        # Extremely out-of-domain probes
        if re.search(
            r"\b(bitcoin|btc price|who is the president|weather in|capital of france)\b",
            q,
        ):
            return _MISSING

        analyzer = SheetAnalyzer()
        findings = findings or analyzer.analyze(
            title=title,
            values_by_sheet=values_by_sheet,
            content_hash="qa-fallback",
            question=question,
            mode="summary",
        )
        # If structured analysis has nothing useful and question looks specific
        has_data = bool(
            findings.get("metrics")
            or findings.get("holdings")
            or any(values_by_sheet.values())
        )
        if not has_data:
            return _MISSING

        # Keyword scan: if user asks for a token never appearing in sheet → missing
        tokens = [t for t in re.findall(r"[A-Za-z]{4,}", question) if t.lower() not in {
            "what", "which", "when", "where", "that", "this", "with", "from", "have",
            "does", "about", "please", "show", "give", "tell", "much", "many",
            "company", "companies", "metric", "metrics", "revenue", "total",
            "compare", "highest", "lowest", "improved", "biggest", "risks",
            "summarize", "analysis", "financial", "numbers", "unusual",
            "attention", "percentage", "growth", "performed", "simple",
            "terms", "explain", "important", "findings", "should", "would",
            "could", "sheet", "spreadsheet", "data", "main", "most",
        }]
        blob = json.dumps(values_by_sheet, ensure_ascii=False).lower()
        for tok in tokens:
            if tok.lower() not in blob and tok.lower() not in (title or "").lower():
                # Only treat as missing if ALL distinctive tokens are absent
                continue
        missing_all = bool(tokens) and all(
            tok.lower() not in blob and tok.lower() not in (title or "").lower()
            for tok in tokens
        )
        if missing_all and not re.search(
            r"\b(summarize|summary|analyze|findings|stand out|trends?|risks?)\b", q
        ):
            return _MISSING

        return analyzer.format_analyst_reply(findings, question=question)
