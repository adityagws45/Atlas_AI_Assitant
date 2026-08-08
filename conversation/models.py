from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel


class Conversation(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    context_summary = models.TextField(blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "conversations"
        ordering = ["-last_message_at"]

    def __str__(self):
        return self.title or f"Conversation {self.id}"


class MessageRole(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"
    SYSTEM = "system", "System"
    TOOL = "tool", "Tool"


class ContentType(models.TextChoices):
    TEXT = "text", "Text"
    VOICE = "voice_transcript", "Voice Transcript"
    IMAGE = "image_description", "Image Description"
    DOCUMENT = "document_ref", "Document Reference"


class Message(UUIDModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=MessageRole.choices)
    content = models.TextField()
    content_type = models.CharField(max_length=32, choices=ContentType.choices, default=ContentType.TEXT)
    telegram_message_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "messages"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["conversation", "created_at"]),
            models.Index(fields=["conversation", "is_archived"]),
        ]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}"
