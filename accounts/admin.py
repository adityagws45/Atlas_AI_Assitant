from django.contrib import admin

from accounts.models import GoogleIntegration, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("telegram_id", "telegram_username", "role", "onboarding_completed", "created_at")
    search_fields = ("telegram_id", "telegram_username", "email")
    list_filter = ("role", "onboarding_completed")


@admin.register(GoogleIntegration)
class GoogleIntegrationAdmin(admin.ModelAdmin):
    list_display = ("user", "service", "is_active", "token_expires_at")
    list_filter = ("service", "is_active")
