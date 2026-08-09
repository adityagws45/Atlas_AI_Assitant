from django.conf import settings
from django.http import JsonResponse
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
