"""Natural-language Sheets intents — never expose API jargon.

Supports ANY Google Sheets URL and follow-ups against the active sheet.
Not tied to a specific company or demo workbook.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# https://docs.google.com/spreadsheets/d/<ID>/edit...
# also pubhtml / export / copy links
SHEETS_URL_RE = re.compile(
    r"(?:https?://)?(?:docs\.google\.com/spreadsheets/d/|sheets\.google\.com/spreadsheet/ccc\?[^\s]*key=)"
    r"([a-zA-Z0-9\-_]{20,})",
    re.IGNORECASE,
)
# Bare spreadsheet IDs are too ambiguous — only accept URL forms.

CONNECT = re.compile(
    r"\b("
    r"connect( my)? (google )?sheets?|link( my)? (google )?sheets?|"
    r"enable( my)? (google )?sheets?|set up( my)? (google )?sheets?"
    r")\b",
    re.IGNORECASE,
)

LIST = re.compile(
    r"\b("
    r"show( me)?( my)? (spreadsheets?|sheets?|workbooks?)|"
    r"list( my)? (spreadsheets?|sheets?)|"
    r"what (spreadsheets?|sheets?) do i have|"
    r"my (spreadsheets?|sheets?)"
    r")\b",
    re.IGNORECASE,
)

OPEN = re.compile(
    r"\b("
    r"open( my)? (portfolio|spreadsheet|sheet|watchlist)|"
    r"load( my)? (portfolio|spreadsheet|sheet)|"
    r"analyze( this| my)? (spreadsheet|sheet|portfolio)|"
    r"use( my)? (portfolio|spreadsheet)"
    r")\b",
    re.IGNORECASE,
)

SUMMARY = re.compile(
    r"\b("
    r"summarize( my)? (portfolio|spreadsheet|sheet)|"
    r"portfolio summary|sheet summary|"
    r"what stands out|total portfolio value|calculate total|"
    r"analyze this sheet|analyze the sheet|analyze that sheet|"
    r"analyze this one|analyze that one|analyze it"
    r")\b",
    re.IGNORECASE,
)

ALLOCATION = re.compile(
    r"\b("
    r"allocation|overweight|sector(s)? (am i|i'?m)|"
    r"which sectors|portfolio allocation|weights?"
    r")\b",
    re.IGNORECASE,
)

BEST = re.compile(
    r"\b(best|top|leading|outperform|performing best|winners?|"
    r"improved the most|biggest (increase|improvement|gain))\b",
    re.IGNORECASE,
)

WORST = re.compile(
    r"\b(worst|laggard|concern|underperform|performing worst|losers?)\b",
    re.IGNORECASE,
)

RISKS = re.compile(
    r"\b(biggest risks?|what risks?|risk (in|for) (my )?portfolio|"
    r"what are the (biggest )?risks)\b",
    re.IGNORECASE,
)

RECS = re.compile(
    r"\b("
    r"recommend|rebalance|improve(ments)?|what should i (do|trim|add)|"
    r"next steps|portfolio improvements?"
    r")\b",
    re.IGNORECASE,
)

CHANGED = re.compile(
    r"\b(what changed|this month|vs last month|compare .{0,20}month|trends?)\b",
    re.IGNORECASE,
)

OUTLIERS = re.compile(
    r"\b(unusual|outlier|anomal|strange transaction)\b",
    re.IGNORECASE,
)

ABOUT_TICKER = re.compile(
    r"\bwhat about\s+([A-Za-z][A-Za-z0-9.\- ]{0,40}?)\??$|"
    r"\b(how is|how'?s)\s+([A-Za-z][A-Za-z0-9.\-]{0,40})\b",
    re.IGNORECASE,
)

CHARTS = re.compile(
    r"\b(what charts?|which charts?|chart suggestions?)\b",
    re.IGNORECASE,
)

# Domains that must NOT be stolen by active-sheet catch-all
OTHER_DOMAIN = re.compile(
    r"\b("
    r"schedule|remind me|calendar|when am i free|block \d|"
    r"meetings? (today|tomorrow)|my (calendar|schedule)|"
    r"next meeting|conflicts?|free (time|slot|hour)|"
    r"(any|do i have)( any)? tasks?|"
    r"check (my )?(email|inbox|gmail)|my (emails?|inbox)|"
    r"(latest|recent|unread) (e)?mails?|find (e)?mails?|emails? (from|about)|"
    r"summarize( (my|the|these|those))? (e)?mails?|anything important|"
    r"finance (e)?mails?|earnings[- ]related|investment alerts?|"
    r"inbox|gmail|"
    r"connect (my )?drive|my (drive|files)|upload (a |the )?pdf|"
    r"/start"
    r")\b",
    re.IGNORECASE,
)

# Follow-ups that should stick to the ACTIVE sheet (any dataset)
ACTIVE_FOLLOWUP = re.compile(
    r"\b("
    r"analyze( this| the| that)? (sheet|one|it)|"
    r"(quick )?analysis|"
    r"this sheet|the sheet|that sheet|this one|that one|"
    r"revenue|net income|earnings|metric|metrics|"
    r"which metric|improved the most|year[- ]?over[- ]?year|"
    r"in 20\d{2}|for 20\d{2}|fy\s*20\d{2}|"
    r"what stands out|summarize|biggest risks?|"
    r"total|allocation|holdings?|portfolio|"
    r"how (did|does|is|many|much)|what (was|were|is|are)|"
    r"which|who|listed|count|average|sum of|"
    r"main (financial )?trends?|financial trends?|"
    r"why\??|compare|percentage|unusual|explain|findings|"
    r"performed|growth|company|companies|students?|"
    r"pay attention|quick analysis"
    r")\b",
    re.IGNORECASE,
)

# Short follow-ups that look like sheet data questions (not schedule/tasks)
_SHEET_DATA_QUESTION = re.compile(
    r"\b("
    r"how many|how much|what|which|who|when|list|show|total|count|"
    r"average|sum|summarize|analyze|compare|explain"
    r")\b",
    re.IGNORECASE,
)
_NOT_SHEET_FOLLOWUP = re.compile(
    r"\b("
    r"tasks?|to-?dos?|reminders?|meetings?|calendar|schedule|"
    r"email|inbox|gmail|(e)?mails?|stock price|share price"
    r")\b",
    re.IGNORECASE,
)

COMPANY_TO_TICKER = {
    "microsoft": "MSFT",
    "apple": "AAPL",
    "nvidia": "NVDA",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "exxon": "XOM",
}


@dataclass
class SheetIntent:
    kind: str  # connect|list|open|open_url|analyze|none
    mode: str = "summary"
    query: str = ""


def extract_spreadsheet_id(text: str) -> str | None:
    """Pull a Google Sheets spreadsheet ID from a pasted URL, if present."""
    raw = (text or "").strip()
    if not raw:
        return None
    m = SHEETS_URL_RE.search(raw)
    if not m:
        return None
    return m.group(1)


def detect_sheet_intent(
    text: str,
    *,
    has_active_sheet: bool = False,
) -> SheetIntent:
    raw = (text or "").strip()
    if not raw:
        return SheetIntent(kind="none")

    # URL always wins — any spreadsheet, any company/dataset
    sheet_id = extract_spreadsheet_id(raw)
    if sheet_id:
        return SheetIntent(kind="open_url", mode="summary", query=sheet_id)

    # Scheduling language belongs to Calendar (M9), not spreadsheet analysis
    if OTHER_DOMAIN.search(raw) or re.search(
        r"\b(schedule|remind me|find time for|block \d|block two hours|when am i free)\b",
        raw,
        re.IGNORECASE,
    ):
        return SheetIntent(kind="none")
    if CONNECT.search(raw):
        return SheetIntent(kind="connect")
    if LIST.search(raw):
        return SheetIntent(kind="list")

    m = ABOUT_TICKER.search(raw)
    if m and not has_active_sheet:
        raw_name = next(
            (g for g in m.groups() if g and g.lower() not in {"how is", "how's", "hows"}),
            "",
        ) or ""
        key = raw_name.strip().lower()
        ticker = COMPANY_TO_TICKER.get(key)
        if not ticker and len(key) <= 5 and key.isalpha():
            ticker = key.upper()
        if ticker:
            return SheetIntent(kind="analyze", mode="ticker", query=ticker)

    if CHANGED.search(raw):
        return SheetIntent(kind="analyze", mode="trends" if not has_active_sheet else "qa", query=raw)
    if OUTLIERS.search(raw):
        return SheetIntent(kind="analyze", mode="qa", query=raw)
    if RISKS.search(raw):
        return SheetIntent(kind="analyze", mode="qa" if has_active_sheet else "risks", query=raw)
    if RECS.search(raw) or CHARTS.search(raw):
        return SheetIntent(kind="analyze", mode="qa" if has_active_sheet else "recs", query=raw)
    if ALLOCATION.search(raw):
        return SheetIntent(kind="analyze", mode="qa" if has_active_sheet else "portfolio", query=raw)
    if BEST.search(raw) and (
        any(x in raw.lower() for x in ("holding", "portfolio", "perform", "metric", "company"))
        or has_active_sheet
    ):
        return SheetIntent(kind="analyze", mode="qa" if has_active_sheet else "best", query=raw)
    if WORST.search(raw) and (
        any(x in raw.lower() for x in ("holding", "portfolio", "concern", "perform", "company"))
        or has_active_sheet
    ):
        return SheetIntent(kind="analyze", mode="qa" if has_active_sheet else "worst", query=raw)
    if SUMMARY.search(raw):
        return SheetIntent(kind="analyze", mode="qa" if has_active_sheet else "summary", query=raw)
    if OPEN.search(raw) or re.search(r"\b(my portfolio|open portfolio)\b", raw, re.I):
        if has_active_sheet and re.search(r"\banalyze\b", raw, re.I):
            return SheetIntent(kind="analyze", mode="qa", query=raw)
        q = "watchlist" if "watchlist" in raw.lower() else "portfolio"
        return SheetIntent(kind="open", mode="summary", query=q)
    if re.search(r"\b(spreadsheet|portfolio tracker|holdings sheet)\b", raw, re.I) and re.search(
        r"\b(analyze|summarize|review|look at)\b", raw, re.I
    ):
        if has_active_sheet:
            return SheetIntent(kind="analyze", mode="qa", query=raw)
        return SheetIntent(kind="open", mode="analysis", query=raw)

    # Stick to the active sheet for clear sheet/data follow-ups — never merely
    # because the message is short, and never for calendar/task language.
    if has_active_sheet:
        if _NOT_SHEET_FOLLOWUP.search(raw) and not re.search(
            r"\b(sheet|spreadsheet|portfolio|revenue|metric|holding|student)\b",
            raw,
            re.I,
        ):
            return SheetIntent(kind="none")
        if ACTIVE_FOLLOWUP.search(raw):
            return SheetIntent(kind="analyze", mode="qa", query=raw)
        if (
            len(raw.split()) <= 16
            and _SHEET_DATA_QUESTION.search(raw)
            and ("?" in raw or raw.lower().startswith(("how ", "what ", "which ", "who ", "show ", "list ")))
        ):
            return SheetIntent(kind="analyze", mode="qa", query=raw)

    return SheetIntent(kind="none")
