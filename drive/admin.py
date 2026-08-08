from django.contrib import admin

from drive.models import DriveFile, DriveSyncState


@admin.register(DriveSyncState)
class DriveSyncStateAdmin(admin.ModelAdmin):
    list_display = ("user", "mode", "status", "last_full_sync_at", "updated_at")
    list_filter = ("mode", "status")


@admin.register(DriveFile)
class DriveFileAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "user",
        "mime_type",
        "is_trashed",
        "is_supported",
        "metadata_only",
        "view_count",
        "updated_at",
    )
    list_filter = ("is_trashed", "is_supported", "metadata_only")
    search_fields = ("name",)
    # Never expose drive_file_id in list for casual browsing — still in detail
    readonly_fields = ("drive_file_id", "content_hash", "md5_checksum")
