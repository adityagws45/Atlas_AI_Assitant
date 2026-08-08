"""Memory extraction and retrieval prompt fragments."""

from __future__ import annotations

import json
from typing import Any


MEMORY_EXTRACTION_SYSTEM = """
You extract long-term assistant memory for a financial AI.
Save only durable preferences, interests, workflow habits, and standing facts.
Ignore temporary market chatter, one-off questions, and ephemeral numbers.
""".strip()


def build_memory_extraction_prompt(
    *,
    user_message: str,
    assistant_message: str,
    existing_keys: list[str] | None = None,
) -> str:
    existing = existing_keys or []
    return f"""
Should anything from this turn become long-term memory?

User: {user_message}
Assistant: {assistant_message}

Existing memory keys (do not duplicate unless updating): {json.dumps(existing)}

Return JSON only:
{{
  "should_save": boolean,
  "memories": [
    {{
      "memory_type": "preference" | "fact" | "task" | "context",
      "key": "snake_case_key",
      "value": object_or_string,
      "confidence": 0.0-1.0,
      "reason": "why this is durable"
    }}
  ]
}}

Examples of good keys: preferred_sectors, favorite_companies, briefing_time,
communication_style, research_interests, researched_companies, meeting_preferences,
investment_style.
If the user researched specific companies, save researched_companies as a list of tickers.
If nothing durable, should_save=false and memories=[].
""".strip()


def format_memories_for_prompt(memories: list[dict[str, Any]]) -> str:
    if not memories:
        return "No long-term memories selected for this turn."
    return json.dumps(memories, ensure_ascii=False, default=str)
