"""Cluster company/market news into themes for analyst-style synthesis."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

THEME_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI / product demand", ("ai", "gpu", "chip", "semiconductor", "datacenter", "cloud", "model")),
    ("Earnings / guidance", ("earn", "guidance", "eps", "revenue", "outlook", "forecast", "quarter")),
    ("Partnerships / deals", ("partner", "deal", "acquisi", "merger", "invest", "stake", "joint")),
    ("Regulation / legal", ("regulat", "antitrust", "lawsuit", "sec ", "probe", "fine", "ban")),
    ("Macro / rates", ("fed", "rate", "inflation", "yield", "recession", "macro")),
    ("Competition", ("rival", "compet", "vs ", "market share", "undercut")),
    ("Leadership / strategy", ("ceo", "cfo", "appoint", "resign", "strategy", "restructur")),
]


def _norm_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9\s]", "", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def dedupe_news(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = _norm_title(str(item.get("title") or ""))[:80]
        if not key or key in seen:
            continue
        # Near-duplicate: share first 8 tokens
        tokens = " ".join(key.split()[:8])
        if tokens in seen:
            continue
        seen.add(key)
        seen.add(tokens)
        out.append(item)
    return out


def cluster_news(items: list[dict[str, Any]] | list[Any], *, max_clusters: int = 4) -> dict[str, Any]:
    """
    Return {clusters: [{theme, headlines, count}], uncategorized: [...]} 
    after deduplication.
    """
    normalized: list[dict[str, Any]] = []
    for item in items or []:
        if hasattr(item, "to_dict"):
            normalized.append(item.to_dict())
        elif isinstance(item, dict):
            normalized.append(item)
    unique = dedupe_news(normalized)

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    leftover: list[dict[str, Any]] = []
    for item in unique:
        blob = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        matched = None
        for theme, keywords in THEME_RULES:
            if any(k in blob for k in keywords):
                matched = theme
                break
        if matched:
            buckets[matched].append(item)
        else:
            leftover.append(item)

    clusters = []
    for theme, rows in buckets.items():
        clusters.append(
            {
                "theme": theme,
                "count": len(rows),
                "headlines": [r.get("title") for r in rows[:3] if r.get("title")],
                "sources": list({r.get("source") for r in rows if r.get("source")})[:3],
            }
        )
    clusters.sort(key=lambda c: c["count"], reverse=True)
    clusters = clusters[:max_clusters]

    if leftover and len(clusters) < max_clusters:
        clusters.append(
            {
                "theme": "Other developments",
                "count": len(leftover),
                "headlines": [r.get("title") for r in leftover[:3] if r.get("title")],
                "sources": list({r.get("source") for r in leftover if r.get("source")})[:3],
            }
        )

    return {
        "cluster_count": len(clusters),
        "article_count": len(unique),
        "clusters": clusters,
        # Keep a tiny sample of raw titles only — no full dump
        "sample_titles": [u.get("title") for u in unique[:5] if u.get("title")],
    }
