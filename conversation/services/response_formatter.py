"""Telegram-oriented response polishing."""

from __future__ import annotations

import re

from ai.services.json_guard import (
    extract_json_object,
    is_orchestration_payload,
    looks_like_orchestration_json,
    public_answer_from_payload,
)

PROVIDER_LEAKS = (
    r"\bfinnhub\b",
    r"\byahoo finance\b",
    r"\byahoo\b",
    r"\byfinance\b",
    r"\bsec edgar\b",
    r"\bapi key\b",
    r"\bembeddings?\b",
    r"\bvector store\b",
    r"\bvector search\b",
    r"\bchunk(?:s|ing)?\b",
    r"\bretrieval\b",
    r"\bresearch brain\b",
    r"\bgemini\b",
    r"\bprovider error\b",
)

ID_LEAKS = (
    r"\bspreadsheet_id\b",
    r"\bmessage_id\b",
    r"\bthread_id\b",
    r"\bevent_id\b",
    r"\bcalendar_id\b",
    r"\btool_request\b",
    r"\boauthlib\b",
)

# Essay / ChatGPT-style section labels the model sometimes invents
BANNED_HEADINGS = re.compile(
    r"^\s*[\*_]*(?:the\s+)?(?:"
    r"bottom line|financial snapshot|student (?:lens|note)|market position|"
    r"here'?s what you need to know|key takeaways?|in summary|in conclusion|"
    r"executive summary|the takeaway|quick primer(?: on(?: the)? stock market)?|"
    r"the stock market\s*\([^)]*\)|stock market\s*\(simplified\)|"
    r"why it matters|recommended next steps|key findings|"
    r"want a deeper dive"
    r")[\*_:]*\s*$",
    re.IGNORECASE | re.MULTILINE,
)

BANNED_CLOSERS = re.compile(
    r"(?:\n|^)\s*(?:would you like(?: me to)?[^.?\n]*[.?]|"
    r"do you want(?: me to)?[^.?\n]*[.?]|"
    r"want a deeper dive[^.?\n]*[.?]?|"
    r"anything else\??|"
    r"let me know if you (?:need|want|have)[^.?\n]*[.?]|"
    r"happy to dig deeper[^.?\n]*[.?])\s*$",
    re.IGNORECASE,
)

# Drop appended stock-market tutorials that often follow a real answer
_STOCK_MARKET_PRIMER = re.compile(
    r"(?:\n{1,2})"
    r"(?:\s*[\*_]*\s*(?:quick primer on (?:the )?stock market|"
    r"the stock market\s*\([^)]*\)|"
    r"stock market\s*\(simplified\))"
    r"[\*_:]*\s*\n)?"
    r"(?:the stock market is essentially[\s\S]*)$",
    re.IGNORECASE,
)

# Never run leak scrubbers inside absolute URLs — they previously turned
# https://www.googleapis.com/... into https://www..com/... (broken OAuth links).
_URL_PLACEHOLDER = "\x00URL{0}\x00"
_URL_RE = re.compile(r"https?://[^\s<>\")\]]+")


class ResponseFormatter:
    """Keep assistant replies readable on Telegram without sounding robotic."""

    def format(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return "Hmm — I blanked for a second. Mind sending that again?"

        # Hard block: never send orchestration / planning JSON to users
        if looks_like_orchestration_json(cleaned) or is_orchestration_payload(
            extract_json_object(cleaned)
        ):
            payload = extract_json_object(cleaned) or {}
            salvaged = public_answer_from_payload(payload)
            cleaned = salvaged or (
                "Let me pull the latest on that. "
                "Rephrase with a ticker if this doesn't resolve."
            )

        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = re.sub(r"^```(?:\w+)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        banned_openers = (
            r"^(certainly[!.,]?\s*)",
            r"^(of course[!.,]?\s*)",
            r"^(great question[!.,]?\s*)",
            r"^(as an ai[^.]*\.\s*)",
            r"^(i'?m happy to help[!.,]?\s*)",
            r"^(i'?d be happy to[!.,]?\s*)",
            r"^(sure[!.,]?\s*)",
            r"^(absolutely[!.,]?\s*)",
            r"^(no problem[!.,]?\s*)",
            r"^(happy to help[!.,]?\s*)",
            r"^(hope this helps[!.,]?\s*)",
            r"^(let'?s dive in[!.,]?\s*)",
            r"^(here'?s a simple explanation[:.\s]*)",
            r"^(think of (?:it|this) as[:.\s]*)",
            r"^(perfect[!.,]?\s*)",
            r"^(let me explain[:.\s]*)",
            r"^(since you asked earlier[^.]*\.\s*)",
            r"^(because you(?:'re| are) a student[^.]*\.\s*)",
            r"^(let me know if you (need|have|want)[^.]*\.\s*)",
        )
        # Peel stacked filler openers (Absolutely! Great question! …)
        changed = True
        while changed:
            changed = False
            for pattern in banned_openers:
                nxt = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).lstrip()
                if nxt != cleaned:
                    cleaned = nxt
                    changed = True

        cleaned = _STOCK_MARKET_PRIMER.sub("", cleaned)
        cleaned = BANNED_HEADINGS.sub("", cleaned)
        cleaned = BANNED_CLOSERS.sub("", cleaned)
        # Role/memory announcements mid-reply
        cleaned = re.sub(
            r"(?i)\bbecause you(?:'re| are) a student[^.!\n]*[.!]?\s*",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?i)\bsince you asked earlier[^.!\n]*[.!]?\s*",
            "",
            cleaned,
        )
        cleaned = re.sub(
            r"(?i)\bbased on what you(?:'ve| have) told me[^.!\n]*[.!]?\s*",
            "",
            cleaned,
        )
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        urls: list[str] = []

        def _stash(match: re.Match) -> str:
            urls.append(match.group(0))
            return _URL_PLACEHOLDER.format(len(urls) - 1)

        cleaned = _URL_RE.sub(_stash, cleaned)

        for pattern in PROVIDER_LEAKS:
            cleaned = re.sub(pattern, "market data", cleaned, flags=re.IGNORECASE)

        for pattern in ID_LEAKS:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r" {2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

        for i, url in enumerate(urls):
            cleaned = cleaned.replace(_URL_PLACEHOLDER.format(i), url)

        # Soft-trim only extreme walls; TelegramAdapter still splits long messages.
        # Keep headroom so "deep dive" / "full report" answers are not silently gutted.
        if len(cleaned) > 3500:
            cut = cleaned[:3200]
            last_break = max(cut.rfind("\n\n"), cut.rfind("\n"))
            if last_break > 2000:
                cut = cut[:last_break]
            cleaned = cut.rstrip()

        return cleaned.strip()
