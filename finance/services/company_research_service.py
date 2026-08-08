"""Company research facade — delegates to FinanceService."""

from __future__ import annotations

from finance.services.finance_service import FinanceService
from finance.types import FinanceResult


class CompanyResearchService:
    def __init__(self, finance: FinanceService | None = None) -> None:
        self.finance = finance or FinanceService()

    def research(self, symbol_or_name: str) -> FinanceResult:
        return self.finance.research_company(symbol_or_name)

    def compare(self, symbols: list[str]) -> FinanceResult:
        return self.finance.compare_companies(symbols)
