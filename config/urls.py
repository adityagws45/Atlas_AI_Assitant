from django.contrib import admin
from django.urls import include, path

from core.views import PrivacyPolicyView, TelegramBotRedirectView

urlpatterns = [
    path("", TelegramBotRedirectView.as_view(), name="telegram-bot-redirect"),
    path("privacy/", PrivacyPolicyView.as_view(), name="privacy-policy"),
    path("admin/", admin.site.urls),
    path("health/", include("core.urls")),
    path("api/telegram/", include("telegram_bot.urls")),
    path("api/oauth/", include("accounts.urls")),
]
