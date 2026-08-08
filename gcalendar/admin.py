from django.contrib import admin

from gcalendar.models import CalendarEvent, CalendarSyncState


@admin.register(CalendarSyncState)
class CalendarSyncStateAdmin(admin.ModelAdmin):
    list_display = ("user", "mode", "status", "timezone", "last_synced_at", "updated_at")
    list_filter = ("mode", "status")


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "start_at", "end_at", "status", "importance")
    list_filter = ("status",)
    search_fields = ("title",)
    readonly_fields = ("event_id", "calendar_id", "description")
