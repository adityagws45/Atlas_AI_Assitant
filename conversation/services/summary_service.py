"""Rolling conversation summary — recent N messages + archived summary."""

from __future__ import annotations

import logging

from django.conf import settings
from django.db import transaction

from ai.prompts.prompt_manager import PromptManager
from ai.providers.base import BaseAIProvider
from ai.types import ProviderConfigError, ProviderError
from conversation.models import Conversation, Message

logger = logging.getLogger("atlas.conversation.summary")


class ConversationSummaryService:
    """
    Keep prompts lean:

    - Recent non-archived messages (max MAX_RECENT_MESSAGES)
    - Rolling text summary on Conversation.context_summary

    When live messages exceed the limit, archive older ones into the summary.
    """

    def __init__(
        self,
        provider: BaseAIProvider | None = None,
        *,
        prompts: PromptManager | None = None,
        max_recent: int | None = None,
    ) -> None:
        self.provider = provider
        self.prompts = prompts or PromptManager()
        self.max_recent = max_recent or int(
            getattr(settings, "MAX_RECENT_MESSAGES", 20)
        )

    def recent_messages(self, conversation: Conversation) -> list[dict[str, str]]:
        qs = (
            Message.objects.filter(conversation=conversation, is_archived=False)
            .order_by("-created_at")[: self.max_recent]
        )
        rows = list(reversed(list(qs)))
        return [{"role": m.role, "content": m.content} for m in rows]

    def get_summary(self, conversation: Conversation) -> str:
        return (conversation.context_summary or "").strip()

    def maybe_roll_summary(self, conversation: Conversation) -> bool:
        """Archive overflow messages and refresh summary. Returns True if rolled."""
        live_count = Message.objects.filter(
            conversation=conversation, is_archived=False
        ).count()
        if live_count <= self.max_recent:
            return False

        overflow = live_count - self.max_recent
        older = list(
            Message.objects.filter(conversation=conversation, is_archived=False)
            .order_by("created_at")[:overflow]
        )
        if not older:
            return False

        older_payload = [{"role": m.role, "content": m.content} for m in older]
        prior = self.get_summary(conversation)
        new_summary = self._summarize(older_payload, prior)

        with transaction.atomic():
            Message.objects.filter(pk__in=[m.pk for m in older]).update(is_archived=True)
            Conversation.objects.filter(pk=conversation.pk).update(
                context_summary=new_summary
            )
            conversation.context_summary = new_summary

        logger.info(
            "event=summary_rolled conversation_id=%s archived=%s summary_chars=%s",
            conversation.id,
            len(older),
            len(new_summary),
        )
        return True

    def _summarize(self, older_messages: list[dict[str, str]], prior: str) -> str:
        if self.provider is None:
            return self._fallback_summary(older_messages, prior)

        system, user = self.prompts.compose_summary(
            older_messages=older_messages, prior_summary=prior
        )
        try:
            light_model = getattr(self.provider, "light_model", None) or getattr(
                settings, "GEMINI_LIGHT_MODEL", None
            )
            response = self.provider.generate_text(
                system=system,
                user=user,
                temperature=0.2,
                max_output_tokens=400,
                model=light_model,
            )
            text = (response.text or "").strip()
            if text:
                return text[:4000]
        except (ProviderConfigError, ProviderError) as exc:
            logger.warning("event=summary_provider_fallback err=%s", type(exc).__name__)
        except Exception:
            logger.exception("event=summary_error")

        return self._fallback_summary(older_messages, prior)

    @staticmethod
    def _fallback_summary(older_messages: list[dict[str, str]], prior: str) -> str:
        lines = [f"{m['role']}: {m['content'][:160]}" for m in older_messages[-8:]]
        chunk = " | ".join(lines)
        merged = f"{prior}\n{chunk}".strip() if prior else chunk
        return merged[:2000]
