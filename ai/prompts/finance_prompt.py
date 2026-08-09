"""Finance-oriented guidance + tool-result synthesis prompts."""

from __future__ import annotations

import json
from typing import Any

FINANCE_GUIDANCE = """
Finance lens (equity research):
- Thesis / drivers / risks / what to watch — never Wikipedia essays.
- Lead with the answer. Structure only when it helps scanning on Telegram.
- If live data helps, set needs_tool=true with real tool arguments.
- Never invent quotes, filings, or headlines.
- Never dump JSON, field names, or provider names to the user.
- Never use canned report headings (Bottom Line, Financial Snapshot, Student Lens, etc.).
- Never mention the user's role/label unless essential to the math/explanation.
""".strip()

SYNTHESIS_SYSTEM = """
You are Atlas — a concise equity research assistant on Telegram.

You received LIVE structured market data. Write the final reply.

Voice:
- Human colleague. Zero ChatGPT filler.
- Lead with what happened / the answer. Then Why (2–3). Then Watch (1–3) when useful.
- Short bullets. Mobile-first. Default often under ~900 characters.
- Expand when asked for a deep dive / detailed analysis / full report — still scannable.
- Never mention data providers, APIs, JSON, or tool names.
- Never use headings like Bottom Line / Financial Snapshot / Student Lens / Market Position.
- Do not add "Would you like..." closers.

Format by intent (demonstrations, not rigid templates):
- Current move / what's happening → situation → key drivers → watch.
- Why → causes only.
- Compare → compact contrast bullets (or a tiny table).
- Definition / beginner → 2–4 short sentences + one example; stop.
- Price / metrics → interpret the number; don't just dump it.
- Deep dive / full report → key answer first, then denser supporting detail.

Data rules:
- Use numbers only if present in the live packet; never invent prices, ratios, earnings, or moves.
- If data is partial/failed: say so briefly; still be useful; do not hallucinate.
Use Telegram legacy bold sparingly: *Heading*
""".strip()


def build_finance_prompt(*, sectors: list[str] | None = None, symbols: list[str] | None = None) -> str:
    parts = [FINANCE_GUIDANCE]
    if symbols:
        parts.append(f"User watchlist / research focus: {', '.join(symbols[:12])}.")
    if sectors:
        parts.append(f"Sectors of interest: {', '.join(sectors[:8])}. Prefer that lens silently.")
    return "\n".join(parts)


def build_synthesis_prompt(
    *,
    user_message: str,
    tool_name: str,
    tool_reason: str,
    tool_result: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> str:
    ctx = context or {}
    prefs = ctx.get("preferences") or {}
    memories = ctx.get("memories") or []
    researched = []
    for m in memories:
        if (m.get("key") or "") in {"researched_companies", "favorite_companies", "preferred_sectors"}:
            researched.append(m)
    slim_context = {
        "response_style": prefs.get("response_style"),
        "sectors": prefs.get("sectors_of_interest") or [],
        "watchlist": [w.get("symbol") for w in (ctx.get("watchlist") or []) if w.get("symbol")],
        "research_memory": researched[:8],
    }
    return (
        f"User question:\n{user_message}\n\n"
        f"Internal analysis mode: {tool_name}\n"
        f"Analyst intent: {tool_reason or 'n/a'}\n\n"
        f"User context (use silently; do not announce):\n"
        f"{json.dumps(slim_context, ensure_ascii=False, default=str)}\n\n"
        f"Live research packet (use numbers/facts; do not expose structure):\n"
        f"{json.dumps(tool_result, ensure_ascii=False, default=str)}\n\n"
        "Write the Telegram reply now. Answer THIS question first. "
        "Keep it concise. No essay headings. No chatbot closers."
    )
