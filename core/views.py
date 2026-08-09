from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
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


class PrivacyPolicyView(View):
    """Public privacy policy — required for Google OAuth production / consent screen."""

    def get(self, request):
        bot = (getattr(settings, "TELEGRAM_BOT_USERNAME", "") or "atlas_ai_financial_bot").lstrip(
            "@"
        )
        html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Atlas AI — Privacy Policy</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:42rem;margin:2rem auto;padding:0 1.25rem;line-height:1.55;color:#111}}
h1{{font-size:1.5rem}} h2{{font-size:1.1rem;margin-top:1.5rem}}
</style></head><body>
<h1>Atlas AI Privacy Policy</h1>
<p>Atlas AI (“Atlas”) is a Telegram financial assistant (<a href="https://t.me/{bot}">@{bot}</a>).</p>
<h2>What we access</h2>
<p>When you tap <b>Connect Google</b>, Atlas may request read access to Gmail, Google Calendar,
Google Drive, and Google Sheets so it can answer your questions inside Telegram.</p>
<h2>How we use data</h2>
<ul>
<li>Tokens are encrypted and stored per Telegram user.</li>
<li>We use Google data only to fulfill your requests (inbox summaries, schedule, files, sheets).</li>
<li>We do not sell your data.</li>
</ul>
<h2>Control</h2>
<p>You can disconnect Google anytime from your Google Account → Security → Third-party access,
or by asking Atlas to disconnect.</p>
<h2>Contact</h2>
<p>Questions: message the bot on Telegram or email the project operator.</p>
<p><small>Last updated: August 2026</small></p>
</body></html>"""
        return HttpResponse(html, content_type="text/html")
