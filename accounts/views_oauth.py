"""OAuth HTTP endpoints for Google services — multi-user, deployment-ready."""

from __future__ import annotations

import logging
import threading

import httpx
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from accounts.services.google_oauth_service import GoogleOAuthService

logger = logging.getLogger("atlas.accounts.oauth_views")


def _html_page(title: str, body: str, *, status: int = 200) -> HttpResponse:
    return HttpResponse(
        f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:36rem;margin:3rem auto;padding:0 1.25rem;line-height:1.5;color:#111}}
h1{{font-size:1.35rem;margin:0 0 .75rem}}
.card{{border:1px solid #e5e7eb;border-radius:12px;padding:1.25rem}}
.ok{{color:#065f46}} .warn{{color:#92400e}}
a{{color:#1d4ed8}}
</style></head>
<body><div class="card"><h1>{title}</h1>{body}</div>
<p style="margin-top:1.5rem;color:#6b7280;font-size:.9rem">You can close this tab and return to Telegram.</p>
</body></html>""",
        content_type="text/html",
        status=status,
    )


def _notify_telegram(telegram_id: int | None, text: str) -> None:
    """Best-effort push so the user doesn't have to hunt for the success page."""
    token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token or not telegram_id:
        return
    try:
        from telegram_bot.adapters.telegram_adapter import (
            extract_google_oauth_url,
            prepare_telegram_markdown,
            scrub_oauth_urls_for_display,
        )

        auth_url = extract_google_oauth_url(text or "")
        body = scrub_oauth_urls_for_display(text or "") if auth_url else (text or "")
        body = prepare_telegram_markdown(body)
        payload: dict = {
            "chat_id": int(telegram_id),
            "text": body,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }
        if auth_url:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": "🔗 Connect Google", "url": auth_url}]]
            }
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        logger.exception("event=oauth_telegram_notify_failed telegram_id=%s", telegram_id)


def _finish_oauth_background(result: dict) -> None:
    """Heavy post-connect work — must never block the OAuth HTTP response (avoids 502)."""
    from django.db import close_old_connections

    close_old_connections()
    telegram_note = "Google connected ✓\nReturn to Telegram and ask me anything."
    try:
        from accounts.models import GoogleService, User

        user = User.objects.filter(telegram_id=result.get("telegram_id")).first()
        service = result.get("service") or GoogleService.DRIVE
        saved = set(result.get("saved_services") or [service])

        if user and GoogleService.GMAIL in saved:
            try:
                from gmail.models import GmailConnectionMode
                from gmail.services.gmail_service import GmailService

                gmail_state = GmailService().ensure_state(user)
                gmail_state.mode = GmailConnectionMode.OAUTH
                gmail_state.save(update_fields=["mode", "updated_at"])
            except Exception:  # noqa: BLE001
                logger.warning("event=oauth_gmail_mode_set_failed", exc_info=True)
        if user and GoogleService.CALENDAR in saved:
            try:
                from gcalendar.models import CalendarConnectionMode
                from gcalendar.services.calendar_service import CalendarService

                cal_state = CalendarService().ensure_state(user)
                cal_state.mode = CalendarConnectionMode.OAUTH
                cal_state.save(update_fields=["mode", "updated_at"])
            except Exception:  # noqa: BLE001
                logger.warning("event=oauth_calendar_mode_set_failed", exc_info=True)
        if user and GoogleService.DRIVE in saved:
            try:
                from drive.models import DriveConnectionMode
                from drive.services.drive_sync import DriveSyncService

                drive_state = DriveSyncService().ensure_state(user)
                drive_state.mode = DriveConnectionMode.OAUTH
                drive_state.save(update_fields=["mode", "updated_at"])
            except Exception:  # noqa: BLE001
                logger.warning("event=oauth_drive_mode_set_failed", exc_info=True)
        if user and GoogleService.SHEETS in saved:
            try:
                from sheets.models import SheetConnectionMode
                from sheets.services.sheet_service import SheetService

                sheet_state = SheetService().ensure_state(user)
                if sheet_state.mode != SheetConnectionMode.PUBLIC:
                    sheet_state.mode = SheetConnectionMode.OAUTH
                    sheet_state.save(update_fields=["mode", "updated_at"])
            except Exception:  # noqa: BLE001
                logger.warning("event=oauth_sheets_mode_set_failed", exc_info=True)

        if user and service == GoogleService.SHEETS:
            from sheets.services.sheet_service import SheetService

            sheets = SheetService()
            opened = sheets.resume_pending_after_oauth(
                user, spreadsheet_id=result.get("pending_spreadsheet_id") or ""
            )
            try:
                sheets.sync_catalog(user)
            except Exception:  # noqa: BLE001
                logger.warning("event=oauth_sheets_catalog_sync_failed", exc_info=True)
            telegram_note = opened.get("reply") or (
                "Google connected ✓\nI've opened your spreadsheet."
            )
            if not str(telegram_note).lower().startswith("google connected"):
                telegram_note = "Google connected ✓\n" + str(telegram_note)
        elif user and service == GoogleService.GMAIL:
            from gmail.services.gmail_service import GmailService

            resumed = GmailService().resume_after_oauth(user)
            telegram_note = resumed.get("reply") or (
                "Google connected ✓\nAsk me to check your inbox."
            )
        elif user and service == GoogleService.CALENDAR:
            from gcalendar.services.calendar_service import CalendarService

            resumed = CalendarService().resume_after_oauth(user)
            telegram_note = resumed.get("reply") or (
                "Google connected ✓\nAsk me about your schedule."
            )
        elif user:
            from drive.services.drive_service import DriveService

            resumed = DriveService().resume_after_oauth(user)
            telegram_note = resumed.get("reply") or (
                "Google connected ✓\nAsk me about your files."
            )
    except Exception:  # noqa: BLE001
        logger.exception("event=oauth_post_sync_failed")
        telegram_note = "Google connected ✓\nReturn to Telegram."

    _notify_telegram(result.get("telegram_id"), telegram_note)
    close_old_connections()


@csrf_exempt
@require_GET
def google_oauth_callback(request):
    """
    Google redirects here after consent.

    Critical: return HTML quickly. Heavy Calendar/Gmail resume runs in a
    background thread so Render free-tier proxies do not 502 on slow work.
    """
    try:
        code = (request.GET.get("code") or "").strip()
        state = (request.GET.get("state") or "").strip()
        error = (request.GET.get("error") or "").strip()
        if error:
            logger.warning("event=oauth_callback_denied error=%s", error[:40])
            denied = error.lower()
            if "access_denied" in denied:
                tip = (
                    "<p class='warn'>You cancelled Google access.</p>"
                    "<p>Return to Telegram and tap <b>Connect Google</b> again, "
                    "then tap <b>Allow</b> on every permission screen.</p>"
                )
            else:
                tip = f"<p class='warn'>Google returned: {error}</p><p>Try Connect Google again from Telegram.</p>"
            return _html_page("Authorization cancelled", tip)

        if not code or not state:
            return _html_page(
                "Link incomplete",
                "<p>This Google callback is missing data.</p>"
                "<p>Go back to Telegram and tap <b>Connect Google</b> for a fresh link.</p>",
                status=400,
            )

        result = GoogleOAuthService().handle_callback(code=code, state=state)
        if not result.get("ok"):
            msg = result.get("error") or "Authorization failed."
            logger.warning(
                "event=oauth_callback_failed code=%s",
                result.get("error_code") or "unknown",
            )
            telegram_id = result.get("telegram_id")
            if telegram_id:
                _notify_telegram(telegram_id, msg)
            return _html_page(
                "Couldn’t finish Google connect",
                f"<p class='warn'>{msg}</p>"
                "<p>Return to Telegram and tap <b>Connect Google</b> again.</p>"
                "<p>If Google showed <i>unverified app</i>, tap "
                "<b>Advanced → Go to Atlas (unsafe) → Allow</b>.</p>",
                status=400,
            )

        logger.info(
            "event=oauth_callback_ok telegram_id=%s service=%s pending_sheet=%s",
            result.get("telegram_id"),
            result.get("service"),
            bool(result.get("pending_spreadsheet_id")),
        )

        # Respond immediately — finish Calendar/Gmail resume off the request path.
        threading.Thread(
            target=_finish_oauth_background,
            args=(result,),
            name="oauth-finish",
            daemon=True,
        ).start()

        return _html_page(
            "Google connected ✓",
            "<p class='ok'><b>You're all set.</b> Calendar, Gmail, Drive, and Sheets "
            "are linked for Atlas.</p>"
            "<p>Return to Telegram — Atlas will continue your request there.</p>",
        )
    except Exception:  # noqa: BLE001
        logger.exception("event=oauth_callback_unhandled")
        # Never 502 — always return a readable page Google/users can see.
        return _html_page(
            "Almost there",
            "<p class='warn'>Google authorization hit a temporary server hiccup.</p>"
            "<p>Return to Telegram and tap <b>Connect Google</b> once more. "
            "If it still fails, wait 30 seconds (server wake-up) and retry.</p>",
            status=200,
        )
