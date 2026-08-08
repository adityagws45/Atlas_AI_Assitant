"""Calendar API client — live Google Calendar + local demo backend."""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from django.conf import settings
from django.core.cache import cache

from gcalendar.services.demo_data import DEMO_EVENTS

logger = logging.getLogger("atlas.calendar.client")

EVENTS_TTL = int(getattr(settings, "CACHE_TTL_CALENDAR_EVENTS", 120) or 120)


@dataclass
class RemoteEvent:
    id: str
    title: str
    description: str
    location: str
    start_at: datetime
    end_at: datetime
    all_day: bool = False
    status: str = "confirmed"
    is_recurring: bool = False
    calendar_id: str = "primary"
    categories: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)


class CalendarClientProtocol(Protocol):
    def list_events(
        self, *, time_min: datetime, time_max: datetime, query: str = ""
    ) -> list[RemoteEvent]: ...

    def create_event(self, **kwargs: Any) -> RemoteEvent: ...

    def update_event(self, event_id: str, **kwargs: Any) -> RemoteEvent | None: ...

    def delete_event(self, event_id: str) -> bool: ...


def _parse_dt(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if not value:
        return datetime.now(tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:  # noqa: BLE001
        return datetime.now(tz=timezone.utc)


class MockCalendarClient:
    """Local demo calendar — mutable in-memory store per process."""

    def __init__(self) -> None:
        self._events: dict[str, dict] = {e["id"]: dict(e) for e in DEMO_EVENTS}

    def list_events(
        self, *, time_min: datetime, time_max: datetime, query: str = ""
    ) -> list[RemoteEvent]:
        q = (query or "").strip().lower()
        out: list[RemoteEvent] = []
        for raw in self._events.values():
            if raw.get("status") == "cancelled":
                continue
            start = _parse_dt(raw["start"])
            end = _parse_dt(raw["end"])
            if end < time_min or start > time_max:
                continue
            blob = " ".join(
                [
                    raw.get("title", ""),
                    raw.get("description", ""),
                    " ".join(raw.get("companies") or []),
                    " ".join(raw.get("tickers") or []),
                ]
            ).lower()
            if q and q not in blob:
                continue
            out.append(self._to_remote(raw))
        out.sort(key=lambda e: e.start_at)
        return out

    def create_event(self, **kwargs: Any) -> RemoteEvent:
        eid = kwargs.get("event_id") or f"demo_evt_{uuid.uuid4().hex[:10]}"
        raw = {
            "id": eid,
            "title": kwargs.get("title") or "Untitled",
            "description": kwargs.get("description") or "",
            "location": kwargs.get("location") or "",
            "start": kwargs["start_at"].isoformat()
            if isinstance(kwargs.get("start_at"), datetime)
            else kwargs.get("start"),
            "end": kwargs["end_at"].isoformat()
            if isinstance(kwargs.get("end_at"), datetime)
            else kwargs.get("end"),
            "recurring": bool(kwargs.get("is_recurring")),
            "categories": list(kwargs.get("categories") or []),
            "companies": list(kwargs.get("companies") or []),
            "tickers": list(kwargs.get("tickers") or []),
            "status": "confirmed",
        }
        self._events[eid] = raw
        return self._to_remote(raw)

    def update_event(self, event_id: str, **kwargs: Any) -> RemoteEvent | None:
        raw = self._events.get(event_id)
        if not raw:
            return None
        if "title" in kwargs and kwargs["title"]:
            raw["title"] = kwargs["title"]
        if "description" in kwargs:
            raw["description"] = kwargs["description"] or ""
        if "start_at" in kwargs and kwargs["start_at"]:
            raw["start"] = kwargs["start_at"].isoformat()
        if "end_at" in kwargs and kwargs["end_at"]:
            raw["end"] = kwargs["end_at"].isoformat()
        if kwargs.get("status"):
            raw["status"] = kwargs["status"]
        return self._to_remote(raw)

    def delete_event(self, event_id: str) -> bool:
        raw = self._events.get(event_id)
        if not raw:
            return False
        raw["status"] = "cancelled"
        return True

    def _to_remote(self, raw: dict) -> RemoteEvent:
        return RemoteEvent(
            id=raw["id"],
            title=raw.get("title") or "Untitled",
            description=raw.get("description") or "",
            location=raw.get("location") or "",
            start_at=_parse_dt(raw.get("start")),
            end_at=_parse_dt(raw.get("end")),
            status=raw.get("status") or "confirmed",
            is_recurring=bool(raw.get("recurring")),
            categories=list(raw.get("categories") or []),
            companies=list(raw.get("companies") or []),
            tickers=list(raw.get("tickers") or []),
        )


class GoogleCalendarClient:
    """Live Google Calendar API."""

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self._svc = None
        self.timezone_name: str = "UTC"

    def _service(self):
        if self._svc is not None:
            return self._svc
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(token=self.access_token)
        self._svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        return self._svc

    def get_timezone(self) -> str:
        try:
            settings_obj = self._service().settings().get(setting="timezone").execute()
            tz = (settings_obj or {}).get("value") or "UTC"
            self.timezone_name = tz
            return tz
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=calendar_tz_failed err=%s", type(exc).__name__)
            return self.timezone_name or "UTC"

    def list_events(
        self, *, time_min: datetime, time_max: datetime, query: str = ""
    ) -> list[RemoteEvent]:
        cache_key = (
            "cal:list:"
            + hashlib.sha1(
                (
                    self.access_token[:12]
                    + "|"
                    + time_min.isoformat()
                    + "|"
                    + time_max.isoformat()
                    + "|"
                    + query
                ).encode()
            ).hexdigest()
        )
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            return cached
        try:
            svc = self._service()
            resp = (
                svc.events()
                .list(
                    calendarId="primary",
                    timeMin=time_min.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    timeMax=time_max.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    singleEvents=True,
                    orderBy="startTime",
                    q=query or None,
                    maxResults=50,
                    timeZone=self.get_timezone(),
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            err_name = type(exc).__name__
            msg = str(exc).lower()
            logger.warning("event=calendar_list_failed err=%s detail=%s", err_name, str(exc)[:240])
            if "401" in msg or "invalid_grant" in msg or "unauthorized" in msg:
                raise PermissionError("auth_required") from exc
            # Calendar API disabled / not enabled on the GCP project — not an OAuth scope issue
            if (
                "has not been used" in msg
                or "it is disabled" in msg
                or "accessnotconfigured" in msg.replace(" ", "")
                or "calendar-json.googleapis.com" in msg
            ):
                raise RuntimeError("api_disabled") from exc
            if "403" in msg or "forbidden" in msg or "insufficient" in msg:
                raise PermissionError("permission_denied") from exc
            if "404" in msg or "notfound" in msg.replace(" ", ""):
                raise FileNotFoundError("calendar_not_found") from exc
            raise
        out = [self._parse(item) for item in (resp.get("items") or [])]
        cache.set(cache_key, out, EVENTS_TTL)
        return out

    def create_event(self, **kwargs: Any) -> RemoteEvent:
        svc = self._service()
        body = {
            "summary": kwargs.get("title") or "Untitled",
            "description": kwargs.get("description") or "",
            "location": kwargs.get("location") or "",
            "start": {"dateTime": kwargs["start_at"].astimezone(timezone.utc).isoformat()},
            "end": {"dateTime": kwargs["end_at"].astimezone(timezone.utc).isoformat()},
        }
        created = svc.events().insert(calendarId="primary", body=body).execute()
        return self._parse(created)

    def update_event(self, event_id: str, **kwargs: Any) -> RemoteEvent | None:
        try:
            svc = self._service()
            existing = svc.events().get(calendarId="primary", eventId=event_id).execute()
            if kwargs.get("title"):
                existing["summary"] = kwargs["title"]
            if "description" in kwargs:
                existing["description"] = kwargs["description"] or ""
            if kwargs.get("start_at"):
                existing["start"] = {
                    "dateTime": kwargs["start_at"].astimezone(timezone.utc).isoformat()
                }
            if kwargs.get("end_at"):
                existing["end"] = {
                    "dateTime": kwargs["end_at"].astimezone(timezone.utc).isoformat()
                }
            if kwargs.get("status") == "cancelled":
                svc.events().delete(calendarId="primary", eventId=event_id).execute()
                return None
            updated = (
                svc.events()
                .update(calendarId="primary", eventId=event_id, body=existing)
                .execute()
            )
            return self._parse(updated)
        except Exception:  # noqa: BLE001
            logger.info("event=calendar_update_failed")
            return None

    def delete_event(self, event_id: str) -> bool:
        try:
            svc = self._service()
            svc.events().delete(calendarId="primary", eventId=event_id).execute()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _parse(self, item: dict) -> RemoteEvent:
        start_raw = (item.get("start") or {}).get("dateTime") or (item.get("start") or {}).get(
            "date"
        )
        end_raw = (item.get("end") or {}).get("dateTime") or (item.get("end") or {}).get("date")
        all_day = "date" in (item.get("start") or {}) and "dateTime" not in (
            item.get("start") or {}
        )
        start = _parse_dt(start_raw)
        end = _parse_dt(end_raw)
        if all_day and end == start:
            end = start + timedelta(days=1)
        return RemoteEvent(
            id=item.get("id") or "",
            title=item.get("summary") or "Untitled",
            description=(item.get("description") or "")[:2000],
            location=item.get("location") or "",
            start_at=start,
            end_at=end,
            all_day=all_day,
            status=item.get("status") or "confirmed",
            is_recurring=bool(item.get("recurringEventId")),
            calendar_id="primary",
        )


def build_calendar_client(*, access_token: str, demo: bool) -> CalendarClientProtocol:
    if demo or (access_token or "").startswith("demo:"):
        return MockCalendarClient()
    return GoogleCalendarClient(access_token)
