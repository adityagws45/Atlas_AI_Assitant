from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("core.urls")),
    path("api/telegram/", include("telegram_bot.urls")),
    path("api/oauth/", include("accounts.urls")),
]
