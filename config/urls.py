from django.contrib import admin
from django.urls import include, path

from core.views import TelegramBotRedirectView

urlpatterns = [
    path("", TelegramBotRedirectView.as_view(), name="telegram-bot-redirect"),
    path("admin/", admin.site.urls),
    path("health/", include("core.urls")),
    path("api/telegram/", include("telegram_bot.urls")),
    path("api/oauth/", include("accounts.urls")),
]
