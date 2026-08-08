"""Calendar catalog — event IDs stay internal; replies never expose them."""

from __future__ import annotations

from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel


class CalendarConnectionMode(models.TextChoices):
    OAUTH = "oauth", "OAuth"
    DEMO = "demo", "Local demo"


class CalendarSyncStatus(models.TextChoices):
    IDLE = "idle", "Idle"
    RUNNING = "running", "Running"
    FAILED = "failed", "Failed"


class CalendarSyncState(UUIDModel, TimeStampedModel):
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="calendar_sync_state"
    )
    mode = models.CharField(
        max_length=16,
        choices=CalendarConnectionMode.choices,
        default=CalendarConnectionMode.DEMO,
    )
    status = models.CharField(
        max_length=16,
        choices=CalendarSyncStatus.choices,
        default=CalendarSyncStatus.IDLE,
    )
    timezone = models.CharField(max_length=64, default="UTC")
    last_synced_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=512, blank=True)
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "calendar_sync_states"


class CalendarEvent(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="calendar_events")
    event_id = models.CharField(max_length=128, db_index=True)
    calendar_id = models.CharField(max_length=128, default="primary")
    title = models.CharField(max_length=512)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=512, blank=True)
    start_at = models.DateTimeField(db_index=True)
    end_at = models.DateTimeField()
    all_day = models.BooleanField(default=False)
    status = models.CharField(max_length=32, default="confirmed")  # confirmed|cancelled|tentative
    is_recurring = models.BooleanField(default=False)
    categories = models.JSONField(default=list, blank=True)
    companies = models.JSONField(default=list, blank=True)
    tickers = models.JSONField(default=list, blank=True)
    importance = models.FloatField(default=0.0)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "calendar_events"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "event_id"], name="uniq_user_calendar_event"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "start_at", "status"]),
        ]

    def __str__(self) -> str:
        return self.title[:80]
