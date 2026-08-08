"""Conversation and message persistence."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from accounts.models import User
from conversation.models import ContentType, Conversation, Message, MessageRole

logger = logging.getLogger("atlas.conversation.message_service")


class MessageService:
    @staticmethod
    def get_or_create_active_conversation(user: User) -> Conversation:
        conversation = (
            Conversation.objects.filter(user=user, is_active=True)
            .order_by("-last_message_at", "-created_at")
            .first()
        )
        if conversation:
            return conversation

        conversation = Conversation.objects.create(
            user=user,
            is_active=True,
            title="Main conversation",
            last_message_at=timezone.now(),
        )
        logger.info(
            "event=conversation_created conversation_id=%s telegram_id=%s",
            conversation.id,
            user.telegram_id,
        )
        return conversation

    @staticmethod
    def save_user_message(
        conversation: Conversation,
        content: str,
        *,
        content_type: str = ContentType.TEXT,
        telegram_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        message = Message.objects.create(
            conversation=conversation,
            role=MessageRole.USER,
            content=content,
            content_type=content_type,
            telegram_message_id=telegram_message_id,
            metadata=metadata or {},
        )
        Conversation.objects.filter(pk=conversation.pk).update(
            last_message_at=timezone.now()
        )
        logger.info(
            "event=user_message_saved message_id=%s conversation_id=%s chars=%s",
            message.id,
            conversation.id,
            len(content),
        )
        return message

    @staticmethod
    def save_assistant_message(
        conversation: Conversation,
        content: str,
        *,
        telegram_message_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        message = Message.objects.create(
            conversation=conversation,
            role=MessageRole.ASSISTANT,
            content=content,
            content_type=ContentType.TEXT,
            telegram_message_id=telegram_message_id,
            metadata=metadata or {},
        )
        Conversation.objects.filter(pk=conversation.pk).update(
            last_message_at=timezone.now()
        )
        logger.info(
            "event=assistant_message_saved message_id=%s conversation_id=%s chars=%s",
            message.id,
            conversation.id,
            len(content),
        )
        return message
