"""
Milestone 4 quality pass verification — Finnhub primary, analyst UX, cache, fallback.
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
from ai.services.clarification_engine import ClarificationEngine  # noqa: E402
from ai.types import ToolRequest  # noqa: E402
from conversation.services.message_service import MessageService  # noqa: E402
from conversation.services.orchestrator import ConversationOrchestrator  # noqa: E402
from conversation.services.response_formatter import ResponseFormatter  # noqa: E402
from finance.integrations.finnhub_client import FinnhubClient  # noqa: E402
from finance.integrations.yahoo_client import YahooClient  # noqa: E402
from finance.services.finance_service import FinanceService  # noqa: E402
from finance.services.news_cluster import cluster_news  # noqa: E402
from finance.services.payload_sanitize import sanitize_tool_payload  # noqa: E402
from finance.types import FinanceError, FinanceNotFound, FinanceRateLimit, FinanceTimeout  # noqa: E402
from memory.models import AssistantMemory  # noqa: E402
from memory.models import UserPreference  # noqa: E402
from memory.services.research_tracker import ResearchInterestTracker  # noqa: E402
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


def test_finnhub_primary() -> None:
    key = (settings.FINNHUB_API_KEY or "").strip()
    check("finnhub_key_loaded", bool(key) and len(key) > 10)
    check("primary_is_finnhub", settings.FINANCE_PRIMARY_PROVIDER.lower() == "finnhub")
    check("fallback_is_yahoo", settings.FINANCE_FALLBACK_PROVIDER.lower() == "yahoo")

    fh = FinnhubClient()
    check("finnhub_configured", fh.configured is True)
    q = fh.get_quote("AAPL")
    check("finnhub_auth_quote", q.price is not None and q.price > 0)

    cache.clear()
    svc = FinanceService()
    for label, fn in [
        ("quote", lambda: svc.get_quote("MSFT")),
        ("profile", lambda: svc.get_profile("MSFT")),
        ("metrics", lambda: svc.get_metrics("MSFT")),
        ("news", lambda: svc.get_news("MSFT", limit=3)),
        ("earnings", lambda: svc.get_earnings("MSFT", limit=2)),
        ("ratings", lambda: svc.get_analyst_ratings("MSFT")),
    ]:
        r = fn()
        check(f"primary_{label}", r.ok and r.source == "finnhub", f"source={r.source} err={r.error}")

    movers = svc.get_market_movers()
    check("movers_fallback_or_ok", movers.ok, movers.error)
    note(f"movers source={movers.source} (yahoo expected if finnhub lacks movers)")


def test_yahoo_fallback_forced() -> None:
    class Broken(FinnhubClient):
        @property
        def configured(self) -> bool:
            return True

        def get_quote(self, symbol: str):
            raise FinanceTimeout("forced timeout")

        def get_profile(self, symbol: str):
            raise FinanceRateLimit("forced 429")

        def get_metrics(self, symbol: str):
            raise FinanceError("bad payload", code="provider")

    cache.clear()
    svc = FinanceService(finnhub=Broken(api_key="x"), yahoo=YahooClient())
    q = svc.get_quote("AAPL")
    check("fallback_timeout_quote", q.ok and q.source == "yahoo", str(q.to_dict())[:120])
    p = svc.get_profile("AAPL")
    check("fallback_rate_limit_profile", p.ok and p.source == "yahoo", p.error)
    m = svc.get_metrics("AAPL")
    check("fallback_invalid_metrics", m.ok and m.source == "yahoo", m.error)


def test_normalization_and_sanitize() -> None:
    cache.clear()
    svc = FinanceService()
    r = svc.get_quote("NVDA")
    d = r.to_dict()["data"]
    check("normalized_quote_fields", all(k in d for k in ("symbol", "price", "change_percent")))
    news = svc.get_news("NVDA", limit=5)
    raw = news.to_dict()
    clean = sanitize_tool_payload("company_news", raw)
    check("sanitize_strips_source", "source" not in clean and "cached" not in clean)
    themes = (clean.get("data") or {}).get("news_themes") or {}
    check("news_clustered", themes.get("cluster_count", 0) >= 1, str(themes)[:160])
    note(f"clusters={ [c.get('theme') for c in themes.get('clusters', [])] }")


def test_clarification_ux() -> None:
    eng = ClarificationEngine()
    r = eng.evaluate("Tell me about Apple")
    check("clarify_needed", r.needed is True)
    q = r.suggested_question or ""
    check("clarify_has_options", "Company Overview" in q and "Earnings" in q and "Products" in q)
    check("clarify_skip_clear", eng.evaluate("What's Apple's latest earnings?").needed is False)


def test_research_memory() -> None:
    tid = 9800000601
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="Mem", onboarding_completed=True)
    tracker = ResearchInterestTracker()
    tracker.remember_symbols(user, ["MSFT", "GOOGL"])
    tracker.remember_symbols(user, ["NVDA"])
    mem = AssistantMemory.objects.filter(user=user, key="researched_companies").first()
    check("research_memory_exists", mem is not None)
    vals = [str(x).upper() for x in (mem.value if mem else [])]
    check("research_memory_order", vals[:3] == ["NVDA", "MSFT", "GOOGL"], str(vals))


def test_cache_no_failures() -> None:
    cache.clear()
    svc = FinanceService()
    bad = svc.get_quote("ZZZZNOTREALTICKER")
    check("unknown_fails", bad.ok is False)
    # Ensure we didn't cache failure under a success shape
    from core.utils.cache_helpers import make_cache_key, cache_get

    hit = cache_get(make_cache_key("quote", "ZZZZNOTREALTICKER"))
    check("failures_not_cached", hit is None)


def test_response_formatter_security() -> None:
    fmt = ResponseFormatter()
    out = fmt.format("Certainly! According to Finnhub and Yahoo, AAPL is fine.")
    check("formatter_strips_providers", "finnhub" not in out.lower() and "yahoo" not in out.lower())
    check("formatter_strips_certainly", not out.lower().startswith("certainly"))


def test_tool_router_matrix() -> None:
    cache.clear()
    router = ToolRouter()
    cases = [
        ("stock_quote", {"symbol": "AAPL"}),
        ("company_research", {"symbol": "MSFT"}),
        ("company_compare", {"symbols": ["MSFT", "GOOGL"]}),
        ("company_news", {"symbol": "NVDA", "limit": 3}),
        ("earnings", {"symbol": "AAPL"}),
        ("sec_filings", {"symbol": "AAPL"}),
        ("market_overview", {}),
        ("market_movers", {}),
        ("analyst_ratings", {"symbol": "AAPL"}),
        ("company_metrics", {"symbol": "AAPL"}),
        ("company_profile", {"symbol": "AAPL"}),
    ]
    for name, args in cases:
        result = router.execute(ToolRequest(name=name, arguments=args, reason="qa"))
        # sec/earnings may be empty-but-ok; require no crash and structured dict
        check(
            f"tool_{name}",
            isinstance(result, dict) and ("ok" in result),
            str(result)[:120],
        )


def test_live_gemini_quality() -> None:
    key = (settings.GEMINI_API_KEY or "").strip()
    if not key:
        print("SKIP  live_gemini_quality")
        return
    tid = 9800000602
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(
        telegram_id=tid,
        first_name="Analyst",
        role="analyst",
        onboarding_completed=True,
        onboarding_step="done",
    )
    UserPreference.objects.create(user=user, sectors_of_interest=["semiconductors", "software"])
    conv = MessageService.get_or_create_active_conversation(user)
    orch = ConversationOrchestrator()

    clar = orch.process(user, conv, "Tell me about Apple")
    check(
        "live_clarify_apple",
        "?" in clar["reply"] and "Overview" in clar["reply"],
        clar["reply"][:200],
    )
    note(f"clarification: {clar['reply'][:180]}")

    time.sleep(1)
    cmp_out = orch.process(user, conv, "Compare Microsoft and Google")
    reply = cmp_out["reply"]
    check("live_compare_length", 80 < len(reply) < 3500, f"len={len(reply)}")
    check(
        "live_compare_no_provider_leak",
        "finnhub" not in reply.lower() and "yahoo" not in reply.lower() and "{" not in reply[:30],
        reply[:160],
    )
    check(
        "live_compare_has_reasoning",
        any(w in reply.lower() for w in ("matter", "because", "bottom", "while", "stronger", "risk")),
        reply[:200],
    )
    note(f"compare: {reply[:240]}")

    # researched companies memory after compare
    mem = AssistantMemory.objects.filter(user=user, key="researched_companies").first()
    check("live_research_memory", mem is not None and len(mem.value or []) >= 2, str(getattr(mem, "value", None)))


def main() -> None:
    print("=== Milestone 4 quality pass verification ===")
    test_finnhub_primary()
    test_yahoo_fallback_forced()
    test_normalization_and_sanitize()
    test_clarification_ux()
    test_research_memory()
    test_cache_no_failures()
    test_response_formatter_security()
    test_tool_router_matrix()
    test_live_gemini_quality()

    print(f"\nRESULT: {PASSED} passed, {FAILED} failed")
    for n in NOTES:
        print(f"  - {n}")
    if FAILED:
        print("MILESTONE_4_QUALITY: FAIL")
        sys.exit(1)
    print("MILESTONE_4_QUALITY: PASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
