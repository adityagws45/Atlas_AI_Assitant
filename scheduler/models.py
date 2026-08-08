from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel


class TaskType(models.TextChoices):
    DAILY_BRIEFING = "daily_briefing", "Daily Briefing"
    EVENING_SUMMARY = "evening_summary", "Evening Summary"
    WATCHLIST_SCAN = "watchlist_scan", "Watchlist Scan"
    EARNINGS_REMINDER = "earnings_reminder", "Earnings Reminder"
    TOKEN_REFRESH = "token_refresh", "Token Refresh"


class ScheduledTask(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, related_name="scheduled_tasks")
    task_type = models.CharField(max_length=32, choices=TaskType.choices)
    cron_expression = models.CharField(max_length=64, blank=True)
    run_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "scheduled_tasks"

    def __str__(self):
        return f"{self.task_type} ({self.user_id or 'system'})"
