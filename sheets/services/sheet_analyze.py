"""Financial analysis over detected spreadsheet data — analyst narrative, not row dumps."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from django.conf import settings
from django.core.cache import cache

from sheets.services.sheet_detect import detect_workbook, parse_number, to_records

logger = logging.getLogger("atlas.sheets.analyze")

ANALYSIS_TTL = int(getattr(settings, "CACHE_TTL_SHEET_ANALYSIS", 300) or 300)


class SheetAnalyzer:
    """Produce structured findings for Gemini / Telegram formatting."""

    def analyze(
        self,
        *,
        title: str,
        values_by_sheet: dict[str, list[list[Any]]],
        content_hash: str,
        question: str = "",
        mode: str = "summary",
    ) -> dict[str, Any]:
        cache_key = (
            "sheets:analysis:"
            + hashlib.sha1(f"{content_hash}|{mode}|{question[:120]}".encode()).hexdigest()
        )
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            return cached

        detected = detect_workbook(values_by_sheet)
        holdings = self._extract_holdings(values_by_sheet, detected)
        monthly = self._extract_timeseries(values_by_sheet, detected)
        metrics = self._extract_financial_metrics(values_by_sheet, detected)

        findings: dict[str, Any] = {
            "title": title,
            "kind": detected.get("primary_kind") or "other",
            "detected": detected,
            "mode": mode,
            "question": question,
            "holdings_count": len(holdings),
            "holdings": holdings[:25],
            "metrics": metrics,
            "sectors": self._sector_weights(holdings),
            "best": [],
            "worst": [],
            "total_value": None,
            "total_pl_pct": None,
            "timeseries": monthly[-6:],
            "outliers": [],
            "risks": [],
            "recommendations": [],
            "changes": {},
        }

        if metrics:
            findings["risks"] = self._financial_risks(metrics)
            findings["recommendations"] = [
                "Ask which metric improved most, or dig into a specific year.",
                "Compare revenue vs net income trajectory across years.",
            ]
            # Best metric by YoY improvement (latest vs prior year)
            improved = []
            for m in metrics:
                series = m.get("series") or {}
                years = sorted(series.keys())
                if len(years) >= 2:
                    y0, y1 = years[-2], years[-1]
                    v0, v1 = series[y0], series[y1]
                    if v0 and abs(v0) > 1e-9:
                        improved.append(
                            {
                                "metric": m.get("name"),
                                "from_year": y0,
                                "to_year": y1,
                                "from": v0,
                                "to": v1,
                                "delta_pct": round(100.0 * (v1 - v0) / abs(v0), 2),
                            }
                        )
            improved.sort(key=lambda x: float(x.get("delta_pct") or 0), reverse=True)
            findings["metric_improvements"] = improved
            if improved:
                findings["best"] = [
                    {
                        "ticker": improved[0]["metric"],
                        "pl_pct": improved[0]["delta_pct"],
                        "company": f"{improved[0]['from_year']}→{improved[0]['to_year']}",
                    }
                ]

        if holdings:
            valued = [h for h in holdings if h.get("value") is not None]
            if valued:
                total = sum(float(h["value"]) for h in valued)
                findings["total_value"] = round(total, 2)
                for h in valued:
                    h["weight_calc"] = round(100.0 * float(h["value"]) / total, 2) if total else 0.0
            by_pl = sorted(
                [
                    h
                    for h in holdings
                    if h.get("pl_pct") is not None
                    and (h.get("ticker") or "").upper() != "CASH"
                ],
                key=lambda x: float(x["pl_pct"]),
                reverse=True,
            )
            findings["best"] = by_pl[:3]
            findings["worst"] = list(reversed(by_pl[-3:])) if by_pl else []
            if by_pl:
                findings["total_pl_pct"] = round(
                    sum(float(h["pl_pct"]) for h in by_pl) / len(by_pl), 2
                )
            findings["outliers"] = self._outliers(holdings)
            findings["risks"] = self._risks(holdings, findings["sectors"])
            findings["recommendations"] = self._recommendations(holdings, findings["sectors"])

        if monthly and len(monthly) >= 2:
            first, last = monthly[0], monthly[-1]
            findings["changes"] = {
                "from": first.get("period"),
                "to": last.get("period"),
                "value_from": first.get("value"),
                "value_to": last.get("value"),
                "return_last": last.get("return_pct"),
            }

        # Mode-specific focus flags
        findings["focus"] = mode
        cache.set(cache_key, findings, ANALYSIS_TTL)
        return findings

    def format_analyst_reply(self, findings: dict[str, Any], *, question: str = "") -> str:
        """Deterministic analyst reply (works offline / under Gemini pressure)."""
        title = findings.get("title") or "your spreadsheet"
        kind = findings.get("kind") or "other"
        mode = findings.get("mode") or "summary"
        focus = findings.get("focus") or mode
        q = (question or findings.get("question") or "").lower()

        # Financial statement / metric sheets (any company)
        if findings.get("metrics") and (
            findings.get("kind") == "financials"
            or any(k in q for k in ("revenue", "income", "metric", "2023", "2024", "2025", "fy"))
        ):
            return self._format_financials(findings, title, question=q)

        # Narrow modes: lead with the asked question, not a full dump
        if focus == "best" or (mode == "best"):
            if findings.get("metric_improvements"):
                return self._format_financials(findings, title, question=q or "improved")
            return self._format_performers(findings, title, winners=True)
        if focus == "worst" or mode == "worst":
            return self._format_performers(findings, title, winners=False)
        if focus == "risks":
            return self._format_risks_only(findings, title)
        if focus == "recs":
            return self._format_recs_only(findings, title)
        if focus == "trends" or "changed" in q:
            return self._format_trends(findings, title)

        lines: list[str] = []
        lines.append(f"*Summary*\nHere's my read on *{title}*")
        if kind == "portfolio" and findings.get("total_value") is not None:
            lines[0] += (
                f" — portfolio value about *{self._fmt_money(findings['total_value'])}* "
                f"across {findings.get('holdings_count', 0)} holdings."
            )
        elif kind == "watchlist":
            lines[0] += f" — {findings.get('holdings_count', 0)} names on the list."
        elif kind == "financials" and findings.get("metrics"):
            lines[0] += f" — {len(findings['metrics'])} metrics across years."
        else:
            lines[0] += "."

        lines.append("")
        lines.append("*Key Findings*")
        if findings.get("metrics") and not findings.get("holdings"):
            for m in (findings.get("metrics") or [])[:5]:
                series = m.get("series") or {}
                bits = ", ".join(f"{y}: {self._fmt_money(v)}" for y, v in list(series.items())[-3:])
                lines.append(f"• {m.get('name')}: {bits}")
        if mode in {"portfolio", "summary", "statistics", "analysis"} or "allocation" in q:
            sectors = findings.get("sectors") or {}
            if sectors:
                top = sorted(sectors.items(), key=lambda x: -x[1])[:4]
                alloc = ", ".join(f"{s} {w:.0f}%" for s, w in top)
                lines.append(f"• Sector mix: {alloc}")
            if findings.get("total_value") is not None:
                lines.append(f"• Total value: {self._fmt_money(findings['total_value'])}")

        if mode in {"portfolio", "summary", "analysis"} or "best" in q or "perform" in q:
            for h in findings.get("best") or []:
                lines.append(
                    f"• Leader: {h.get('ticker')} "
                    f"({self._fmt_pct(h.get('pl_pct'))} P/L)"
                )
        if mode in {"portfolio", "summary", "analysis"} or "worst" in q or "concern" in q:
            for h in findings.get("worst") or []:
                lines.append(
                    f"• Laggard: {h.get('ticker')} "
                    f"({self._fmt_pct(h.get('pl_pct'))} P/L)"
                )

        if mode in {"trends", "compare", "summary"} or "changed" in q or "trend" in q:
            ch = findings.get("changes") or {}
            if ch.get("value_from") is not None and ch.get("value_to") is not None:
                lines.append(
                    f"• Trend: {ch.get('from')} → {ch.get('to')}: "
                    f"{self._fmt_money(ch['value_from'])} to {self._fmt_money(ch['value_to'])}"
                    + (
                        f" (last month {self._fmt_pct(ch.get('return_last'))})"
                        if ch.get("return_last") is not None
                        else ""
                    )
                )

        if mode in {"outliers", "analysis"} or "unusual" in q:
            for o in findings.get("outliers") or []:
                lines.append(f"• Outlier: {o}")

        if len(lines) <= 3:
            lines.append("• Structure looks readable; ask about allocation, performers, or risks.")

        lines.append("")
        lines.append("*Why It Matters*")
        if kind == "portfolio":
            lines.append(
                "Concentration and P/L dispersion drive both upside capture and drawdown risk — "
                "this is where rebalancing decisions start."
            )
        else:
            lines.append(
                "Seeing the shape of the data lets us prioritize follow-ups instead of scrolling cells."
            )

        lines.append("")
        lines.append("*Risks*")
        risks = findings.get("risks") or ["No acute red flags from the numbers alone."]
        for r in risks[:4]:
            lines.append(f"• {r}")

        lines.append("")
        lines.append("*Recommended Next Steps*")
        recs = findings.get("recommendations") or [
            "Ask what changed this month, or which names concern me most."
        ]
        for r in recs[:4]:
            lines.append(f"• {r}")

        return "\n".join(lines)

    def _format_performers(self, findings: dict, title: str, *, winners: bool) -> str:
        rows = findings.get("best") if winners else findings.get("worst")
        label = "performing best" if winners else "that concern me"
        lines = [
            f"*Summary*\nIn *{title}*, here are the holdings {label}.",
            "",
            "*Key Findings*",
        ]
        for h in rows or []:
            lines.append(
                f"• {h.get('ticker')} — {self._fmt_pct(h.get('pl_pct'))} P/L"
                + (
                    f", ~{h.get('weight') or h.get('weight_calc') or '?'}% weight"
                    if (h.get("weight") or h.get("weight_calc"))
                    else ""
                )
            )
        if len(lines) <= 3:
            lines.append("• Not enough P/L columns to rank performers yet.")
        lines.extend(
            [
                "",
                "*Why It Matters*",
                "Relative P/L shows where conviction paid off vs where the book is dragging.",
                "",
                "*Risks*",
                "• Chasing winners or averaging losers without a thesis both create silent risk.",
                "",
                "*Recommended Next Steps*",
                "• Ask what to rebalance, or dig into a single ticker.",
            ]
        )
        return "\n".join(lines)

    def _format_risks_only(self, findings: dict, title: str) -> str:
        risks = findings.get("risks") or ["No acute red flags from the numbers alone."]
        outliers = findings.get("outliers") or []
        lines = [
            f"*Summary*\nBiggest risks I see in *{title}*.",
            "",
            "*Key Findings*",
        ]
        for r in risks[:4]:
            lines.append(f"• {r}")
        for o in outliers[:2]:
            lines.append(f"• {o}")
        lines.extend(
            [
                "",
                "*Why It Matters*",
                "Risks here are mostly concentration and correlation — they hit hardest in a risk-off tape.",
                "",
                "*Risks*",
            ]
        )
        for r in risks[:3]:
            lines.append(f"• {r}")
        lines.extend(
            [
                "",
                "*Recommended Next Steps*",
                "• Ask for rebalance ideas or which sector to trim first.",
            ]
        )
        return "\n".join(lines)

    def _format_recs_only(self, findings: dict, title: str) -> str:
        recs = findings.get("recommendations") or [
            "Ask what changed this month, or which names concern me most."
        ]
        lines = [
            f"*Summary*\nSuggested improvements for *{title}*.",
            "",
            "*Key Findings*",
        ]
        sectors = findings.get("sectors") or {}
        if sectors:
            top = next(iter(sectors.items()))
            lines.append(f"• Heaviest sleeve: {top[0]} (~{top[1]:.0f}%)")
        lines.extend(
            [
                "",
                "*Why It Matters*",
                "Small allocation shifts often matter more than picking a new name.",
                "",
                "*Risks*",
            ]
        )
        for r in (findings.get("risks") or ["Watch concentration."])[:2]:
            lines.append(f"• {r}")
        lines.append("")
        lines.append("*Recommended Next Steps*")
        for r in recs[:4]:
            lines.append(f"• {r}")
        return "\n".join(lines)

    def _format_trends(self, findings: dict, title: str) -> str:
        ch = findings.get("changes") or {}
        lines = [
            f"*Summary*\nWhat changed in *{title}*.",
            "",
            "*Key Findings*",
        ]
        if ch.get("value_from") is not None and ch.get("value_to") is not None:
            lines.append(
                f"• {ch.get('from')} → {ch.get('to')}: "
                f"{self._fmt_money(ch['value_from'])} to {self._fmt_money(ch['value_to'])}"
            )
            if ch.get("return_last") is not None:
                lines.append(f"• Latest monthly return: {self._fmt_pct(ch.get('return_last'))}")
        else:
            lines.append("• No monthly time-series tab yet — add months if you want trend reads.")
        lines.extend(
            [
                "",
                "*Why It Matters*",
                "Path of value matters as much as the snapshot — it shows whether risk is paying.",
                "",
                "*Risks*",
                "• Short windows can overstate momentum; don’t overfit one good month.",
                "",
                "*Recommended Next Steps*",
                "• Ask which holdings drove the move, or compare to a prior sheet.",
            ]
        )
        return "\n".join(lines)

    def _extract_holdings(
        self, values_by_sheet: dict[str, list[list[Any]]], detected: dict[str, Any]
    ) -> list[dict[str, Any]]:
        tabs = detected.get("tabs") or []
        target = None
        for t in tabs:
            if t.get("kind") in {"portfolio", "watchlist"}:
                target = t
                break
        if not target and tabs:
            target = tabs[0]
        if not target:
            return []
        name = target.get("sheet_name")
        rows = values_by_sheet.get(name) or []
        headers, records = to_records(rows)
        cols = target.get("columns") or {}
        out: list[dict[str, Any]] = []
        for rec in records:
            ticker = str(rec.get(cols.get("ticker") or "", "") or "").strip().upper()
            if not ticker and headers:
                ticker = str(rec.get(headers[0], "")).strip().upper()
            if not ticker or ticker in {"TOTAL", "CASH"} and False:
                pass
            if not ticker:
                continue
            company = str(rec.get(cols.get("company") or "", "") or "").strip()
            sector = str(rec.get(cols.get("sector") or "", "") or "").strip() or "Other"
            value = parse_number(rec.get(cols.get("value") or ""))
            shares = parse_number(rec.get(cols.get("shares") or ""))
            price = parse_number(rec.get(cols.get("price") or ""))
            cost = parse_number(rec.get(cols.get("cost") or ""))
            pl = parse_number(rec.get(cols.get("pl") or ""))
            weight = parse_number(rec.get(cols.get("weight") or ""))
            if value is None and shares is not None and price is not None:
                value = shares * price
            if pl is None and cost and price and cost != 0:
                pl = ((price - cost) / cost) * 100.0
            out.append(
                {
                    "ticker": ticker,
                    "company": company,
                    "sector": sector,
                    "shares": shares,
                    "price": price,
                    "cost": cost,
                    "value": value,
                    "pl_pct": pl,
                    "weight": weight,
                }
            )
        return out

    def _extract_timeseries(
        self, values_by_sheet: dict[str, list[list[Any]]], detected: dict[str, Any]
    ) -> list[dict[str, Any]]:
        for t in detected.get("tabs") or []:
            if t.get("kind") != "timeseries":
                continue
            name = t.get("sheet_name")
            rows = values_by_sheet.get(name) or []
            _, records = to_records(rows)
            cols = t.get("columns") or {}
            series = []
            for rec in records:
                period = str(rec.get(cols.get("date") or "", "") or "").strip()
                if not period and rec:
                    period = str(next(iter(rec.values()), "")).strip()
                value = parse_number(rec.get(cols.get("value") or ""))
                # fallback: second numeric-looking column
                if value is None:
                    for k, v in rec.items():
                        if k == cols.get("date"):
                            continue
                        value = parse_number(v)
                        if value is not None:
                            break
                ret = parse_number(rec.get(cols.get("pl") or ""))
                series.append({"period": period, "value": value, "return_pct": ret})
            return series
        return []

    def _sector_weights(self, holdings: list[dict[str, Any]]) -> dict[str, float]:
        valued = [h for h in holdings if h.get("value") is not None]
        total = sum(float(h["value"]) for h in valued) or 0.0
        if not total:
            return {}
        acc: dict[str, float] = {}
        for h in valued:
            sec = h.get("sector") or "Other"
            acc[sec] = acc.get(sec, 0.0) + 100.0 * float(h["value"]) / total
        return {k: round(v, 1) for k, v in sorted(acc.items(), key=lambda x: -x[1])}

    def _outliers(self, holdings: list[dict[str, Any]]) -> list[str]:
        out = []
        for h in holdings:
            pl = h.get("pl_pct")
            w = h.get("weight") or h.get("weight_calc")
            if pl is not None and abs(float(pl)) >= 80:
                out.append(f"{h.get('ticker')} P/L at {self._fmt_pct(pl)} looks extreme vs peers")
            if w is not None and float(w) >= 30:
                out.append(f"{h.get('ticker')} is a heavy weight (~{float(w):.0f}%)")
        return out[:5]

    def _risks(self, holdings: list[dict[str, Any]], sectors: dict[str, float]) -> list[str]:
        risks = []
        if sectors:
            top_s, top_w = next(iter(sectors.items()))
            if top_w >= 45:
                risks.append(f"Heavy {top_s} concentration (~{top_w:.0f}%) amplifies sector shocks.")
        techish = sum(v for k, v in sectors.items() if k.lower() in {"technology", "semiconductors", "ai", "cloud"})
        if techish >= 55:
            risks.append("Tech/semiconductor tilt is high — correlated drawdowns are more likely.")
        if any((h.get("ticker") or "").upper() == "CASH" for h in holdings):
            cash = next((h for h in holdings if (h.get("ticker") or "").upper() == "CASH"), None)
            if cash and (cash.get("weight") or cash.get("weight_calc") or 0) < 3:
                risks.append("Cash buffer looks thin if you need dry powder for dips.")
        if not risks:
            risks.append("No single-name red flag dominates — watch concentration and momentum extremes.")
        return risks

    def _recommendations(
        self, holdings: list[dict[str, Any]], sectors: dict[str, float]
    ) -> list[str]:
        recs = []
        if sectors:
            top_s, top_w = next(iter(sectors.items()))
            if top_w >= 40:
                recs.append(f"Consider trimming {top_s} exposure on strength to fund underweights.")
        worst = sorted(
            [h for h in holdings if h.get("pl_pct") is not None],
            key=lambda x: float(x["pl_pct"]),
        )[:1]
        if worst:
            recs.append(
                f"Review the thesis on {worst[0].get('ticker')} — "
                "either add on conviction or cut the drag."
            )
        recs.append("Ask me what changed this month, or compare to a prior snapshot when you have one.")
        return recs[:4]

    def _extract_financial_metrics(
        self, values_by_sheet: dict[str, list[list[Any]]], detected: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Parse Metric × Year tables dynamically (any company / dataset)."""
        import re

        out: list[dict[str, Any]] = []
        for t in detected.get("tabs") or []:
            if t.get("kind") not in {"financials", "other"}:
                continue
            name = t.get("sheet_name")
            rows = values_by_sheet.get(name) or []
            if not rows or len(rows) < 2:
                continue
            headers = [str(c).strip() for c in rows[0]]
            year_cols: list[tuple[int, str]] = []
            for i, h in enumerate(headers):
                m = re.search(r"(20\d{2})", h)
                if m:
                    year_cols.append((i, m.group(1)))
            if len(year_cols) < 2:
                continue
            for row in rows[1:]:
                if not row:
                    continue
                metric = str(row[0]).strip()
                if not metric or metric.lower() in {"metric", "line item", "item"}:
                    continue
                series: dict[str, float] = {}
                for idx, year in year_cols:
                    if idx < len(row):
                        val = parse_number(row[idx])
                        if val is not None:
                            series[year] = val
                if series:
                    out.append({"name": metric, "series": series, "sheet": name})
        return out

    def _financial_risks(self, metrics: list[dict[str, Any]]) -> list[str]:
        risks = []
        by_name = {str(m.get("name") or "").lower(): m for m in metrics}
        rev = by_name.get("revenue") or by_name.get("total revenue") or by_name.get("net sales")
        ni = by_name.get("net income")
        if rev and ni:
            years = sorted(set(rev.get("series", {})) & set(ni.get("series", {})))
            if years:
                y = years[-1]
                r, n = rev["series"][y], ni["series"][y]
                if r and n / r < 0.05:
                    risks.append(f"Net margin looks thin in {y} relative to revenue.")
        if not risks:
            risks.append("Watch year-over-year volatility in the key P&L lines.")
        return risks

    def _format_financials(self, findings: dict, title: str, *, question: str = "") -> str:
        q = (question or "").lower()
        metrics = findings.get("metrics") or []
        by_name = {str(m.get("name") or "").lower(): m for m in metrics}
        lines = [f"*Summary*\nRead of *{title}* from the active sheet.", "", "*Key Findings*"]

        # Direct revenue / income questions
        year_m = None
        import re

        ym = re.search(r"(20\d{2})", q)
        if ym:
            year_m = ym.group(1)

        def _pick(*names: str):
            for n in names:
                if n in by_name:
                    return by_name[n]
            for key, m in by_name.items():
                if any(n in key for n in names):
                    return m
            return None

        if "revenue" in q:
            m = _pick("revenue", "total revenue", "net sales")
            if m:
                series = m.get("series") or {}
                if year_m and year_m in series:
                    lines.append(f"• Revenue in {year_m}: {self._fmt_money(series[year_m])}")
                else:
                    bits = ", ".join(f"{y}: {self._fmt_money(v)}" for y, v in series.items())
                    lines.append(f"• Revenue — {bits}")
        if "income" in q or "earnings" in q:
            m = _pick("net income", "operating income")
            if m:
                series = m.get("series") or {}
                if year_m and year_m in series:
                    lines.append(f"• {m.get('name')} in {year_m}: {self._fmt_money(series[year_m])}")
                else:
                    bits = ", ".join(f"{y}: {self._fmt_money(v)}" for y, v in series.items())
                    lines.append(f"• {m.get('name')} — {bits}")

        if "improv" in q or "most" in q or findings.get("mode") == "best":
            imps = findings.get("metric_improvements") or []
            if imps:
                top = imps[0]
                lines.append(
                    f"• Biggest improvement: *{top['metric']}* "
                    f"({top['from_year']}→{top['to_year']}: {self._fmt_pct(top['delta_pct'])})"
                )
                for row in imps[1:3]:
                    lines.append(
                        f"• {row['metric']}: {self._fmt_pct(row['delta_pct'])} "
                        f"({row['from_year']}→{row['to_year']})"
                    )

        if len(lines) <= 3:
            for m in metrics[:5]:
                series = m.get("series") or {}
                bits = ", ".join(f"{y}: {self._fmt_money(v)}" for y, v in list(series.items())[-3:])
                lines.append(f"• {m.get('name')}: {bits}")

        lines.extend(
            [
                "",
                "*Why It Matters*",
                "These figures come from the sheet you connected — not a default watchlist.",
                "",
                "*Risks*",
            ]
        )
        for r in (findings.get("risks") or ["Review YoY swings before extrapolating."])[:3]:
            lines.append(f"• {r}")
        lines.extend(
            [
                "",
                "*Recommended Next Steps*",
                "• Ask about another year, which metric improved most, or paste a different Sheet URL.",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _fmt_money(v: Any) -> str:
        try:
            return f"${float(v):,.0f}"
        except (TypeError, ValueError):
            return str(v)

    @staticmethod
    def _fmt_pct(v: Any) -> str:
        try:
            return f"{float(v):+.1f}%"
        except (TypeError, ValueError):
            return "n/a"
