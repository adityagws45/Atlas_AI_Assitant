"""Shared AI orchestration types.

Provider-agnostic contracts used by AIService, ContextBuilder, ToolRouter,
and memory services. Business logic depends on these — never on Gemini SDKs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolName(str, Enum):
    """Registered tool identifiers."""

    STOCK_QUOTE = "stock_quote"
    COMPANY_PROFILE = "company_profile"
    COMPANY_METRICS = "company_metrics"
    COMPANY_NEWS = "company_news"
    COMPANY_RESEARCH = "company_research"
    COMPANY_COMPARE = "company_compare"
    SEC_FILINGS = "sec_filings"
    EARNINGS = "earnings"
    MARKET_OVERVIEW = "market_overview"
    MARKET_MOVERS = "market_movers"
    ANALYST_RATINGS = "analyst_ratings"
    SHEET_LOOKUP = "sheet_lookup"
    SHEET_SEARCH = "sheet_search"
    SHEET_OPEN = "sheet_open"
    SHEET_SUMMARY = "sheet_summary"
    SHEET_ANALYSIS = "sheet_analysis"
    SHEET_COMPARE = "sheet_compare"
    SHEET_PORTFOLIO = "sheet_portfolio"
    SHEET_STATISTICS = "sheet_statistics"
    SHEET_FIND_OUTLIERS = "sheet_find_outliers"
    SHEET_TRENDS = "sheet_trends"
    DOCUMENT_QA = "document_qa"
    DOCUMENT_COMPARE = "document_compare"
    DRIVE_SEARCH = "drive_search"
    DRIVE_IMPORT = "drive_import"
    GMAIL_SEARCH = "gmail_search"
    GMAIL_SUMMARY = "gmail_summary"
    GMAIL_UNREAD = "gmail_unread"
    GMAIL_PRIORITY = "gmail_priority"
    GMAIL_THREAD = "gmail_thread"
    GMAIL_ATTACHMENT = "gmail_attachment"
    GMAIL_DRAFT = "gmail_draft"
    GMAIL_REPLY = "gmail_reply"
    GMAIL_ARCHIVE = "gmail_archive"
    GMAIL_MARK_READ = "gmail_mark_read"
    CALENDAR_LOOKUP = "calendar_lookup"
    CALENDAR_TODAY = "calendar_today"
    CALENDAR_SEARCH = "calendar_search"
    CALENDAR_CREATE = "calendar_create"
    CALENDAR_UPDATE = "calendar_update"
    CALENDAR_DELETE = "calendar_delete"
    CALENDAR_FREE_TIME = "calendar_free_time"
    CALENDAR_CONFLICTS = "calendar_conflicts"
    CALENDAR_DEADLINES = "calendar_deadlines"


@dataclass
class ToolRequest:
    """Structured tool call decided by the model — not executed in Milestone 3."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "arguments": self.arguments,
            "reason": self.reason,
        }


@dataclass
class ProviderMessage:
    role: str  # user | assistant | system | model
    content: str


@dataclass
class ProviderResponse:
    """Normalized provider output."""

    text: str
    raw: Any = None
    model: str = ""
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: int = 0

    @property
    def ok(self) -> bool:
        return bool((self.text or "").strip())


@dataclass
class StructuredAIDecision:
    """Parsed decision from the analyst turn."""

    answer: str = ""
    needs_clarification: bool = False
    clarification_question: str = ""
    needs_tool: bool = False
    tool_request: ToolRequest | None = None
    confidence: float = 1.0
    raw_json: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryCandidate:
    """Candidate long-term memory from extraction."""

    memory_type: str
    key: str
    value: Any
    confidence: float = 0.8
    reason: str = ""


@dataclass
class ConversationContext:
    """Everything the model may need for one turn."""

    user_id: str
    telegram_id: int
    user_profile: dict[str, Any] = field(default_factory=dict)
    preferences: dict[str, Any] = field(default_factory=dict)
    watchlist: list[dict[str, Any]] = field(default_factory=list)
    memories: list[dict[str, Any]] = field(default_factory=list)
    onboarding_state: dict[str, Any] = field(default_factory=dict)
    conversation_id: str = ""
    conversation_summary: str = ""
    recent_messages: list[dict[str, str]] = field(default_factory=list)
    current_user_message: str = ""
    clarification_hint: str | None = None
    available_tools: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "user_profile": self.user_profile,
            "preferences": self.preferences,
            "watchlist": self.watchlist,
            "memories": self.memories,
            "onboarding_state": self.onboarding_state,
            "conversation_summary": self.conversation_summary,
            "recent_messages": self.recent_messages,
            "current_user_message": self.current_user_message,
            "clarification_hint": self.clarification_hint,
            "available_tools": self.available_tools,
            "extras": self.extras,
        }


@dataclass
class AITurnResult:
    """Final orchestrated result returned to Telegram."""

    answer: str
    decision: StructuredAIDecision | None = None
    tool_request: ToolRequest | None = None
    memories_saved: list[str] = field(default_factory=list)
    used_clarification: bool = False
    provider_model: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class ProviderError(Exception):
    """Base provider failure."""


class ProviderTimeoutError(ProviderError):
    """Request exceeded configured timeout."""


class ProviderRetryExhausted(ProviderError):
    """All retries failed."""


class ProviderConfigError(ProviderError):
    """Missing API key / misconfiguration."""
