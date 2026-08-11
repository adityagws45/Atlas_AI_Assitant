"""Live market-move answers without a Gemini decide+synthesize round-trip."""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from finance.services.finance_service import FinanceService
from finance.utils.ticker_resolve import resolve_symbol, resolve_symbols

logger = logging.getLogger("atlas.conversation.market_fast")

_MOVE = re.compile(
    r"\b("
    r"what'?s happening|whats happening|what is happening|"
    r"why is .{0,40}\b(up|down|moving|rallying|falling|dropping)|"
    r"what'?s moving|whats moving|why .+ moving|"
    r"market update|quick (?:market )?update|"
    r"how is .{0,40}\b(trading|doing) today|"
    r"tell me about .{0,40}\b(price|today|stock|shares?)\b|"
    r"\b(?:nvda|nvidia|amd|aapl|apple|msft|microsoft|tsla|tesla)\b.{0,30}"
    r"\b(?:price|today|trading|doing)\b"
    r")\b",
    re.IGNORECASE,
)
_MARKET_WIDE = re.compile(
    r"\b(market update|quick (?:market )?update|what'?s (?:the )?market|"
    r"how'?s the market)\b",
    re.IGNORECASE,
)
_EXPLAIN = re.compile(
    r"\b(explain|like i'?m a beginner|teach me|what is a |what is an )\b",
    re.IGNORECASE,
)


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


def _news_headlines(news_data: Any, *, limit: int = 3) -> list[str]:
    items: list[Any] = []
    if isinstance(news_data, list):
        items = news_data
    elif isinstance(news_data, dict):
        items = news_data.get("items") or news_data.get("news") or []
    out: list[str] = []
    for item in items[:limit]:
        if isinstance(item, dict):
            title = (item.get("headline") or item.get("title") or "").strip()
        else:
            title = (_attr(item, "headline", "title") or "").strip()
        if title:
            out.append(title[:140])
    return out


def try_market_move_fast_answer(
    text: str,
    *,
    default_symbol: str | None = None,
) -> dict[str, Any] | None:
    """
    Answer 'what's happening with X today' / quick market update using
    finance APIs only — no Gemini decision or synthesis call.
    """
    q = (text or "").strip()
    if not q or _EXPLAIN.search(q):
        return None
    if not _MOVE.search(q):
        return None

    t0 = time.perf_counter()
    finance = FinanceService()
    timings: dict[str, int] = {}

    # Broad market update (no single ticker required)
    if _MARKET_WIDE.search(q) and not resolve_symbol(q):
        t_api = time.perf_counter()
        # News only — avoid slow movers fallbacks on the hot path.
        news = finance.get_market_news(limit=5)
        timings["finance_api"] = int((time.perf_counter() - t_api) * 1000)
        headlines = _news_headlines(news.data if news.ok else [], limit=4)
        lines = ["*Market — quick update*", ""]
        if headlines:
            lines.append("*Drivers*")
            for h in headlines[:4]:
                lines.append(f"• {h}")
            lines.append("")
            lines.append("*Watch*")
            lines.append("• Index direction into the close")
            lines.append("• Next macro / sector catalyst")
        if len(lines) <= 2:
            return None
        timings["total"] = int((time.perf_counter() - t0) * 1000)
        timings["gemini_ms"] = 0
        logger.info("event=market_fast_ok mode=broad timing_ms=%s", timings)
        return {
            "reply": "\n".join(lines).strip(),
            "metadata": {
                "pipeline": "market_fast",
                "used_gemini": False,
                "timing_ms": timings,
            },
        }

    symbol = resolve_symbol(q)
    if not symbol:
        syms = resolve_symbols(q)
        symbol = syms[0] if syms else None
    if not symbol:
        symbol = (default_symbol or "").strip().upper() or None
    if not symbol:
        return None

    t_api = time.perf_counter()
    with ThreadPoolExecutor(max_workers=3) as pool:
        f_quote = pool.submit(finance.get_quote, symbol)
        f_news = pool.submit(finance.get_news, symbol, limit=5)
        f_profile = pool.submit(finance.get_profile, symbol)
        quote = f_quote.result()
        news = f_news.result()
        profile = f_profile.result()
    timings["finance_api"] = int((time.perf_counter() - t_api) * 1000)

    if not quote.ok or not quote.data:
        return None

    price = _attr(quote.data, "price", "c")
    change_pct = _attr(quote.data, "change_percent", "dp")
    name = _attr(profile.data if profile.ok else None, "name") or symbol
    direction = "flat"
    if isinstance(change_pct, (int, float)):
        if change_pct > 0.15:
            direction = "up"
        elif change_pct < -0.15:
            direction = "down"

    move_bit = ""
    if price is not None:
        move_bit = f" around *${float(price):.2f}*"
    if isinstance(change_pct, (int, float)):
        move_bit += f" ({float(change_pct):+.2f}% today)"

    if direction == "up":
        lead = f"*{name}* (*{symbol}*) is moving higher today{move_bit}."
    elif direction == "down":
        lead = f"*{name}* (*{symbol}*) is lower today{move_bit}."
    else:
        lead = f"*{name}* (*{symbol}*) is little-changed today{move_bit}."

    headlines = _news_headlines(news.data if news.ok else [], limit=3)
    lines = [lead, ""]
    if headlines:
        lines.append("*Key drivers*")
        for h in headlines:
            lines.append(f"• {h}")
    else:
        lines.append("*Key drivers*")
        lines.append("• Live tape and sector sentiment (no fresh headline cluster yet)")

    lines.append("")
    lines.append("*Watch*")
    lines.append("• Next catalyst / guidance")
    lines.append("• Valuation vs recent move")

    timings["total"] = int((time.perf_counter() - t0) * 1000)
    timings["gemini_ms"] = 0
    logger.info(
        "event=market_fast_ok symbol=%s timing_ms=%s",
        symbol,
        timings,
    )
    return {
        "reply": "\n".join(lines).strip(),
        "metadata": {
            "pipeline": "market_fast",
            "symbol": symbol,
            "used_gemini": False,
            "timing_ms": timings,
        },
    }
