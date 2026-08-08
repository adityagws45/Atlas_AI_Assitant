"""Heuristic metadata extraction from filename + opening text."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from documents.models import DocumentKind

COMPANY_HINTS = {
    "apple": "Apple",
    "aapl": "Apple",
    "microsoft": "Microsoft",
    "msft": "Microsoft",
    "google": "Alphabet",
    "alphabet": "Alphabet",
    "googl": "Alphabet",
    "amazon": "Amazon",
    "amzn": "Amazon",
    "meta": "Meta",
    "nvidia": "Nvidia",
    "nvda": "Nvidia",
    "tesla": "Tesla",
    "tsla": "Tesla",
}

KIND_PATTERNS = [
    (r"\b10[\s\-]?k\b|annual report", DocumentKind.ANNUAL_REPORT),
    (r"\b10[\s\-]?q\b|quarterly report", DocumentKind.QUARTERLY_REPORT),
    (r"earnings call|transcript", DocumentKind.TRANSCRIPT),
    (r"earnings|results", DocumentKind.EARNINGS),
    (r"investor (day )?presentation|slide deck|deck", DocumentKind.PRESENTATION),
    (r"\b8[\s\-]?k\b|sec filing", DocumentKind.SEC_FILING),
]

SECTION_HEADINGS = re.compile(
    r"^(item\s+\d+[A-Z]?[\.:)]?\s+.{3,80}|risk factors|management.?s discussion|"
    r"md&a|business overview|financial statements|liquidity and capital|"
    r"critical accounting|forward[- ]looking|note\s+\d+|"
    r"consolidated statements? of operations)",
    re.IGNORECASE | re.MULTILINE,
)

# Bond maturities / coupon schedules look like years but are not fiscal periods.
_NON_FISCAL_YEAR = re.compile(
    r"(?:notes?|bonds?|debentures?|due|maturing|maturity)\s+(?:on\s+)?"
    r"(?:january|february|march|april|may|june|july|august|september|october|november|december)?\s*"
    r"\d{0,2},?\s*(20[1-3]\d)",
    re.IGNORECASE,
)


def extract_metadata(
    *,
    filename: str,
    text: str,
    page_count: int,
) -> dict[str, Any]:
    name = filename or ""
    opening = (text or "")[:12000]
    blob = f"{name}\n{opening}".lower()

    company = ""
    for key, label in COMPANY_HINTS.items():
        if re.search(rf"\b{re.escape(key)}\b", blob):
            company = label
            break

    kind = DocumentKind.OTHER
    for pattern, value in KIND_PATTERNS:
        if re.search(pattern, blob, re.IGNORECASE):
            kind = value
            break

    year = _resolve_fiscal_year(filename=name, text=opening)
    fiscal = ""
    q = re.search(r"\b(Q[1-4])\s*(20[1-3]\d|\d{2})?\b", blob, re.IGNORECASE)
    fy = re.search(r"\bFY\s*(20[1-3]\d|\d{2})\b", blob, re.IGNORECASE)
    if q:
        fiscal = q.group(0).upper().replace(" ", "")
    elif fy:
        fiscal = fy.group(0).upper().replace(" ", "")
    elif year:
        fiscal = str(year)

    title_bits = []
    if company:
        title_bits.append(company)
    if kind != DocumentKind.OTHER:
        title_bits.append(kind.label if hasattr(kind, "label") else str(kind))
    if fiscal:
        title_bits.append(fiscal)
    nice_title = " — ".join(title_bits) if title_bits else (filename or "Financial document")

    return {
        "company": company,
        "kind": kind.value if hasattr(kind, "value") else str(kind),
        "kind_label": getattr(kind, "label", str(kind)),
        "year": year,
        "fiscal_period": fiscal,
        "page_count": page_count,
        "suggested_title": nice_title[:200],
    }


def _resolve_fiscal_year(*, filename: str, text: str) -> int | None:
    """Pick a plausible reporting year — never bond maturity years like 'Notes due 2031'."""
    this_year = datetime.now().year
    max_plausible = this_year + 1
    min_plausible = 1995

    # 1) Filename is usually authoritative for filed 10-Ks (…-2025-As-Filed.pdf)
    file_years = [int(y) for y in re.findall(r"(20[1-3]\d)", filename or "")]
    file_years = [y for y in file_years if min_plausible <= y <= max_plausible]
    if file_years:
        return max(file_years)

    body = text or ""
    # 2) Explicit fiscal-year phrasing
    for pattern in (
        r"fiscal year ended\s+[a-z]+\s+\d{1,2},?\s*(20[1-3]\d)",
        r"year(?:s)? ended\s+[a-z]+\s+\d{1,2},?\s*(20[1-3]\d)",
        r"for the fiscal year\s*(20[1-3]\d)",
        r"form\s*10[\s\-]?k\s*.{0,40}?(20[1-3]\d)",
        r"\bFY\s*(20[1-3]\d)\b",
    ):
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            y = int(m.group(1))
            if min_plausible <= y <= max_plausible:
                return y

    # 3) Remaining years in the opening, excluding bond/note maturities
    cleaned = _NON_FISCAL_YEAR.sub(" ", body)
    years = [int(y) for y in re.findall(r"\b(20[1-3]\d)\b", cleaned)]
    years = [y for y in years if min_plausible <= y <= max_plausible]
    if not years:
        return None
    # Prefer the most common year; break ties toward recent but not future outliers
    counts: dict[int, int] = {}
    for y in years:
        counts[y] = counts.get(y, 0) + 1
    best = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))[0][0]
    return best


def detect_section(text: str) -> str:
    m = SECTION_HEADINGS.search(text or "")
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(0)).strip()[:120]
