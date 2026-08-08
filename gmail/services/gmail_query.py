"""Convert natural-language email requests into Gmail search queries."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from gmail.services.gmail_relevance import FINANCE_GMAIL_OR, finance_search_query


def build_gmail_query(text: str, *, kind: str = "search") -> str:
    """Map user phrasing to a Gmail `q` string (generic, not company-hardcoded)."""
    raw = (text or "").strip()
    low = raw.lower()

    if kind == "unread":
        return "is:unread newer_than:30d"
    if kind == "unread_finance":
        return finance_search_query("unread_finance")
    if kind in {"finance", "earnings", "investments"}:
        return finance_search_query(kind)
    if kind in {"check", "latest", "summary"}:
        base = "in:inbox"
        if "today" in low:
            return f"{base} newer_than:1d"
        if "yesterday" in low:
            return f"{base} newer_than:2d older_than:1d"
        if re.search(r"\blast week\b", low):
            return f"{base} newer_than:7d"
        if re.search(r"\blast month\b", low):
            return f"{base} newer_than:30d"
        return f"{base} newer_than:14d"
    if kind == "priority":
        return f"in:inbox newer_than:30d ({FINANCE_GMAIL_OR} OR is:important OR is:unread)"

    if re.search(r"\b(from:|to:|subject:|is:|newer_than:|older_than:|after:|before:)\b", low):
        return raw

    parts: list[str] = []

    m_from = re.search(
        r"\b(?:emails?|mail)?\s*(?:from)\s+([A-Za-z0-9.@&\- ]{2,60})",
        raw,
        re.I,
    )
    if m_from:
        sender = m_from.group(1).strip(" .?!")
        if "@" in sender or "." in sender.replace(" ", ""):
            parts.append(f"from:{sender.split()[0]}")
        else:
            parts.append(f"from:({sender})")

    m_about = re.search(
        r"\b(?:emails?|mail)?\s*(?:about|regarding|re:?)\s+([A-Za-z0-9.& \-]{2,60})",
        raw,
        re.I,
    )
    if m_about and not m_from:
        topic = m_about.group(1).strip(" .?!")
        parts.append(f'"{topic}"' if " " in topic else topic)

    if re.search(r"\bunread\b", low):
        parts.append("is:unread")
    if re.search(r"\bimportant\b", low):
        parts.append("is:important")
    if re.search(r"\blast week\b", low):
        parts.append("newer_than:7d")
    elif re.search(r"\blast month\b", low):
        parts.append("newer_than:30d")
    elif re.search(r"\btoday\b", low):
        parts.append("newer_than:1d")

    if not parts:
        q = re.sub(
            r"^(find|search|show|list|get)(\s+me)?(\s+my)?(\s+(e)?mails?)?\s*(for|about|from)?\s*",
            "",
            raw,
            flags=re.I,
        ).strip(" .?!")
        q = re.sub(r"^(emails?|mail)\s+", "", q, flags=re.I).strip(" .?!")
        if q and q.lower() not in {"latest", "recent", "inbox", "finance"}:
            parts.append(q if " " not in q else f'"{q}"')

    return " ".join(parts).strip() or "in:inbox newer_than:14d"


def relative_day_bounds(*, days_ago_start: int, days_ago_end: int = 0) -> tuple[datetime, datetime]:
    now = datetime.now(tz=timezone.utc)
    end = now - timedelta(days=days_ago_end)
    start = now - timedelta(days=days_ago_start)
    return start, end
