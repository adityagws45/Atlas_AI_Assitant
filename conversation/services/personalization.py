"""Personalized watch / briefing helpers (memory-first, no spam)."""

from __future__ import annotations

import re

from accounts.models import User
from memory.models import UserPreference, Watchlist

SECTOR_SEED_TICKERS: dict[str, list[str]] = {
    "semiconductors": ["NVDA", "AMD", "AVGO", "INTC", "TSM"],
    "ai": ["NVDA", "MSFT", "GOOGL", "AMD", "AVGO"],
    "cloud computing": ["MSFT", "AMZN", "GOOGL"],
    "cloud": ["MSFT", "AMZN", "GOOGL"],
    "technology": ["AAPL", "MSFT", "GOOGL", "NVDA"],
    "fintech": ["SQ", "PYPL", "V", "MA"],
    "financials": ["JPM", "BAC", "GS"],
    "banking": ["JPM", "BAC", "GS", "WFC"],
    "healthcare": ["UNH", "JNJ", "LLY"],
    "biotech": ["MRNA", "REGN", "VRTX"],
    "energy": ["XOM", "CVX"],
    "crypto": ["COIN", "MSTR"],
}

# Longer keys first so "cloud computing" wins over "cloud"
SECTOR_KEYWORD_MAP: list[tuple[str, str]] = [
    ("cloud computing", "cloud computing"),
    ("semiconductor", "semiconductors"),
    ("semiconductors", "semiconductors"),
    ("banking", "banking"),
    ("fintech", "fintech"),
    ("healthcare", "healthcare"),
    ("biotech", "biotech"),
    ("financials", "financials"),
    ("financial", "financials"),
    ("banks", "banking"),
    ("bank", "banking"),
    ("chip", "semiconductors"),
    ("crypto", "crypto"),
    ("energy", "energy"),
    ("cloud", "cloud computing"),
    ("ai", "AI"),
]


def normalize_sector(text: str) -> str | None:
    sectors = infer_sectors_from_text(text)
    return sectors[0] if sectors else None


def infer_sectors_from_text(text: str) -> list[str]:
    lower = (text or "").lower()
    found: list[str] = []
    for key, sector in sorted(SECTOR_KEYWORD_MAP, key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(key)}\b", lower) and sector not in found:
            found.append(sector)
    return found


def seed_tickers_for_sectors(sectors: list[str]) -> list[str]:
    out: list[str] = []
    for sector in sectors:
        key = sector.lower()
        for seed_key, tickers in SECTOR_SEED_TICKERS.items():
            if seed_key in key or key in seed_key:
                for t in tickers:
                    if t not in out:
                        out.append(t)
    return out[:8]


def user_focus_snapshot(user: User) -> dict:
    prefs = UserPreference.objects.filter(user=user).first()
    symbols = list(Watchlist.objects.filter(user=user).values_list("symbol", flat=True)[:10])
    sectors = list((prefs.sectors_of_interest if prefs else []) or [])
    markets = list((prefs.markets_of_interest if prefs else []) or [])
    return {
        "symbols": symbols,
        "sectors": sectors,
        "markets": markets,
        "role": user.role or "",
        "briefing_time": str(prefs.preferred_briefing_time) if prefs and prefs.preferred_briefing_time else "",
    }


def build_watch_today_reply(user: User) -> str:
    snap = user_focus_snapshot(user)
    symbols = list(snap["symbols"])
    sectors = list(snap["sectors"])

    if not symbols and sectors:
        symbols = seed_tickers_for_sectors(sectors)

    if not symbols and not sectors:
        return (
            "I don't have a personal focus list yet.\n\n"
            "Tell me a sector or a few tickers and I'll build today's watch from that."
        )

    lines = ["Based on what you've told me, I'd keep an eye on:\n"]
    if symbols:
        lines.append("• " + "\n• ".join(symbols[:6]))
    themes: list[str] = []
    for s in sectors[:3]:
        themes.append(s)
    if any("semi" in (s or "").lower() or "ai" in (s or "").lower() for s in sectors):
        themes.append("AI infrastructure spending")
        themes.append("Major cloud / hyperscaler earnings")
    # dedupe themes preserving order
    seen = set()
    clean_themes = []
    for t in themes:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            clean_themes.append(t)
    if clean_themes:
        lines.append("")
        lines.append("Themes:")
        for t in clean_themes[:4]:
            lines.append(f"• {t}")
    lines.append("\nWant a deeper dive on any of these?")
    return "\n".join(lines)


def build_tell_me_everything_reply(user: User) -> str:
    snap = user_focus_snapshot(user)
    symbols = list(snap["symbols"]) or seed_tickers_for_sectors(snap["sectors"])
    sectors = list(snap["sectors"])

    parts = ["Here's a tight personal briefing from what I know so far:\n"]
    if sectors:
        parts.append(f"*Focus areas:* {', '.join(sectors[:4])}")
    if symbols:
        parts.append(f"*Names on radar:* {', '.join(symbols[:6])}")
    else:
        parts.append("*Names on radar:* not set yet — tell me a few tickers anytime.")
    parts.append(
        "\n*What I'd check next:*\n"
        "• Moves and news on your core list\n"
        "• Sector headlines in your focus areas\n"
        "• Any major market-moving events today"
    )
    if symbols:
        parts.append(
            f"\nSay the word and I'll start with {symbols[0]} — "
            "or ask for today's market-moving events."
        )
    else:
        parts.append("\nAsk for today's market-moving events, or give me a ticker to research.")
    return "\n".join(parts)


def build_sector_follow_reply(sector: str, seeded: list[str]) -> str:
    names = ", ".join(seeded[:5]) if seeded else "the leaders in that space"
    return (
        f"Great. I'll prioritize {sector} — names like {names} — "
        "in future research and briefings.\n\n"
        "If there are specific companies you care about most, tell me and I'll weight those higher."
    )
