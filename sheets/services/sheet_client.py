"""Sheets API client — live Google Sheets + local demo backend."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from django.conf import settings
from django.core.cache import cache

from sheets.services.demo_data import DEMO_WORKBOOKS

logger = logging.getLogger("atlas.sheets.client")

VALUES_CACHE_TTL = int(getattr(settings, "CACHE_TTL_SHEET_VALUES", 180) or 180)
META_CACHE_TTL = int(getattr(settings, "CACHE_TTL_SHEET_META", 300) or 300)


@dataclass
class RemoteWorkbook:
    id: str
    title: str
    modified_time: datetime | None = None
    sheet_names: list[str] = field(default_factory=list)


@dataclass
class WorkbookPayload:
    id: str
    title: str
    sheet_names: list[str]
    values_by_sheet: dict[str, list[list[Any]]]
    content_hash: str


class SheetsClientProtocol(Protocol):
    def list_spreadsheets(self, *, query: str = "", page_size: int = 25) -> list[RemoteWorkbook]: ...

    def load_workbook(self, spreadsheet_id: str) -> WorkbookPayload | None: ...


def _hash_values(values_by_sheet: dict[str, list[list[Any]]]) -> str:
    raw = json.dumps(values_by_sheet, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MockSheetsClient:
    """Local demo sheets — no Google API calls."""

    def __init__(self) -> None:
        self._books = {b["id"]: b for b in DEMO_WORKBOOKS}

    def list_spreadsheets(self, *, query: str = "", page_size: int = 25) -> list[RemoteWorkbook]:
        q = (query or "").strip().lower()
        out: list[RemoteWorkbook] = []
        for book in self._books.values():
            if q and q not in book["title"].lower():
                continue
            out.append(
                RemoteWorkbook(
                    id=book["id"],
                    title=book["title"],
                    modified_time=datetime.now(tz=timezone.utc),
                    sheet_names=list(book["sheets"]),
                )
            )
        return out[:page_size]

    def load_workbook(self, spreadsheet_id: str) -> WorkbookPayload | None:
        book = self._books.get(spreadsheet_id)
        if not book:
            return None
        values = {k: [list(r) for r in v] for k, v in book["values"].items()}
        return WorkbookPayload(
            id=book["id"],
            title=book["title"],
            sheet_names=list(book["sheets"]),
            values_by_sheet=values,
            content_hash=_hash_values(values),
        )


class GoogleSheetsClient:
    """Live Google Sheets + Drive discovery (readonly)."""

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self._drive = None
        self._sheets = None

    def _drive_svc(self):
        if self._drive is not None:
            return self._drive
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(token=self.access_token)
        self._drive = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._drive

    def _sheets_svc(self):
        if self._sheets is not None:
            return self._sheets
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(token=self.access_token)
        self._sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
        return self._sheets

    def list_spreadsheets(self, *, query: str = "", page_size: int = 25) -> list[RemoteWorkbook]:
        cache_key = (
            "sheets:list:"
            + hashlib.sha1((self.access_token[:12] + "|" + query).encode()).hexdigest()
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        q_parts = [
            "mimeType='application/vnd.google-apps.spreadsheet'",
            "trashed=false",
        ]
        if query:
            safe = query.replace("'", "\\'")
            q_parts.append(f"name contains '{safe}'")
        try:
            resp = (
                self._drive_svc()
                .files()
                .list(
                    q=" and ".join(q_parts),
                    pageSize=min(page_size, 50),
                    fields="files(id,name,modifiedTime)",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=sheets_list_failed err=%s", type(exc).__name__)
            raise
        out: list[RemoteWorkbook] = []
        for f in resp.get("files") or []:
            mt = f.get("modifiedTime")
            try:
                modified = datetime.fromisoformat(mt.replace("Z", "+00:00")) if mt else None
            except ValueError:
                modified = None
            out.append(
                RemoteWorkbook(
                    id=str(f.get("id") or ""),
                    title=str(f.get("name") or "Untitled"),
                    modified_time=modified,
                )
            )
        cache.set(cache_key, out, META_CACHE_TTL)
        return out

    def load_workbook(self, spreadsheet_id: str) -> WorkbookPayload | None:
        cache_key = "sheets:vals:" + hashlib.sha1(spreadsheet_id.encode()).hexdigest()
        cached = cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            meta = (
                self._sheets_svc()
                .spreadsheets()
                .get(spreadsheetId=spreadsheet_id, fields="properties.title,sheets.properties.title")
                .execute()
            )
            titles = [
                s.get("properties", {}).get("title")
                for s in (meta.get("sheets") or [])
                if s.get("properties", {}).get("title")
            ]
            title = (meta.get("properties") or {}).get("title") or "Untitled"
            values_by_sheet: dict[str, list[list[Any]]] = {}
            # Cap tabs and rows for hackathon latency
            for tab in titles[:8]:
                result = (
                    self._sheets_svc()
                    .spreadsheets()
                    .values()
                    .get(spreadsheetId=spreadsheet_id, range=f"'{tab}'!A1:Z200")
                    .execute()
                )
                values_by_sheet[tab] = result.get("values") or []
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=sheets_load_failed err=%s", type(exc).__name__)
            return None
        payload = WorkbookPayload(
            id=spreadsheet_id,
            title=title,
            sheet_names=list(values_by_sheet.keys()),
            values_by_sheet=values_by_sheet,
            content_hash=_hash_values(values_by_sheet),
        )
        cache.set(cache_key, payload, VALUES_CACHE_TTL)
        return payload


def build_sheets_client(*, access_token: str | None, demo: bool = False) -> SheetsClientProtocol:
    if demo or not access_token or str(access_token).startswith("demo:"):
        return MockSheetsClient()
    return GoogleSheetsClient(access_token)
