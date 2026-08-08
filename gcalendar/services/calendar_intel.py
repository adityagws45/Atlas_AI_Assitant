"""Calendar intelligence — conflicts, free slots, finance-aware classification."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from gcalendar.services.calendar_client import RemoteEvent

COMPANY_MAP = {
    "nvidia": ("NVIDIA", "NVDA"),
    "apple": ("Apple", "AAPL"),
    "microsoft": ("Microsoft", "MSFT"),
    "amazon": ("Amazon", "AMZN"),
    "google": ("Google", "GOOGL"),
    "meta": ("Meta", "META"),
    "tesla": ("Tesla", "TSLA"),
    "fomc": ("FOMC", ""),
}

CATEGORY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("interview", re.compile(r"\b(interview|candidate)\b", re.I)),
    ("earnings", re.compile(r"\b(earnings|epscall|print|reports?)\b", re.I)),
    ("finance", re.compile(r"\b(portfolio|allocation|research|fomc|earnings|investor)\b", re.I)),
    ("deadline", re.compile(r"\b(deadline|due|submit|board pack)\b", re.I)),
    ("travel", re.compile(r"\b(travel|flight|airport|train)\b", re.I)),
    ("recurring", re.compile(r"\b(standup|weekly|daily|recurring)\b", re.I)),
    ("important", re.compile(r"\b(board|interview|earnings|urgent)\b", re.I)),
    ("meeting", re.compile(r"\b(meeting|sync|call|review)\b", re.I)),
]


def enrich_event(ev: RemoteEvent) -> dict[str, Any]:
    blob = f"{ev.title} {ev.description} {ev.location}"
    low = blob.lower()
    categories = list(ev.categories or [])
    for name, pat in CATEGORY_RULES:
        if pat.search(blob) and name not in categories:
            categories.append(name)
    if ev.is_recurring and "recurring" not in categories:
        categories.append("recurring")

    companies = list(ev.companies or [])
    tickers = list(ev.tickers or [])
    for key, (company, ticker) in COMPANY_MAP.items():
        if key in low:
            if company and company not in companies:
                companies.append(company)
            if ticker and ticker not in tickers:
                tickers.append(ticker)

    importance = 0.0
    weights = {
        "important": 20,
        "interview": 18,
        "earnings": 16,
        "deadline": 14,
        "finance": 10,
        "travel": 8,
        "meeting": 4,
    }
    for c in categories:
        importance += weights.get(c, 0)
    return {
        "categories": categories,
        "companies": companies,
        "tickers": tickers,
        "importance": float(importance),
    }


def find_conflicts(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_ev = sorted(events, key=lambda e: e["start_at"])
    conflicts = []
    for i, a in enumerate(sorted_ev):
        for b in sorted_ev[i + 1 :]:
            if b["start_at"] >= a["end_at"]:
                break
            if a["start_at"] < b["end_at"] and b["start_at"] < a["end_at"]:
                overlap_start = max(a["start_at"], b["start_at"])
                overlap_end = min(a["end_at"], b["end_at"])
                minutes = max(0, int((overlap_end - overlap_start).total_seconds() // 60))
                conflicts.append(
                    {
                        "a": a,
                        "b": b,
                        "overlap_minutes": minutes,
                        "overlap_start": overlap_start,
                        "overlap_end": overlap_end,
                    }
                )
    return conflicts


def format_conflicts(conflicts: list[dict[str, Any]], *, title: str = "Calendar Conflict") -> str:
    if not conflicts:
        return "No overlapping meetings in this window."
    lines: list[str] = []
    for c in conflicts[:5]:
        a, b = c["a"], c["b"]
        lines.append(f"⚠️ *{title}*")
        lines.append("")
        lines.append(f"Meeting A: {_fmt_range(a['start_at'], a['end_at'])} — *{a['title']}*")
        lines.append(f"Meeting B: {_fmt_range(b['start_at'], b['end_at'])} — *{b['title']}*")
        lines.append(f"Overlap: {c.get('overlap_minutes', 0)} minutes")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_day(events: list[dict[str, Any]], *, title: str = "Today") -> str:
    if not events:
        return f"📅 You have no events scheduled for {title.lower()}."
    lines = [f"*{title}*", ""]
    for e in events:
        flag = "★" if e.get("importance", 0) >= 14 else "•"
        lines.append(
            f"{flag} {_fmt_range(e['start_at'], e['end_at'])} — *{e['title']}*"
        )
        cats = e.get("categories") or []
        if "interview" in cats:
            lines.append("  Interview")
        elif "earnings" in cats or "finance" in cats:
            lines.append("  Finance-related")
        elif "deadline" in cats:
            lines.append("  Deadline")
    conflicts = find_conflicts(events)
    if conflicts:
        lines.append("")
        lines.append(format_conflicts(conflicts))
    return "\n".join(lines)


def format_busy(events: list[dict[str, Any]], *, title: str = "Today") -> str:
    if not events:
        return f"📅 You’re free — no calendar events for {title.lower()}."
    total_min = 0
    for e in events:
        try:
            total_min += max(0, int((e["end_at"] - e["start_at"]).total_seconds() // 60))
        except Exception:  # noqa: BLE001
            continue
    hours = total_min / 60.0
    conf = find_conflicts(events)
    lines = [
        f"*{title} — busyness*",
        "",
        f"• {len(events)} event{'s' if len(events) != 1 else ''}",
        f"• ~{hours:.1f} hours blocked",
    ]
    if conf:
        lines.append(f"• {len(conf)} overlap{'s' if len(conf) != 1 else ''}")
    else:
        lines.append("• No overlaps")
    lines.append("")
    lines.append(format_day(events, title=title))
    return "\n".join(lines)


def longest_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    best = None
    best_min = -1
    for e in events:
        try:
            mins = int((e["end_at"] - e["start_at"]).total_seconds() // 60)
        except Exception:  # noqa: BLE001
            continue
        if mins > best_min:
            best_min = mins
            best = e
    return best


def is_free_at(
    events: list[dict[str, Any]], *, moment: datetime, duration_minutes: int = 1
) -> bool:
    end = moment + timedelta(minutes=duration_minutes)
    for e in events:
        if e["start_at"] < end and e["end_at"] > moment:
            return False
    return True


def find_free_slots(
    events: list[dict[str, Any]],
    *,
    day_start: datetime,
    day_end: datetime,
    duration_minutes: int = 60,
) -> list[tuple[datetime, datetime]]:
    """Find free windows on a day within work hours."""
    work_start = day_start.replace(hour=9, minute=0, second=0, microsecond=0)
    work_end = day_start.replace(hour=18, minute=0, second=0, microsecond=0)
    if work_start < day_start:
        work_start = day_start
    if work_end > day_end:
        work_end = day_end
    busy = sorted(
        [
            (max(e["start_at"], work_start), min(e["end_at"], work_end))
            for e in events
            if e["end_at"] > work_start and e["start_at"] < work_end
        ],
        key=lambda x: x[0],
    )
    slots: list[tuple[datetime, datetime]] = []
    cursor = work_start
    need = timedelta(minutes=duration_minutes)
    for b0, b1 in busy:
        if b0 > cursor and (b0 - cursor) >= need:
            slots.append((cursor, cursor + need))
        cursor = max(cursor, b1)
    if work_end > cursor and (work_end - cursor) >= need:
        slots.append((cursor, cursor + need))
    return slots[:5]


def format_free_slots(slots: list[tuple[datetime, datetime]], *, label: str) -> str:
    if not slots:
        return f"No open {label} in working hours (09:00–18:00). Want me to check another day?"
    lines = [f"*Free time* ({label})", ""]
    for a, b in slots:
        lines.append(f"• {_fmt_range(a, b)}")
    return "\n".join(lines)


def _fmt_range(start: datetime, end: datetime) -> str:
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return f"{start.strftime('%a %H:%M')}–{end.strftime('%H:%M')}"


def parse_when(text: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Lightweight natural-time parse for scheduling / free-time."""
    now = now or datetime.now(tz=timezone.utc)
    low = (text or "").lower()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if "tomorrow" in low:
        day = day + timedelta(days=1)
    elif "friday" in low:
        delta = (4 - day.weekday()) % 7
        if delta == 0 and "next" in low:
            delta = 7
        day = day + timedelta(days=delta or 0)
    elif "monday" in low:
        delta = (0 - day.weekday()) % 7
        day = day + timedelta(days=delta or 7)
    elif "next week" in low:
        day = day + timedelta(days=7)

    hour, minute = 14, 0
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", low)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = (m.group(3) or "").lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
    elif "morning" in low:
        hour = 10
    elif "afternoon" in low:
        hour = 15
    elif "evening" in low:
        hour = 18

    duration = 60
    dm = re.search(r"\b(\d+)\s*-?\s*hour", low)
    if dm:
        duration = int(dm.group(1)) * 60
    elif "two hours" in low or "2 hours" in low:
        duration = 120
    elif "30 min" in low or "half hour" in low:
        duration = 30

    start = day.replace(hour=hour, minute=minute, tzinfo=now.tzinfo or timezone.utc)
    end = start + timedelta(minutes=duration)
    return {"start_at": start, "end_at": end, "duration_minutes": duration}


def day_bounds(now: datetime, *, offset_days: int = 0) -> tuple[datetime, datetime]:
    """Return [day_start, day_end) in the same timezone as now."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    day0 = (now + timedelta(days=offset_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return day0, day0 + timedelta(days=1)
