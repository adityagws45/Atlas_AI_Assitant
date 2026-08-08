"""Drive API client — real Google Drive + local demo backend."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from django.conf import settings
from django.core.cache import cache

from drive.services.mime_map import (
    FOLDER,
    export_spec,
    is_folder,
    is_metadata_only,
    is_supported_for_import,
    suggested_extension,
)

logger = logging.getLogger("atlas.drive.client")

LIST_CACHE_TTL = int(getattr(settings, "CACHE_TTL_DRIVE_LIST", 120) or 120)


@dataclass
class DriveRemoteFile:
    id: str
    name: str
    mime_type: str
    modified_time: datetime | None = None
    md5_checksum: str = ""
    size_bytes: int | None = None
    parents: list[str] = field(default_factory=list)
    trashed: bool = False
    web_view_link: str = ""

    @property
    def is_folder(self) -> bool:
        return is_folder(self.mime_type)

    @property
    def metadata_only(self) -> bool:
        return is_metadata_only(self.mime_type)

    @property
    def supported(self) -> bool:
        return is_supported_for_import(self.mime_type, self.name)


@dataclass
class DownloadedFile:
    name: str
    mime_type: str
    data: bytes
    content_hash: str
    metadata_only: bool = False


class DriveClientProtocol(Protocol):
    def list_files(
        self, *, query: str = "", page_size: int = 50, folder_id: str | None = None
    ) -> list[DriveRemoteFile]: ...

    def search(self, query: str, *, page_size: int = 25) -> list[DriveRemoteFile]: ...

    def get_file(self, file_id: str) -> DriveRemoteFile | None: ...

    def download(self, file: DriveRemoteFile) -> DownloadedFile: ...

    def list_changes(self, page_token: str | None) -> tuple[list[DriveRemoteFile], str]: ...


def _parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class MockDriveClient:
    """Local demo Drive backed by demo/documents — no Google API calls."""

    def __init__(self, root: Path | None = None) -> None:
        base = Path(settings.BASE_DIR)
        self.root = root or (base / "demo" / "documents")
        if not self.root.exists():
            self.root = base / "documents" / "fixtures"
        self._files = self._scan()

    def _scan(self) -> dict[str, DriveRemoteFile]:
        out: dict[str, DriveRemoteFile] = {}
        if not self.root.exists():
            return out
        for path in sorted(self.root.iterdir()):
            if not path.is_file():
                continue
            data = path.read_bytes()
            fid = "demo_" + hashlib.sha1(path.name.encode()).hexdigest()[:16]
            mime = "text/markdown" if path.suffix.lower() in {".md", ".markdown"} else "text/plain"
            if path.suffix.lower() == ".pdf":
                mime = "application/pdf"
            elif path.suffix.lower() == ".docx":
                mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            out[fid] = DriveRemoteFile(
                id=fid,
                name=path.name,
                mime_type=mime,
                modified_time=datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
                md5_checksum=hashlib.md5(data).hexdigest(),
                size_bytes=len(data),
                parents=["demo_root"],
            )
        # Synthetic Google Doc + Sheet for edge-case coverage
        sheet_id = "demo_sheet_meta"
        out[sheet_id] = DriveRemoteFile(
            id=sheet_id,
            name="Portfolio_Tracker.xlsx",
            mime_type="application/vnd.google-apps.spreadsheet",
            modified_time=datetime.now(tz=timezone.utc),
            md5_checksum="",
            size_bytes=0,
            parents=["demo_root"],
        )
        return out

    def list_files(
        self, *, query: str = "", page_size: int = 50, folder_id: str | None = None
    ) -> list[DriveRemoteFile]:
        files = [f for f in self._files.values() if not f.trashed]
        if query:
            q = query.lower()
            files = [f for f in files if q in f.name.lower()]
        return files[:page_size]

    def search(self, query: str, *, page_size: int = 25) -> list[DriveRemoteFile]:
        return self.list_files(query=query, page_size=page_size)

    def get_file(self, file_id: str) -> DriveRemoteFile | None:
        return self._files.get(file_id)

    def download(self, file: DriveRemoteFile) -> DownloadedFile:
        if file.metadata_only:
            return DownloadedFile(
                name=file.name,
                mime_type=file.mime_type,
                data=b"",
                content_hash="",
                metadata_only=True,
            )
        path = self.root / file.name
        if not path.exists():
            # synthetic google doc style
            data = f"# {file.name}\n\nDemo document content for {file.name}.\n".encode()
        else:
            data = path.read_bytes()
        return DownloadedFile(
            name=file.name,
            mime_type=file.mime_type,
            data=data,
            content_hash=hashlib.sha256(data).hexdigest(),
        )

    def list_changes(self, page_token: str | None) -> tuple[list[DriveRemoteFile], str]:
        # Demo: full list as "changes"; token advances but content stable
        token = page_token or "0"
        nxt = str(int(token) + 1) if token.isdigit() else "1"
        return list(self._files.values()), nxt

    def mark_deleted(self, file_id: str) -> None:
        f = self._files.get(file_id)
        if f:
            f.trashed = True

    def rename(self, file_id: str, new_name: str) -> None:
        f = self._files.get(file_id)
        if f:
            f.name = new_name
            f.modified_time = datetime.now(tz=timezone.utc)


class GoogleDriveClient:
    """Live Google Drive API (readonly)."""

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self._service = None

    def _svc(self):
        if self._service is not None:
            return self._service
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(token=self.access_token)
        self._service = build("drive", "v3", credentials=creds, cache_discovery=False)
        return self._service

    def list_files(
        self, *, query: str = "", page_size: int = 50, folder_id: str | None = None
    ) -> list[DriveRemoteFile]:
        cache_key = f"drive:list:{hashlib.sha1((self.access_token[:12]+query+str(folder_id)).encode()).hexdigest()}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        q_parts = ["trashed = false"]
        if folder_id:
            q_parts.append(f"'{folder_id}' in parents")
        if query:
            safe = query.replace("'", "\\'")
            q_parts.append(f"name contains '{safe}'")
        q = " and ".join(q_parts)
        try:
            resp = (
                self._svc()
                .files()
                .list(
                    q=q,
                    pageSize=min(page_size, 100),
                    fields=(
                        "files(id,name,mimeType,modifiedTime,md5Checksum,size,parents,"
                        "trashed,webViewLink)"
                    ),
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=drive_list_failed err=%s", type(exc).__name__)
            raise
        files = [_from_api(f) for f in resp.get("files") or []]
        cache.set(cache_key, files, LIST_CACHE_TTL)
        return files

    def search(self, query: str, *, page_size: int = 25) -> list[DriveRemoteFile]:
        return self.list_files(query=query, page_size=page_size)

    def get_file(self, file_id: str) -> DriveRemoteFile | None:
        try:
            raw = (
                self._svc()
                .files()
                .get(
                    fileId=file_id,
                    fields=(
                        "id,name,mimeType,modifiedTime,md5Checksum,size,parents,"
                        "trashed,webViewLink"
                    ),
                    supportsAllDrives=True,
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=drive_get_failed err=%s", type(exc).__name__)
            return None
        return _from_api(raw)

    def download(self, file: DriveRemoteFile) -> DownloadedFile:
        if file.metadata_only:
            return DownloadedFile(
                name=file.name,
                mime_type=file.mime_type,
                data=b"",
                content_hash="",
                metadata_only=True,
            )
        from googleapiclient.http import MediaIoBaseDownload
        import io

        svc = self._svc()
        buf = io.BytesIO()
        exp = export_spec(file.mime_type)
        try:
            if exp:
                request = svc.files().export_media(fileId=file.id, mimeType=exp[0])
                ext = exp[1]
                out_mime = exp[0]
                out_name = _ensure_ext(file.name, ext)
            else:
                request = svc.files().get_media(fileId=file.id)
                out_mime = file.mime_type
                out_name = file.name
            downloader = MediaIoBaseDownload(buf, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=drive_download_failed err=%s", type(exc).__name__)
            raise
        data = buf.getvalue()
        return DownloadedFile(
            name=out_name,
            mime_type=out_mime,
            data=data,
            content_hash=hashlib.sha256(data).hexdigest() if data else "",
        )

    def list_changes(self, page_token: str | None) -> tuple[list[DriveRemoteFile], str]:
        svc = self._svc()
        if not page_token:
            start = svc.changes().getStartPageToken().execute()
            page_token = start.get("startPageToken") or ""
        changes: list[DriveRemoteFile] = []
        token = page_token
        new_token = page_token
        try:
            while token:
                resp = (
                    svc.changes()
                    .list(
                        pageToken=token,
                        fields=(
                            "nextPageToken,newStartPageToken,"
                            "changes(fileId,removed,file(id,name,mimeType,modifiedTime,"
                            "md5Checksum,size,parents,trashed,webViewLink))"
                        ),
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )
                for ch in resp.get("changes") or []:
                    if ch.get("removed"):
                        changes.append(
                            DriveRemoteFile(
                                id=ch.get("fileId") or "",
                                name="",
                                mime_type="",
                                trashed=True,
                            )
                        )
                        continue
                    raw = ch.get("file")
                    if raw:
                        changes.append(_from_api(raw))
                if resp.get("newStartPageToken"):
                    new_token = resp["newStartPageToken"]
                token = resp.get("nextPageToken")
                if not token:
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=drive_changes_failed err=%s", type(exc).__name__)
            raise
        return changes, new_token or page_token


def _from_api(raw: dict[str, Any]) -> DriveRemoteFile:
    size = raw.get("size")
    try:
        size_i = int(size) if size is not None else None
    except (TypeError, ValueError):
        size_i = None
    return DriveRemoteFile(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or "Untitled"),
        mime_type=str(raw.get("mimeType") or ""),
        modified_time=_parse_rfc3339(raw.get("modifiedTime")),
        md5_checksum=str(raw.get("md5Checksum") or ""),
        size_bytes=size_i,
        parents=list(raw.get("parents") or []),
        trashed=bool(raw.get("trashed")),
        web_view_link=str(raw.get("webViewLink") or ""),
    )


def _ensure_ext(name: str, ext: str) -> str:
    lower = name.lower()
    if lower.endswith(ext):
        return name
    if "." in name:
        return name.rsplit(".", 1)[0] + ext
    return name + ext


def build_drive_client(*, access_token: str | None, demo: bool = False) -> DriveClientProtocol:
    if demo or not access_token or access_token.startswith("demo:"):
        return MockDriveClient()
    return GoogleDriveClient(access_token)
