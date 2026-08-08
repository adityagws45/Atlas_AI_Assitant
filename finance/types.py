"""Normalized finance domain objects — never expose raw provider payloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class StockQuote:
    symbol: str
    price: float | None = None
    change: float | None = None
    change_percent: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    previous_close: float | None = None
    volume: int | None = None
    currency: str = "USD"
    as_of: str = ""
    market_state: str = ""  # open | closed | pre | post | unknown

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompanyProfile:
    symbol: str
    name: str = ""
    exchange: str = ""
    industry: str = ""
    sector: str = ""
    country: str = ""
    website: str = ""
    description: str = ""
    market_cap: float | None = None
    employees: int | None = None
    ipo: str = ""
    logo: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CompanyMetrics:
    symbol: str
    pe: float | None = None
    forward_pe: float | None = None
    peg: float | None = None
    eps: float | None = None
    revenue_ttm: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    profit_margin: float | None = None
    roe: float | None = None
    debt_to_equity: float | None = None
    dividend_yield: float | None = None
    beta: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    revenue_growth: float | None = None
    earnings_growth: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NewsItem:
    title: str
    summary: str = ""
    source: str = ""
    url: str = ""
    published_at: str = ""
    symbol: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EarningsEvent:
    symbol: str
    period: str = ""
    report_date: str = ""
    eps_actual: float | None = None
    eps_estimate: float | None = None
    revenue_actual: float | None = None
    revenue_estimate: float | None = None
    surprise_percent: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SecFiling:
    symbol: str
    form: str
    filed_at: str = ""
    description: str = ""
    url: str = ""
    accession: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MarketMover:
    symbol: str
    name: str = ""
    change_percent: float | None = None
    price: float | None = None
    direction: str = ""  # up | down

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalystRating:
    symbol: str
    strong_buy: int = 0
    buy: int = 0
    hold: int = 0
    sell: int = 0
    strong_sell: int = 0
    consensus: str = ""
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finance_payload_to_dict(value: Any) -> Any:
    """Serialize nested finance objects; tolerate cache-hydrated dicts."""
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return value


@dataclass
class CompanyResearchBundle:
    symbol: str
    profile: CompanyProfile | None = None
    quote: StockQuote | None = None
    metrics: CompanyMetrics | None = None
    earnings: list[EarningsEvent] = field(default_factory=list)
    news: list[NewsItem] = field(default_factory=list)
    ratings: AnalystRating | None = None

    def to_dict(self) -> dict[str, Any]:
        # Sub-results may be dataclasses (fresh fetch) or dicts (from cache).
        return {
            "symbol": self.symbol,
            "profile": _finance_payload_to_dict(self.profile),
            "quote": _finance_payload_to_dict(self.quote),
            "metrics": _finance_payload_to_dict(self.metrics),
            "earnings": [_finance_payload_to_dict(e) for e in (self.earnings or [])],
            "news": [_finance_payload_to_dict(n) for n in (self.news or [])],
            "ratings": _finance_payload_to_dict(self.ratings),
        }


@dataclass
class FinanceResult:
    """Wrapper for any finance call — normalized + provenance."""

    ok: bool
    data: Any = None
    error: str = ""
    error_code: str = ""  # not_found | provider | timeout | rate_limit | invalid
    source: str = ""  # finnhub | yahoo | sec | cache
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = self.data
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        elif isinstance(payload, list):
            payload = [x.to_dict() if hasattr(x, "to_dict") else x for x in payload]
        return {
            "ok": self.ok,
            "data": payload,
            "error": self.error,
            "error_code": self.error_code,
            "source": self.source,
            "cached": self.cached,
        }


class FinanceError(Exception):
    def __init__(self, message: str, *, code: str = "provider") -> None:
        super().__init__(message)
        self.code = code


class FinanceNotFound(FinanceError):
    def __init__(self, message: str = "Symbol not found") -> None:
        super().__init__(message, code="not_found")


class FinanceRateLimit(FinanceError):
    def __init__(self, message: str = "Rate limited") -> None:
        super().__init__(message, code="rate_limit")


class FinanceTimeout(FinanceError):
    def __init__(self, message: str = "Provider timeout") -> None:
        super().__init__(message, code="timeout")
