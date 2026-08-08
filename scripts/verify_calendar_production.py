"""Google Calendar production verification — OAuth gate, NL Q&A, isolation, Sheets routing."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from accounts.models import GoogleIntegration, GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService
from conversation.models import Message
from core.crypto import encrypt_text
from gcalendar.models import CalendarConnectionMode, CalendarEvent, CalendarSyncState
from gcalendar.services.calendar_client import RemoteEvent
from gcalendar.services.calendar_intent import detect_calendar_intent
from gcalendar.services.calendar_intel import find_conflicts, find_free_slots, format_conflicts
from gcalendar.services.calendar_memory import CalendarMemory
from gcalendar.services.calendar_service import CalendarService
from sheets.services.demo_data import DEMO_MSFT_FINANCIALS
from telegram_bot.services.conversation_processor import ConversationProcessor


def _pass(label: str) -> None:
    print(f"PASS {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"FAIL {label}: {detail}")
    raise SystemExit(1)


def _ask(p: ConversationProcessor, tid: int, text: str) -> tuple[str, dict]:
    r = (
        p.handle_text(
            telegram_id=tid,
            text=text,
            username="calprod",
            first_name="CalProd",
            telegram_message_id=int(time.time() * 1000) % 10_000_000,
        )
        or ""
    )
    m = (
        Message.objects.filter(conversation__user__telegram_id=tid, role="assistant")
        .order_by("-created_at")
        .first()
    )
    return r, (m.metadata or {}) if m else {}


def _attach_live(user: User, token: str) -> None:
    GoogleIntegration.objects.update_or_create(
        user=user,
        service=GoogleService.CALENDAR,
        defaults={
            "access_token_encrypted": encrypt_text(token),
            "refresh_token_encrypted": encrypt_text(f"refresh:{token}"),
            "token_expires_at": datetime.now(tz=timezone.utc) + timedelta(hours=2),
            "is_active": True,
            "scopes": [
                "https://www.googleapis.com/auth/calendar.readonly",
                "https://www.googleapis.com/auth/calendar.events",
            ],
        },
    )
    state, _ = CalendarSyncState.objects.get_or_create(user=user)
    state.mode = CalendarConnectionMode.OAUTH
    state.timezone = "UTC"
    state.save(update_fields=["mode", "timezone", "updated_at"])


# Avoid live tokeninfo calls during FakeCalClient tests
_ORIG_RESOLVE = GoogleOAuthService._resolve_actual_scopes


def _fake_resolve(self, tokens):  # noqa: ANN001
    access = (tokens.get("access_token") or "").strip()
    if access.startswith("cal-token") or access.startswith("tok-"):
        claimed = [
            s for s in str(tokens.get("scope") or "").replace(",", " ").split() if s
        ]
        return claimed
    return _ORIG_RESOLVE(self, tokens)


GoogleOAuthService._resolve_actual_scopes = _fake_resolve  # type: ignore[method-assign]


def _events_for(token: str, day0: datetime) -> list[RemoteEvent]:
    """Distinct calendars per token for isolation tests."""
    if token == "cal-token-A":
        return [
            RemoteEvent(
                id="a1",
                title="Alpha Standup",
                description="",
                location="",
                start_at=day0.replace(hour=9, minute=0),
                end_at=day0.replace(hour=9, minute=30),
            ),
            RemoteEvent(
                id="a2",
                title="Microsoft sync",
                description="Weekly with Microsoft",
                location="",
                start_at=day0.replace(hour=10, minute=0),
                end_at=day0.replace(hour=11, minute=0),
            ),
            RemoteEvent(
                id="a3",
                title="Design review",
                description="",
                location="",
                start_at=day0.replace(hour=10, minute=30),
                end_at=day0.replace(hour=11, minute=30),
            ),
        ]
    return [
        RemoteEvent(
            id="b1",
            title="Beta Only Private",
            description="",
            location="",
            start_at=day0.replace(hour=13, minute=0),
            end_at=day0.replace(hour=14, minute=0),
        ),
    ]


class FakeCalClient:
    def __init__(self, access_token: str, demo: bool = False):
        self.token = access_token
        self.timezone_name = "UTC"

    def get_timezone(self) -> str:
        return "UTC"

    def list_events(self, *, time_min, time_max, query: str = ""):
        # Anchor demo events to "today" inside the requested window
        span_start = time_min
        if time_max - time_min > timedelta(days=2):
            # sync_range uses [now-1d, now+14d] — place on calendar today
            span_start = time_min + timedelta(days=1)
        day0 = span_start.replace(hour=0, minute=0, second=0, microsecond=0)
        events = _events_for(self.token, day0)
        # Also add a tomorrow event for token A
        if self.token == "cal-token-A":
            events.append(
                RemoteEvent(
                    id="a_tom",
                    title="Tomorrow planning",
                    description="",
                    location="",
                    start_at=day0.replace(hour=9, minute=0) + timedelta(days=1),
                    end_at=day0.replace(hour=10, minute=0) + timedelta(days=1),
                )
            )
        if query:
            q = query.lower()
            events = [e for e in events if q in e.title.lower() or q in e.description.lower()]
        return [e for e in events if e.end_at >= time_min and e.start_at <= time_max]

    def create_event(self, **kwargs):
        raise NotImplementedError

    def update_event(self, event_id, **kwargs):
        return None

    def delete_event(self, event_id):
        return False


def test_intents() -> None:
    assert detect_calendar_intent("What's on my calendar today?").kind == "today"
    assert detect_calendar_intent("What do I have tomorrow?").kind == "tomorrow"
    assert detect_calendar_intent("What's my schedule this week?").kind == "week"
    assert detect_calendar_intent("When is my next meeting?").kind == "next"
    assert detect_calendar_intent("Find me a free 1-hour slot tomorrow.").kind == "free"
    assert detect_calendar_intent("Do I have overlapping meetings?").kind == "conflicts"
    assert detect_calendar_intent("How busy am I today?").kind == "busy"
    assert detect_calendar_intent("What meetings do I have with Microsoft?").kind == "search"
    assert detect_calendar_intent("Apple stock price").kind == "none"
    assert (
        detect_calendar_intent("Which one is the longest?", has_calendar_context=True).kind
        == "followup"
    )
    assert (
        detect_calendar_intent("do i have any task", has_calendar_context=True).kind
        == "clarify_task"
    )
    assert detect_calendar_intent("do i have any task", has_calendar_context=False).kind == "none"
    _pass("calendar_intents_expanded")


def test_permission_failure_not_empty() -> None:
    """API permission failures must never look like an empty calendar."""
    tid = 9944000102
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="PermFail", onboarding_completed=True)
    _attach_live(user, "cal-token-bad")
    CalendarEvent.objects.filter(user=user).delete()

    class DenyClient(FakeCalClient):
        def list_events(self, *, time_min, time_max, query: str = ""):
            raise PermissionError("permission_denied")

    with patch(
        "gcalendar.services.calendar_service.build_calendar_client", side_effect=DenyClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        nxt = CalendarService().next_meeting(user)
        reply = (nxt.get("reply") or "").lower()
        assert nxt.get("ok") is False or nxt.get("error_code") == "permission_denied" or nxt.get(
            "needs_oauth"
        )
        assert "no upcoming" not in reply
        assert "permission" in reply or "reconnect" in reply or "connect google" in reply
        _pass("next_meeting_permission_not_empty")

        day = CalendarService().day_view(user, question="What's on my calendar today?")
        dreply = (day.get("reply") or "").lower()
        assert "no calendar events" not in dreply
        assert day.get("error_code") == "permission_denied" or day.get("needs_oauth")
        _pass("day_view_permission_not_empty")


def test_resume_requires_verify() -> None:
    tid = 9944000103
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="ResumeCal", onboarding_completed=True)
    _attach_live(user, "cal-token-A")
    CalendarMemory().remember_pending_question(user, "What's on my calendar today?")

    class DenyClient(FakeCalClient):
        def list_events(self, *, time_min, time_max, query: str = ""):
            raise PermissionError("permission_denied")

    with patch(
        "gcalendar.services.calendar_service.build_calendar_client", side_effect=DenyClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        resumed = CalendarService().resume_after_oauth(user)
        reply = resumed.get("reply") or ""
        assert resumed.get("ok") is False
        assert "Google Calendar connected" not in reply
        assert "permission" in reply.lower() or "reconnect" in reply.lower()
        # Pending question retained for reconnect resume
        assert CalendarMemory().pop_pending_question(user) == "What's on my calendar today?"
        _pass("resume_after_oauth_verify_before_claim")


def test_oauth_rejects_missing_calendar_scopes() -> None:
    tid = 9944000104
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="ScopeFail", onboarding_completed=True)
    oauth = GoogleOAuthService()
    from django.core.cache import cache

    state = "test-cal-scope-state"
    cache.set(
        f"oauth:state:{state}",
        {
            "user_id": str(user.id),
            "service": GoogleService.CALENDAR,
            "telegram_id": user.telegram_id,
            "code_verifier": "",
            "scopes": [
                "https://www.googleapis.com/auth/calendar.readonly",
                "openid",
            ],
            "pending_spreadsheet_id": "",
            "pending_action": "",
        },
        600,
    )
    with patch.object(
        GoogleOAuthService,
        "_exchange_code",
        return_value={
            "access_token": "tok-sheets-only",
            "refresh_token": "ref",
            "expires_in": 3600,
            "scope": (
                "https://www.googleapis.com/auth/spreadsheets.readonly "
                "https://www.googleapis.com/auth/drive.readonly openid"
            ),
        },
    ), patch.object(
        GoogleOAuthService,
        "_resolve_actual_scopes",
        return_value=[
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
            "openid",
        ],
    ):
        result = oauth.handle_callback(code="fake", state=state)
    assert result.get("ok") is False
    assert result.get("error_code") == "insufficient_scopes"
    assert not GoogleIntegration.objects.filter(
        user=user, service=GoogleService.CALENDAR, is_active=True
    ).exists()
    _pass("oauth_rejects_missing_calendar_scopes")

def test_fresh_user_oauth_prompt() -> None:
    tid = 9944000100
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="FreshCal", onboarding_completed=True)
    p = ConversationProcessor()
    r, meta = _ask(p, tid, "What's on my calendar today?")
    low = r.lower()
    assert meta.get("pipeline") == "calendar"
    if GoogleOAuthService().is_configured():
        assert meta.get("needs_oauth") is True or "connect google" in low
        assert "accounts.google.com" in r or "connect google" in low
        assert "demo" not in low or "portfolio review" not in low
    _pass("fresh_user_oauth_prompt")
    print("  ", r[:160].encode("ascii", "replace").decode("ascii").replace("\n", " | "))


def test_live_schedule_features() -> None:
    tid = 9944000101
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="LiveCal", onboarding_completed=True)
    _attach_live(user, "cal-token-A")
    CalendarEvent.objects.filter(user=user).delete()

    with patch("gcalendar.services.calendar_service.build_calendar_client", side_effect=FakeCalClient):
        svc = CalendarService()
        today = svc.day_view(user, offset_days=0, question="What's on my calendar today?")
        assert today.get("ok"), today
        reply = today.get("reply") or ""
        assert "Alpha Standup" in reply
        assert "Microsoft sync" in reply
        assert "ai watchlist" not in reply.lower()
        _pass("today_schedule")

        conf = svc.conflicts(user, "Do I have overlapping meetings?")
        assert conf.get("ok")
        cr = conf.get("reply") or ""
        assert "overlap" in cr.lower() or "conflict" in cr.lower()
        assert "30" in cr  # 30 minute overlap 10:30-11:00
        _pass("conflict_detection")
        print("  ", cr[:180].encode("ascii", "replace").decode("ascii").replace("\n", " | "))

        free = svc.free_time(user, "Find me a free 1-hour slot today")
        assert free.get("ok")
        assert free.get("slots") is not None
        _pass("free_time_detection")

        nxt = svc.next_meeting(user)
        assert nxt.get("ok")
        _pass("next_meeting")

        search = svc.search(user, "What meetings do I have with Microsoft?")
        assert search.get("ok")
        assert "Microsoft" in (search.get("reply") or "")
        _pass("search_company_meeting")

        follow = svc.followup(user, "Which one is the longest?")
        assert follow.get("ok")
        assert "longest" in (follow.get("reply") or "").lower() or "Design review" in (
            follow.get("reply") or ""
        ) or "Microsoft" in (follow.get("reply") or "")
        _pass("followup_context")

        # Empty day → not an error
        empty_day = datetime.now(tz=timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=3)

        class EmptyClient(FakeCalClient):
            def list_events(self, *, time_min, time_max, query: str = ""):
                return []

        with patch(
            "gcalendar.services.calendar_service.build_calendar_client", side_effect=EmptyClient
        ):
            # Clear local events so day_view reflects empty remote sync
            CalendarEvent.objects.filter(user=user).delete()
            empty = CalendarService().day_view(user, offset_days=3, question="schedule")
            assert empty.get("ok")
            assert "no events scheduled" in (empty.get("reply") or "").lower()
            _pass("no_events_day")


def test_multi_user_isolation() -> None:
    tid_a, tid_b = 9944000110, 9944000111
    User.objects.filter(telegram_id__in=[tid_a, tid_b]).delete()
    user_a = User.objects.create(telegram_id=tid_a, first_name="CalA", onboarding_completed=True)
    user_b = User.objects.create(telegram_id=tid_b, first_name="CalB", onboarding_completed=True)
    _attach_live(user_a, "cal-token-A")
    _attach_live(user_b, "cal-token-B")
    CalendarEvent.objects.filter(user__in=[user_a, user_b]).delete()

    with patch("gcalendar.services.calendar_service.build_calendar_client", side_effect=FakeCalClient):
        a = CalendarService().day_view(user_a)
        b = CalendarService().day_view(user_b)
        assert "Alpha Standup" in (a.get("reply") or "")
        assert "Beta Only Private" in (b.get("reply") or "")
        assert "Beta Only Private" not in (a.get("reply") or "")
        assert "Alpha Standup" not in (b.get("reply") or "")
        assert CalendarMemory().has_recent_context(user_a)
        assert CalendarMemory().has_recent_context(user_b)
    _pass("multi_user_isolation")


def test_routing_vs_sheets() -> None:
    tid = 9944000120
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="RouteMix", onboarding_completed=True)
    # Give user an active sheet first
    from sheets.services.sheet_service import SheetService

    SheetService().connect_demo(user)
    SheetService().open_by_spreadsheet_id(user, DEMO_MSFT_FINANCIALS["id"])
    p = ConversationProcessor()
    r, meta = _ask(p, tid, "What's on my calendar today?")
    assert meta.get("pipeline") == "calendar", meta
    assert "watchlist" not in r.lower()
    # After calendar context, follow-up should stay calendar when connected via demo if oauth off
    # Ensure demo calendar for follow-up path when oauth would otherwise prompt
    if not CalendarService().is_live_connected(user):
        CalendarService().connect_demo(user)
        CalendarService().sync_range(user)
    r2, meta2 = _ask(p, tid, "Any conflicts?")
    assert meta2.get("pipeline") == "calendar"
    # Ambiguous task must NOT steal into Sheets after calendar context
    r_task, meta_task = _ask(p, tid, "do i have any task")
    assert meta_task.get("pipeline") == "calendar", meta_task
    assert "spreadsheet" not in r_task.lower()
    assert "calendar" in r_task.lower() or "task" in r_task.lower()
    # Sheet question should still work
    r3, meta3 = _ask(p, tid, "Analyze this sheet.")
    assert meta3.get("pipeline") == "sheets"
    # After sheet, calendar still routes correctly
    r4, meta4 = _ask(p, tid, "What's on my calendar today?")
    assert meta4.get("pipeline") == "calendar"
    # Revenue after sheet context stays sheets; not calendar
    r5, meta5 = _ask(p, tid, "What is my revenue?")
    assert meta5.get("pipeline") == "sheets"
    _pass("routing_isolation_calendar_vs_sheets")


def test_conflict_format_unit() -> None:
    day0 = datetime.now(tz=timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    events = [
        {
            "title": "A",
            "start_at": day0.replace(hour=10),
            "end_at": day0.replace(hour=11),
        },
        {
            "title": "B",
            "start_at": day0.replace(hour=10, minute=30),
            "end_at": day0.replace(hour=11, minute=30),
        },
    ]
    conf = find_conflicts(events)
    assert conf and conf[0]["overlap_minutes"] == 30
    text = format_conflicts(conf)
    assert "Overlap: 30 minutes" in text
    assert "Meeting A" in text and "Meeting B" in text
    slots = find_free_slots(events, day_start=day0, day_end=day0 + timedelta(days=1), duration_minutes=60)
    assert slots
    _pass("conflict_and_free_slot_units")


def test_api_disabled_not_permission() -> None:
    """GCP Calendar API disabled must not be reported as missing OAuth scopes."""
    tid = 9944000105
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="ApiOff", onboarding_completed=True)
    _attach_live(user, "cal-token-A")

    class ApiOffClient(FakeCalClient):
        def list_events(self, *, time_min, time_max, query: str = ""):
            raise RuntimeError("api_disabled")

    with patch(
        "gcalendar.services.calendar_service.build_calendar_client", side_effect=ApiOffClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        verified = CalendarService().verify_calendar_access(user)
        assert verified.get("ok") is False
        assert verified.get("error_code") == "api_disabled"
        assert "Calendar API" in (verified.get("error") or "")
        assert GoogleIntegration.objects.filter(
            user=user, service=GoogleService.CALENDAR, is_active=True
        ).exists()
        day = CalendarService().day_view(user, question="What's on my calendar today?")
        assert "Connect Google" not in (day.get("reply") or "")
        assert day.get("error_code") == "api_disabled"
    _pass("api_disabled_distinct_from_oauth")


def test_oauth_sync_purges_stale_fixture_rows() -> None:
    """Real OAuth sync must delete leftover fixture/demo event rows."""
    tid = 9944000106
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="Purge", onboarding_completed=True)
    _attach_live(user, "cal-token-A")
    now = datetime.now(tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    CalendarEvent.objects.create(
        user=user,
        event_id="a1",
        title="Alpha Standup",
        start_at=now.replace(hour=9),
        end_at=now.replace(hour=9, minute=30),
        status="confirmed",
    )
    CalendarEvent.objects.create(
        user=user,
        event_id="a2",
        title="Microsoft sync",
        start_at=now.replace(hour=10),
        end_at=now.replace(hour=11),
        status="confirmed",
    )

    class RealOnlyClient(FakeCalClient):
        def list_events(self, *, time_min, time_max, query: str = ""):
            return [
                RemoteEvent(
                    id="real_google_evt_1",
                    title="Real Board Meeting",
                    description="",
                    location="",
                    start_at=now.replace(hour=15),
                    end_at=now.replace(hour=16),
                )
            ]

    with patch(
        "gcalendar.services.calendar_service.build_calendar_client", side_effect=RealOnlyClient
    ), patch.object(GoogleOAuthService, "token_has_required_scopes", return_value=True):
        synced = CalendarService().sync_range(user)
        assert synced.get("ok") and synced.get("source") == "google_calendar_api"
        assert synced.get("stale_deleted", 0) >= 2
        titles = set(CalendarEvent.objects.filter(user=user).values_list("title", flat=True))
        assert titles == {"Real Board Meeting"}
        assert "Alpha Standup" not in titles
        day = CalendarService().day_view(user, offset_days=0)
        assert "Real Board Meeting" in (day.get("reply") or "")
        assert "Alpha Standup" not in (day.get("reply") or "")
    _pass("oauth_sync_purges_stale_fixture_rows")


def main() -> None:
    print("=== Calendar production verification ===")
    test_intents()
    test_conflict_format_unit()
    test_oauth_rejects_missing_calendar_scopes()
    test_permission_failure_not_empty()
    test_resume_requires_verify()
    test_api_disabled_not_permission()
    test_oauth_sync_purges_stale_fixture_rows()
    test_fresh_user_oauth_prompt()
    test_live_schedule_features()
    test_multi_user_isolation()
    test_routing_vs_sheets()
    print("\nCALENDAR_PRODUCTION_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
