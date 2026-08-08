"""Tool catalog — finance tools are executable in Milestone 4."""

from __future__ import annotations

from ai.types import ToolName

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": ToolName.STOCK_QUOTE.value,
        "description": "Fetch a live stock quote (price, change, volume) for a ticker or company.",
        "parameters": {"symbol": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.COMPANY_PROFILE.value,
        "description": "Company profile / overview (sector, industry, description, market cap).",
        "parameters": {"symbol": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.COMPANY_METRICS.value,
        "description": "Key financial metrics (valuation, margins, growth, profitability).",
        "parameters": {"symbol": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.COMPANY_NEWS.value,
        "description": "Recent company news useful for explaining price moves or catalysts.",
        "parameters": {"symbol": "string", "limit": "integer"},
        "implemented": True,
    },
    {
        "name": ToolName.COMPANY_RESEARCH.value,
        "description": "Bundled research: profile + quote + metrics + earnings + news + ratings.",
        "parameters": {"symbol": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.COMPANY_COMPARE.value,
        "description": "Compare two or more companies on profile, quote, and metrics.",
        "parameters": {"symbols": "list[string]"},
        "implemented": True,
    },
    {
        "name": ToolName.SEC_FILINGS.value,
        "description": "Recent SEC filings (10-K, 10-Q, 8-K, etc.).",
        "parameters": {"symbol": "string", "form": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.EARNINGS.value,
        "description": "Recent or upcoming earnings results / estimates.",
        "parameters": {"symbol": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.MARKET_OVERVIEW.value,
        "description": "Broad market news / overview for today.",
        "parameters": {"market": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.MARKET_MOVERS.value,
        "description": "Today's notable gainers and losers among liquid names.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.ANALYST_RATINGS.value,
        "description": "Analyst recommendation trends / consensus and price targets when available.",
        "parameters": {"symbol": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_LOOKUP.value,
        "description": (
            "Look up or analyze the user's connected Google Sheets / portfolio spreadsheet. "
            "Use for portfolio value, allocation, performers, risks, and what changed."
        ),
        "parameters": {"query": "string", "mode": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_SEARCH.value,
        "description": "List or search the user's spreadsheets by name.",
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_OPEN.value,
        "description": "Open a spreadsheet / portfolio for analysis.",
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_SUMMARY.value,
        "description": "Summarize the active spreadsheet / portfolio.",
        "parameters": {"question": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_ANALYSIS.value,
        "description": "Deep analysis of the active spreadsheet.",
        "parameters": {"question": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_PORTFOLIO.value,
        "description": "Portfolio allocation, weights, and sector mix.",
        "parameters": {"question": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_STATISTICS.value,
        "description": "Key statistics for the active portfolio spreadsheet.",
        "parameters": {"question": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_FIND_OUTLIERS.value,
        "description": "Find unusual positions or outliers in the portfolio sheet.",
        "parameters": {"question": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_TRENDS.value,
        "description": "Trends and month-over-month changes in the spreadsheet.",
        "parameters": {"question": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.SHEET_COMPARE.value,
        "description": "Compare periods or snapshots in the user's spreadsheet.",
        "parameters": {"question": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.DOCUMENT_QA.value,
        "description": (
            "Answer questions about an uploaded financial document "
            "(10-K, 10-Q, earnings, transcript, presentation). "
            "Use when the user asks about risks, revenue, guidance, management, "
            "AI strategy, or anything in a report they uploaded."
        ),
        "parameters": {"question": "string", "document_id": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.DOCUMENT_COMPARE.value,
        "description": (
            "Compare two uploaded reports (companies, years, or quarters). "
            "Explain differences and significance — not just number lists."
        ),
        "parameters": {"question": "string", "document_ids": "list[string]"},
        "implemented": True,
    },
    {
        "name": ToolName.DRIVE_SEARCH.value,
        "description": (
            "Search the user's connected Google Drive / file library by name or topic. "
            "Use when they ask what documents they have about a company or theme."
        ),
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.DRIVE_IMPORT.value,
        "description": (
            "Import / load a file from the user's Drive into document analysis "
            "(e.g. 'analyze my Apple annual report')."
        ),
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_SEARCH.value,
        "description": (
            "Search the user's inbox by company, person, topic (invoices, earnings, resumes). "
            "Use when they ask about emails from/about someone or a theme."
        ),
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_SUMMARY.value,
        "description": "Summarize recent / today's emails at a high level.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_UNREAD.value,
        "description": "List unread emails that need a glance.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_PRIORITY.value,
        "description": "Show what needs attention — urgent, finance, meetings, reply requests.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_THREAD.value,
        "description": "Open / summarize the active email thread or a named conversation.",
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_ATTACHMENT.value,
        "description": "Summarize the attachment on the active email via the document pipeline.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_DRAFT.value,
        "description": (
            "Draft a reply to the active email (polite/formal/analyst/founder). "
            "Never sends without explicit confirmation."
        ),
        "parameters": {"tone": "string", "instruction": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_REPLY.value,
        "description": "Draft or refine a reply to the active email. Does not send automatically.",
        "parameters": {"tone": "string", "instruction": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_ARCHIVE.value,
        "description": "Archive the active email after the user is done with it.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.GMAIL_MARK_READ.value,
        "description": "Mark the active email as read.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_LOOKUP.value,
        "description": "Look up today's calendar / schedule.",
        "parameters": {"time_range": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_TODAY.value,
        "description": "Show today's meetings and schedule conflicts.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_SEARCH.value,
        "description": "Search calendar events by title/topic (interview, earnings, etc.).",
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_CREATE.value,
        "description": (
            "Propose creating a calendar event (meeting, research block, earnings prep). "
            "Requires user confirmation before booking."
        ),
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_UPDATE.value,
        "description": "Propose moving/rescheduling the active event. Requires confirmation.",
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_DELETE.value,
        "description": "Propose cancelling the active event. Requires confirmation.",
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_FREE_TIME.value,
        "description": "Find free slots on the calendar for a given duration.",
        "parameters": {"query": "string"},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_CONFLICTS.value,
        "description": "Detect overlapping meetings today.",
        "parameters": {},
        "implemented": True,
    },
    {
        "name": ToolName.CALENDAR_DEADLINES.value,
        "description": "List upcoming deadlines and earnings-related calendar items.",
        "parameters": {},
        "implemented": True,
    },
]


def list_tool_names() -> list[str]:
    return [t["name"] for t in TOOL_DEFINITIONS]


def list_implemented_tool_names() -> list[str]:
    return [t["name"] for t in TOOL_DEFINITIONS if t.get("implemented")]


def get_tool_definition(name: str) -> dict | None:
    for tool in TOOL_DEFINITIONS:
        if tool["name"] == name:
            return tool
    return None
