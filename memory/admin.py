from django.contrib import admin

from memory.models import AssistantMemory, UserPreference, Watchlist


@admin.register(UserPreference)
class UserPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "preferred_briefing_time", "briefing_timezone", "response_style")


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("user", "symbol", "company_name", "alert_on_price_move_pct")
    search_fields = ("symbol", "company_name")


@admin.register(AssistantMemory)
class AssistantMemoryAdmin(admin.ModelAdmin):
    list_display = ("user", "memory_type", "key", "source", "confidence", "created_at")
    list_filter = ("memory_type", "source")
    search_fields = ("key",)
