"""Clarification engine — avoid assumptions on ambiguous asks."""

from __future__ import annotations

import re
from dataclasses import dataclass


AMBIGUOUS_OPENERS = (
    r"tell me about",
    r"what about",
    r"what'?s up with",
    r"give me (an )?overview (of|on)",
    r"info on",
    r"information on",
    r"look(?:ing)? into",
    r"\bresearch\b(?!\s+analyst)",  # "research NVDA" yes; "equity research analyst" no
    r"break(?: )?down",
    r"thoughts on",
    r"analysis (of|on)",
    r"analyze",
)

# Known company / ticker-ish tokens that often need an angle
KNOWN_NAMES = {
    "apple",
    "aapl",
    "microsoft",
    "msft",
    "google",
    "alphabet",
    "googl",
    "amazon",
    "amzn",
    "meta",
    "facebook",
    "nvidia",
    "nvda",
    "tesla",
    "tsla",
    "amd",
    "intel",
    "intc",
    "netflix",
    "nflx",
    "berkshire",
    "jpmorgan",
    "jp morgan",
}

# Never treat these as company subjects
SUBJECT_BLOCKLIST = {
    "i",
    "i'm",
    "im",
    "me",
    "my",
    "we",
    "you",
    "it",
    "a",
    "an",
    "the",
    "and",
    "or",
    "for",
    "to",
    "on",
    "in",
    "of",
    "is",
    "am",
    "are",
    "was",
    "be",
    "as",
    "at",
    "by",
    "vs",
    "ceo",
    "cfo",
    "ipo",
    "usd",
    "etf",
    "ai",  # theme, not a ticker clarification subject alone
}

PROFILE_STATEMENT = re.compile(
    r"\b(i'?m|i am|i work|i cover|i follow|i focus|interested in|"
    r"my (role|watchlist|briefing)|i prefer)\b",
    re.IGNORECASE,
)

INTENT_HINTS = {
    "stock": "stock analysis",
    "price": "stock analysis",
    "valuation": "valuation",
    "pe ": "valuation",
    "earnings": "earnings",
    "eps": "earnings",
    "news": "recent news",
    "filing": "filings",
    "sec": "filings",
    "product": "products",
    "overview": "company overview",
    "business": "company overview",
    "competitor": "competitive landscape",
    "compare": "comparison",
    " vs ": "comparison",
    "versus": "comparison",
    "why is": "catalyst",
    "moving": "catalyst",
    "summarize": "summary",
    "profit": "financial analysis",
    "margin": "financial analysis",
    "growing": "growth",
}


@dataclass
class ClarificationResult:
    needed: bool
    hint: str | None = None
    subject: str | None = None
    suggested_question: str | None = None


class ClarificationEngine:
    """
    Lightweight rules that bias the model toward clarifying ambiguous company asks.

    Does not replace the model — injects a hint into the prompt.
    """

    def evaluate(self, text: str) -> ClarificationResult:
        raw = (text or "").strip()
        if not raw:
            return ClarificationResult(needed=False)

        lower = raw.lower()

        # Profile / preference statements are never company clarifications
        if PROFILE_STATEMENT.search(lower) and not any(
            re.search(p, lower) for p in (r"tell me about", r"what about", r"thoughts on")
        ):
            return ClarificationResult(needed=False)

        if self._has_clear_intent(lower):
            return ClarificationResult(needed=False)

        # Multi-company / compare asks are actionable — do not clarify away
        if self._is_comparison(lower):
            return ClarificationResult(needed=False)

        subject = self._extract_subject(lower, raw)
        opener_ambiguous = any(re.search(p, lower) for p in AMBIGUOUS_OPENERS)
        # Truly bare: "Apple" / "NVDA" / "Tell me about Apple"
        word_count = len(raw.split())
        bare_company = bool(subject) and word_count <= 2

        if not (opener_ambiguous or bare_company):
            return ClarificationResult(needed=False)

        if not subject and opener_ambiguous:
            subject = self._loose_subject(raw)

        if not subject or subject.lower().rstrip(".") in SUBJECT_BLOCKLIST:
            return ClarificationResult(needed=False)

        question = (
            f"Happy to dig into {subject}. Which angle is most useful?\n\n"
            "• Company Overview\n"
            "• Stock Performance\n"
            "• Latest News\n"
            "• Earnings\n"
            "• Financial Analysis\n"
            "• Products"
        )
        hint = (
            f"User ask about '{subject}' is ambiguous. Ask ONE concise clarifying question "
            "with the options: Company Overview, Stock Performance, Latest News, Earnings, "
            "Financial Analysis, Products. Do not answer yet."
        )
        return ClarificationResult(
            needed=True,
            hint=hint,
            subject=subject,
            suggested_question=question,
        )

    @staticmethod
    def _has_clear_intent(lower: str) -> bool:
        return any(k in lower for k in INTENT_HINTS)

    @staticmethod
    def _is_comparison(lower: str) -> bool:
        if "compare" in lower or " versus " in lower or " vs " in lower or " vs. " in lower:
            return True
        # Two known names in one short ask
        hits = sum(1 for n in KNOWN_NAMES if re.search(rf"\b{re.escape(n)}\b", lower))
        return hits >= 2

    @staticmethod
    def _extract_subject(lower: str, raw: str) -> str | None:
        for name in sorted(KNOWN_NAMES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", lower):
                return name.title() if name.islower() else name.upper()
        tickers = re.findall(r"\b[A-Z]{1,5}\b", raw)
        for tok in tickers:
            if tok.lower() in SUBJECT_BLOCKLIST:
                continue
            # Skip single-letter "tickers" (almost always grammar, e.g. I'm)
            if len(tok) < 2:
                continue
            return tok
        return None

    @staticmethod
    def _loose_subject(raw: str) -> str | None:
        m = re.search(
            r"(?:tell me about|what about|thoughts on|research|info on|information on)\s+(.+)$",
            raw,
            re.IGNORECASE,
        )
        if not m:
            return None
        subject = m.group(1).strip(" ?.!,")
        if not subject or subject.lower() in SUBJECT_BLOCKLIST:
            return None
        # Don't treat role phrases as companies
        if re.search(r"\b(analyst|investor|founder|student)\b", subject, re.IGNORECASE):
            return None
        return subject[:60] if subject else None
