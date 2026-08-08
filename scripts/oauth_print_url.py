"""Print + open Google Drive OAuth URL for telegram_id 9910000610 (live verify user)."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from accounts.models import GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService

TID = int(os.environ.get("ATLAS_LIVE_TELEGRAM_ID") or "9910000610")


def main() -> None:
    user, _ = User.objects.get_or_create(
        telegram_id=TID,
        defaults={"first_name": "LiveOAuth", "onboarding_completed": True},
    )
    if not user.onboarding_completed:
        user.onboarding_completed = True
        user.save(update_fields=["onboarding_completed"])
    oauth = GoogleOAuthService()
    if not oauth.is_configured():
        print("FAIL: GOOGLE_CLIENT_ID/SECRET not configured")
        raise SystemExit(1)
    started = oauth.start_auth(user, service=GoogleService.DRIVE)
    if not started.get("ok"):
        print("FAIL:", started.get("error"))
        raise SystemExit(1)
    url = started["auth_url"]
    (BASE / "oauth_auth_url.txt").write_text(url, encoding="utf-8")
    print(url)
    webbrowser.open(url)


if __name__ == "__main__":
    main()
