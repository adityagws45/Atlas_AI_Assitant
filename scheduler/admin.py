from django.contrib import admin

from scheduler.models import ScheduledTask


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = ("task_type", "user", "is_active", "last_run_at", "next_run_at")
    list_filter = ("task_type", "is_active")
