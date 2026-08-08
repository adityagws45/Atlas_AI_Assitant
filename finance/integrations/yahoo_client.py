"""Yahoo Finance client — fallback provider via yfinance."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from finance.types import (
    AnalystRating,
    CompanyMetrics,
    CompanyProfile,
    EarningsEvent,
    FinanceError,
    FinanceNotFound,
    FinanceTimeout,
    MarketMover,
    NewsItem,
    SecFiling,
    StockQuote,
)

logger = logging.getLogger("atlas.finance.yahoo")

# Liquid proxies for "market movers" when Yahoo screener is unavailable
_MOVER_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "NFLX",
    "JPM", "V", "MA", "XOM", "UNH", "COST", "CRM", "ORCL", "INTC", "BA",
]


class YahooClient:
    name = "yahoo"

    def __init__(self, *, timeout: float = 15.0) -> None:
        self.timeout = timeout

    def _ticker(self, symbol: str):
        try:
            import yfinance as yf
        except ImportError as exc:
            raise FinanceError("yfinance is not installed") from exc
        return yf.Ticker(symbol)

    def get_quote(self, symbol: str) -> StockQuote:
        try:
            t = self._ticker(symbol)
            info = t.fast_info if hasattr(t, "fast_info") else {}
            # fast_info can be object-like
            price = _attr(info, "last_price") or _attr(info, "lastPrice")
            prev = _attr(info, "previous_close") or _attr(info, "previousClose")
            if price is None:
                hist = t.history(period="5d")
                if hist is None or hist.empty:
                    raise FinanceNotFound(f"No Yahoo quote for {symbol}")
                price = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else None
            price_f = _f(price)
            prev_f = _f(prev)
            change = None
            pct = None
            if price_f is not None and prev_f not in (None, 0):
                change = round(price_f - prev_f, 4)
                pct = round((change / prev_f) * 100, 4)
            return StockQuote(
                symbol=symbol,
                price=price_f,
                change=change,
                change_percent=pct,
                open=_f(_attr(info, "open")),
                high=_f(_attr(info, "day_high") or _attr(info, "dayHigh")),
                low=_f(_attr(info, "day_low") or _attr(info, "dayLow")),
                previous_close=prev_f,
                volume=_i(_attr(info, "last_volume") or _attr(info, "lastVolume")),
                currency=str(_attr(info, "currency") or "USD"),
                as_of=datetime.now(tz=timezone.utc).isoformat(),
                market_state=str(_attr(info, "market_state") or _attr(info, "marketState") or "unknown"),
            )
        except FinanceNotFound:
            raise
        except Exception as exc:  # noqa: BLE001
            if "timeout" in str(exc).lower():
                raise FinanceTimeout(str(exc)) from exc
            raise FinanceError(f"Yahoo quote failed: {exc}") from exc

    def get_profile(self, symbol: str) -> CompanyProfile:
        try:
            info = self._ticker(symbol).info or {}
        except Exception as exc:  # noqa: BLE001
            raise FinanceError(f"Yahoo profile failed: {exc}") from exc
        name = info.get("longName") or info.get("shortName") or ""
        if not name:
            raise FinanceNotFound(f"No Yahoo profile for {symbol}")
        return CompanyProfile(
            symbol=symbol,
            name=name,
            exchange=info.get("exchange") or info.get("fullExchangeName") or "",
            industry=info.get("industry") or "",
            sector=info.get("sector") or "",
            country=info.get("country") or "",
            website=info.get("website") or "",
            description=(info.get("longBusinessSummary") or "")[:1200],
            market_cap=_f(info.get("marketCap")),
            employees=_i(info.get("fullTimeEmployees")),
            ipo="",
            logo="",
        )

    def get_metrics(self, symbol: str) -> CompanyMetrics:
        try:
            info = self._ticker(symbol).info or {}
        except Exception as exc:  # noqa: BLE001
            raise FinanceError(f"Yahoo metrics failed: {exc}") from exc
        if not info.get("symbol") and not info.get("shortName") and not info.get("trailingPE"):
            # Still may have useful fields
            pass
        return CompanyMetrics(
            symbol=symbol,
            pe=_f(info.get("trailingPE")),
            forward_pe=_f(info.get("forwardPE")),
            peg=_f(info.get("pegRatio")),
            eps=_f(info.get("trailingEps")),
            revenue_ttm=_f(info.get("totalRevenue")),
            gross_margin=_f(info.get("grossMargins")),
            operating_margin=_f(info.get("operatingMargins")),
            profit_margin=_f(info.get("profitMargins")),
            roe=_f(info.get("returnOnEquity")),
            debt_to_equity=_f(info.get("debtToEquity")),
            dividend_yield=_f(info.get("dividendYield")),
            beta=_f(info.get("beta")),
            fifty_two_week_high=_f(info.get("fiftyTwoWeekHigh")),
            fifty_two_week_low=_f(info.get("fiftyTwoWeekLow")),
            revenue_growth=_f(info.get("revenueGrowth")),
            earnings_growth=_f(info.get("earningsGrowth")),
        )

    def get_news(self, symbol: str, *, limit: int = 5) -> list[NewsItem]:
        try:
            rows = self._ticker(symbol).news or []
        except Exception as exc:  # noqa: BLE001
            raise FinanceError(f"Yahoo news failed: {exc}") from exc
        items: list[NewsItem] = []
        for row in rows[: max(1, limit)]:
            # yfinance news shape varies by version
            content = row.get("content") if isinstance(row.get("content"), dict) else None
            if content:
                title = content.get("title") or ""
                summary = content.get("summary") or ""
                pub = content.get("pubDate") or ""
                url = ""
                click = content.get("clickThroughUrl") or {}
                if isinstance(click, dict):
                    url = click.get("url") or ""
                provider = content.get("provider") or {}
                source = provider.get("displayName") if isinstance(provider, dict) else ""
            else:
                title = row.get("title") or ""
                summary = ""
                url = row.get("link") or row.get("url") or ""
                source = ""
                if isinstance(row.get("publisher"), str):
                    source = row.get("publisher") or ""
                ts = row.get("providerPublishTime")
                pub = (
                    datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                    if isinstance(ts, (int, float))
                    else ""
                )
            if not title:
                continue
            items.append(
                NewsItem(
                    title=title.strip(),
                    summary=(summary or "")[:400],
                    source=source or "Yahoo",
                    url=url,
                    published_at=str(pub),
                    symbol=symbol,
                )
            )
        return items

    def get_earnings(self, symbol: str, *, limit: int = 4) -> list[EarningsEvent]:
        try:
            t = self._ticker(symbol)
            df = getattr(t, "earnings_dates", None)
            if callable(df):
                df = t.earnings_dates
            # Newer yfinance: earnings_history / calendar
            out: list[EarningsEvent] = []
            if df is not None and hasattr(df, "iterrows"):
                for idx, row in list(df.iterrows())[:limit]:
                    out.append(
                        EarningsEvent(
                            symbol=symbol,
                            period=str(idx),
                            report_date=str(idx),
                            eps_actual=_f(row.get("Reported EPS") or row.get("EPS Actual")),
                            eps_estimate=_f(row.get("EPS Estimate")),
                            surprise_percent=_f(row.get("Surprise(%)")),
                        )
                    )
                if out:
                    return out
            cal = t.calendar
            if isinstance(cal, dict):
                earn = cal.get("Earnings Date") or cal.get("earningsDate")
                return [
                    EarningsEvent(
                        symbol=symbol,
                        period="next",
                        report_date=str(earn[0]) if isinstance(earn, (list, tuple)) and earn else str(earn or ""),
                        eps_estimate=_f(cal.get("Earnings Average") or cal.get("epsAverage")),
                        revenue_estimate=_f(cal.get("Revenue Average") or cal.get("revenueAverage")),
                    )
                ]
            return []
        except Exception as exc:  # noqa: BLE001
            raise FinanceError(f"Yahoo earnings failed: {exc}") from exc

    def get_filings(self, symbol: str, *, form: str = "", limit: int = 5) -> list[SecFiling]:
        # Yahoo doesn't expose SEC cleanly — return empty to allow SEC client
        return []

    def get_recommendations(self, symbol: str) -> AnalystRating:
        try:
            t = self._ticker(symbol)
            info = t.info or {}
            rec = (info.get("recommendationKey") or "").replace("_", " ").title()
            target = _f(info.get("targetMeanPrice"))
            return AnalystRating(
                symbol=symbol,
                consensus=rec or "N/A",
                target_mean=target,
                target_high=_f(info.get("targetHighPrice")),
                target_low=_f(info.get("targetLowPrice")),
            )
        except Exception as exc:  # noqa: BLE001
            raise FinanceError(f"Yahoo ratings failed: {exc}") from exc

    def get_market_news(self, *, limit: int = 8) -> list[NewsItem]:
        # Use a liquid ETF as a proxy for general market news feed
        return self.get_news("SPY", limit=limit)

    def get_movers(self) -> dict[str, list[MarketMover]]:
        movers: list[MarketMover] = []
        for sym in _MOVER_UNIVERSE:
            try:
                q = self.get_quote(sym)
                if q.change_percent is None:
                    continue
                movers.append(
                    MarketMover(
                        symbol=sym,
                        change_percent=q.change_percent,
                        price=q.price,
                        direction="up" if (q.change_percent or 0) >= 0 else "down",
                    )
                )
            except Exception:  # noqa: BLE001
                continue
        gainers = sorted(
            [m for m in movers if (m.change_percent or 0) > 0],
            key=lambda m: m.change_percent or 0,
            reverse=True,
        )[:5]
        losers = sorted(
            [m for m in movers if (m.change_percent or 0) < 0],
            key=lambda m: m.change_percent or 0,
        )[:5]
        return {"gainers": gainers, "losers": losers}


def _attr(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


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
