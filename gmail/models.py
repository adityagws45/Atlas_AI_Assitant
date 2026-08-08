"""Gmail catalog — message IDs stay internal; user-facing replies never expose them."""

from __future__ import annotations

from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel


class GmailConnectionMode(models.TextChoices):
    OAUTH = "oauth", "OAuth"
    DEMO = "demo", "Local demo"


class GmailSyncStatus(models.TextChoices):
    IDLE = "idle", "Idle"
    RUNNING = "running", "Running"
    FAILED = "failed", "Failed"


class GmailSyncState(UUIDModel, TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="gmail_sync_state")
    mode = models.CharField(
        max_length=16, choices=GmailConnectionMode.choices, default=GmailConnectionMode.DEMO
    )
    status = models.CharField(
        max_length=16, choices=GmailSyncStatus.choices, default=GmailSyncStatus.IDLE
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=512, blank=True)
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "gmail_sync_states"


class GmailMessage(UUIDModel, TimeStampedModel):
    """Indexed email metadata + snipped body for analysis (never log full bodies)."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="gmail_messages")
    message_id = models.CharField(max_length=128, db_index=True)
    thread_id = models.CharField(max_length=128, blank=True, db_index=True)
    subject = models.CharField(max_length=512, blank=True)
    from_name = models.CharField(max_length=256, blank=True)
    from_email = models.CharField(max_length=320, blank=True)
    snippet = models.CharField(max_length=512, blank=True)
    body_text = models.TextField(blank=True)
    received_at = models.DateTimeField(null=True, blank=True)
    is_unread = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False)
    is_important = models.BooleanField(default=False)
    labels = models.JSONField(default=list, blank=True)
    companies = models.JSONField(default=list, blank=True)
    tickers = models.JSONField(default=list, blank=True)
    people = models.JSONField(default=list, blank=True)
    categories = models.JSONField(default=list, blank=True)
    priority_score = models.FloatField(default=0.0)
    has_attachment = models.BooleanField(default=False)
    attachments = models.JSONField(default=list, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "gmail_messages"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "message_id"], name="uniq_user_gmail_message"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_unread", "is_archived"]),
            models.Index(fields=["user", "priority_score"]),
        ]

    def __str__(self) -> str:
        return (self.subject or "(no subject)")[:80]
