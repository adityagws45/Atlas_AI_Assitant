from django.urls import path

from telegram_bot import webhook_views

urlpatterns = [
    path("webhook/", webhook_views.telegram_webhook, name="telegram-webhook"),
]
