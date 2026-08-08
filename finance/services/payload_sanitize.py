"""Sanitize finance tool payloads before Gemini synthesis."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from finance.services.news_cluster import cluster_news

PROVIDER_KEYS = {"source", "provider", "cached", "raw", "url", "logo", "accession"}


def sanitize_tool_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Strip provider provenance and reshape news into clusters.
    Gemini should reason on facts, not know Finnhub vs Yahoo.
    """
    clean = deepcopy(payload) if isinstance(payload, dict) else {"ok": False, "data": None}
    clean.pop("source", None)
    clean.pop("cached", None)
    clean.pop("tool", None)
    clean.pop("tool_ok", None)

    data = clean.get("data")
    if tool_name in {"company_news", "market_overview"} and isinstance(data, list):
        clean["data"] = {"news_themes": cluster_news(data)}
    elif tool_name == "company_research" and isinstance(data, dict):
        news = data.get("news")
        if isinstance(news, list):
            data = dict(data)
            data["news_themes"] = cluster_news(news)
            data.pop("news", None)
            clean["data"] = data
        clean["data"] = _strip_deep(clean.get("data"))
    elif tool_name == "company_compare" and isinstance(data, dict):
        companies = data.get("companies") or []
        slim = []
        for row in companies:
            slim.append(
                {
                    "symbol": row.get("symbol"),
                    "profile": _strip_deep((row.get("profile") or {}).get("data") or row.get("profile")),
                    "quote": _strip_deep((row.get("quote") or {}).get("data") or row.get("quote")),
                    "metrics": _strip_deep((row.get("metrics") or {}).get("data") or row.get("metrics")),
                }
            )
        clean["data"] = {"companies": slim}
    else:
        clean["data"] = _strip_deep(data)

    # Keep ok/error for honesty; drop error_code internals optionally
    if clean.get("error_code"):
        clean["status"] = clean.pop("error_code")
    return clean


def _strip_deep(value: Any) -> Any:
    if isinstance(value, dict):
        # Nested FinanceResult-shaped dicts
        if "data" in value and ("ok" in value or "source" in value):
            return _strip_deep(value.get("data"))
        out = {}
        for k, v in value.items():
            if k in PROVIDER_KEYS:
                continue
            out[k] = _strip_deep(v)
        return out
    if isinstance(value, list):
        return [_strip_deep(v) for v in value]
    return value
