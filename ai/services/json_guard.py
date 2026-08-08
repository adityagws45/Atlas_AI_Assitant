"""Helpers to keep orchestration JSON from ever reaching Telegram users."""

from __future__ import annotations

import json
import re
from typing import Any


ORCH_KEYS = {
    "needs_tool",
    "needs_clarification",
    "clarification_question",
    "confidence",
    "tool",
    "tool_request",
}


def looks_like_orchestration_json(text: str) -> bool:
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return False
    lower = raw.lower()
    return any(k in lower for k in ("needs_tool", "needs_clarification", '"tool"', "confidence"))


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Parse a JSON object even when the model adds trailing braces or fences."""
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()

    # Trim trailing junk after the balanced object
    candidates = [raw]
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        cleaned = candidate.strip()
        # Drop extra closing braces: {...}}
        while cleaned.endswith("}}") and cleaned.count("{") < cleaned.count("}"):
            cleaned = cleaned[:-1]
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue

        # Progressive trim from the end until it parses
        for i in range(len(cleaned), max(len(cleaned) - 40, 10), -1):
            chunk = cleaned[:i].rstrip()
            if not chunk.endswith("}"):
                continue
            try:
                data = json.loads(chunk)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
    return None


def is_orchestration_payload(data: dict[str, Any] | None) -> bool:
    if not isinstance(data, dict):
        return False
    keys = set(data.keys())
    return bool(keys & ORCH_KEYS)


def public_answer_from_payload(data: dict[str, Any]) -> str:
    """Pick only user-facing text from a planning payload."""
    for key in ("answer", "clarification_question", "message", "reply"):
        val = data.get(key)
        if isinstance(val, str) and val.strip() and not looks_like_orchestration_json(val):
            text = val.strip()
            if is_progress_placeholder(text):
                continue
            return text
    return ""


PROGRESS_PLACEHOLDER = re.compile(
    r"(?i)^("
    r"fetching\b.{0,80}|"
    r"pulling\b.{0,80}|"
    r"looking up\b.{0,80}|"
    r"getting (the )?(latest |current )?.{0,60}|"
    r"let me (pull|fetch|check|look|grab|get)\b.{0,80}|"
    r"one moment\b.{0,40}|"
    r"hold on\b.{0,40}|"
    r"working on (it|that)\b.{0,40}"
    r")$"
)


def is_progress_placeholder(text: str) -> bool:
    """True for interim status lines that must never reach Telegram."""
    raw = (text or "").strip()
    if not raw:
        return False
    # Short status-style lines only
    if len(raw) > 160:
        return False
    if PROGRESS_PLACEHOLDER.match(raw):
        return True
    low = raw.lower()
    return any(
        p in low
        for p in (
            "fetching current",
            "fetching market",
            "fetching the latest",
            "pulling up today",
            "let me pull up",
            "let me fetch",
        )
    ) and len(raw) < 120