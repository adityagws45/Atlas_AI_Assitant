"""Google Sheets catalog — metadata only in user-facing replies (never expose IDs)."""

from __future__ import annotations

from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel


class SheetConnectionMode(models.TextChoices):
    OAUTH = "oauth", "OAuth"
    DEMO = "demo", "Local demo"
    PUBLIC = "public", "Public link"


class SheetSyncStatus(models.TextChoices):
    IDLE = "idle", "Idle"
    RUNNING = "running", "Running"
    FAILED = "failed", "Failed"


class SheetSyncState(UUIDModel, TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="sheet_sync_state")
    mode = models.CharField(
        max_length=16, choices=SheetConnectionMode.choices, default=SheetConnectionMode.DEMO
    )
    status = models.CharField(
        max_length=16, choices=SheetSyncStatus.choices, default=SheetSyncStatus.IDLE
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    error_message = models.CharField(max_length=512, blank=True)
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "sheet_sync_states"


class SheetWorkbook(UUIDModel, TimeStampedModel):
    """Indexed spreadsheet. spreadsheet_id is internal only."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sheet_workbooks")
    spreadsheet_id = models.CharField(max_length=128, db_index=True)
    title = models.CharField(max_length=512)
    modified_time = models.DateTimeField(null=True, blank=True)
    sheet_names = models.JSONField(default=list, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    detected = models.JSONField(default=dict, blank=True)
    is_trashed = models.BooleanField(default=False)
    view_count = models.PositiveIntegerField(default=0)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "sheet_workbooks"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "spreadsheet_id"], name="uniq_user_spreadsheet"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_trashed", "title"]),
        ]

    def __str__(self) -> str:
        return self.title[:80]
