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
- If a requested column/metric (e.g. "score") does not exist, say so briefly and
  mention what metrics ARE available.
- Only reply with exactly "I couldn't find that information in the spreadsheet."
  when the sheet is empty or truly has no relevant numbers for the ask.
- Do NOT invent companies, metrics, years, or figures that are not in the data.
- Do NOT use general market knowledge or append stock-market tutorials.
- Do NOT use essay headings (Bottom Line, Why It Matters, Student Lens, etc.).
- Keep the reply Telegram-friendly: concise, short bullets when listing.
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

        # Prefer Python arithmetic for avg / max / min / YoY before Gemini.
        det = self._try_deterministic(
            question=q, title=title, values_by_sheet=values_by_sheet, findings=findings
        )
        if det:
            return {"ok": True, "reply": det, "source": "deterministic_calc"}

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

    def _try_deterministic(
        self,
        *,
        question: str,
        title: str,
        values_by_sheet: dict[str, list[list[Any]]],
        findings: dict[str, Any] | None,
    ) -> str | None:
        from sheets.services.sheet_analyze import SheetAnalyzer

        q = question.lower()
        analyzer = SheetAnalyzer()
        findings = findings or analyzer.analyze(
            title=title,
            values_by_sheet=values_by_sheet,
            content_hash="qa-det",
            question=question,
            mode="summary",
        )
        metrics = findings.get("metrics") or []
        by_name = {str(m.get("name") or "").lower(): m for m in metrics}

        # Explicit missing-column probes (e.g. "average score" on a P&L sheet)
        if re.search(r"\b(score|grades?|gpa)\b", q) and not any(
            "score" in n or "grade" in n for n in by_name
        ):
            available = ", ".join(m.get("name") or "" for m in metrics[:8]) or "none detected"
            return (
                "There's no score column in this spreadsheet. "
                f"Available metrics: {available}."
            )

        def _pick(*names: str):
            for n in names:
                if n in by_name:
                    return by_name[n]
            for key, m in by_name.items():
                if any(n in key for n in names):
                    return m
            return None

        target = None
        label = None
        if "revenue" in q or "sales" in q:
            target = _pick("revenue", "total revenue", "net sales", "sales")
            label = "Revenue"
        elif "gross profit" in q:
            target = _pick("gross profit")
            label = "Gross Profit"
        elif "operating income" in q or "operating profit" in q:
            target = _pick("operating income", "operating profit")
            label = "Operating Income"
        elif "net income" in q or "profit" in q:
            target = _pick("net income", "net profit")
            label = "Net Income"
        elif "eps" in q:
            target = _pick("diluted eps", "eps", "earnings per share")
            label = "EPS"

        if not target or not target.get("series"):
            # Expense / analyze without a clear metric → leave to Gemini/fallback
            if re.search(r"\b(average|avg|mean|highest|lowest|max|min|which year)\b", q):
                # try first numeric metric if question is generic average
                if metrics and re.search(r"\b(average|avg|mean)\b", q) and not label:
                    return None
            return None

        series: dict[str, float] = dict(target["series"])
        years = sorted(series.keys())
        vals = [series[y] for y in years]
        if not vals:
            return None

        if re.search(r"\b(average|avg|mean)\b", q):
            avg = sum(vals) / len(vals)
            return (
                f"Average {label.lower()} across {years[0]}–{years[-1]} "
                f"is *{analyzer._fmt_money(avg)}* "
                f"({len(vals)} years)."
            )
        if re.search(r"\b(yoy|year[- ]over[- ]year|growth)\b", q) and len(years) >= 2:
            y0, y1 = years[-2], years[-1]
            v0, v1 = series[y0], series[y1]
            if v0 and abs(v0) > 1e-9:
                pct = 100.0 * (v1 - v0) / abs(v0)
                return (
                    f"{label} grew *{pct:+.1f}%* from {y0} to {y1} "
                    f"(*{analyzer._fmt_money(v0)}* → *{analyzer._fmt_money(v1)}*)."
                )
        if re.search(r"\b(highest|max|largest|peak)\b", q):
            y = max(years, key=lambda yy: series[yy])
            return f"{label} peaked in *{y}* at *{analyzer._fmt_money(series[y])}*."
        if re.search(r"\b(lowest|min|smallest)\b", q):
            y = min(years, key=lambda yy: series[yy])
            return f"{label} was lowest in *{y}* at *{analyzer._fmt_money(series[y])}*."
        if re.search(r"\b(which year|what year)\b", q) and "high" in q:
            y = max(years, key=lambda yy: series[yy])
            return f"*{y}* — {label.lower()} *{analyzer._fmt_money(series[y])}*."
        return None

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
