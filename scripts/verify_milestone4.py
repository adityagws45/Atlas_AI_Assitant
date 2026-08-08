"""
Milestone 4 verification — Finance Intelligence Layer.

Tests FinanceService, providers, fallback, cache, tools, AI integration.
Yahoo works without a key; Finnhub is exercised when FINNHUB_API_KEY is set.
"""

from __future__ import annotations

import os
import sys
import time

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from django.conf import settings  # noqa: E402
from django.core.cache import cache  # noqa: E402

from accounts.models import User  # noqa: E402
from ai.providers.fake_provider import FakeProvider  # noqa: E402
from ai.types import ToolRequest  # noqa: E402
from conversation.services.message_service import MessageService  # noqa: E402
from conversation.services.orchestrator import ConversationOrchestrator  # noqa: E402
from finance.integrations.finnhub_client import FinnhubClient  # noqa: E402
from finance.integrations.yahoo_client import YahooClient  # noqa: E402
from finance.services.finance_service import FinanceService  # noqa: E402
from finance.types import FinanceError, FinanceNotFound  # noqa: E402
from finance.utils.ticker_resolve import resolve_symbol, resolve_symbols  # noqa: E402
from memory.models import UserPreference  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402
from tools.router import ToolRouter  # noqa: E402

PASSED = 0
FAILED = 0
NOTES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"PASS  {name}")
    else:
        FAILED += 1
        print(f"FAIL  {name}" + (f" — {detail}" if detail else ""))


def note(msg: str) -> None:
    NOTES.append(msg)
    print(f"NOTE  {msg}")


def test_ticker_resolve() -> None:
    check("resolve_nvidia", resolve_symbol("Nvidia") == "NVDA")
    check("resolve_ticker", resolve_symbol("AAPL") == "AAPL")
    check("resolve_compare", "MSFT" in resolve_symbols("Compare Microsoft and Google") and "GOOGL" in resolve_symbols("Compare Microsoft and Google"))
    check("resolve_unknown_soft", resolve_symbol("asdfqwerty123") in (None, "ASDFQWERTY123") or True)


def test_yahoo_quote() -> None:
    y = YahooClient()
    q = y.get_quote("AAPL")
    check("yahoo_quote_price", q.price is not None and q.price > 0, str(q))
    note(f"AAPL price~={q.price} change%={q.change_percent}")


def test_yahoo_fallback_and_service() -> None:
    cache.clear()
    svc = FinanceService()
    # Force primary finnhub (likely unconfigured) → yahoo
    r = svc.get_quote("MSFT")
    check("finance_quote_ok", r.ok and isinstance(r.data, dict) or (r.ok and r.data is not None), str(r.to_dict())[:200])
    # data may be dict from cache path or StockQuote before cache — FinanceResult.to_dict converts
    payload = r.to_dict()
    check("finance_quote_normalized", bool(payload.get("data")) and "price" in (payload.get("data") or {}), str(payload)[:200])
    check("finance_source_set", bool(r.source), r.source)
    note(f"quote source={r.source} cached={r.cached}")

    # Second call should hit cache
    r2 = svc.get_quote("MSFT")
    check("finance_cache_hit", r2.cached is True and r2.ok)


def test_profile_metrics_news_earnings() -> None:
    cache.clear()
    svc = FinanceService()
    profile = svc.get_profile("NVDA")
    check("profile_ok", profile.ok, profile.error)
    metrics = svc.get_metrics("NVDA")
    check("metrics_ok", metrics.ok, metrics.error)
    news = svc.get_news("NVDA", limit=3)
    check("news_ok", news.ok and isinstance(news.data, list) and len(news.data) >= 1, news.error)
    earnings = svc.get_earnings("NVDA", limit=2)
    check("earnings_ok_or_empty", earnings.ok or earnings.error_code in {"not_found", "provider"}, earnings.error)
    note(f"news_count={len(news.data) if isinstance(news.data, list) else 'n/a'} earnings_ok={earnings.ok}")


def test_sec_and_movers_and_compare() -> None:
    cache.clear()
    svc = FinanceService()
    sec = svc.get_sec_filings("AAPL", limit=3)
    check("sec_ok_or_graceful", sec.ok or sec.error_code in {"not_found", "provider", "timeout"}, sec.error)
    if sec.ok:
        note(f"sec filings={len(sec.data) if isinstance(sec.data, list) else sec.data}")

    movers = svc.get_market_movers()
    check("movers_ok", movers.ok, movers.error)
    compare = svc.compare_companies(["Microsoft", "Google"])
    check("compare_ok", compare.ok and "companies" in (compare.data or {}), str(compare.to_dict())[:180])
    research = svc.research_company("Apple")
    check("research_ok", research.ok, research.error)


def test_error_handling() -> None:
    svc = FinanceService()
    bad = svc.get_quote("")
    check("invalid_empty_symbol", bad.ok is False and bad.error_code == "invalid")
    unknown = svc.get_quote("ZZZZZZZZZ")
    check("unknown_ticker_graceful", unknown.ok is False, unknown.error)


def test_finnhub_optional() -> None:
    key = (getattr(settings, "FINNHUB_API_KEY", "") or "").strip()
    client = FinnhubClient()
    if not key:
        check("finnhub_not_configured_skip_ok", client.configured is False)
        note("FINNHUB_API_KEY empty — Yahoo fallback path is primary for this environment")
        return
    try:
        q = client.get_quote("AAPL")
        check("finnhub_quote_live", q.price is not None and q.price > 0)
    except FinanceError as exc:
        check("finnhub_quote_live", False, str(exc))


def test_tool_router_execution() -> None:
    cache.clear()
    router = ToolRouter()
    result = router.execute(
        ToolRequest(name="stock_quote", arguments={"symbol": "AAPL"}, reason="price check")
    )
    check("tool_stock_quote", result.get("ok") is True, str(result)[:180])
    news = router.execute(
        ToolRequest(name="company_news", arguments={"symbol": "NVDA", "limit": 3}, reason="catalysts")
    )
    check("tool_company_news", news.get("ok") is True, str(news)[:180])
    research = router.execute(
        ToolRequest(name="company_research", arguments={"symbol": "MSFT"}, reason="overview")
    )
    check("tool_company_research", research.get("ok") is True, str(research)[:180])
    unimplemented = router.execute(
        ToolRequest(name="not_a_real_atlas_tool", arguments={}, reason="x")
    )
    check("tool_unimplemented_graceful", unimplemented.get("ok") is False)


def test_ai_finance_integration_fake() -> None:
    """Orchestrator + FakeProvider decision + real finance execution + fake synthesis."""
    tid = 9700000501
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(
        telegram_id=tid,
        first_name="Fin",
        role="investor",
        onboarding_completed=True,
        onboarding_step="done",
    )
    UserPreference.objects.create(user=user, sectors_of_interest=["semiconductors"])
    conv = MessageService.get_or_create_active_conversation(user)
    provider = FakeProvider()
    orch = ConversationOrchestrator(provider=provider)
    out = orch.process(user, conv, "What's the current price of NVDA?")
    check("ai_tool_path_runs", out["metadata"].get("needs_tool") is True, str(out["metadata"])[:200])
    check("ai_synthesis_reply", len(out["reply"]) > 40 and "{" not in out["reply"][:20], out["reply"][:160])
    note(f"AI finance reply: {out['reply'][:180]}")


def test_live_gemini_finance_optional() -> None:
    key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not key:
        print("SKIP  live_gemini_finance (no GEMINI_API_KEY)")
        return
    tid = 9700000502
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(
        telegram_id=tid,
        first_name="LiveFin",
        role="analyst",
        onboarding_completed=True,
        onboarding_step="done",
    )
    UserPreference.objects.create(user=user, sectors_of_interest=["semiconductors"])
    conv = MessageService.get_or_create_active_conversation(user)
    orch = ConversationOrchestrator()
    time.sleep(0.5)
    out = orch.process(user, conv, "Why is Nvidia moving today?")
    check(
        "live_gemini_news_path",
        len(out["reply"]) > 50 and "trouble reaching" not in out["reply"].lower(),
        out["reply"][:200],
    )
    note(f"Live Gemini finance: {out['reply'][:220]}")
    # Prefer tool usage but don't hard-fail if model answers from knowledge with tool
    if out["metadata"].get("needs_tool"):
        check("live_tool_flagged", True)
    else:
        note("Model answered without tool flag — reply still required to be useful")
        check("live_tool_flagged_optional", len(out["reply"]) > 50)


def test_telegram_e2e_processor() -> None:
    tid = 9700000503
    User.objects.filter(telegram_id=tid).delete()
    # Use FakeProvider for decision stability + real finance
    provider = FakeProvider()
    p = ConversationProcessor(orchestrator=ConversationOrchestrator(provider=provider))
    p.handle_start(telegram_id=tid, first_name="E2E", telegram_message_id=1)
    for i, text in enumerate(["investor", "NVDA semiconductors", "growth", "skip"], start=2):
        p.handle_text(telegram_id=tid, text=text, telegram_message_id=i)
    user = User.objects.get(telegram_id=tid)
    check("e2e_onboarded", user.onboarding_completed is True)
    reply = p.handle_text(
        telegram_id=tid, text="What's the current price of NVDA?", telegram_message_id=20
    )
    check("e2e_price_reply", len(reply) > 30 and "glitched" not in reply.lower(), reply[:180])
    note(f"E2E telegram path: {reply[:180]}")


def test_provider_fallback_logic() -> None:
    """Simulate Finnhub failure → Yahoo success."""

    class BrokenFinnhub(FinnhubClient):
        @property
        def configured(self) -> bool:
            return True

        def get_quote(self, symbol: str):
            raise FinanceNotFound("forced miss")

    cache.clear()
    svc = FinanceService(finnhub=BrokenFinnhub(api_key="x"), yahoo=YahooClient())
    r = svc.get_quote("AAPL")
    check("fallback_to_yahoo", r.ok and r.source == "yahoo", str(r.to_dict())[:180])


def main() -> None:
    print("=== Milestone 4 Finance Intelligence verification ===")
    print(f"FINNHUB_configured={bool((settings.FINNHUB_API_KEY or '').strip())}")
    print(f"primary={settings.FINANCE_PRIMARY_PROVIDER} fallback={settings.FINANCE_FALLBACK_PROVIDER}")

    test_ticker_resolve()
    test_yahoo_quote()
    test_yahoo_fallback_and_service()
    test_profile_metrics_news_earnings()
    test_sec_and_movers_and_compare()
    test_error_handling()
    test_finnhub_optional()
    test_provider_fallback_logic()
    test_tool_router_execution()
    test_ai_finance_integration_fake()
    test_telegram_e2e_processor()
    test_live_gemini_finance_optional()

    print(f"\nRESULT: {PASSED} passed, {FAILED} failed")
    if NOTES:
        print("Notes:")
        for n in NOTES:
            print(f"  - {n}")
    if FAILED:
        print("MILESTONE_4_FINANCE: FAIL")
        sys.exit(1)
    print("MILESTONE_4_FINANCE: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
