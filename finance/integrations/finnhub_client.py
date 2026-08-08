"""Finnhub market data client — primary provider."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from django.conf import settings

from finance.types import (
    AnalystRating,
    CompanyMetrics,
    CompanyProfile,
    EarningsEvent,
    FinanceError,
    FinanceNotFound,
    FinanceRateLimit,
    FinanceTimeout,
    MarketMover,
    NewsItem,
    SecFiling,
    StockQuote,
)

logger = logging.getLogger("atlas.finance.finnhub")


class FinnhubClient:
    name = "finnhub"

    def __init__(self, api_key: str | None = None, *, timeout: float = 12.0) -> None:
        self.api_key = (api_key if api_key is not None else getattr(settings, "FINNHUB_API_KEY", "") or "").strip()
        self.timeout = timeout
        self._client = None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _sdk(self):
        if not self.configured:
            raise FinanceError("Finnhub API key not configured", code="provider")
        if self._client is None:
            import finnhub

            self._client = finnhub.Client(api_key=self.api_key)
        return self._client

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "429" in msg or "rate" in msg:
                raise FinanceRateLimit(str(exc)) from exc
            if "timeout" in msg or "timed out" in msg:
                raise FinanceTimeout(str(exc)) from exc
            if "404" in msg or "not found" in msg:
                raise FinanceNotFound(str(exc)) from exc
            raise FinanceError(str(exc)[:300]) from exc

    def get_quote(self, symbol: str) -> StockQuote:
        data = self._call(self._sdk().quote, symbol)
        if not data or data.get("c") in (None, 0) and data.get("pc") in (None, 0):
            # Finnhub returns zeros for unknown symbols sometimes
            if not data or (data.get("c") == 0 and data.get("pc") == 0 and data.get("t") == 0):
                raise FinanceNotFound(f"No quote for {symbol}")
        price = _f(data.get("c"))
        prev = _f(data.get("pc"))
        change = _f(data.get("d"))
        pct = _f(data.get("dp"))
        ts = data.get("t") or 0
        as_of = (
            datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if ts
            else ""
        )
        return StockQuote(
            symbol=symbol,
            price=price,
            change=change,
            change_percent=pct,
            open=_f(data.get("o")),
            high=_f(data.get("h")),
            low=_f(data.get("l")),
            previous_close=prev,
            as_of=as_of,
            market_state="unknown",
        )

    def get_profile(self, symbol: str) -> CompanyProfile:
        data = self._call(self._sdk().company_profile2, symbol=symbol) or {}
        if not data or not data.get("name"):
            raise FinanceNotFound(f"No profile for {symbol}")
        return CompanyProfile(
            symbol=symbol,
            name=data.get("name") or "",
            exchange=data.get("exchange") or "",
            industry=data.get("finnhubIndustry") or "",
            country=data.get("country") or "",
            website=data.get("weburl") or "",
            description="",
            market_cap=_f(data.get("marketCapitalization")),
            employees=_i(data.get("employeeTotal")),
            ipo=data.get("ipo") or "",
            logo=data.get("logo") or "",
        )

    def get_metrics(self, symbol: str) -> CompanyMetrics:
        data = self._call(self._sdk().company_basic_financials, symbol, "all") or {}
        metric = data.get("metric") or {}
        if not metric:
            raise FinanceNotFound(f"No metrics for {symbol}")
        return CompanyMetrics(
            symbol=symbol,
            pe=_f(metric.get("peNormalizedAnnual") or metric.get("peBasicExclExtraTTM")),
            forward_pe=_f(metric.get("forwardPE")),
            peg=_f(metric.get("pegRatio")),
            eps=_f(metric.get("epsNormalizedAnnual") or metric.get("epsBasicExclExtraItemsTTM")),
            revenue_ttm=_f(metric.get("revenuePerShareTTM")),
            gross_margin=_f(metric.get("grossMarginTTM")),
            operating_margin=_f(metric.get("operatingMarginTTM")),
            profit_margin=_f(metric.get("netProfitMarginTTM")),
            roe=_f(metric.get("roeTTM")),
            debt_to_equity=_f(metric.get("totalDebt/totalEquityAnnual")),
            dividend_yield=_f(metric.get("dividendYieldIndicatedAnnual")),
            beta=_f(metric.get("beta")),
            fifty_two_week_high=_f(metric.get("52WeekHigh")),
            fifty_two_week_low=_f(metric.get("52WeekLow")),
            revenue_growth=_f(metric.get("revenueGrowthTTMYoy")),
            earnings_growth=_f(metric.get("epsGrowthTTMYoy")),
        )

    def get_news(self, symbol: str, *, limit: int = 5) -> list[NewsItem]:
        end = datetime.now(tz=timezone.utc).date()
        start = end - timedelta(days=14)
        rows = self._call(
            self._sdk().company_news,
            symbol,
            _d(start),
            _d(end),
        ) or []
        items: list[NewsItem] = []
        for row in rows[: max(1, limit)]:
            ts = row.get("datetime") or 0
            published = (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                if ts
                else ""
            )
            items.append(
                NewsItem(
                    title=(row.get("headline") or "").strip(),
                    summary=(row.get("summary") or "").strip()[:400],
                    source=row.get("source") or "",
                    url=row.get("url") or "",
                    published_at=published,
                    symbol=symbol,
                )
            )
        return [i for i in items if i.title]

    def get_earnings(self, symbol: str, *, limit: int = 4) -> list[EarningsEvent]:
        data = self._call(self._sdk().company_earnings, symbol, limit=limit) or []
        out: list[EarningsEvent] = []
        for row in data[:limit]:
            actual = _f(row.get("actual"))
            estimate = _f(row.get("estimate"))
            surprise = None
            if actual is not None and estimate not in (None, 0):
                surprise = round(((actual - estimate) / abs(estimate)) * 100, 2)
            out.append(
                EarningsEvent(
                    symbol=symbol,
                    period=str(row.get("period") or ""),
                    report_date=str(row.get("period") or ""),
                    eps_actual=actual,
                    eps_estimate=estimate,
                    surprise_percent=surprise,
                )
            )
        return out

    def get_filings(self, symbol: str, *, form: str = "", limit: int = 5) -> list[SecFiling]:
        # Finnhub filings endpoint
        try:
            rows = self._call(self._sdk().filings, symbol=symbol) or []
        except FinanceError:
            raise
        out: list[SecFiling] = []
        for row in rows:
            f = (row.get("form") or "").strip()
            if form and form.upper() not in f.upper():
                continue
            out.append(
                SecFiling(
                    symbol=symbol,
                    form=f,
                    filed_at=str(row.get("filedDate") or row.get("acceptedDate") or ""),
                    description=f,
                    url=row.get("reportUrl") or row.get("filingUrl") or "",
                    accession=row.get("accessNumber") or "",
                )
            )
            if len(out) >= limit:
                break
        return out

    def get_recommendations(self, symbol: str) -> AnalystRating:
        rows = self._call(self._sdk().recommendation_trends, symbol) or []
        if not rows:
            raise FinanceNotFound(f"No analyst ratings for {symbol}")
        latest = rows[0]
        sb = _i(latest.get("strongBuy")) or 0
        b = _i(latest.get("buy")) or 0
        h = _i(latest.get("hold")) or 0
        s = _i(latest.get("sell")) or 0
        ss = _i(latest.get("strongSell")) or 0
        consensus = _consensus(sb, b, h, s, ss)
        return AnalystRating(
            symbol=symbol,
            strong_buy=sb,
            buy=b,
            hold=h,
            sell=s,
            strong_sell=ss,
            consensus=consensus,
        )

    def get_market_news(self, *, limit: int = 8) -> list[NewsItem]:
        rows = self._call(self._sdk().general_news, "general", min_id=0) or []
        items: list[NewsItem] = []
        for row in rows[:limit]:
            ts = row.get("datetime") or 0
            published = (
                datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                if ts
                else ""
            )
            items.append(
                NewsItem(
                    title=(row.get("headline") or "").strip(),
                    summary=(row.get("summary") or "").strip()[:400],
                    source=row.get("source") or "",
                    url=row.get("url") or "",
                    published_at=published,
                )
            )
        return [i for i in items if i.title]

    def get_movers(self) -> dict[str, list[MarketMover]]:
        # Finnhub free tier may not include market movers; raise to trigger fallback
        raise FinanceError("Finnhub movers not available on this plan", code="provider")


def _f(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v: Any) -> int | None:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _d(value) -> str:
    return value.isoformat()


def _consensus(sb: int, b: int, h: int, s: int, ss: int) -> str:
    scores = {
        "Strong Buy": sb,
        "Buy": b,
        "Hold": h,
        "Sell": s,
        "Strong Sell": ss,
    }
    return max(scores, key=scores.get) if any(scores.values()) else "N/A"
