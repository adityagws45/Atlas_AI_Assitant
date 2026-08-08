"""Market data facade — delegates to FinanceService."""

from __future__ import annotations

from finance.services.finance_service import FinanceService
from finance.types import FinanceResult


class MarketDataService:
    def __init__(self, finance: FinanceService | None = None) -> None:
        self.finance = finance or FinanceService()

    def quote(self, symbol: str) -> FinanceResult:
        return self.finance.get_quote(symbol)

    def news(self, symbol: str, *, limit: int = 5) -> FinanceResult:
        return self.finance.get_news(symbol, limit=limit)

    def movers(self) -> FinanceResult:
        return self.finance.get_market_movers()

    def earnings(self, symbol: str) -> FinanceResult:
        return self.finance.get_earnings(symbol)
