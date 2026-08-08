from django.contrib import admin

from sheets.models import SheetSyncState, SheetWorkbook


@admin.register(SheetSyncState)
class SheetSyncStateAdmin(admin.ModelAdmin):
    list_display = ("user", "mode", "status", "last_synced_at", "updated_at")
    list_filter = ("mode", "status")


@admin.register(SheetWorkbook)
class SheetWorkbookAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "is_trashed", "view_count", "updated_at")
    list_filter = ("is_trashed",)
    search_fields = ("title",)
    readonly_fields = ("spreadsheet_id", "content_hash")
