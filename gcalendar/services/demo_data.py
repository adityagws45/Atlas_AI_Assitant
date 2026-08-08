"""Demo calendar used when Google Calendar OAuth isn't configured."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

NOW = datetime.now(tz=timezone.utc)


def _at(day_offset: int, hour: int, minute: int = 0) -> str:
    base = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    # Align to local-ish workday from "today"
    dt = base + timedelta(days=day_offset, hours=hour, minutes=minute)
    return dt.isoformat()


DEMO_EVENTS = [
    {
        "id": "demo_evt_standup",
        "title": "Daily standup",
        "description": "Recurring team sync",
        "location": "Zoom",
        "start": _at(0, 9, 0),
        "end": _at(0, 9, 30),
        "recurring": True,
        "categories": ["recurring", "meeting"],
        "companies": [],
        "tickers": [],
    },
    {
        "id": "demo_evt_portfolio",
        "title": "Portfolio review",
        "description": "Weekly allocation check with Alex",
        "location": "Conference A",
        "start": _at(0, 10, 0),
        "end": _at(0, 11, 0),
        "recurring": False,
        "categories": ["finance", "meeting", "important"],
        "companies": [],
        "tickers": [],
    },
    {
        "id": "demo_evt_interview",
        "title": "Interview — Equity Research Analyst",
        "description": "Candidate Jordan Lee",
        "location": "Meet",
        "start": _at(0, 14, 0),
        "end": _at(0, 15, 0),
        "recurring": False,
        "categories": ["interview", "important"],
        "companies": [],
        "tickers": [],
    },
    {
        "id": "demo_evt_conflict_a",
        "title": "Vendor check-in",
        "description": "Overlaps interview window intentionally for demo conflicts",
        "location": "",
        "start": _at(0, 14, 30),
        "end": _at(0, 15, 0),
        "recurring": False,
        "categories": ["meeting"],
        "companies": [],
        "tickers": [],
    },
    {
        "id": "demo_evt_nvda",
        "title": "NVIDIA earnings prep",
        "description": "Review GPU allocation notes before print",
        "location": "",
        "start": _at(1, 15, 0),
        "end": _at(1, 16, 0),
        "recurring": False,
        "categories": ["finance", "earnings", "deadline"],
        "companies": ["NVIDIA"],
        "tickers": ["NVDA"],
    },
    {
        "id": "demo_evt_travel",
        "title": "Travel — airport",
        "description": "Flight buffer",
        "location": "Airport",
        "start": _at(3, 6, 0),
        "end": _at(3, 9, 0),
        "recurring": False,
        "categories": ["travel"],
        "companies": [],
        "tickers": [],
    },
    {
        "id": "demo_evt_board",
        "title": "Board pack deadline",
        "description": "Comments due",
        "location": "",
        "start": _at(2, 18, 0),
        "end": _at(2, 18, 30),
        "recurring": False,
        "categories": ["deadline", "important"],
        "companies": [],
        "tickers": [],
    },
    {
        "id": "demo_evt_friday_research",
        "title": "Deep research block",
        "description": "Open research focus time",
        "location": "",
        "start": _at(4, 10, 0),
        "end": _at(4, 12, 0),
        "recurring": False,
        "categories": ["finance", "research"],
        "companies": [],
        "tickers": [],
    },
]
