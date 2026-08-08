"""Finance/business relevance scoring for real Gmail messages.

Generic keyword + pattern scoring — no sender allowlists, no fabricated mail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

# Thresholds (0–100 scale after normalization)
FINANCE_THRESHOLD = 22.0
STRONG_FINANCE_THRESHOLD = 45.0
NOISE_FLOOR = -25.0

HIGH_TERMS: list[tuple[str, float]] = [
    (r"\bearnings?\b", 22),
    (r"\brevenue\b", 18),
    (r"\b(net )?profit\b", 16),
    (r"\beps\b", 20),
    (r"\bguidance\b", 16),
    (r"\b10-?[kq]\b", 24),
    (r"\bsec\b", 14),
    (r"\binvestor relations?\b", 20),
    (r"\bdividend\b", 16),
    (r"\b(stock|share)s?\b", 12),
    (r"\bportfolio\b", 16),
    (r"\bmarket(s)?\b", 10),
    (r"\bipo\b", 18),
    (r"\bacquisition\b", 14),
    (r"\bmerger\b", 14),
    (r"\bvaluation\b", 12),
    (r"\bp/?e\b", 12),
    (r"\binvestment(s)?\b", 14),
    (r"\banalyst\b", 12),
    (r"\b(buy|sell|hold)\s+(rating|rated|recommendation)?\b", 14),
    (r"\bfinancial results?\b", 20),
    (r"\bquarterly results?\b", 20),
    (r"\bannual report\b", 18),
    (r"\b(economic|macro)\b", 10),
    (r"\binterest rates?\b", 14),
    (r"\binflation\b", 12),
    (r"\bbanking\b", 10),
    (r"\b(mutual )?fund(s)?\b", 12),
    (r"\betf\b", 14),
    (r"\bbond(s)?\b", 12),
    (r"\bbroker(age)?\b", 12),
    (r"\btrade(r|s|ing)?\b", 8),
    (r"\b(price target|fair value)\b", 14),
    (r"\b(balance sheet|cash flow|income statement)\b", 16),
    (r"\b(invoice|payment|transaction|wire transfer|ach)\b", 12),
    (r"\b(filings?|8-?k|proxy statement)\b", 16),
    (r"\b(nasdaq|nyse|s&p|dow)\b", 12),
    (r"\b(capital markets?|equity research)\b", 14),
]

MEDIUM_TERMS: list[tuple[str, float]] = [
    (r"\b(company|corporate)\s+(announce|update|news)\b", 8),
    (r"\bbusiness strategy\b", 8),
    (r"\b(corporate action|executive change|ceo|cfo)\b", 8),
    (r"\bindustry (news|outlook)\b", 6),
    (r"\b(fundraising|raised \$|series [a-d])\b", 10),
    (r"\b(capex|capital expenditure)\b", 10),
    (r"\b(partnership|contract award)\b", 6),
    (r"\b(board|shareholder)\b", 8),
    (r"\b(forecast|outlook)\b", 8),
    (r"\b(margin|ebitda|opex)\b", 10),
    (r"\b(research note|market update|daily brief)\b", 8),
    (r"\b(fintech|asset management)\b", 8),
]

NOISE_TERMS: list[tuple[str, float]] = [
    (r"\b(job alert|jobs? for you|new jobs?|hiring alert)\b", -40),
    (r"\b(internshala|jooble|indeed|naukri|glassdoor|apna\.?jobs?)\b", -45),
    (r"\blinkedin job\b", -40),
    (r"\b(campus ambassador|internship opportunity|apply now)\b", -35),
    (r"\b(open roles?|we'?re hiring|job opening)\b", -30),
    (r"\b(shopping|order shipped|your cart|flash sale|% off)\b", -30),
    (r"\b(unfollow|social notification|liked your|new follower)\b", -25),
    (r"\b(newsletter.*(entertainment|lifestyle|gaming)|daily digest.*(fun|meme))\b", -15),
    (r"\b(otp|one[- ]time password|verification code)\b", -10),
    (r"\b(game|fantasy sports|ipl|streaming)\b", -12),
]

FINANCE_ATTACHMENT = re.compile(
    r"\.(pdf|xlsx?|csv|pptx?)$|"
    r"\b(10-?[kq]|earnings|annual.?report|financial|prospectus|fact.?sheet|pitch.?deck)\b",
    re.I,
)

FINANCE_GMAIL_OR = (
    "earnings OR revenue OR profit OR stock OR market OR investment OR "
    "dividend OR portfolio OR \"financial results\" OR \"investor relations\" OR "
    "EPS OR SEC OR ETF OR broker OR invoice OR \"quarterly results\""
)


@dataclass
class RelevanceResult:
    score: float
    band: str  # high | medium | low | noise
    signals: list[str] = field(default_factory=list)
    why: str = ""
    is_finance: bool = False
    is_noise: bool = False
    has_finance_attachment: bool = False


def score_finance_relevance(
    *,
    subject: str = "",
    snippet: str = "",
    body: str = "",
    from_name: str = "",
    from_email: str = "",
    attachments: list[dict[str, Any]] | None = None,
    watchlist_symbols: Iterable[str] | None = None,
    watchlist_names: Iterable[str] | None = None,
    labels: list[str] | None = None,
    unread: bool = False,
) -> RelevanceResult:
    blob = " ".join(
        [
            subject or "",
            snippet or "",
            (body or "")[:2500],
            from_name or "",
            from_email or "",
        ]
    )
    low = blob.lower()
    score = 0.0
    signals: list[str] = []

    for pat, w in HIGH_TERMS:
        if re.search(pat, low, re.I):
            score += w
            signals.append(pat.strip("\\b").replace("\\", "")[:28])

    for pat, w in MEDIUM_TERMS:
        if re.search(pat, low, re.I):
            score += w
            signals.append(f"med:{pat[:20]}")

    noise_hits = 0
    for pat, w in NOISE_TERMS:
        if re.search(pat, low, re.I):
            score += w
            noise_hits += 1
            signals.append("noise")

    has_fin_att = False
    for att in attachments or []:
        name = str(att.get("filename") or att.get("name") or "")
        mime = str(att.get("mime_type") or "")
        if FINANCE_ATTACHMENT.search(name) or "pdf" in mime.lower():
            # PDF alone is mild; finance-named PDF is stronger
            if FINANCE_ATTACHMENT.search(name):
                score += 14
                has_fin_att = True
                signals.append("finance_attachment")
            elif any(
                t in low
                for t in ("earnings", "financial", "report", "invoice", "portfolio", "sec")
            ):
                score += 8
                has_fin_att = True
                signals.append("pdf_attachment")

    # Watchlist personalization (boost, never sole gate)
    wl_syms = {str(s).upper().strip() for s in (watchlist_symbols or []) if str(s).strip()}
    wl_names = {str(n).lower().strip() for n in (watchlist_names or []) if str(n).strip()}
    for sym in wl_syms:
        if len(sym) >= 2 and re.search(rf"\b{re.escape(sym)}\b", blob, re.I):
            score += 12
            signals.append(f"watchlist:{sym}")
            break
    for name in wl_names:
        if len(name) >= 3 and name in low:
            score += 10
            signals.append("watchlist_name")
            break

    if unread:
        score += 3
    if labels and "IMPORTANT" in labels:
        score += 6

    # Cap runaway scores
    score = max(-50.0, min(100.0, score))

    is_noise = score <= NOISE_FLOOR or (noise_hits >= 1 and score < FINANCE_THRESHOLD)
    is_finance = score >= FINANCE_THRESHOLD and not (is_noise and score < FINANCE_THRESHOLD)

    if score >= STRONG_FINANCE_THRESHOLD:
        band = "high"
    elif score >= FINANCE_THRESHOLD:
        band = "medium"
    elif is_noise or score < 8:
        band = "noise" if is_noise else "low"
    else:
        band = "low"

    why = _build_why(
        subject=subject,
        snippet=snippet,
        score=score,
        band=band,
        signals=signals,
        has_fin_att=has_fin_att,
        is_noise=is_noise,
    )
    return RelevanceResult(
        score=score,
        band=band,
        signals=signals[:12],
        why=why,
        is_finance=is_finance,
        is_noise=bool(is_noise and not is_finance),
        has_finance_attachment=has_fin_att,
    )


def _build_why(
    *,
    subject: str,
    snippet: str,
    score: float,
    band: str,
    signals: list[str],
    has_fin_att: bool,
    is_noise: bool,
) -> str:
    subj = (subject or "").strip()
    snip = (snippet or "").strip()
    grounded = snip[:140] if snip else subj[:140]

    if is_noise and band == "noise":
        return "Looks like a job/marketing alert — low priority for finance research."

    # Ground explanations in detected signal classes present in the text
    low = f"{subj} {snip}".lower()
    if re.search(r"\bearnings?|eps|quarterly results|guidance\b", low):
        return (
            "Mentions earnings/results content that may include revenue, margins, "
            "or guidance useful for market research."
            + (f" Snippet: {grounded}" if grounded else "")
        )[:220]
    if re.search(r"\binvestor relations?|shareholder|10-?[kq]|sec\b", low):
        return (
            "Investor/filings-related language — may contain disclosures or IR updates."
            + (f" Snippet: {grounded}" if grounded else "")
        )[:220]
    if re.search(r"\b(stock|share|portfolio|broker|etf|dividend)\b", low):
        return (
            "Market/investment language detected in the subject or snippet."
            + (f" Snippet: {grounded}" if grounded else "")
        )[:220]
    if re.search(r"\b(invoice|payment|transaction|billing)\b", low):
        return "Payment/transaction language — relevant to cash-flow tracking."
    if has_fin_att:
        return "Includes a document that may be a financial report or statement."
    if band in {"high", "medium"}:
        return (
            "Business/finance cues in the email content."
            + (f" Snippet: {grounded}" if grounded else "")
        )[:220]
    if grounded:
        return f"Recent inbox item. Snippet: {grounded}"[:180]
    return "Recent inbox item with limited finance signals."


def partition_by_finance(
    messages: list[dict[str, Any]],
    *,
    finance_limit: int = 6,
    other_limit: int = 4,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked = sorted(
        messages,
        key=lambda m: (
            float(m.get("finance_score") or m.get("priority_score") or 0),
            1 if m.get("is_unread") else 0,
        ),
        reverse=True,
    )
    finance = [m for m in ranked if m.get("is_finance")][:finance_limit]
    finance_ids = {m.get("id") or m.get("message_id") for m in finance}
    other = [
        m
        for m in ranked
        if (m.get("id") or m.get("message_id")) not in finance_ids and not m.get("is_noise")
    ][:other_limit]
    # If everything was noise, still surface a few others honestly
    if not finance and not other:
        other = [m for m in ranked if not m.get("is_finance")][:other_limit]
    return finance, other


def format_finance_digest(
    messages: list[dict[str, Any]],
    *,
    mode: str = "latest",
    total_scanned: int | None = None,
) -> str:
    finance, other = partition_by_finance(messages)
    scanned = total_scanned if total_scanned is not None else len(messages)

    if mode in {"finance", "earnings", "investments"} and not finance:
        lines = [
            f"📧 I found {scanned} recent email{'s' if scanned != 1 else ''}, "
            "but none appear strongly related to finance or markets.",
        ]
        if other:
            lines.extend(["", "📬 Other recent emails", ""])
            lines.extend(_format_items(other, start=1, include_why=False))
        return "\n".join(lines)

    if not finance and mode in {"latest", "check", "unread", "priority", "summary"}:
        lines = [
            f"📧 I found {scanned} recent email{'s' if scanned != 1 else ''}, "
            "but none appear strongly related to finance or markets.",
        ]
        show = other or messages[:4]
        if show:
            lines.extend(["", "📬 Other recent emails", ""])
            lines.extend(_format_items(show, start=1, include_why=True))
        lines.append("")
        lines.append("Ask me to search a company, open one, or check attachments.")
        return "\n".join(lines)

    lines = ["📧 *Finance & Business*", ""]
    lines.extend(_format_items(finance, start=1, include_why=True))
    if other and mode in {"latest", "check", "unread", "priority"}:
        lines.extend(["", "📬 *Other recent emails*", ""])
        lines.extend(_format_items(other, start=1, include_why=False))
    lines.append("")
    lines.append(
        "Ask me to summarize one, check an attachment, or search a company/ticker."
    )
    return "\n".join(lines)


def _format_items(
    items: list[dict[str, Any]], *, start: int = 1, include_why: bool = True
) -> list[str]:
    lines: list[str] = []
    for i, m in enumerate(items, start):
        subj = m.get("subject") or "(no subject)"
        frm = m.get("from_name") or m.get("from_email") or "Unknown"
        when = m.get("received_display") or m.get("received_at") or ""
        why = m.get("why") or ""
        lines.append(f"{i}. *{subj}*")
        lines.append(f"   From: {frm}")
        if when:
            lines.append(f"   Time: {when}")
        if include_why and why:
            lines.append(f"   Why it matters: {why}")
        if m.get("has_finance_attachment") or (
            m.get("has_attachment")
            and float(m.get("finance_score") or 0) >= FINANCE_THRESHOLD
        ):
            lines.append("   📎 Financial attachment detected")
        elif m.get("has_attachment"):
            lines.append("   📎 Attachment")
        if i < start + len(items) - 1:
            lines.append("")
    return lines


def finance_search_query(kind: str = "finance") -> str:
    if kind == "earnings":
        return (
            "in:inbox newer_than:90d "
            "(earnings OR EPS OR \"quarterly results\" OR guidance OR \"financial results\")"
        )
    if kind == "investments":
        return (
            "in:inbox newer_than:90d "
            "(portfolio OR broker OR \"price target\" OR dividend OR ETF OR "
            "\"stock alert\" OR investment OR \"market alert\")"
        )
    if kind == "unread_finance":
        return f"is:unread newer_than:30d ({FINANCE_GMAIL_OR})"
    # Broad finance candidate pull
    return f"in:inbox newer_than:30d ({FINANCE_GMAIL_OR})"
