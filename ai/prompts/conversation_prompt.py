"""Conversation turn framing and structured output contract."""

from __future__ import annotations

import json
from typing import Any


TURN_OUTPUT_CONTRACT = """
Respond with a single JSON object only (no markdown fences) using this schema:
{
  "needs_clarification": boolean,
  "clarification_question": string,   // one natural follow-up if clarification needed; else ""
  "needs_tool": boolean,
  "tool": {                           // required when needs_tool is true; else null
    "name": string,
    "arguments": object,
    "reason": string
  },
  "answer": string,                   // Telegram-ready reply (or the clarification question)
  "confidence": number                // 0-1
}

Rules:
- If needs_clarification is true: put the follow-up in both clarification_question and answer;
  set needs_tool false; tool null.
- If needs_tool is true: choose the best implemented tool and fill arguments (symbol/symbols).
  Keep "answer" as a brief placeholder — live data will be fetched and a final reply synthesized.
  Never invent live quotes, filings, or headlines.
- Prefer clarification over guessing when the ask is broad ("tell me about Apple") with no angle.
- For "why is X moving", prefer company_news. For comparisons, prefer company_compare.
  For deep research / "tell me about X" after an angle is clear, prefer company_research.
  For price questions, prefer stock_quote. For profitability/valuation, prefer company_metrics.
- If the user has active uploaded documents and asks about risks, revenue, guidance,
  management, AI strategy, or "the report/filing", prefer document_qa (or document_compare
  when comparing two reports/years). Pass the user question in arguments.question.
- If the user asks what files/documents they have (about a company/topic) or to search
  their Drive/files, prefer drive_search. To load/analyze a named file from Drive,
  prefer drive_import. Never invent file IDs; pass a natural query string.
- If the user asks about spreadsheets, portfolio allocation, holdings performance,
  what changed in their sheet, or to open/summarize a portfolio, prefer sheet_open /
  sheet_summary / sheet_portfolio / sheet_trends as appropriate. Never invent sheet IDs.
- If the user asks about their inbox, unread mail, what needs attention, emails from/about
  a company, drafting a reply, or summarizing an email attachment, prefer gmail_search /
  gmail_priority / gmail_draft / gmail_attachment as appropriate. Never invent message IDs.
  Never claim an email was sent unless the tool result says so.
- If the user asks about their day, meetings, free time, scheduling, conflicts, or deadlines,
  prefer calendar_today / calendar_free_time / calendar_create / calendar_conflicts.
  Never invent calendar IDs. Never claim an event was created/moved/cancelled without
  confirmation in the tool result.
- Keep answer concise by default — usually 1–4 sentences for simple asks,
  or 3–8 short bullets for market moves. Expand when the user asks for a
  deep dive / detailed analysis / full report. No essay headings
  (Bottom Line / Student Lens / Financial Snapshot / Market Position).
- Do not announce memory or role. Do not end with "Would you like…".
- Shape "answer" to the user's intent. Simple asks get simple answers;
  follow-ups stay in context. For "what's happening with X", lead with
  the current situation, then drivers, then what to watch.
- Never invent live prices, ratios, earnings, or market moves.
""".strip()


def build_conversation_prompt(
    *,
    context: dict[str, Any],
    clarification_hint: str | None = None,
) -> str:
    payload = {
        "user_profile": context.get("user_profile") or {},
        "preferences": context.get("preferences") or {},
        "watchlist": context.get("watchlist") or [],
        "relevant_memories": context.get("memories") or [],
        "onboarding_state": context.get("onboarding_state") or {},
        "conversation_summary": context.get("conversation_summary") or "",
        "recent_messages": context.get("recent_messages") or [],
        "available_tools": context.get("available_tools") or [],
        "current_user_message": context.get("current_user_message") or "",
        "active_documents": (context.get("extras") or {}).get("active_documents") or [],
    }
    hint = clarification_hint or context.get("clarification_hint")
    blocks = [
        "Conversation context (JSON):",
        json.dumps(payload, ensure_ascii=False, default=str),
        "",
        TURN_OUTPUT_CONTRACT,
    ]
    if hint:
        blocks.insert(0, f"Clarification hint from rules engine: {hint}\n")
    return "\n".join(blocks)


def build_summary_prompt(*, older_messages: list[dict[str, str]], prior_summary: str) -> str:
    transcript = "\n".join(
        f"{m.get('role', 'user').upper()}: {m.get('content', '')}" for m in older_messages
    )
    return (
        "Update the rolling conversation summary for a financial assistant.\n"
        "Keep durable facts, preferences, open questions, and research threads.\n"
        "Drop greetings and one-off chit-chat. Max ~180 words.\n\n"
        f"Prior summary:\n{prior_summary or '(none)'}\n\n"
        f"Older messages to fold in:\n{transcript}\n\n"
        "Return only the new summary text."
    )
