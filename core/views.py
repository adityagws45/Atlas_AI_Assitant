from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.views import View


class HealthCheckView(View):
    def get(self, request):
        redirect = (getattr(settings, "GOOGLE_REDIRECT_URI", "") or "").strip()
        return JsonResponse(
            {
                "status": "ok",
                "service": "atlas-ai",
                # Safe to expose — Google already shows this on redirect_uri_mismatch.
                "oauth_redirect_uri": redirect,
                "oauth_redirect_is_https": redirect.startswith("https://"),
                "oauth_redirect_is_localhost": (
                    "localhost" in redirect or "127.0.0.1" in redirect
                ),
            }
        )


class TelegramBotRedirectView(View):
    """Visiting the deploy root opens the Atlas bot in Telegram."""

    def get(self, request):
        username = (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "").strip().lstrip("@")
        if not username:
            username = "atlas_ai_financial_bot"
        # Opens Telegram chat with START (same UX as hackathon demo submissions).
        return HttpResponseRedirect(f"https://t.me/{username}?start=web")
