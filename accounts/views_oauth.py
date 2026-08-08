"""OAuth HTTP endpoints for Google services — multi-user, deployment-ready."""

from __future__ import annotations

import logging

import httpx
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET

from accounts.services.google_oauth_service import GoogleOAuthService

logger = logging.getLogger("atlas.accounts.oauth_views")


def _notify_telegram(telegram_id: int | None, text: str) -> None:
    """Best-effort push so the user doesn't have to hunt for the success page."""
    token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    if not token or not telegram_id:
        return
    try:
        httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": int(telegram_id),
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        logger.exception("event=oauth_telegram_notify_failed telegram_id=%s", telegram_id)


@require_GET
def google_oauth_callback(request):
    code = (request.GET.get("code") or "").strip()
    state = (request.GET.get("state") or "").strip()
    error = (request.GET.get("error") or "").strip()
    if error:
        logger.warning("event=oauth_callback_denied error=%s", error[:40])
        return HttpResponse(
            "<html><body><h2>Authorization cancelled</h2>"
            "<p>You can close this tab and try again from Telegram.</p></body></html>",
            content_type="text/html",
        )
    if not code or not state:
        return HttpResponseBadRequest("Missing code or state.")
    result = GoogleOAuthService().handle_callback(code=code, state=state)
    if not result.get("ok"):
        msg = result.get("error") or "Authorization failed."
        logger.warning(
            "event=oauth_callback_failed code=%s",
            result.get("error_code") or "unknown",
        )
        # Tell Telegram the truth — never imply Calendar is connected
        telegram_id = result.get("telegram_id")
        service = result.get("service") or ""
        if telegram_id and result.get("error_code") == "insufficient_scopes":
            try:
                from accounts.models import GoogleService, User
                from gcalendar.services.calendar_service import CalendarService

                user = User.objects.filter(telegram_id=telegram_id).first()
                if user and service == GoogleService.CALENDAR:
                    reconnect = CalendarService().connect(user)
                    note = msg
                    if reconnect.get("auth_url"):
                        note = (
                            f"{msg}\n\n"
                            "Tap *Connect Google* again and make sure Calendar access is allowed:\n"
                            f"{reconnect['auth_url']}"
                        )
                    _notify_telegram(telegram_id, note)
                else:
                    _notify_telegram(telegram_id, msg)
            except Exception:  # noqa: BLE001
                logger.exception("event=oauth_insufficient_scopes_notify_failed")
                _notify_telegram(telegram_id, msg)
        elif telegram_id:
            _notify_telegram(telegram_id, msg)
        return HttpResponse(
            f"<html><body><h2>Couldn’t connect</h2><p>{msg}</p></body></html>",
            content_type="text/html",
            status=400,
        )
    logger.info(
        "event=oauth_callback_ok telegram_id=%s service=%s pending_sheet=%s",
        result.get("telegram_id"),
        result.get("service"),
        bool(result.get("pending_spreadsheet_id")),
    )

    telegram_note = "Connected. Return to Telegram."
    try:
        from accounts.models import GoogleService, User

        user = User.objects.filter(telegram_id=result.get("telegram_id")).first()
        service = result.get("service") or GoogleService.DRIVE
        if user and service == GoogleService.SHEETS:
            from sheets.models import SheetConnectionMode
            from sheets.services.sheet_service import SheetService

            sheets = SheetService()
            state_row = sheets.ensure_state(user)
            state_row.mode = SheetConnectionMode.OAUTH
            state_row.save(update_fields=["mode", "updated_at"])
            opened = sheets.resume_pending_after_oauth(
                user, spreadsheet_id=result.get("pending_spreadsheet_id") or ""
            )
            try:
                sheets.sync_catalog(user)
            except Exception:  # noqa: BLE001
                logger.warning("event=oauth_sheets_catalog_sync_failed", exc_info=True)
            telegram_note = opened.get("reply") or (
                "✅ Google connected. I've opened your spreadsheet. "
                "What would you like me to analyze?"
            )
            msg = (
                "Google Sheets is linked. Return to Telegram — Atlas will use "
                "the spreadsheet you shared."
            )
        elif user and service == GoogleService.GMAIL:
            from gmail.models import GmailConnectionMode
            from gmail.services.gmail_service import GmailService

            gmail = GmailService()
            resumed = gmail.resume_after_oauth(user)
            if resumed.get("ok"):
                state_row = gmail.ensure_state(user)
                state_row.mode = GmailConnectionMode.OAUTH
                state_row.save(update_fields=["mode", "updated_at"])
                msg = (
                    "Gmail is linked (read-only). Return to Telegram and ask Atlas "
                    "to check your email."
                )
                telegram_note = resumed.get("reply") or (
                    "✅ Gmail connected. Ask me to check your inbox."
                )
            else:
                msg = resumed.get("reply") or (
                    "Gmail permission could not be verified. "
                    "Return to Telegram and reconnect Google with Gmail access."
                )
                telegram_note = msg
                _notify_telegram(result.get("telegram_id"), telegram_note)
                return HttpResponse(
                    f"<html><body><h2>Gmail permission needed</h2><p>{msg}</p>"
                    "<p>You can close this tab and reconnect from Telegram.</p></body></html>",
                    content_type="text/html",
                    status=400,
                )
        elif user and service == GoogleService.CALENDAR:
            from gcalendar.models import CalendarConnectionMode
            from gcalendar.services.calendar_service import CalendarService

            calendar = CalendarService()
            resumed = calendar.resume_after_oauth(user)
            if resumed.get("ok"):
                state_row = calendar.ensure_state(user)
                state_row.mode = CalendarConnectionMode.OAUTH
                state_row.save(update_fields=["mode", "updated_at"])
                msg = (
                    "Google Calendar is linked. Return to Telegram — Atlas will use "
                    "your real schedule."
                )
                telegram_note = resumed.get("reply") or (
                    "✅ Google Calendar connected. Ask me about your schedule."
                )
            else:
                msg = resumed.get("reply") or (
                    "Calendar permission could not be verified. "
                    "Return to Telegram and reconnect Google with Calendar access."
                )
                telegram_note = msg
                # Do not show a success page when verification failed
                _notify_telegram(result.get("telegram_id"), telegram_note)
                return HttpResponse(
                    f"<html><body><h2>Calendar permission needed</h2><p>{msg}</p>"
                    "<p>You can close this tab and reconnect from Telegram.</p></body></html>",
                    content_type="text/html",
                    status=400,
                )
        elif user:
            from drive.models import DriveConnectionMode
            from drive.services.drive_sync import DriveSyncService

            sync = DriveSyncService()
            state_row = sync.ensure_state(user)
            state_row.mode = DriveConnectionMode.OAUTH
            state_row.save(update_fields=["mode", "updated_at"])
            sync.full_sync(user)
            msg = "Google Drive is linked. Return to Telegram and ask Atlas about your files."
            telegram_note = "✅ Google Drive connected. Ask me about your files."
        else:
            msg = "Connected. Return to Telegram."
    except Exception:  # noqa: BLE001
        logger.exception("event=oauth_post_sync_failed")
        msg = "Connected. Return to Telegram."
        telegram_note = "✅ Google connected. Return to Telegram."

    _notify_telegram(result.get("telegram_id"), telegram_note)
    return HttpResponse(
        f"<html><body><h2>Connected</h2><p>{msg}</p>"
        "<p>You can close this tab and return to Telegram.</p></body></html>",
        content_type="text/html",
    )
