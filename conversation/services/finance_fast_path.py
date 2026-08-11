"""Deterministic finance lookups — skip Gemini for simple metric questions."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from finance.services.finance_service import FinanceService
from finance.utils.ticker_resolve import resolve_symbol, resolve_symbols

logger = logging.getLogger("atlas.conversation.finance_fast")

_MARKET_CAP = re.compile(
    r"\b(market\s*cap(?:itali[sz]ation)?|mkt\s*cap)\b",
    re.IGNORECASE,
)
_PRICE = re.compile(
    r"\b("
    r"share\s*price|stock\s*price|current\s*price|trading\s*at|quote|"
    r"price\s+today|price\s+now|today'?s\s+price|price\s+of|"
    r"what(?:'s| is) (?:the )?(?:share |stock )?price|"
    r"tell me about .{0,50}\bprice\b|"
    r"how(?:'s| is) .{0,40}\bprice\b|"
    r"\b(?:nvda|nvidia|amd|aapl|apple|msft|microsoft|tsla|tesla|googl|google|"
    r"amzn|amazon|meta)\b.{0,20}\bprice\b|"
    r"\bprice\b.{0,20}\b(?:nvda|nvidia|amd|aapl|apple|msft|microsoft|tsla|tesla)\b"
    r")\b",
    re.IGNORECASE,
)
_PE = re.compile(r"\b(p\s*/\s*e|pe\s*ratio|price[- ]to[- ]earnings)\b", re.IGNORECASE)
_EXPLAIN = re.compile(
    r"\b(explain|what\s+is\s+(a|an|the)\s+|like\s+i'?m\s+a\s+beginner|teach|define)\b",
    re.IGNORECASE,
)


def _fmt_market_cap_millions(value: float | None) -> str | None:
    """Finnhub marketCapitalization is typically in millions of USD."""
    if value is None:
        return None
    v = float(value)
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}T"
    if v >= 1_000:
        return f"${v / 1_000:.2f}B"
    if v >= 1:
        return f"${v:.2f}B"
    return f"${v * 1_000:.0f}M"


def _attr(obj: Any, *names: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        for n in names:
            if obj.get(n) is not None:
                return obj.get(n)
        return None
    for n in names:
        if hasattr(obj, n) and getattr(obj, n) is not None:
            return getattr(obj, n)
    return None


def try_finance_fast_answer(
    text: str,
    *,
    default_symbol: str | None = None,
) -> dict[str, Any] | None:
    """Answer obvious single-metric finance questions without Gemini."""
    q = (text or "").strip()
    if not q or _EXPLAIN.search(q):
        return None
    if re.search(
        r"docs\.google\.com/spreadsheets|sheets\.google\.com|\bgoogle\s+sheets?\b",
        q,
        re.I,
    ):
        return None

    symbol = resolve_symbol(q)
    if not symbol:
        syms = resolve_symbols(q)
        symbol = syms[0] if syms else None
    if not symbol:
        symbol = (default_symbol or "").strip().upper() or None
    if not symbol:
        return None

    want_cap = bool(_MARKET_CAP.search(q))
    want_price = bool(_PRICE.search(q)) and not want_cap
    want_pe = bool(_PE.search(q))

    if not (want_cap or want_price or want_pe):
        return None
    if re.search(r"\b(vs\.?|versus|compare|competitors?)\b", q, re.IGNORECASE):
        return None

    t0 = time.perf_counter()
    finance = FinanceService()
    try:
        if want_cap:
            profile = finance.get_profile(symbol)
            if not profile.ok or not profile.data:
                return None
            data = profile.data
            cap = _attr(data, "market_cap")
            name = _attr(data, "name") or symbol
            formatted = _fmt_market_cap_millions(
                float(cap) if cap is not None else None
            )
            if not formatted:
                return None
            reply = (
                f"{name} ({symbol}) is around {formatted} in market cap right now."
            )
            tool = "company_profile"
        elif want_pe:
            metrics = finance.get_metrics(symbol)
            if not metrics.ok or not metrics.data:
                return None
            data = metrics.data
            pe = _attr(data, "pe", "pe_ratio", "peTTM")
            if pe is None and isinstance(data, dict):
                metric = data.get("metric") or {}
                if isinstance(metric, dict):
                    pe = metric.get("peTTM") or metric.get("peAnnual")
            if pe is None:
                return None
            reply = f"*{symbol}* trades at a P/E of about *{float(pe):.1f}* right now."
            tool = "company_metrics"
        else:
            quote = finance.get_quote(symbol)
            if not quote.ok or not quote.data:
                return None
            data = quote.data
            price = _attr(data, "price", "c", "current")
            change_pct = _attr(data, "change_percent", "dp")
            if price is None:
                return None
            move = ""
            if change_pct is not None:
                move = f" ({float(change_pct):+.2f}% today)"
            reply = f"*{symbol}* is around *${float(price):.2f}*{move}."
            tool = "stock_quote"
    except Exception:
        logger.exception("event=finance_fast_failed symbol=%s", symbol)
        return None

    ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "event=finance_fast_ok symbol=%s tool=%s latency_ms=%s",
        symbol,
        tool,
        ms,
    )
    return {
        "reply": reply,
        "metadata": {
            "pipeline": "finance_fast",
            "tool": tool,
            "symbol": symbol,
            "timing_ms": {"finance_api": ms, "total": ms},
            "used_gemini": False,
        },
    }
