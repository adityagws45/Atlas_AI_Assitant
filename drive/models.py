"""Google Drive catalog — metadata + sync state (content lives in FinancialDocument)."""

from __future__ import annotations

from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel
from documents.models import FinancialDocument


class DriveSyncStatus(models.TextChoices):
    IDLE = "idle", "Idle"
    RUNNING = "running", "Running"
    FAILED = "failed", "Failed"


class DriveConnectionMode(models.TextChoices):
    OAUTH = "oauth", "OAuth"
    DEMO = "demo", "Local demo"


class DriveSyncState(UUIDModel, TimeStampedModel):
    """Per-user Drive sync cursor / status."""

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="drive_sync_state"
    )
    mode = models.CharField(
        max_length=16, choices=DriveConnectionMode.choices, default=DriveConnectionMode.DEMO
    )
    status = models.CharField(
        max_length=16, choices=DriveSyncStatus.choices, default=DriveSyncStatus.IDLE
    )
    page_token = models.CharField(max_length=512, blank=True)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    last_incremental_sync_at = models.DateTimeField(null=True, blank=True)
    selected_folder_ids = models.JSONField(default=list, blank=True)
    error_message = models.CharField(max_length=512, blank=True)
    stats = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "drive_sync_states"

    def __str__(self) -> str:
        return f"drive-sync:{self.user_id}"


class DriveFile(UUIDModel, TimeStampedModel):
    """Indexed Drive file metadata. Never expose drive_file_id to users."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="drive_files")
    drive_file_id = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=512)
    mime_type = models.CharField(max_length=256, blank=True)
    modified_time = models.DateTimeField(null=True, blank=True)
    md5_checksum = models.CharField(max_length=64, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    parents = models.JSONField(default=list, blank=True)
    web_view_link = models.CharField(max_length=1024, blank=True)
    is_folder = models.BooleanField(default=False)
    is_trashed = models.BooleanField(default=False)
    is_supported = models.BooleanField(default=True)
    metadata_only = models.BooleanField(default=False)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    document = models.ForeignKey(
        FinancialDocument,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drive_origins",
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    view_count = models.PositiveIntegerField(default=0)
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "drive_files"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "drive_file_id"], name="uniq_user_drive_file"
            ),
        ]
        indexes = [
            models.Index(fields=["user", "is_trashed", "name"]),
            models.Index(fields=["user", "modified_time"]),
        ]

    def __str__(self) -> str:
        return self.name[:80]
