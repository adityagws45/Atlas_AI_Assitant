"""Central FinanceService — only entry point to market data providers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from django.conf import settings

from core.utils.cache_helpers import make_cache_key
from finance.integrations.finnhub_client import FinnhubClient
from finance.integrations.sec_edgar_client import SecEdgarClient
from finance.integrations.yahoo_client import YahooClient
from finance.types import (
    CompanyResearchBundle,
    FinanceError,
    FinanceNotFound,
    FinanceResult,
    SecFiling,
)
from finance.utils.ticker_resolve import normalize_symbol, resolve_symbol

logger = logging.getLogger("atlas.finance.service")


class FinanceService:
    """
    Finnhub primary → Yahoo fallback, with Redis/LocMem caching.

    Domain modules and tools must call this — never providers directly.
    """

    def __init__(
        self,
        *,
        finnhub: FinnhubClient | None = None,
        yahoo: YahooClient | None = None,
        sec: SecEdgarClient | None = None,
    ) -> None:
        self.finnhub = finnhub or FinnhubClient()
        self.yahoo = yahoo or YahooClient()
        self.sec = sec or SecEdgarClient()
        self.primary = (getattr(settings, "FINANCE_PRIMARY_PROVIDER", "finnhub") or "finnhub").lower()
        self.fallback = (getattr(settings, "FINANCE_FALLBACK_PROVIDER", "yahoo") or "yahoo").lower()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_quote(self, symbol_or_name: str) -> FinanceResult:
        symbol = self._require_symbol(symbol_or_name)
        if isinstance(symbol, FinanceResult):
            return symbol
        ttl = int(getattr(settings, "CACHE_TTL_STOCK", 300))
        return self._cached(
            "quote",
            symbol,
            ttl,
            lambda: self._with_fallback("quote", symbol, lambda c: c.get_quote(symbol)),
        )

    def get_profile(self, symbol_or_name: str) -> FinanceResult:
        symbol = self._require_symbol(symbol_or_name)
        if isinstance(symbol, FinanceResult):
            return symbol
        ttl = int(getattr(settings, "CACHE_TTL_PROFILE", 600))
        return self._cached(
            "profile",
            symbol,
            ttl,
            lambda: self._with_fallback("profile", symbol, lambda c: c.get_profile(symbol)),
        )

    def get_metrics(self, symbol_or_name: str) -> FinanceResult:
        symbol = self._require_symbol(symbol_or_name)
        if isinstance(symbol, FinanceResult):
            return symbol
        ttl = int(getattr(settings, "CACHE_TTL_PROFILE", 600))
        return self._cached(
            "metrics",
            symbol,
            ttl,
            lambda: self._with_fallback("metrics", symbol, lambda c: c.get_metrics(symbol)),
        )

    def get_news(self, symbol_or_name: str, *, limit: int = 5) -> FinanceResult:
        symbol = self._require_symbol(symbol_or_name)
        if isinstance(symbol, FinanceResult):
            return symbol
        ttl = int(getattr(settings, "CACHE_TTL_NEWS", 600))
        return self._cached(
            "news",
            f"{symbol}:{limit}",
            ttl,
            lambda: self._with_fallback(
                "news", symbol, lambda c: c.get_news(symbol, limit=limit)
            ),
        )

    def get_market_news(self, *, limit: int = 8) -> FinanceResult:
        ttl = int(getattr(settings, "CACHE_TTL_NEWS", 600))
        return self._cached(
            "market_news",
            str(limit),
            ttl,
            lambda: self._with_fallback(
                "market_news", "SPY", lambda c: c.get_market_news(limit=limit)
            ),
        )

    def get_earnings(self, symbol_or_name: str, *, limit: int = 4) -> FinanceResult:
        symbol = self._require_symbol(symbol_or_name)
        if isinstance(symbol, FinanceResult):
            return symbol
        ttl = int(getattr(settings, "CACHE_TTL_PROFILE", 600))
        return self._cached(
            "earnings",
            f"{symbol}:{limit}",
            ttl,
            lambda: self._with_fallback(
                "earnings", symbol, lambda c: c.get_earnings(symbol, limit=limit)
            ),
        )

    def get_sec_filings(
        self, symbol_or_name: str, *, form: str = "", limit: int = 5
    ) -> FinanceResult:
        symbol = self._require_symbol(symbol_or_name)
        if isinstance(symbol, FinanceResult):
            return symbol
        ttl = int(getattr(settings, "CACHE_TTL_SEC", 600))

        def _fetch() -> FinanceResult:
            # Prefer SEC EDGAR, then Finnhub filings, then empty-friendly error
            try:
                rows = self.sec.get_filings(symbol, form=form, limit=limit)
                if rows:
                    return FinanceResult(ok=True, data=rows, source="sec")
            except FinanceError as exc:
                logger.info("event=sec_fail symbol=%s err=%s", symbol, type(exc).__name__)

            def _provider_filings(client) -> list[SecFiling]:
                return client.get_filings(symbol, form=form, limit=limit)

            result = self._with_fallback("filings", symbol, _provider_filings)
            if result.ok and result.data:
                return result
            return FinanceResult(
                ok=False,
                error=f"No recent SEC filings found for {symbol}.",
                error_code="not_found",
                source=result.source or "sec",
            )

        return self._cached("sec", f"{symbol}:{form}:{limit}", ttl, _fetch)

    def get_analyst_ratings(self, symbol_or_name: str) -> FinanceResult:
        symbol = self._require_symbol(symbol_or_name)
        if isinstance(symbol, FinanceResult):
            return symbol
        ttl = int(getattr(settings, "CACHE_TTL_PROFILE", 600))
        return self._cached(
            "ratings",
            symbol,
            ttl,
            lambda: self._with_fallback(
                "ratings", symbol, lambda c: c.get_recommendations(symbol)
            ),
        )

    def get_market_movers(self) -> FinanceResult:
        ttl = int(getattr(settings, "CACHE_TTL_STOCK", 300))
        return self._cached(
            "movers",
            "us",
            ttl,
            lambda: self._with_fallback("movers", "US", lambda c: c.get_movers()),
        )

    def compare_companies(self, symbols_or_names: list[str]) -> FinanceResult:
        resolved: list[str] = []
        for raw in symbols_or_names:
            sym = resolve_symbol(raw) or normalize_symbol(raw)
            if sym and sym not in resolved:
                resolved.append(sym)
        if len(resolved) < 2:
            return FinanceResult(
                ok=False,
                error="I need at least two companies or tickers to compare.",
                error_code="invalid",
            )

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _pack(sym: str) -> dict:
            profile = self.get_profile(sym)
            quote = self.get_quote(sym)
            metrics = self.get_metrics(sym)
            return {
                "symbol": sym,
                "profile": profile.to_dict() if profile.ok else None,
                "quote": quote.to_dict() if quote.ok else None,
                "metrics": metrics.to_dict() if metrics.ok else None,
                "sources": [s for s in [profile.source, quote.source, metrics.source] if s],
                "errors": [
                    e
                    for e in [
                        None if profile.ok else profile.error,
                        None if quote.ok else quote.error,
                        None if metrics.ok else metrics.error,
                    ]
                    if e
                ],
            }

        rows: list[dict] = []
        sources: set[str] = set()
        with ThreadPoolExecutor(max_workers=min(4, len(resolved[:4]))) as pool:
            futures = {pool.submit(_pack, sym): sym for sym in resolved[:4]}
            for fut in as_completed(futures):
                row = fut.result()
                sources.update(row.pop("sources", []))
                rows.append(row)
        # Stable order by requested symbols
        order = {s: i for i, s in enumerate(resolved)}
        rows.sort(key=lambda r: order.get(r["symbol"], 99))
        return FinanceResult(
            ok=True,
            data={"companies": rows},
            source="+".join(sorted(sources)) or "mixed",
        )

    def research_company(self, symbol_or_name: str) -> FinanceResult:
        symbol = self._require_symbol(symbol_or_name)
        if isinstance(symbol, FinanceResult):
            return symbol

        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=6) as pool:
            f_profile = pool.submit(self.get_profile, symbol)
            f_quote = pool.submit(self.get_quote, symbol)
            f_metrics = pool.submit(self.get_metrics, symbol)
            f_earnings = pool.submit(self.get_earnings, symbol, limit=3)
            f_news = pool.submit(self.get_news, symbol, limit=4)
            f_ratings = pool.submit(self.get_analyst_ratings, symbol)
            profile = f_profile.result()
            quote = f_quote.result()
            metrics = f_metrics.result()
            earnings = f_earnings.result()
            news = f_news.result()
            ratings = f_ratings.result()
        if not any([profile.ok, quote.ok, metrics.ok]):
            return FinanceResult(
                ok=False,
                error=f"I couldn't pull reliable research data for {symbol}.",
                error_code="not_found",
            )
        bundle = CompanyResearchBundle(
            symbol=symbol,
            profile=profile.data if profile.ok else None,
            quote=quote.data if quote.ok else None,
            metrics=metrics.data if metrics.ok else None,
            earnings=earnings.data if earnings.ok else [],
            news=news.data if news.ok else [],
            ratings=ratings.data if ratings.ok else None,
        )
        sources = {
            s
            for s in [
                profile.source,
                quote.source,
                metrics.source,
                earnings.source,
                news.source,
                ratings.source,
            ]
            if s
        }
        return FinanceResult(
            ok=True,
            data=bundle,
            source="+".join(sorted(sources)) or "mixed",
            cached=any(
                [
                    profile.cached,
                    quote.cached,
                    metrics.cached,
                    earnings.cached,
                    news.cached,
                    ratings.cached,
                ]
            ),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _require_symbol(self, symbol_or_name: str) -> str | FinanceResult:
        raw = (symbol_or_name or "").strip()
        if not raw:
            return FinanceResult(
                ok=False,
                error="Which company or ticker should I look up?",
                error_code="invalid",
            )
        symbol = resolve_symbol(raw) or normalize_symbol(raw)
        if not symbol:
            return FinanceResult(
                ok=False,
                error=(
                    f"I couldn't map “{raw}” to a ticker. "
                    "Try a symbol like NVDA or a clearer company name."
                ),
                error_code="invalid",
            )
        return symbol

    def _providers_ordered(self) -> list[Any]:
        providers = []
        mapping = {
            "finnhub": self.finnhub,
            "yahoo": self.yahoo,
        }
        primary = mapping.get(self.primary)
        fallback = mapping.get(self.fallback)
        if primary is not None:
            # Skip Finnhub when unconfigured to avoid noisy failures
            if getattr(primary, "name", "") == "finnhub" and not getattr(primary, "configured", True):
                logger.info("event=finnhub_skipped reason=not_configured")
            else:
                providers.append(primary)
        if fallback is not None and fallback not in providers:
            providers.append(fallback)
        # Ensure yahoo always available as last resort
        if self.yahoo not in providers:
            providers.append(self.yahoo)
        return providers

    def _with_fallback(
        self, op: str, symbol: str, call: Callable[[Any], Any]
    ) -> FinanceResult:
        last_error = "Provider unavailable"
        last_code = "provider"
        for client in self._providers_ordered():
            try:
                data = call(client)
                logger.info(
                    "event=finance_ok op=%s symbol=%s source=%s",
                    op,
                    symbol,
                    getattr(client, "name", "?"),
                )
                return FinanceResult(
                    ok=True,
                    data=data,
                    source=getattr(client, "name", ""),
                )
            except FinanceNotFound as exc:
                last_error = str(exc)
                last_code = "not_found"
                logger.info(
                    "event=finance_not_found op=%s symbol=%s source=%s",
                    op,
                    symbol,
                    getattr(client, "name", "?"),
                )
            except FinanceError as exc:
                last_error = str(exc)
                last_code = exc.code
                logger.warning(
                    "event=finance_provider_fail op=%s symbol=%s source=%s code=%s",
                    op,
                    symbol,
                    getattr(client, "name", "?"),
                    exc.code,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = "Something went wrong fetching market data."
                last_code = "provider"
                logger.exception(
                    "event=finance_unexpected op=%s symbol=%s source=%s err=%s",
                    op,
                    symbol,
                    getattr(client, "name", "?"),
                    type(exc).__name__,
                )
        friendly = {
            "not_found": f"I couldn't find reliable data for {symbol}.",
            "rate_limit": "Market data is rate-limited right now — try again in a minute.",
            "timeout": "The market data provider timed out. Try once more shortly.",
            "provider": "Market data is briefly unavailable. Try again in a moment.",
        }.get(last_code, last_error)
        return FinanceResult(ok=False, error=friendly, error_code=last_code)

    def _cached(
        self,
        namespace: str,
        key_part: str,
        ttl: int,
        producer: Callable[[], FinanceResult],
    ) -> FinanceResult:
        from core.utils.cache_helpers import cache_get, cache_set

        cache_key = make_cache_key(namespace, key_part)
        hit = cache_get(cache_key)
        if isinstance(hit, dict) and hit.get("ok"):
            return FinanceResult(
                ok=True,
                data=hit.get("data"),
                source=hit.get("source") or "",
                cached=True,
            )

        result = producer()
        if result.ok:
            cache_set(cache_key, result.to_dict(), ttl)
        return result
