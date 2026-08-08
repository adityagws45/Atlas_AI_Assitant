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
                "If this doesn't resolve, rephrase with a ticker and I'll dig in."
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
            r"^(let me know if you (need|have|want)[^.]*\.\s*)",
        )
        for pattern in banned_openers:
            cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).lstrip()

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

        # Soft-trim extreme walls; TelegramAdapter still splits long messages
        if len(cleaned) > 3200:
            cut = cleaned[:3000]
            last_break = max(cut.rfind("\n\n"), cut.rfind("\n"))
            if last_break > 2000:
                cut = cut[:last_break]
            cleaned = cut.rstrip() + "\n\nAsk if you want me to go deeper on any point."

        return cleaned.strip()
