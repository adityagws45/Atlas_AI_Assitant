"""Prioritize and classify emails — finance-first, action-oriented."""

from __future__ import annotations

import re
from typing import Any, Iterable

from gmail.services.gmail_client import RemoteMessage
from gmail.services.gmail_relevance import (
    format_finance_digest,
    score_finance_relevance,
)

CATEGORY_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("urgent", re.compile(r"\b(urgent|asap|immediately|action required)\b", re.I)),
    ("invoice", re.compile(r"\b(invoice|payment due|billing|amount due)\b", re.I)),
    ("resume", re.compile(r"\b(resume|cv|application|candidate)\b", re.I)),
    ("meeting", re.compile(r"\b(meeting|invite|calendar|zoom|teams|rsvp)\b", re.I)),
    ("earnings", re.compile(r"\b(earnings|eps|guidance|quarterly results)\b", re.I)),
    (
        "financial_report",
        re.compile(r"\b(financial (report|highlights)|10-[kq]|annual report)\b", re.I),
    ),
    (
        "investor_update",
        re.compile(r"\b(investor|ir update|capital allocation|board pack)\b", re.I),
    ),
    (
        "reply_request",
        re.compile(
            r"\b(please (reply|confirm|respond)|reply requested|looking forward to hearing)\b",
            re.I,
        ),
    ),
    (
        "deadline",
        re.compile(
            r"\b(by (monday|tuesday|wednesday|thursday|friday|sunday)|due (in|by)|deadline)\b",
            re.I,
        ),
    ),
    ("follow_up", re.compile(r"\b(follow[- ]?up|circling back|checking in)\b", re.I)),
    (
        "finance",
        re.compile(r"\b(portfolio|allocation|invoice|earnings|revenue|capex|stock|market)\b", re.I),
    ),
    ("job_alert", re.compile(r"\b(job alert|jobs for you|hiring|internship|campus ambassador)\b", re.I)),
]


def enrich_message(
    msg: RemoteMessage,
    *,
    watchlist_symbols: Iterable[str] | None = None,
    watchlist_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    blob = " ".join(
        [msg.subject, msg.snippet, msg.body_text, msg.from_name, msg.from_email]
    )

    categories = list(msg.categories or [])
    for name, pat in CATEGORY_RULES:
        if pat.search(blob) and name not in categories:
            categories.append(name)

    # Light entity extraction from content — not a sender allowlist
    companies = list(msg.companies or [])
    tickers = list(msg.tickers or [])
    for sym in watchlist_symbols or []:
        s = str(sym).upper().strip()
        if s and re.search(rf"\b{re.escape(s)}\b", blob, re.I) and s not in tickers:
            tickers.append(s)
    for name in watchlist_names or []:
        n = str(name).strip()
        if n and n.lower() in blob.lower() and n not in companies:
            companies.append(n)

    people = list(msg.people or [])
    if msg.from_name and msg.from_name not in people:
        people.append(msg.from_name)

    rel = score_finance_relevance(
        subject=msg.subject,
        snippet=msg.snippet,
        body=msg.body_text,
        from_name=msg.from_name,
        from_email=msg.from_email,
        attachments=msg.attachments,
        watchlist_symbols=watchlist_symbols,
        watchlist_names=watchlist_names,
        labels=msg.labels,
        unread=msg.unread,
    )

    # Blend legacy priority with finance relevance (finance dominates default views)
    legacy = _legacy_priority(categories, msg)
    priority = max(float(rel.score), legacy * 0.5)
    if rel.is_noise:
        priority = min(priority, 5.0)

    return {
        "categories": categories,
        "companies": companies,
        "tickers": tickers,
        "people": people,
        "priority_score": priority,
        "finance_score": float(rel.score),
        "finance_band": rel.band,
        "is_finance": rel.is_finance,
        "is_noise": rel.is_noise,
        "has_finance_attachment": rel.has_finance_attachment,
        "why": rel.why,
    }


def _legacy_priority(categories: list[str], msg: RemoteMessage) -> float:
    score = 0.0
    weights = {
        "urgent": 40,
        "reply_request": 18,
        "deadline": 16,
        "meeting": 14,
        "investor_update": 16,
        "earnings": 14,
        "financial_report": 12,
        "invoice": 12,
        "finance": 8,
        "follow_up": 8,
        "resume": 5,
        "job_alert": -30,
    }
    for c in categories:
        score += weights.get(c, 0)
    if msg.unread:
        score += 8
    if "IMPORTANT" in (msg.labels or []):
        score += 10
    if msg.attachments:
        score += 4
    return float(score)


def format_inbox_digest(
    messages: list[dict[str, Any]],
    *,
    title: str = "Inbox",
    mode: str = "latest",
    total_scanned: int | None = None,
) -> str:
    # Prefer finance-layered presentation for inbox views
    if mode in {
        "latest",
        "check",
        "unread",
        "priority",
        "finance",
        "earnings",
        "investments",
        "summary",
    }:
        return format_finance_digest(
            messages, mode=mode, total_scanned=total_scanned
        )
    if not messages:
        return f"*{title}*\nNothing standing out right now."
    lines = [f"*{title}*", ""]
    for i, m in enumerate(messages[:6], 1):
        flag = "●" if m.get("is_unread") else "○"
        subj = m.get("subject") or "(no subject)"
        frm = m.get("from_name") or m.get("from_email") or "Unknown"
        why = m.get("why") or ""
        lines.append(f"{flag} *{subj}*")
        lines.append(f"  From {frm}")
        if why:
            lines.append(f"  Why: {why}")
        if i < min(6, len(messages)):
            lines.append("")
    lines.append("")
    lines.append("Ask me to open one, draft a reply, or summarize an attachment.")
    return "\n".join(lines)


def format_thread_summary(msg: dict[str, Any]) -> str:
    subj = msg.get("subject") or "(no subject)"
    lines = [
        f"*Summary*\n*{subj}* from {msg.get('from_name') or 'sender'}.",
        "",
        "*Key Points*",
    ]
    body = (msg.get("body_text") or msg.get("snippet") or "").strip()
    for chunk in _bulletize(body)[:4]:
        lines.append(f"• {chunk}")
    why = msg.get("why") or "Worth a look given your priorities."
    lines.extend(["", "*Why It Matters*", why, "", "*Suggested Next Steps*"])
    cats = msg.get("categories") or []
    if "reply_request" in cats:
        lines.append("• Draft a reply (I won’t send without confirmation).")
    if msg.get("has_attachment") or msg.get("attachments"):
        if msg.get("has_finance_attachment"):
            lines.append("• 📎 Financial attachment detected — ask me to summarize it.")
        else:
            lines.append("• Summarize the attachment.")
    if "meeting" in cats:
        lines.append("• Confirm the meeting time — I can put it on your calendar.")
    if len(lines) < 10:
        lines.append("• Archive if handled, or ask what to prioritize next.")
    return "\n".join(lines)


def _bulletize(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    out = []
    for p in parts:
        p = p.strip()
        if len(p) < 8:
            continue
        out.append(p[:180] + ("…" if len(p) > 180 else ""))
    if not out and text:
        out.append(text[:180])
    return out
