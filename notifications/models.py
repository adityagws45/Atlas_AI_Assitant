from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel


class NotificationPreference(UUIDModel, TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="notification_preferences")
    daily_briefing_enabled = models.BooleanField(default=True)
    evening_summary_enabled = models.BooleanField(default=False)
    max_notifications_per_day = models.PositiveIntegerField(default=5)
    quiet_hours_start = models.TimeField(null=True, blank=True)
    quiet_hours_end = models.TimeField(null=True, blank=True)
    last_briefing_sent_at = models.DateTimeField(null=True, blank=True)
    last_proactive_sent_at = models.DateTimeField(null=True, blank=True)
    silence_if_nothing_important = models.BooleanField(default=True)

    class Meta:
        db_table = "notification_preferences"

    def __str__(self):
        return f"Notification prefs for {self.user_id}"


class NotificationType(models.TextChoices):
    BRIEFING = "briefing", "Briefing"
    ALERT = "alert", "Alert"
    REMINDER = "reminder", "Reminder"
    PROACTIVE = "proactive", "Proactive"


class NotificationLog(UUIDModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notification_logs")
    notification_type = models.CharField(max_length=16, choices=NotificationType.choices)
    content = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    telegram_message_id = models.BigIntegerField(null=True, blank=True)
    was_suppressed = models.BooleanField(default=False)
    suppression_reason = models.CharField(max_length=128, blank=True)

    class Meta:
        db_table = "notification_logs"
        ordering = ["-sent_at"]
