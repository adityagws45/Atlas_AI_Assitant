from django.db import models

from accounts.models import User
from core.models import UUIDModel
from memory.models import Watchlist


class CompanyResearchHistory(UUIDModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="research_history")
    symbol = models.CharField(max_length=16, blank=True)
    company_name = models.CharField(max_length=255)
    query = models.TextField()
    response_summary = models.TextField()
    sources = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "company_research_history"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.company_name} - {self.query[:50]}"


class AlertType(models.TextChoices):
    PRICE_MOVE = "price_move", "Price Move"
    EARNINGS = "earnings", "Earnings"
    NEWS = "news", "News"
    SEC_FILING = "sec_filing", "SEC Filing"


class MarketAlert(UUIDModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="market_alerts")
    watchlist = models.ForeignKey(
        Watchlist, on_delete=models.SET_NULL, null=True, blank=True, related_name="alerts"
    )
    alert_type = models.CharField(max_length=16, choices=AlertType.choices)
    trigger_data = models.JSONField(default=dict, blank=True)
    triggered_at = models.DateTimeField(auto_now_add=True)
    notified = models.BooleanField(default=False)
    notification = models.ForeignKey(
        "notifications.NotificationLog",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="market_alerts",
    )

    class Meta:
        db_table = "market_alerts"
        ordering = ["-triggered_at"]

    def __str__(self):
        return f"{self.alert_type} for {self.user_id}"
