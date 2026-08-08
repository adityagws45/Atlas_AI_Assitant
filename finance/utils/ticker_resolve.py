"""Resolve company names / tickers to symbols."""

from __future__ import annotations

import re

# Shared alias map (kept local to avoid coupling onboarding internals)
COMPANY_ALIASES: dict[str, str] = {
    "nvidia": "NVDA",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "amd": "AMD",
    "intel": "INTC",
    "broadcom": "AVGO",
    "netflix": "NFLX",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "berkshire": "BRK-B",
    "byd": "BYDDF",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "visa": "V",
    "mastercard": "MA",
    "salesforce": "CRM",
    "oracle": "ORCL",
    "adobe": "ADBE",
    "costco": "COST",
    "walmart": "WMT",
    "coca cola": "KO",
    "coca-cola": "KO",
    "disney": "DIS",
    "uber": "UBER",
    "airbnb": "ABNB",
    "spotify": "SPOT",
    "palantir": "PLTR",
    "snowflake": "SNOW",
    "shopify": "SHOP",
}


def normalize_symbol(raw: str) -> str:
    sym = (raw or "").strip().upper().replace(".", "-")
    sym = re.sub(r"[^A-Z0-9\-]", "", sym)
    return sym[:16]


def resolve_symbol(text: str) -> str | None:
    """Resolve a free-text company/ticker mention to a symbol."""
    raw = (text or "").strip()
    if not raw:
        return None

    # Direct ticker
    direct = normalize_symbol(raw)
    if re.fullmatch(r"[A-Z]{1,5}(?:-[A-Z])?", direct):
        return direct

    lower = raw.lower()
    for name, ticker in sorted(COMPANY_ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return ticker

    # Extract ticker-like token from sentence
    for token in re.findall(r"\b[A-Za-z]{1,5}\b", raw):
        up = token.upper()
        if up in {v for v in COMPANY_ALIASES.values()}:
            return up
        if len(up) <= 5 and up.isalpha() and up not in {
            "THE", "AND", "FOR", "WHY", "HOW", "WHAT", "ABOUT", "TELL", "ME",
            "IS", "ARE", "WAS", "A", "AN", "OF", "TO", "ON", "IN",
        }:
            # Prefer known aliases over random words
            continue
    # Last pass: $TICKER
    m = re.search(r"\$([A-Za-z]{1,5})\b", raw)
    if m:
        return normalize_symbol(m.group(1))
    return None


def resolve_symbols(text: str, *, limit: int = 5) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    found: list[str] = []
    lower = raw.lower()
    for name, ticker in sorted(COMPANY_ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(name)}\b", lower) and ticker not in found:
            found.append(ticker)
            if len(found) >= limit:
                return found
    for token in re.findall(r"\$?[A-Za-z]{1,5}\b", raw):
        up = normalize_symbol(token.lstrip("$"))
        if up in COMPANY_ALIASES.values() and up not in found:
            found.append(up)
        if len(found) >= limit:
            break
    # Compare patterns: "AAPL vs MSFT"
    for token in re.findall(r"\b[A-Z]{1,5}\b", raw):
        up = normalize_symbol(token)
        if 1 <= len(up) <= 5 and up not in found and up not in {
            "VS", "AND", "OR", "THE", "CEO", "CFO", "IPO", "USD", "ETF",
        }:
            # Only accept if looks like known or explicit ticker context
            if up in COMPANY_ALIASES.values() or "vs" in lower or "compare" in lower:
                found.append(up)
        if len(found) >= limit:
            break
    return found[:limit]
