"""Modular prompt composition — no hardcoded prompts in business logic."""

from __future__ import annotations

from typing import Any

from ai.prompts.conversation_prompt import build_conversation_prompt, build_summary_prompt
from ai.prompts.finance_prompt import build_finance_prompt
from ai.prompts.memory_prompt import (
    MEMORY_EXTRACTION_SYSTEM,
    build_memory_extraction_prompt,
)
from ai.prompts.onboarding_prompt import build_onboarding_prompt
from ai.prompts.system_prompt import build_system_prompt
from ai.types import ConversationContext


class PromptManager:
    """Compose system + user prompts from modular prompt files."""

    def compose_turn(self, context: ConversationContext) -> tuple[str, str]:
        style = (context.preferences or {}).get("response_style") or "concise"
        sectors = (context.preferences or {}).get("sectors_of_interest") or []
        symbols = [w.get("symbol") for w in context.watchlist if w.get("symbol")]

        system_parts = [
            build_system_prompt(response_style=style),
            build_finance_prompt(sectors=sectors, symbols=symbols),
            build_onboarding_prompt(onboarding_state=context.onboarding_state),
        ]
        system = "\n\n".join(p for p in system_parts if p)

        user = build_conversation_prompt(
            context=context.to_prompt_dict(),
            clarification_hint=context.clarification_hint,
        )
        return system, user

    def compose_summary(
        self, *, older_messages: list[dict[str, str]], prior_summary: str
    ) -> tuple[str, str]:
        system = (
            "You write compact rolling summaries for a financial assistant. "
            "Preserve durable context only."
        )
        user = build_summary_prompt(
            older_messages=older_messages, prior_summary=prior_summary
        )
        return system, user

    def compose_memory_extraction(
        self,
        *,
        user_message: str,
        assistant_message: str,
        existing_keys: list[str] | None = None,
    ) -> tuple[str, str]:
        return (
            MEMORY_EXTRACTION_SYSTEM,
            build_memory_extraction_prompt(
                user_message=user_message,
                assistant_message=assistant_message,
                existing_keys=existing_keys,
            ),
        )

    def compose_from_dict(self, context_dict: dict[str, Any]) -> tuple[str, str]:
        """Test helper: compose from a plain dict shaped like ConversationContext."""
        ctx = ConversationContext(
            user_id=str(context_dict.get("user_id", "")),
            telegram_id=int(context_dict.get("telegram_id") or 0),
            user_profile=context_dict.get("user_profile") or {},
            preferences=context_dict.get("preferences") or {},
            watchlist=context_dict.get("watchlist") or [],
            memories=context_dict.get("memories") or [],
            onboarding_state=context_dict.get("onboarding_state") or {},
            conversation_summary=context_dict.get("conversation_summary") or "",
            recent_messages=context_dict.get("recent_messages") or [],
            current_user_message=context_dict.get("current_user_message") or "",
            clarification_hint=context_dict.get("clarification_hint"),
            available_tools=context_dict.get("available_tools") or [],
        )
        return self.compose_turn(ctx)
