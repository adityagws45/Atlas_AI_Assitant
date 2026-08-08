from django.contrib import admin

from notifications.models import NotificationLog, NotificationPreference


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "daily_briefing_enabled", "max_notifications_per_day")


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ("user", "notification_type", "was_suppressed", "sent_at")
    list_filter = ("notification_type", "was_suppressed")
