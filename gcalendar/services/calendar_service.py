"""Calendar facade — schedule intelligence for Telegram + tools."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from django.utils import timezone as dj_tz

from accounts.models import GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService
from telegram_bot.adapters.oauth_ux import (
    google_access_required_reply,
    google_connected_prefix,
)
from core.crypto import encrypt_text
from gcalendar.models import (
    CalendarConnectionMode,
    CalendarEvent,
    CalendarSyncState,
    CalendarSyncStatus,
)
from gcalendar.services.calendar_client import build_calendar_client
from gcalendar.services.calendar_intel import (
    day_bounds,
    enrich_event,
    find_conflicts,
    find_free_slots,
    format_busy,
    format_conflicts,
    format_day,
    format_free_slots,
    is_free_at,
    longest_event,
    parse_when,
)
from gcalendar.services.calendar_intent import detect_calendar_intent
from gcalendar.services.calendar_memory import CalendarMemory

logger = logging.getLogger("atlas.calendar.service")

# Per-user demo clients (never share one global calendar across Telegram users)
_DEMO_CLIENTS: dict[str, Any] = {}


class CalendarService:
    def __init__(
        self,
        *,
        oauth: GoogleOAuthService | None = None,
        memory: CalendarMemory | None = None,
    ) -> None:
        self.oauth = oauth or GoogleOAuthService()
        self.memory = memory or CalendarMemory()

    def handle_intent(self, user: User, text: str) -> dict[str, Any] | None:
        # Pending confirmation always wins on yes/confirm
        if self.memory.has_pending(user) and detect_calendar_intent(text).kind == "confirm":
            return self.confirm_pending(user)
        intent = detect_calendar_intent(
            text, has_calendar_context=self.memory.has_recent_context(user)
        )
        if intent.kind == "none":
            return None
        if intent.kind == "confirm":
            if self.memory.has_pending(user):
                return self.confirm_pending(user)
            return None  # let other pipelines handle bare "yes"
        handlers = {
            "connect": lambda: self.connect(user),
            "today": lambda: self.day_view(user, offset_days=0, question=text),
            "tomorrow": lambda: self.day_view(user, offset_days=1, question=text),
            "week": lambda: self.week_view(user, question=text),
            "next": lambda: self.next_meeting(user),
            "free": lambda: self.free_time(user, text),
            "conflicts": lambda: self.conflicts(user, text),
            "deadlines": lambda: self.deadlines(user),
            "busy": lambda: self.busy_view(user, text),
            "followup": lambda: self.followup(user, text),
            "create": lambda: self.propose_create(user, text),
            "update": lambda: self.propose_update(user, text),
            "cancel": lambda: self.propose_cancel(user, text),
            "search": lambda: self.search(user, text),
            "clarify_task": lambda: {
                "ok": True,
                "handled": True,
                "reply": "Do you mean tasks or events on your Google Calendar?",
            },
        }
        handler = handlers.get(intent.kind)
        return handler() if handler else None

    def ensure_state(self, user: User) -> CalendarSyncState:
        state, _ = CalendarSyncState.objects.get_or_create(user=user)
        return state

    def is_live_connected(self, user: User) -> bool:
        token = self.oauth.get_valid_access_token(user, service=GoogleService.CALENDAR)
        if not token:
            return False
        # Token must actually carry Calendar scopes — DB flags alone are not enough
        if not self.oauth.token_has_required_scopes(user, service=GoogleService.CALENDAR):
            self.oauth.disconnect(user, service=GoogleService.CALENDAR)
            return False
        return True

    def is_connected(self, user: User) -> bool:
        state = CalendarSyncState.objects.filter(user=user).first()
        if state and state.mode == CalendarConnectionMode.DEMO:
            return True
        return self.is_live_connected(user)

    def verify_calendar_access(self, user: User) -> dict[str, Any]:
        """Lightweight live Calendar API probe — call before claiming 'connected'."""
        token = self.oauth.get_valid_access_token(user, service=GoogleService.CALENDAR)
        if not token:
            return {
                "ok": False,
                "error_code": "auth_required",
                "error": (
                    "🔐 I don't currently have permission to read your Google Calendar. "
                    "Please reconnect Google."
                ),
            }
        if not self.oauth.token_has_required_scopes(user, service=GoogleService.CALENDAR):
            self.oauth.disconnect(user, service=GoogleService.CALENDAR)
            return {
                "ok": False,
                "error_code": "permission_denied",
                "error": (
                    "🔐 I don't currently have permission to read your Google Calendar. "
                    "Please reconnect Google and allow Calendar access."
                ),
            }
        try:
            client = build_calendar_client(access_token=token, demo=False)
            now = self._now(user)
            # Tiny window — proves scopes + Calendar API, not a full sync
            client.list_events(time_min=now, time_max=now + timedelta(hours=1))
            return {"ok": True}
        except PermissionError as exc:
            code = str(exc) or "permission_denied"
            # Only revoke token when scopes/auth are actually wrong
            self.oauth.disconnect(user, service=GoogleService.CALENDAR)
            if code == "auth_required":
                return {
                    "ok": False,
                    "error_code": "auth_required",
                    "error": (
                        "🔐 I don't currently have permission to read your Google Calendar. "
                        "Please reconnect Google."
                    ),
                }
            return {
                "ok": False,
                "error_code": "permission_denied",
                "error": (
                    "🔐 I don't currently have permission to read your Google Calendar. "
                    "Please reconnect Google and allow Calendar access."
                ),
            }
        except RuntimeError as exc:
            if str(exc) == "api_disabled":
                logger.error(
                    "event=calendar_api_disabled telegram_id=%s project_hint=73169070550",
                    user.telegram_id,
                )
                # Keep the valid OAuth token — enabling the API will make it work
                return {
                    "ok": False,
                    "error_code": "api_disabled",
                    "error": (
                        "⚙️ Google Calendar API is not enabled on this project's Google Cloud "
                        "account (project 73169070550).\n\n"
                        "Enable it here, wait 1–2 minutes, then ask again "
                        "(OAuth is already done — just enable the API):\n"
                        "https://console.developers.google.com/apis/api/calendar-json.googleapis.com/overview?project=73169070550"
                    ),
                }
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=calendar_verify_failed err=%s", type(exc).__name__
            )
            return {
                "ok": False,
                "error_code": "temporary",
                "error": (
                    "⚠️ I couldn't reach Google Calendar right now. Please try again."
                ),
            }

    def _handle_sync_failure(
        self,
        user: User,
        synced: dict[str, Any],
        *,
        question: str = "",
    ) -> dict[str, Any]:
        """Map sync failures to user-facing replies — never pretend the calendar is empty."""
        code = synced.get("error_code") or "temporary"
        if code == "auth_required":
            return self.connect(user, pending_question=question)
        if code == "permission_denied":
            self.oauth.disconnect(user, service=GoogleService.CALENDAR)
            linked = self.connect(user, pending_question=question)
            linked["reply"] = (
                "🔐 I don't currently have permission to read your Google Calendar. "
                "Please reconnect Google.\n\n"
                + (linked.get("reply") or "")
            )
            linked["error_code"] = "permission_denied"
            return linked
        if code == "api_disabled":
            return {
                "ok": False,
                "handled": True,
                "error_code": "api_disabled",
                "reply": synced.get("error")
                or (
                    "⚙️ Google Calendar API is not enabled on this Google Cloud project. "
                    "Enable Calendar API, wait a minute, then ask again."
                ),
            }
        if code == "temporary":
            return {
                "ok": False,
                "handled": True,
                "error_code": "temporary",
                "reply": (
                    synced.get("error")
                    or "⚠️ I couldn't reach Google Calendar right now. Please try again."
                ),
            }
        return {
            "ok": False,
            "handled": True,
            "error_code": code,
            "reply": synced.get("error") or "I couldn't load your calendar.",
        }

    def connect_demo(self, user: User) -> CalendarSyncState:
        integ, _ = user.google_integrations.get_or_create(
            service=GoogleService.CALENDAR,
            defaults={
                "access_token_encrypted": encrypt_text("demo:calendar"),
                "refresh_token_encrypted": encrypt_text("demo:calendar"),
                "is_active": True,
                "scopes": ["calendar.events.demo"],
            },
        )
        if not integ.is_active:
            integ.is_active = True
            integ.access_token_encrypted = encrypt_text("demo:calendar")
            integ.save(update_fields=["is_active", "access_token_encrypted", "updated_at"])
        state = self.ensure_state(user)
        state.mode = CalendarConnectionMode.DEMO
        state.status = CalendarSyncStatus.IDLE
        state.error_message = ""
        state.save(update_fields=["mode", "status", "error_message", "updated_at"])
        return state

    def connect(self, user: User, *, pending_question: str = "") -> dict[str, Any]:
        if pending_question:
            self.memory.remember_pending_question(user, pending_question)
        if self.oauth.is_configured():
            started = self.oauth.start_auth(user, service=GoogleService.CALENDAR)
            if started.get("ok") and started.get("auth_url"):
                auth_url = started["auth_url"]
                return {
                    "ok": False,
                    "handled": True,
                    "needs_oauth": True,
                    "auth_url": auth_url,
                    "reply": google_access_required_reply(
                        auth_url,
                        purpose=(
                            "Connect Google once for Calendar, Gmail, Drive, and Sheets."
                        ),
                    ),
                }
            return {
                "ok": False,
                "handled": True,
                "needs_oauth": True,
                "reply": started.get("error")
                or "I couldn't start Google Calendar authorization. Please try again.",
            }
        # Dev-only fallback when OAuth isn't configured on this server
        self.connect_demo(user)
        self.sync_range(user)
        return {
            "ok": True,
            "handled": True,
            "demo": True,
            "reply": (
                "Your schedule is ready (local demo calendar — live Google OAuth is not "
                "configured on this server).\n\n"
                "Try:\n"
                "• “What does my day look like?”\n"
                "• “When am I free?”\n"
                "• “Any conflicts?”"
            ),
        }

    def ensure_ready(self, user: User, *, question: str = "") -> dict[str, Any] | None:
        """Return an OAuth prompt dict if the user can't access live/demo calendar yet."""
        if self.is_live_connected(user):
            state = self.ensure_state(user)
            if state.mode != CalendarConnectionMode.OAUTH:
                state.mode = CalendarConnectionMode.OAUTH
                state.save(update_fields=["mode", "updated_at"])
            return None
        state = CalendarSyncState.objects.filter(user=user).first()
        if state and state.mode == CalendarConnectionMode.DEMO:
            return None
        # Prefer live OAuth whenever configured — never silently substitute demo
        if self.oauth.is_configured():
            return self.connect(user, pending_question=question)
        self.connect_demo(user)
        return None

    def resume_after_oauth(self, user: User) -> dict[str, Any]:
        """Called from OAuth callback — verify API access, then answer pending question."""
        verified = self.verify_calendar_access(user)
        if not verified.get("ok"):
            # Keep pending question so reconnect can resume it
            question = self.memory.pop_pending_question(user)
            if question:
                self.memory.remember_pending_question(user, question)
            reconnect = self.connect(user, pending_question=question or "")
            reason = verified.get("error") or (
                "🔐 Calendar permission could not be verified. Please reconnect Google."
            )
            return {
                "ok": False,
                "error_code": verified.get("error_code") or "permission_denied",
                "needs_oauth": True,
                "auth_url": reconnect.get("auth_url"),
                "reply": (
                    f"{reason}\n\n"
                    + (
                        reconnect.get("reply")
                        if reconnect.get("auth_url")
                        else "Tap Connect Google again and allow Calendar access."
                    )
                ),
            }

        state = self.ensure_state(user)
        state.mode = CalendarConnectionMode.OAUTH
        state.error_message = ""
        state.save(update_fields=["mode", "error_message", "updated_at"])

        synced = self.sync_range(user)
        question = self.memory.pop_pending_question(user)
        prefix = google_connected_prefix(action="Checking today's schedule…")
        if not synced.get("ok"):
            # Verified scopes but sync failed — do NOT claim success with empty data
            fail = self._handle_sync_failure(user, synced, question=question or "")
            if question:
                self.memory.remember_pending_question(user, question)
            return {
                "ok": False,
                "error_code": fail.get("error_code"),
                "needs_oauth": bool(fail.get("needs_oauth")),
                "auth_url": fail.get("auth_url"),
                "reply": fail.get("reply")
                or "I couldn't load your calendar just yet. Try asking again.",
            }
        if question:
            result = self.handle_intent(user, question) or {}
            body = result.get("reply") or "Ask me what’s on your calendar."
            # If the resumed answer itself needs oauth, don't prefix "connected"
            if result.get("needs_oauth") or result.get("error_code") in {
                "permission_denied",
                "auth_required",
                "temporary",
                "api_disabled",
            }:
                return {
                    "ok": bool(result.get("ok")),
                    "error_code": result.get("error_code"),
                    "needs_oauth": bool(result.get("needs_oauth")),
                    "auth_url": result.get("auth_url"),
                    "reply": body,
                }
            return {"ok": True, "reply": prefix + body}
        day = self.day_view(user, offset_days=0)
        if day.get("needs_oauth") or day.get("error_code") in {
            "permission_denied",
            "auth_required",
            "temporary",
            "api_disabled",
        }:
            return {
                "ok": bool(day.get("ok")),
                "error_code": day.get("error_code"),
                "needs_oauth": bool(day.get("needs_oauth")),
                "auth_url": day.get("auth_url"),
                "reply": day.get("reply") or "",
            }
        return {"ok": True, "reply": prefix + (day.get("reply") or "")}

    def _client_for(self, user: User):
        state = self.ensure_state(user)
        token = self.oauth.get_valid_access_token(user, service=GoogleService.CALENDAR)
        if token:
            # Real OAuth token — NEVER fall back to demo/mock calendar
            if str(token).startswith("demo:"):
                raise PermissionError("auth_required")
            if state.mode != CalendarConnectionMode.OAUTH:
                state.mode = CalendarConnectionMode.OAUTH
                state.save(update_fields=["mode", "updated_at"])
            return build_calendar_client(access_token=token, demo=False)
        if state.mode == CalendarConnectionMode.OAUTH:
            # OAuth mode but token missing/invalid — do not silently serve demo
            raise PermissionError("auth_required")
        if state.mode == CalendarConnectionMode.DEMO:
            key = str(user.id)
            client = _DEMO_CLIENTS.get(key)
            if client is None:
                client = build_calendar_client(access_token="demo:calendar", demo=True)
                _DEMO_CLIENTS[key] = client
            return client
        raise PermissionError("auth_required")

    def _user_tz(self, user: User):
        """Return tzinfo for the user (calendar timezone when known)."""
        from zoneinfo import ZoneInfo

        state = self.ensure_state(user)
        name = (state.timezone or "UTC").strip() or "UTC"
        try:
            return ZoneInfo(name)
        except Exception:  # noqa: BLE001
            return timezone.utc

    def _now(self, user: User) -> datetime:
        return datetime.now(tz=self._user_tz(user))

    def sync_range(
        self,
        user: User,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> dict[str, Any]:
        state = self.ensure_state(user)
        state.status = CalendarSyncStatus.RUNNING
        state.save(update_fields=["status", "updated_at"])
        now = self._now(user)
        start = start or (now - timedelta(days=1))
        end = end or (now + timedelta(days=14))
        try:
            client = self._client_for(user)
            if hasattr(client, "get_timezone"):
                try:
                    tz_name = client.get_timezone()
                    if tz_name and tz_name != state.timezone:
                        state.timezone = tz_name[:64]
                        state.save(update_fields=["timezone", "updated_at"])
                except Exception:  # noqa: BLE001
                    pass
            remotes = client.list_events(time_min=start, time_max=end)
            is_demo = (
                state.mode == CalendarConnectionMode.DEMO
                or type(client).__name__ == "MockCalendarClient"
            )
            if state.mode == CalendarConnectionMode.OAUTH and is_demo:
                logger.error(
                    "event=calendar_demo_blocked_for_oauth telegram_id=%s",
                    user.telegram_id,
                )
                raise PermissionError("auth_required")
            remote_ids = {remote.id for remote in remotes if remote.id}
            for remote in remotes:
                enriched = enrich_event(remote)
                CalendarEvent.objects.update_or_create(
                    user=user,
                    event_id=remote.id,
                    defaults={
                        "calendar_id": remote.calendar_id or "primary",
                        "title": (remote.title or "Untitled")[:512],
                        "description": (remote.description or "")[:4000],
                        "location": (remote.location or "")[:512],
                        "start_at": remote.start_at,
                        "end_at": remote.end_at,
                        "all_day": remote.all_day,
                        "status": remote.status or "confirmed",
                        "is_recurring": remote.is_recurring,
                        "categories": enriched["categories"],
                        "companies": enriched["companies"],
                        "tickers": enriched["tickers"],
                        "importance": enriched["importance"],
                    },
                )
            # Drop stale local rows in this window that Google no longer returns
            # (prevents fixture contamination from surviving a real sync).
            stale_qs = CalendarEvent.objects.filter(
                user=user,
                start_at__lt=end,
                end_at__gt=start,
            )
            if remote_ids:
                stale_qs = stale_qs.exclude(event_id__in=remote_ids)
            deleted, _ = stale_qs.delete()
            logger.info(
                "event=calendar_sync_ok telegram_id=%s source=%s listed=%s stale_deleted=%s sample_ids=%s",
                user.telegram_id,
                "demo" if is_demo else "google_calendar_api",
                len(remotes),
                deleted,
                [r.id for r in remotes[:5]],
            )
            state.status = CalendarSyncStatus.IDLE
            state.last_synced_at = dj_tz.now()
            state.stats = {
                "listed": len(remotes),
                "stale_deleted": deleted,
                "source": "demo" if is_demo else "google_calendar_api",
            }
            state.error_message = ""
            state.save()
            return {
                "ok": True,
                "count": len(remotes),
                "source": "demo" if is_demo else "google_calendar_api",
                "stale_deleted": deleted,
            }
        except PermissionError as exc:
            state.status = CalendarSyncStatus.FAILED
            code = str(exc) or "auth_required"
            state.error_message = code[:200]
            state.save(update_fields=["status", "error_message", "updated_at"])
            if code == "permission_denied":
                self.oauth.disconnect(user, service=GoogleService.CALENDAR)
                return {
                    "ok": False,
                    "error_code": "permission_denied",
                    "error": (
                        "🔐 I don't currently have permission to read your Google Calendar. "
                        "Please reconnect Google."
                    ),
                }
            return {
                "ok": False,
                "error_code": "auth_required",
                "error": (
                    "🔐 I don't currently have permission to read your Google Calendar. "
                    "Please reconnect Google."
                ),
            }
        except RuntimeError as exc:
            if str(exc) == "api_disabled":
                state.status = CalendarSyncStatus.FAILED
                state.error_message = "api_disabled"
                state.save(update_fields=["status", "error_message", "updated_at"])
                return {
                    "ok": False,
                    "error_code": "api_disabled",
                    "error": (
                        "⚙️ Google Calendar API is not enabled on this project's Google Cloud "
                        "account (project 73169070550).\n\n"
                        "Enable it here, wait 1–2 minutes, then ask again "
                        "(OAuth is already done — just enable the API):\n"
                        "https://console.developers.google.com/apis/api/calendar-json.googleapis.com/overview?project=73169070550"
                    ),
                }
            raise
        except FileNotFoundError:
            state.status = CalendarSyncStatus.FAILED
            state.error_message = "calendar_not_found"
            state.save(update_fields=["status", "error_message", "updated_at"])
            return {
                "ok": False,
                "error_code": "not_found",
                "error": "I couldn't find your primary Google Calendar.",
            }
        except Exception as exc:  # noqa: BLE001
            state.status = CalendarSyncStatus.FAILED
            state.error_message = exc.__class__.__name__[:200]
            state.save(update_fields=["status", "error_message", "updated_at"])
            return {
                "ok": False,
                "error_code": "temporary",
                "error": (
                    "⚠️ I couldn't reach Google Calendar right now. Please try again."
                ),
            }

    def day_view(
        self, user: User, *, offset_days: int = 0, question: str = ""
    ) -> dict[str, Any]:
        gate = self.ensure_ready(user, question=question or "What's on my calendar today?")
        if gate:
            return gate
        synced = self.sync_range(user)
        if not synced.get("ok"):
            return self._handle_sync_failure(
                user, synced, question=question or "What's on my calendar today?"
            )
        now = self._now(user)
        day0, day1 = day_bounds(now, offset_days=offset_days)
        rows = list(
            CalendarEvent.objects.filter(
                user=user,
                status="confirmed",
                start_at__lt=day1,
                end_at__gt=day0,
            ).order_by("start_at")
        )
        # Afternoon filter
        low = (question or "").lower()
        if "afternoon" in low:
            aft = day0.replace(hour=12, minute=0)
            rows = [r for r in rows if r.end_at > aft]
        if "morning" in low:
            noon = day0.replace(hour=12, minute=0)
            rows = [r for r in rows if r.start_at < noon]
        if "important" in low:
            rows = [r for r in rows if (r.importance or 0) >= 10] or rows

        payload = [self._row_dict(r) for r in rows]
        # Re-localize display times into user tz for formatting
        for p in payload:
            p["start_at"] = p["start_at"].astimezone(self._user_tz(user))
            p["end_at"] = p["end_at"].astimezone(self._user_tz(user))
        title = "Today" if offset_days == 0 else ("Tomorrow" if offset_days == 1 else day0.strftime("%A"))
        reply = format_day(payload, title=title)
        self.memory.remember_context(user, events=payload, label=title, offset_days=offset_days)
        return {"ok": True, "handled": True, "reply": reply, "events": payload}

    def week_view(self, user: User, *, question: str = "") -> dict[str, Any]:
        gate = self.ensure_ready(user, question=question or "What's my schedule this week?")
        if gate:
            return gate
        synced = self.sync_range(user)
        if not synced.get("ok"):
            return self._handle_sync_failure(
                user, synced, question=question or "What's my schedule this week?"
            )
        now = self._now(user)
        start, _ = day_bounds(now, offset_days=0)
        end = start + timedelta(days=7)
        rows = list(
            CalendarEvent.objects.filter(
                user=user, status="confirmed", start_at__gte=start, start_at__lt=end
            ).order_by("start_at")[:30]
        )
        payload = [self._row_dict(r) for r in rows]
        for p in payload:
            p["start_at"] = p["start_at"].astimezone(self._user_tz(user))
            p["end_at"] = p["end_at"].astimezone(self._user_tz(user))
        reply = format_day(payload, title="This week")
        self.memory.remember_context(user, events=payload, label="This week", offset_days=0)
        return {"ok": True, "handled": True, "reply": reply, "events": payload}

    def next_meeting(self, user: User) -> dict[str, Any]:
        gate = self.ensure_ready(user, question="When is my next meeting?")
        if gate:
            return gate
        synced = self.sync_range(user)
        if not synced.get("ok"):
            return self._handle_sync_failure(
                user, synced, question="When is my next meeting?"
            )
        now = self._now(user)
        row = (
            CalendarEvent.objects.filter(user=user, status="confirmed", start_at__gte=now)
            .order_by("start_at")
            .first()
        )
        if not row:
            return {
                "ok": True,
                "handled": True,
                "reply": "📅 You have no upcoming calendar events.",
            }
        data = self._row_dict(row)
        data["start_at"] = data["start_at"].astimezone(self._user_tz(user))
        data["end_at"] = data["end_at"].astimezone(self._user_tz(user))
        self.memory.remember_event(user, data)
        self.memory.remember_context(user, events=[data], label="Next meeting")
        return {
            "ok": True,
            "handled": True,
            "reply": (
                f"*Next meeting*\n*{row.title}* — "
                f"{data['start_at'].strftime('%a %H:%M')}–{data['end_at'].strftime('%H:%M')}."
            ),
            "event": data,
        }

    def free_time(self, user: User, text: str) -> dict[str, Any]:
        gate = self.ensure_ready(user, question=text)
        if gate:
            return gate
        synced = self.sync_range(user)
        if not synced.get("ok"):
            return self._handle_sync_failure(user, synced, question=text)
        when = parse_when(text, now=self._now(user))
        low = text.lower()
        now = self._now(user)
        day0, day1 = day_bounds(now, offset_days=0)
        if "tomorrow" in low:
            day0, day1 = day_bounds(now, offset_days=1)
        rows = list(
            CalendarEvent.objects.filter(
                user=user, status="confirmed", start_at__lt=day1, end_at__gt=day0
            )
        )
        payload = [self._row_dict(r) for r in rows]
        for p in payload:
            p["start_at"] = p["start_at"].astimezone(self._user_tz(user))
            p["end_at"] = p["end_at"].astimezone(self._user_tz(user))

        # "Am I free at 3 PM?" / "between 2 and 5"
        if re.search(r"\bam i free at\b", low) or re.search(r"\bbetween\b", low):
            moment = when["start_at"]
            if "between" in low:
                m2 = re.findall(r"\b(\d{1,2})\s*(am|pm)?\b", low)
                if len(m2) >= 2:
                    # window free check: any event overlapping the window?
                    start = when["start_at"]
                    # re-parse end hour roughly
                    end_hour = start.hour + 3
                    end = start.replace(hour=min(end_hour, 23))
                    overlaps = [
                        e for e in payload if e["start_at"] < end and e["end_at"] > start
                    ]
                    if not overlaps:
                        reply = (
                            f"Yes — you're free {_fmt_simple(start)}–{_fmt_simple(end)} "
                            f"on {day0.strftime('%a %d %b')}."
                        )
                    else:
                        reply = (
                            f"You have {len(overlaps)} event(s) in that window:\n"
                            + format_day(overlaps, title="Busy window")
                        )
                    self.memory.remember_context(
                        user, events=payload, label=day0.strftime("%A")
                    )
                    return {"ok": True, "handled": True, "reply": reply, "events": payload}
            free = is_free_at(payload, moment=moment, duration_minutes=when.get("duration_minutes") or 60)
            if free:
                reply = f"Yes — you're free around {moment.strftime('%a %H:%M')}."
            else:
                hits = [e for e in payload if e["start_at"] < moment + timedelta(minutes=60) and e["end_at"] > moment]
                reply = "No — that overlaps:\n" + format_day(hits or payload[:3], title="Conflict")
            return {"ok": True, "handled": True, "reply": reply, "events": payload}

        duration = when.get("duration_minutes") or 60
        if "two hours" in low or "2 hours" in low or "1-hour" in low or "one-hour" in low or "1 hour" in low:
            if "two" in low or "2 hour" in low:
                duration = 120
            elif "1-hour" in low or "one-hour" in low or "1 hour" in low or "free hour" in low:
                duration = 60
        slots = find_free_slots(
            payload, day_start=day0, day_end=day1, duration_minutes=duration
        )
        label = f"{duration} min on {day0.strftime('%a %d %b')}"
        self.memory.remember_context(user, events=payload, label=day0.strftime("%A"))
        return {
            "ok": True,
            "handled": True,
            "reply": format_free_slots(slots, label=label),
            "slots": [(a.isoformat(), b.isoformat()) for a, b in slots],
        }

    def conflicts(self, user: User, text: str = "") -> dict[str, Any]:
        gate = self.ensure_ready(user, question=text or "Any conflicts?")
        if gate:
            return gate
        low = (text or "").lower()
        offset = 1 if "tomorrow" in low else 0
        day = self.day_view(user, offset_days=offset, question=text)
        if day.get("needs_oauth") or (
            not day.get("ok")
            and day.get("error_code")
            in {"auth_required", "permission_denied", "temporary", "not_found"}
        ):
            return day
        events = day.get("events") or []
        conf = find_conflicts(events)
        title = "Tomorrow" if offset else "Today"
        if not conf:
            return {
                "ok": True,
                "handled": True,
                "reply": f"No overlapping meetings on {title.lower()}.",
            }
        reply = format_conflicts(conf)
        self.memory.remember_context(user, events=events, label=title, offset_days=offset)
        return {"ok": True, "handled": True, "reply": reply, "conflicts": conf}

    def busy_view(self, user: User, text: str) -> dict[str, Any]:
        low = (text or "").lower()
        offset = 1 if "tomorrow" in low else 0
        day = self.day_view(user, offset_days=offset, question=text)
        if day.get("needs_oauth") or (not day.get("ok") and day.get("error_code")):
            return day
        events = day.get("events") or []
        title = "Tomorrow" if offset else "Today"
        reply = format_busy(events, title=title)
        return {"ok": True, "handled": True, "reply": reply, "events": events}

    def followup(self, user: User, text: str) -> dict[str, Any]:
        low = (text or "").lower()
        ctx = self.memory.get_context(user)
        events = ctx.get("events") or []
        # Rehydrate datetimes
        hydrated = []
        for e in events:
            item = dict(e)
            for k in ("start_at", "end_at"):
                if isinstance(item.get(k), str):
                    try:
                        item[k] = datetime.fromisoformat(item[k])
                    except Exception:  # noqa: BLE001
                        pass
            hydrated.append(item)
        if "tomorrow" in low:
            return self.day_view(user, offset_days=1, question=text)
        if "conflict" in low or "overlap" in low:
            return self.conflicts(user, text)
        if "free" in low:
            return self.free_time(user, text)
        if "longest" in low:
            if not hydrated:
                return self.day_view(user, offset_days=int(ctx.get("offset_days") or 0), question=text)
            best = longest_event(hydrated)
            if not best:
                return {
                    "ok": True,
                    "handled": True,
                    "reply": "I couldn't compare durations from the current calendar window.",
                }
            mins = int((best["end_at"] - best["start_at"]).total_seconds() // 60)
            return {
                "ok": True,
                "handled": True,
                "reply": (
                    f"The longest is *{best.get('title')}* "
                    f"({mins} minutes, {_fmt_simple(best['start_at'])}–{_fmt_simple(best['end_at'])})."
                ),
            }
        if "important" in low:
            ranked = sorted(hydrated, key=lambda e: float(e.get("importance") or 0), reverse=True)
            return {
                "ok": True,
                "handled": True,
                "reply": format_day(ranked[:5] or hydrated, title="Most important"),
            }
        if "summarize" in low or "busy" in low:
            return {
                "ok": True,
                "handled": True,
                "reply": format_busy(hydrated, title=str(ctx.get("label") or "Schedule")),
            }
        # Default: restate context
        if hydrated:
            return {
                "ok": True,
                "handled": True,
                "reply": format_day(hydrated, title=str(ctx.get("label") or "Your schedule")),
            }
        return self.day_view(user, offset_days=0, question=text)

    def deadlines(self, user: User) -> dict[str, Any]:
        gate = self.ensure_ready(user, question="What deadlines are coming up?")
        if gate:
            return gate
        synced = self.sync_range(user)
        if not synced.get("ok"):
            return self._handle_sync_failure(
                user, synced, question="What deadlines are coming up?"
            )
        now = self._now(user)
        rows = list(
            CalendarEvent.objects.filter(
                user=user, status="confirmed", start_at__gte=now, start_at__lt=now + timedelta(days=10)
            ).order_by("start_at")
        )
        hits = [
            r
            for r in rows
            if "deadline" in (r.categories or []) or "earnings" in (r.categories or [])
        ]
        if not hits:
            return {
                "ok": True,
                "handled": True,
                "reply": "📅 No hard deadlines in the next week on your calendar.",
            }
        lines = ["*Coming deadlines*", ""]
        for r in hits[:6]:
            local_start = r.start_at.astimezone(self._user_tz(user))
            lines.append(f"• {local_start.strftime('%a %d %b %H:%M')} — *{r.title}*")
        self.memory.remember_event(user, self._row_dict(hits[0]))
        return {"ok": True, "handled": True, "reply": "\n".join(lines)}

    def search(self, user: User, text: str) -> dict[str, Any]:
        gate = self.ensure_ready(user, question=text)
        if gate:
            return gate
        synced = self.sync_range(user)
        if not synced.get("ok"):
            return self._handle_sync_failure(user, synced, question=text)
        q = re.sub(
            r"^(find|show|search)\s+(my\s+)?(meeting|event|interview|earnings)?\s*",
            "",
            text,
            flags=re.I,
        ).strip(" .!?")
        q = re.sub(r"^(what )?meetings? (do i have )?with\s+", "", q, flags=re.I).strip()
        rows = list(
            CalendarEvent.objects.filter(user=user, status="confirmed").order_by("start_at")[:60]
        )
        if q:
            rows = [
                r
                for r in rows
                if q.lower() in (r.title or "").lower()
                or q.lower() in (r.description or "").lower()
                or any(q.lower() in str(c).lower() for c in (r.categories or []))
                or any(q.lower() in str(c).lower() for c in (r.companies or []))
            ]
        if not rows:
            return {
                "ok": True,
                "handled": True,
                "reply": f"📅 No events matching *{q or 'that'}* on your calendar.",
            }
        payload = [self._row_dict(r) for r in rows[:8]]
        for p in payload:
            p["start_at"] = p["start_at"].astimezone(self._user_tz(user))
            p["end_at"] = p["end_at"].astimezone(self._user_tz(user))
        self.memory.remember_context(user, events=payload, label=f"Matches: {q or 'events'}")
        return {
            "ok": True,
            "handled": True,
            "reply": format_day(payload, title=f"Matches: {q or 'events'}"),
            "events": payload,
        }

    def propose_create(self, user: User, text: str) -> dict[str, Any]:
        gate = self.ensure_ready(user, question=text)
        if gate:
            return gate
        self.sync_range(user)
        when = parse_when(text, now=self._now(user))
        title = self._infer_title(text)
        overlap = CalendarEvent.objects.filter(
            user=user,
            status="confirmed",
            start_at__lt=when["end_at"],
            end_at__gt=when["start_at"],
        ).first()
        action = {
            "type": "create",
            "title": title,
            "start_at": when["start_at"].isoformat(),
            "end_at": when["end_at"].isoformat(),
            "categories": self._infer_categories(text, title),
        }
        self.memory.set_pending(user, action)
        reply = (
            f"*Draft event*\n*{title}*\n"
            f"{when['start_at'].strftime('%a %d %b %H:%M')}–"
            f"{when['end_at'].strftime('%H:%M')}\n\n"
        )
        if overlap:
            reply += (
                f"⚠️ Overlaps *{overlap.title}*. I can still book it, or pick another slot.\n\n"
            )
        reply += "Reply *YES* to confirm. I won’t change your calendar without that."
        return {"ok": True, "handled": True, "reply": reply, "pending": action}

    def propose_update(self, user: User, text: str) -> dict[str, Any]:
        gate = self.ensure_ready(user, question=text)
        if gate:
            return gate
        ev = self._resolve_active(user, text)
        if ev is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Which meeting should I move? Open today’s schedule first, or name it.",
            }
        when = parse_when(text)
        # If only "what about friday" — move to Friday same clock time
        low = text.lower()
        duration = ev.end_at - ev.start_at
        if "friday" in low and not re.search(r"\d{1,2}\s*(am|pm|:)", low):
            day = datetime.now(tz=timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            delta = (4 - day.weekday()) % 7
            day = day + timedelta(days=delta)
            start = day.replace(
                hour=ev.start_at.hour,
                minute=ev.start_at.minute,
                tzinfo=ev.start_at.tzinfo or timezone.utc,
            )
            end = start + duration
        else:
            start, end = when["start_at"], when["end_at"]
            # preserve duration if user didn't specify hours length
            if "hour" not in low and "min" not in low:
                end = start + duration

        action = {
            "type": "update",
            "event_pk": str(ev.id),
            "event_id": ev.event_id,
            "title": ev.title,
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        }
        self.memory.set_pending(user, action)
        self.memory.remember_event(user, self._row_dict(ev))
        return {
            "ok": True,
            "handled": True,
            "reply": (
                f"*Reschedule*\nMove *{ev.title}* to "
                f"{start.strftime('%a %d %b %H:%M')}–{end.strftime('%H:%M')}?\n\n"
                "Reply *YES* to confirm."
            ),
            "pending": action,
        }

    def propose_cancel(self, user: User, text: str) -> dict[str, Any]:
        gate = self.ensure_ready(user, question=text)
        if gate:
            return gate
        ev = self._resolve_active(user, text)
        if ev is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Nothing active to cancel. Show today’s meetings, then say “cancel that.”",
            }
        action = {
            "type": "cancel",
            "event_pk": str(ev.id),
            "event_id": ev.event_id,
            "title": ev.title,
        }
        self.memory.set_pending(user, action)
        self.memory.remember_event(user, self._row_dict(ev))
        return {
            "ok": True,
            "handled": True,
            "reply": f"Cancel *{ev.title}*? Reply *YES* to confirm — I won’t remove it otherwise.",
            "pending": action,
        }

    def confirm_pending(self, user: User) -> dict[str, Any]:
        action = self.memory.get_pending(user)
        if not action:
            return {
                "ok": False,
                "handled": True,
                "reply": "Nothing pending on the calendar.",
            }
        kind = action.get("type")
        try:
            if kind == "create":
                result = self._apply_create(user, action)
            elif kind == "update":
                result = self._apply_update(user, action)
            elif kind == "cancel":
                result = self._apply_cancel(user, action)
            else:
                result = {"ok": False, "reply": "I couldn’t apply that change."}
        finally:
            self.memory.clear_pending(user)
        result["handled"] = True
        return result

    def _apply_create(self, user: User, action: dict) -> dict[str, Any]:
        start = datetime.fromisoformat(action["start_at"])
        end = datetime.fromisoformat(action["end_at"])
        title = action.get("title") or "Meeting"
        event_id = f"demo_evt_{uuid.uuid4().hex[:10]}"
        try:
            client = self._client_for(user)
            remote = client.create_event(
                event_id=event_id,
                title=title,
                start_at=start,
                end_at=end,
                categories=action.get("categories") or [],
            )
            event_id = remote.id
            start, end = remote.start_at, remote.end_at
            title = remote.title
        except Exception:  # noqa: BLE001
            pass
        row = CalendarEvent.objects.create(
            user=user,
            event_id=event_id,
            title=title[:512],
            start_at=start,
            end_at=end,
            status="confirmed",
            categories=action.get("categories") or [],
            importance=10.0,
        )
        self.memory.remember_event(user, self._row_dict(row))
        return {
            "ok": True,
            "reply": (
                f"Booked *{title}* — {start.strftime('%a %H:%M')}–{end.strftime('%H:%M')}. "
                "Want a short prep block before it?"
            ),
        }

    def _apply_update(self, user: User, action: dict) -> dict[str, Any]:
        row = CalendarEvent.objects.filter(user=user, id=action.get("event_pk")).first()
        if not row:
            row = CalendarEvent.objects.filter(
                user=user, event_id=action.get("event_id")
            ).first()
        if not row:
            return {"ok": False, "reply": "That event is gone — it may have been deleted."}
        start = datetime.fromisoformat(action["start_at"])
        end = datetime.fromisoformat(action["end_at"])
        try:
            client = self._client_for(user)
            client.update_event(row.event_id, start_at=start, end_at=end, title=row.title)
        except Exception:  # noqa: BLE001
            pass
        row.start_at = start
        row.end_at = end
        row.save(update_fields=["start_at", "end_at", "updated_at"])
        self.memory.remember_event(user, self._row_dict(row))
        return {
            "ok": True,
            "reply": (
                f"Moved *{row.title}* to "
                f"{start.strftime('%a %d %b %H:%M')}–{end.strftime('%H:%M')}."
            ),
        }

    def _apply_cancel(self, user: User, action: dict) -> dict[str, Any]:
        row = CalendarEvent.objects.filter(user=user, id=action.get("event_pk")).first()
        if not row:
            row = CalendarEvent.objects.filter(
                user=user, event_id=action.get("event_id")
            ).first()
        if not row:
            return {"ok": False, "reply": "That event is already gone."}
        try:
            client = self._client_for(user)
            client.delete_event(row.event_id)
        except Exception:  # noqa: BLE001
            pass
        title = row.title
        row.status = "cancelled"
        row.save(update_fields=["status", "updated_at"])
        return {"ok": True, "reply": f"Cancelled *{title}*. Calendar’s cleaner now."}

    def _resolve_active(self, user: User, text: str = "") -> CalendarEvent | None:
        pk = self.memory.active_event_id(user)
        if pk:
            row = CalendarEvent.objects.filter(user=user, id=pk, status="confirmed").first()
            if row:
                return row
        low = (text or "").lower()
        if "tomorrow" in low:
            now = datetime.now(tz=timezone.utc)
            day0 = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            return (
                CalendarEvent.objects.filter(
                    user=user,
                    status="confirmed",
                    start_at__gte=day0,
                    start_at__lt=day0 + timedelta(days=1),
                )
                .order_by("start_at")
                .first()
            )
        if "interview" in low:
            row = (
                CalendarEvent.objects.filter(user=user, status="confirmed", title__icontains="interview")
                .order_by("start_at")
                .first()
            )
            if row:
                return row
        return (
            CalendarEvent.objects.filter(user=user, status="confirmed")
            .order_by("-importance", "start_at")
            .first()
        )

    def _infer_title(self, text: str) -> str:
        low = text.lower()
        if "portfolio" in low:
            return "Portfolio review"
        if "nvidia" in low or "nvda" in low:
            return "NVIDIA earnings review"
        if "apple" in low:
            return "Apple earnings review"
        if "fomc" in low:
            return "Post-FOMC review block"
        if "research" in low:
            return "Research block"
        if "interview" in low:
            return "Interview"
        m = re.search(
            r"(?:schedule|block|add|set up|find time for)\s+(?:a\s+)?(.+?)(?:\s+(?:tomorrow|today|friday|monday|at|on)\b|$)",
            text,
            re.I,
        )
        if m:
            title = m.group(1).strip(" .!")
            if 3 < len(title) < 80:
                return title[:1].upper() + title[1:]
        return "Meeting"

    def _infer_categories(self, text: str, title: str) -> list[str]:
        blob = f"{text} {title}".lower()
        cats = ["meeting"]
        if any(x in blob for x in ("earnings", "nvidia", "apple", "portfolio", "fomc", "research")):
            cats.append("finance")
        if "earnings" in blob:
            cats.append("earnings")
        if "interview" in blob:
            cats.append("interview")
        if "research" in blob or "block" in blob:
            cats.append("research")
        return cats

    def _row_dict(self, row: CalendarEvent) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "event_id": row.event_id,
            "title": row.title,
            "start_at": row.start_at,
            "end_at": row.end_at,
            "categories": row.categories or [],
            "companies": row.companies or [],
            "tickers": row.tickers or [],
            "importance": row.importance,
            "status": row.status,
        }


def _fmt_simple(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%H:%M")
