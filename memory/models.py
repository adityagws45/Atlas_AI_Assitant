from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel


class ResponseStyle(models.TextChoices):
    CONCISE = "concise", "Concise"
    DETAILED = "detailed", "Detailed"


class UserPreference(UUIDModel, TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="preferences")
    preferred_briefing_time = models.TimeField(null=True, blank=True)
    briefing_timezone = models.CharField(max_length=64, default="UTC")
    insight_types = models.JSONField(default=list, blank=True)
    response_style = models.CharField(
        max_length=16, choices=ResponseStyle.choices, default=ResponseStyle.CONCISE
    )
    sectors_of_interest = models.JSONField(default=list, blank=True)
    markets_of_interest = models.JSONField(default=list, blank=True)
    additional_verticals = models.JSONField(default=list, blank=True)
    language = models.CharField(max_length=16, default="en")

    class Meta:
        db_table = "user_preferences"

    def __str__(self):
        return f"Preferences for {self.user_id}"


class Watchlist(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="watchlist_items")
    symbol = models.CharField(max_length=16)
    company_name = models.CharField(max_length=255, blank=True)
    alert_on_news = models.BooleanField(default=True)
    alert_on_sec_filing = models.BooleanField(default=True)
    alert_on_price_move_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    notes = models.TextField(blank=True)

    class Meta:
        db_table = "watchlist"
        constraints = [
            models.UniqueConstraint(fields=["user", "symbol"], name="uniq_user_watchlist_symbol"),
        ]

    def __str__(self):
        return f"{self.symbol} ({self.user_id})"


class MemoryType(models.TextChoices):
    FACT = "fact", "Fact"
    PREFERENCE = "preference", "Preference"
    TASK = "task", "Task"
    CONTEXT = "context", "Context"


class MemorySource(models.TextChoices):
    ONBOARDING = "onboarding", "Onboarding"
    CONVERSATION = "conversation", "Conversation"
    INFERRED = "inferred", "Inferred"


class AssistantMemory(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memories")
    memory_type = models.CharField(max_length=16, choices=MemoryType.choices)
    key = models.CharField(max_length=128, db_index=True)
    value = models.JSONField()
    source = models.CharField(max_length=16, choices=MemorySource.choices)
    confidence = models.FloatField(default=1.0)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "assistant_memory"
        indexes = [
            models.Index(fields=["user", "key"]),
        ]

    def __str__(self):
        return f"{self.key}: {self.value}"
