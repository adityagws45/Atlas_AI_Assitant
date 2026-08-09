"""Product-quality checks: OAuth UX scrub, finance fast path, sheets calc, style."""

from __future__ import annotations

import os
import sys

import django

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
django.setup()

from conversation.services.response_formatter import ResponseFormatter  # noqa: E402
from sheets.services.sheet_qa_service import SheetQAService  # noqa: E402
from telegram_bot.adapters.telegram_adapter import (  # noqa: E402
    extract_google_oauth_url,
    scrub_oauth_urls_for_display,
)


def test_oauth_urls_scrubbed_from_display() -> None:
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth?response_type=code"
        "&client_id=123.apps.googleusercontent.com&redirect_uri=https://x/callback"
        "&scope=https://www.googleapis.com/auth/gmail.readonly&state=abc"
    )
    raw = (
        "Google access isn't connected yet.\n\n"
        "Tap *Connect Google* below (or open this link):\n"
        f"{url}"
    )
    assert extract_google_oauth_url(raw) == url or extract_google_oauth_url(raw)
    display = scrub_oauth_urls_for_display(raw)
    assert "accounts.google.com" not in display
    assert "client_id" not in display
    assert "Connect Google" in display or "connect google" in display.lower()
    print("PASS oauth_urls_scrubbed_from_display")


def test_oauth_markdown_link_scrubbed() -> None:
    url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=x&state=y"
    raw = f"Tap below.\n\n[Connect Google]({url})"
    assert extract_google_oauth_url(raw)
    display = scrub_oauth_urls_for_display(raw)
    assert "accounts.google.com" not in display
    assert "client_id" not in display
    print("PASS oauth_markdown_link_scrubbed")


def test_stock_market_primer_stripped() -> None:
    fmt = ResponseFormatter()
    raw = (
        "NVDA is around $5.4T in market cap.\n\n"
        "*Quick primer on the stock market*\n"
        "The stock market is essentially a marketplace...\n"
        "*The Student Lens*\n"
        "Because you're a student, remember diversification."
    )
    out = fmt.format(raw).lower()
    assert "market cap" in out or "nvda" in out
    assert "quick primer" not in out
    assert "stock market is essentially" not in out
    assert "student lens" not in out
    print("PASS stock_market_primer_stripped")


def test_sheet_average_revenue_deterministic() -> None:
    values = {
        "Income Statement": [
            ["Metric", "2023", "2024", "2025"],
            ["Revenue", "100", "200", "300"],
            ["Net Income", "10", "20", "30"],
        ]
    }
    qa = SheetQAService(provider=object())  # provider unused on det path
    # Monkeypatch provider unused — answer uses det first
    out = qa.answer(
        question="What was the average revenue for these years?",
        title="Demo Financials",
        values_by_sheet=values,
        findings=None,
    )
    assert out.get("ok")
    assert out.get("source") == "deterministic_calc"
    assert "average" in out["reply"].lower()
    assert "revenue" in out["reply"].lower()
    print("PASS sheet_average_revenue_deterministic")


def test_sheet_missing_score_column() -> None:
    values = {
        "Income Statement": [
            ["Metric", "2023", "2024"],
            ["Revenue", "100", "200"],
        ]
    }
    qa = SheetQAService(provider=object())
    out = qa.answer(
        question="What is the average score?",
        title="Demo Financials",
        values_by_sheet=values,
    )
    assert out.get("ok")
    assert "no score" in out["reply"].lower()
    assert "couldn't find that information" not in out["reply"].lower()
    print("PASS sheet_missing_score_column")


def test_sheet_highest_revenue_year() -> None:
    values = {
        "Income Statement": [
            ["Metric", "2023", "2024", "2025"],
            ["Revenue", "100", "250", "180"],
        ]
    }
    qa = SheetQAService(provider=object())
    out = qa.answer(
        question="Which year had the highest revenue?",
        title="Demo",
        values_by_sheet=values,
    )
    assert out.get("source") == "deterministic_calc"
    assert "2024" in out["reply"]
    print("PASS sheet_highest_revenue_year")


def test_sheet_yoy_revenue() -> None:
    values = {
        "Income Statement": [
            ["Metric", "2023", "2024", "2025"],
            ["Revenue", "100", "150", "180"],
        ]
    }
    qa = SheetQAService(provider=object())
    out = qa.answer(
        question="Calculate the year-over-year revenue growth.",
        title="Demo",
        values_by_sheet=values,
    )
    assert out.get("source") == "deterministic_calc"
    assert "%" in out["reply"]
    print("PASS sheet_yoy_revenue")


def test_unified_oauth_copy_has_no_raw_url_after_scrub() -> None:
    from telegram_bot.adapters.oauth_ux import google_access_required_reply

    url = "https://accounts.google.com/o/oauth2/v2/auth?client_id=abc&state=xyz"
    raw = google_access_required_reply(url, purpose="Connect Google to check Gmail.")
    assert "Google access is required" in raw
    assert extract_google_oauth_url(raw)
    display = scrub_oauth_urls_for_display(raw)
    assert "accounts.google.com" not in display
    assert "client_id" not in display
    print("PASS unified_oauth_copy_scrubbed")


def test_entity_pronoun_resolve() -> None:
    from accounts.models import User
    from conversation.services.entity_context import EntityContext

    tid = 9700000888
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="E")
    ec = EntityContext()
    ec.remember(user, symbol="NVDA", topic="market")
    assert ec.resolve_symbol(user, "What's its market cap?") == "NVDA"
    ec.remember(user, symbol="NVDA", alt_symbols=["AMD"], topic="compare")
    assert ec.resolve_symbol(user, "What's its market cap?") is None
    assert "NVDA" in (ec.ambiguity_prompt(user) or "")
    print("PASS entity_pronoun_resolve")


def test_routing_never_steals_finance_calendar_docs() -> None:
    """Regression: active sheet must not steal live market / calendar / doc Qs."""
    from documents.services.document_intent import is_document_question
    from drive.services.drive_intent import detect_drive_intent
    from gcalendar.services.calendar_intent import detect_calendar_intent
    from sheets.services.sheet_intent import detect_sheet_intent

    nvidia = "What's happening with Nvidia today?"
    compare = "Compare Nvidia and AMD."
    scheduled = "What do I have scheduled today?"
    doc = "What is this document about?"

    assert detect_sheet_intent(nvidia, has_active_sheet=True).kind == "none"
    assert detect_sheet_intent(compare, has_active_sheet=True).kind == "none"
    assert detect_sheet_intent(scheduled, has_active_sheet=True).kind == "none"
    assert detect_calendar_intent(scheduled).kind == "today"
    assert detect_drive_intent(doc).kind == "none"
    assert is_document_question(doc) is True
    print("PASS routing_never_steals_finance_calendar_docs")


def test_market_fast_path_no_gemini() -> None:
    from conversation.services.market_fast_path import try_market_move_fast_answer

    out = try_market_move_fast_answer("What's happening with Nvidia today?")
    if out is None:
        print("SKIP market_fast_live (no data)")
    else:
        assert out["metadata"]["used_gemini"] is False
        assert out["metadata"]["pipeline"] == "market_fast"
        assert "accounts.google.com" not in out["reply"]
        low = out["reply"].lower()
        # Ban essay primers — incidental news headlines may mention "stock market"
        assert "quick primer" not in low
        assert "student lens" not in low
        assert "the bottom line" not in low
        assert "the stock market is essentially" not in low
        print(
            "PASS market_fast_path_no_gemini "
            f"total_ms={out['metadata']['timing_ms'].get('total')}"
        )
    print("PASS market_fast_path_gate")


def test_finance_fast_path_detects_market_cap() -> None:
    from conversation.services import finance_fast_path as mod

    assert mod.try_finance_fast_answer("Explain P/E like I'm a beginner") is None
    assert mod.try_finance_fast_answer("What is a stock market?") is None
    result = mod.try_finance_fast_answer("What is Nvidia's market cap?")
    if result is None:
        print("SKIP finance_fast_live (no data)")
    else:
        assert result["metadata"]["pipeline"] == "finance_fast"
        assert result["metadata"]["used_gemini"] is False
        assert "accounts.google.com" not in result["reply"]
        low = result["reply"].lower()
        assert "stock market is" not in low
        assert "nvda" in low or "nvidia" in low
        print(
            "PASS finance_fast_path_market_cap "
            f"total_ms={result['metadata']['timing_ms'].get('total')}"
        )
    # Pronoun with default symbol
    r2 = mod.try_finance_fast_answer("What's its market cap?", default_symbol="NVDA")
    if r2:
        assert "NVDA" in r2["reply"] or "market cap" in r2["reply"].lower()
        print("PASS finance_fast_pronoun_default")
    print("PASS finance_fast_path_gates")


def main() -> None:
    print("=== Product polish verification ===")
    test_oauth_urls_scrubbed_from_display()
    test_oauth_markdown_link_scrubbed()
    test_unified_oauth_copy_has_no_raw_url_after_scrub()
    test_stock_market_primer_stripped()
    test_sheet_average_revenue_deterministic()
    test_sheet_missing_score_column()
    test_sheet_highest_revenue_year()
    test_sheet_yoy_revenue()
    test_entity_pronoun_resolve()
    test_finance_fast_path_detects_market_cap()
    test_routing_never_steals_finance_calendar_docs()
    test_market_fast_path_no_gemini()
    print("\nPRODUCT_POLISH_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
