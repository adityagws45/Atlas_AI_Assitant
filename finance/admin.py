from django.contrib import admin

from finance.models import CompanyResearchHistory, MarketAlert


@admin.register(CompanyResearchHistory)
class CompanyResearchHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "symbol", "company_name", "created_at")
    search_fields = ("symbol", "company_name", "query")


@admin.register(MarketAlert)
class MarketAlertAdmin(admin.ModelAdmin):
    list_display = ("user", "alert_type", "notified", "triggered_at")
    list_filter = ("alert_type", "notified")
