"""Manual OAuth finish — paste the full callback URL from the browser.

Usage:
  1. python manage.py runserver 8000
  2. python scripts/oauth_print_url.py          # opens Google consent
  3. After Google redirects (or if the page fails to load), copy the
     address bar URL (contains code= and state=) and run:

  python scripts/oauth_finish_from_url.py "http://localhost:8000/api/oauth/google/callback/?code=...&state=..."
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from accounts.services.google_oauth_service import GoogleOAuthService
from drive.models import DriveConnectionMode
from drive.services.drive_sync import DriveSyncService


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/oauth_finish_from_url.py \"<callback-url>\"")
        raise SystemExit(2)
    raw = sys.argv[1].strip().strip('"').strip("'")
    qs = parse_qs(urlparse(raw).query)
    code = (qs.get("code") or [""])[0]
    state = (qs.get("state") or [""])[0]
    err = (qs.get("error") or [""])[0]
    if err:
        print("Google returned error:", err)
        raise SystemExit(1)
    if not code or not state:
        print("URL missing code or state")
        raise SystemExit(1)
    result = GoogleOAuthService().handle_callback(code=code, state=state)
    print("callback_ok", result.get("ok"), "error", result.get("error_code") or "")
    if not result.get("ok"):
        print(result.get("error"))
        raise SystemExit(1)
    from accounts.models import User

    user = User.objects.filter(telegram_id=result.get("telegram_id")).first()
    if user:
        sync = DriveSyncService()
        st = sync.ensure_state(user)
        st.mode = DriveConnectionMode.OAUTH
        st.save(update_fields=["mode", "updated_at"])
        sync_result = sync.full_sync(user)
        print("full_sync_ok", sync_result.get("ok"), "stats", sync_result.get("stats"))
    print("DONE")


if __name__ == "__main__":
    main()
