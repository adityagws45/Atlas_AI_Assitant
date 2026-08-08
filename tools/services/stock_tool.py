"""Finance tool handlers — execute via FinanceService only."""

from __future__ import annotations

import logging
from typing import Any

from ai.types import ToolName, ToolRequest
from finance.services.finance_service import FinanceService
from finance.types import FinanceResult
from finance.utils.ticker_resolve import resolve_symbol, resolve_symbols

logger = logging.getLogger("atlas.tools.finance")


class FinanceToolExecutor:
    """Maps tool requests to FinanceService methods."""

    def __init__(self, finance: FinanceService | None = None) -> None:
        self.finance = finance or FinanceService()

    def execute(self, request: ToolRequest) -> dict[str, Any]:
        name = request.name
        args = request.arguments or {}
        logger.info("event=finance_tool_exec name=%s args=%s", name, list(args.keys()))

        if name == ToolName.STOCK_QUOTE.value:
            return self._result(self.finance.get_quote(self._symbol(args, request)))

        if name == ToolName.COMPANY_PROFILE.value:
            return self._result(self.finance.get_profile(self._symbol(args, request)))

        if name == ToolName.COMPANY_METRICS.value:
            return self._result(self.finance.get_metrics(self._symbol(args, request)))

        if name == ToolName.COMPANY_NEWS.value:
            limit = int(args.get("limit") or 5)
            return self._result(
                self.finance.get_news(self._symbol(args, request), limit=limit)
            )

        if name == ToolName.COMPANY_RESEARCH.value:
            return self._result(self.finance.research_company(self._symbol(args, request)))

        if name == ToolName.COMPANY_COMPARE.value:
            symbols = args.get("symbols") or args.get("tickers") or []
            if isinstance(symbols, str):
                symbols = resolve_symbols(symbols) or [symbols]
            if not symbols:
                # Try symbol + symbol_b style
                a = args.get("symbol") or args.get("symbol_a")
                b = args.get("symbol_b") or args.get("compare_to")
                symbols = [x for x in [a, b] if x]
            if not symbols and request.reason:
                symbols = resolve_symbols(request.reason)
            return self._result(self.finance.compare_companies([str(s) for s in symbols]))

        if name == ToolName.SEC_FILINGS.value:
            form = str(args.get("form") or "")
            return self._result(
                self.finance.get_sec_filings(self._symbol(args, request), form=form)
            )

        if name == ToolName.EARNINGS.value:
            return self._result(self.finance.get_earnings(self._symbol(args, request)))

        if name == ToolName.MARKET_OVERVIEW.value:
            return self._result(self.finance.get_market_news(limit=8))

        if name == ToolName.MARKET_MOVERS.value:
            return self._result(self.finance.get_market_movers())

        if name == ToolName.ANALYST_RATINGS.value:
            return self._result(self.finance.get_analyst_ratings(self._symbol(args, request)))

        return {
            "ok": False,
            "error": f"Tool `{name}` is not available yet.",
            "error_code": "unimplemented",
            "tool": name,
        }

    def _symbol(self, args: dict[str, Any], request: ToolRequest) -> str:
        for key in ("symbol", "ticker", "company", "name", "query"):
            val = args.get(key)
            if val:
                return str(val)
        # Fall back to parsing reason / empty
        if request.reason:
            resolved = resolve_symbol(request.reason)
            if resolved:
                return resolved
        return ""

    @staticmethod
    def _result(result: FinanceResult) -> dict[str, Any]:
        payload = result.to_dict()
        payload["tool_ok"] = result.ok
        return payload
