"""Drive sync + import — feeds the Milestone 5 DocumentPipeline."""

from __future__ import annotations

import logging
import re
from typing import Any

from django.db import transaction
from django.utils import timezone

from accounts.models import GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService
from documents.models import DocumentSource, FinancialDocument, ProcessingStatus
from documents.services.document_memory import DocumentMemory
from documents.services.document_pipeline import DocumentPipeline
from drive.models import DriveConnectionMode, DriveFile, DriveSyncState, DriveSyncStatus
from drive.services.drive_client import (
    DriveRemoteFile,
    DownloadedFile,
    build_drive_client,
)
from drive.services.mime_map import is_folder, is_metadata_only, is_supported_for_import

logger = logging.getLogger("atlas.drive.sync")


class DriveSyncService:
    """Incremental / full sync and selective import into document intelligence."""

    def __init__(
        self,
        *,
        oauth: GoogleOAuthService | None = None,
        pipeline: DocumentPipeline | None = None,
        memory: DocumentMemory | None = None,
    ) -> None:
        self.oauth = oauth or GoogleOAuthService()
        self.pipeline = pipeline or DocumentPipeline()
        self.memory = memory or DocumentMemory()

    def ensure_state(self, user: User) -> DriveSyncState:
        state, _ = DriveSyncState.objects.get_or_create(user=user)
        return state

    def is_connected(self, user: User) -> bool:
        state = DriveSyncState.objects.filter(user=user).first()
        if state and state.mode == DriveConnectionMode.DEMO:
            return True
        return self.oauth.is_connected(user, service=GoogleService.DRIVE)

    def connect_demo(self, user: User) -> DriveSyncState:
        """Local hackathon/demo connection without Google credentials."""
        from core.crypto import encrypt_text

        integ, _ = user.google_integrations.get_or_create(
            service=GoogleService.DRIVE,
            defaults={
                "access_token_encrypted": encrypt_text("demo:local"),
                "refresh_token_encrypted": encrypt_text("demo:local"),
                "is_active": True,
                "scopes": ["drive.readonly.demo"],
            },
        )
        if not integ.is_active:
            integ.is_active = True
            integ.access_token_encrypted = encrypt_text("demo:local")
            integ.save(update_fields=["is_active", "access_token_encrypted", "updated_at"])
        state = self.ensure_state(user)
        state.mode = DriveConnectionMode.DEMO
        state.status = DriveSyncStatus.IDLE
        state.error_message = ""
        state.save(update_fields=["mode", "status", "error_message", "updated_at"])
        return state

    def _client_for(self, user: User):
        state = self.ensure_state(user)
        if state.mode == DriveConnectionMode.DEMO:
            return build_drive_client(access_token="demo:local", demo=True)
        token = self.oauth.get_valid_access_token(user, service=GoogleService.DRIVE)
        if not token:
            raise PermissionError("Drive isn't connected.")
        return build_drive_client(access_token=token, demo=False)

    def full_sync(self, user: User, *, folder_id: str | None = None) -> dict[str, Any]:
        state = self.ensure_state(user)
        state.status = DriveSyncStatus.RUNNING
        state.error_message = ""
        state.save(update_fields=["status", "error_message", "updated_at"])
        stats = {"seen": 0, "upserted": 0, "trashed": 0, "imported": 0, "skipped": 0}
        try:
            client = self._client_for(user)
            files = client.list_files(page_size=100, folder_id=folder_id)
            seen_ids: set[str] = set()
            for remote in files:
                stats["seen"] += 1
                seen_ids.add(remote.id)
                self._upsert_metadata(user, remote)
                stats["upserted"] += 1
            # Mark missing (within prior catalog) as trashed when doing root full sync
            if folder_id is None:
                qs = DriveFile.objects.filter(user=user, is_trashed=False).exclude(
                    drive_file_id__in=seen_ids
                )
                # Only auto-trash demo/full catalog files we previously indexed from root
                # Keep files that were never in this listing page if API truncated — soft approach:
                # only trash if catalog was small
                if stats["seen"] < 100:
                    trashed_n = qs.update(is_trashed=True, updated_at=timezone.now())
                    stats["trashed"] = trashed_n
            # Seed change token for incremental
            try:
                _, token = client.list_changes(None)
                state.page_token = token or state.page_token
            except Exception:  # noqa: BLE001
                pass
            state.last_full_sync_at = timezone.now()
            state.status = DriveSyncStatus.IDLE
            state.stats = {**(state.stats or {}), "last_full": stats}
            state.save()
            logger.info(
                "event=drive_full_sync_ok telegram_id=%s seen=%s",
                user.telegram_id,
                stats["seen"],
            )
            return {"ok": True, "stats": stats}
        except Exception as exc:  # noqa: BLE001
            state.status = DriveSyncStatus.FAILED
            state.error_message = type(exc).__name__[:200]
            state.save(update_fields=["status", "error_message", "updated_at"])
            logger.warning(
                "event=drive_full_sync_failed telegram_id=%s err=%s",
                user.telegram_id,
                type(exc).__name__,
            )
            return {
                "ok": False,
                "error_code": "sync_failed",
                "error": "I couldn't refresh your files just now. Try again in a moment.",
                "stats": stats,
            }

    def incremental_sync(self, user: User) -> dict[str, Any]:
        state = self.ensure_state(user)
        if not state.page_token and state.mode != DriveConnectionMode.DEMO:
            return self.full_sync(user)
        state.status = DriveSyncStatus.RUNNING
        state.save(update_fields=["status", "updated_at"])
        stats = {"changed": 0, "trashed": 0, "reimported": 0}
        try:
            client = self._client_for(user)
            changes, new_token = client.list_changes(state.page_token or None)
            for remote in changes:
                stats["changed"] += 1
                if remote.trashed or not remote.name:
                    DriveFile.objects.filter(
                        user=user, drive_file_id=remote.id
                    ).update(is_trashed=True, updated_at=timezone.now())
                    stats["trashed"] += 1
                    continue
                row = self._upsert_metadata(user, remote)
                # Re-import if previously imported and checksum changed
                if row.document_id and remote.md5_checksum:
                    if row.md5_checksum and row.md5_checksum != remote.md5_checksum:
                        try:
                            self.import_file(user, drive_file_id=remote.id, force=True)
                            stats["reimported"] += 1
                        except Exception:  # noqa: BLE001
                            pass
            state.page_token = new_token or state.page_token
            state.last_incremental_sync_at = timezone.now()
            state.status = DriveSyncStatus.IDLE
            state.stats = {**(state.stats or {}), "last_incremental": stats}
            state.save()
            return {"ok": True, "stats": stats}
        except Exception as exc:  # noqa: BLE001
            state.status = DriveSyncStatus.FAILED
            state.error_message = type(exc).__name__[:200]
            state.save(update_fields=["status", "error_message", "updated_at"])
            return {
                "ok": False,
                "error_code": "sync_failed",
                "error": "Sync was interrupted. Your earlier files are still available.",
                "stats": stats,
            }

    def search_files(
        self,
        user: User,
        query: str,
        *,
        limit: int = 10,
        refresh: bool = True,
    ) -> list[DriveFile]:
        if refresh and self.is_connected(user):
            try:
                client = self._client_for(user)
                # Prefer primary keyword for remote API search
                remote_q = (query or "").strip().split()[0] if (query or "").strip() else ""
                for remote in client.search(remote_q or query, page_size=max(limit, 25)):
                    self._upsert_metadata(user, remote)
            except Exception:  # noqa: BLE001
                pass
        qs = DriveFile.objects.filter(user=user, is_trashed=False, is_folder=False)
        q = (query or "").strip()
        if q:
            from django.db.models import Q

            tokens = [t for t in re.split(r"[\s_\-]+", q) if len(t) > 1]
            clause = Q()
            for tok in tokens or [q]:
                clause &= Q(name__icontains=tok)
            qs = qs.filter(clause)
        return list(qs.order_by("-view_count", "-modified_time", "-updated_at")[:limit])

    def list_recent(self, user: User, *, limit: int = 10) -> list[DriveFile]:
        return list(
            DriveFile.objects.filter(user=user, is_trashed=False, is_folder=False)
            .order_by("-modified_time", "-updated_at")[:limit]
        )

    def find_by_company_or_topic(self, user: User, topic: str, *, limit: int = 10) -> list[DriveFile]:
        topic = (topic or "").strip()
        if not topic:
            return self.list_recent(user, limit=limit)
        # Filename + linked document metadata
        by_name = self.search_files(user, topic, limit=limit, refresh=True)
        doc_ids = [
            str(d.id)
            for d in FinancialDocument.objects.filter(
                user=user,
                processing_status=ProcessingStatus.READY,
                metadata__company__icontains=topic,
            )[:limit]
        ]
        linked = list(
            DriveFile.objects.filter(
                user=user, document_id__in=doc_ids, is_trashed=False
            )[:limit]
        )
        seen = set()
        out: list[DriveFile] = []
        for f in by_name + linked:
            if f.id in seen:
                continue
            seen.add(f.id)
            out.append(f)
            if len(out) >= limit:
                break
        return out

    def import_file(
        self,
        user: User,
        *,
        drive_file_id: str | None = None,
        name_query: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        row = None
        if drive_file_id:
            row = DriveFile.objects.filter(user=user, drive_file_id=drive_file_id).first()
        if row is None and name_query:
            matches = self.search_files(user, name_query, limit=5, refresh=True)
            if not matches:
                return {
                    "ok": False,
                    "error_code": "not_found",
                    "error": f"I couldn't find a file matching “{name_query}” in your Drive.",
                }
            # Prefer supported importable files
            prefer = [m for m in matches if m.is_supported and not m.metadata_only]
            row = prefer[0] if prefer else matches[0]

        if row is None:
            return {
                "ok": False,
                "error_code": "not_found",
                "error": "I couldn't find that file.",
            }

        if row.metadata_only:
            return {
                "ok": False,
                "error_code": "metadata_only",
                "error": (
                    f"*{row.name}* is a spreadsheet — say “open my portfolio” or "
                    "“show my spreadsheets” and I’ll analyze it as holdings data. "
                    "For documents, try a PDF, Doc, or text/Markdown report."
                ),
            }
        if not row.is_supported:
            return {
                "ok": False,
                "error_code": "unsupported",
                "error": f"I can't read *{row.name}* yet. PDF, Docs, TXT, Markdown, or DOCX work best.",
            }

        # Reuse existing document if content unchanged
        if row.document_id and not force:
            doc = row.document
            if doc and doc.processing_status == ProcessingStatus.READY:
                row.view_count += 1
                row.save(update_fields=["view_count", "updated_at"])
                self.memory.remember_upload(user, doc)
                self._remember_drive_file(user, row)
                return {
                    "ok": True,
                    "reused": True,
                    "document": doc,
                    "drive_file": row,
                    "reply": (
                        f"I've got *{doc.title}* ready from your files. "
                        "What would you like to dig into?"
                    ),
                }

        try:
            client = self._client_for(user)
            remote = client.get_file(row.drive_file_id) or DriveRemoteFile(
                id=row.drive_file_id,
                name=row.name,
                mime_type=row.mime_type,
                md5_checksum=row.md5_checksum,
                size_bytes=row.size_bytes,
            )
            downloaded = client.download(remote)
        except PermissionError:
            return {
                "ok": False,
                "error_code": "not_connected",
                "error": "Connect your Google Drive first, then ask me to pull that file in.",
            }
        except Exception:  # noqa: BLE001
            return {
                "ok": False,
                "error_code": "download_failed",
                "error": f"I couldn't download *{row.name}*. It may have been moved or access was revoked.",
            }

        if downloaded.metadata_only:
            return {
                "ok": False,
                "error_code": "metadata_only",
                "error": f"*{row.name}* doesn't support text import yet.",
            }

        # Content-hash reuse across sources
        existing = (
            FinancialDocument.objects.filter(
                user=user,
                content_hash=downloaded.content_hash,
                processing_status=ProcessingStatus.READY,
            )
            .order_by("-created_at")
            .first()
            if downloaded.content_hash
            else None
        )
        if existing and not force:
            row.document = existing
            row.content_hash = downloaded.content_hash
            row.md5_checksum = remote.md5_checksum or row.md5_checksum
            row.last_synced_at = timezone.now()
            row.view_count += 1
            row.save()
            self.memory.remember_upload(user, existing)
            self._remember_drive_file(user, row)
            return {
                "ok": True,
                "reused": True,
                "document": existing,
                "drive_file": row,
                "reply": (
                    f"*{existing.title}* was already in your library — reusing it "
                    "(no duplicate indexing)."
                ),
            }

        try:
            doc = self.pipeline.ingest_bytes(
                user,
                data=downloaded.data,
                filename=downloaded.name or row.name,
                mime_type=downloaded.mime_type or row.mime_type,
                source=DocumentSource.DRIVE,
                title_override=row.name,
                extra_metadata={
                    "drive_file_id": row.drive_file_id,
                    "drive_name": row.name,
                    "source_label": "your files",
                },
            )
        except ValueError as exc:
            return {"ok": False, "error_code": "parse_failed", "error": str(exc)}
        except Exception:  # noqa: BLE001
            logger.exception("event=drive_import_failed telegram_id=%s", user.telegram_id)
            return {
                "ok": False,
                "error_code": "import_failed",
                "error": f"I hit a snag importing *{row.name}*. Try another format or a shorter export.",
            }

        row.document = doc
        row.content_hash = downloaded.content_hash or doc.content_hash
        row.md5_checksum = remote.md5_checksum or row.md5_checksum
        row.last_synced_at = timezone.now()
        row.view_count += 1
        row.save()
        self.memory.remember_upload(user, doc)
        self._remember_drive_file(user, row)
        pages = f"{doc.page_count} pages" if doc.page_count else "the full file"
        return {
            "ok": True,
            "reused": False,
            "document": doc,
            "drive_file": row,
            "reply": (
                f"Pulled in *{doc.title}* from your files ({pages}). "
                "Ask me about the business, risks, numbers, or strategy."
            ),
        }

    def import_matching(self, user: User, query: str) -> dict[str, Any]:
        return self.import_file(user, name_query=query)

    def background_sync_hook(self, user: User) -> dict[str, Any]:
        """Lightweight hook for schedulers — incremental when possible."""
        if not self.is_connected(user):
            return {"ok": False, "error_code": "not_connected"}
        state = self.ensure_state(user)
        if state.page_token or state.mode == DriveConnectionMode.DEMO:
            return self.incremental_sync(user)
        return self.full_sync(user)

    def _upsert_metadata(self, user: User, remote: DriveRemoteFile) -> DriveFile:
        defaults = {
            "name": (remote.name or "Untitled")[:512],
            "mime_type": remote.mime_type or "",
            "modified_time": remote.modified_time,
            "md5_checksum": remote.md5_checksum or "",
            "size_bytes": remote.size_bytes,
            "parents": remote.parents or [],
            "web_view_link": (remote.web_view_link or "")[:1024],
            "is_folder": is_folder(remote.mime_type),
            "is_trashed": bool(remote.trashed),
            "is_supported": is_supported_for_import(remote.mime_type, remote.name),
            "metadata_only": is_metadata_only(remote.mime_type),
            "last_synced_at": timezone.now(),
        }
        with transaction.atomic():
            row, created = DriveFile.objects.update_or_create(
                user=user,
                drive_file_id=remote.id,
                defaults=defaults,
            )
            # Rename detection: name change on same id
            if not created and row.name != defaults["name"]:
                extra = dict(row.extra or {})
                extra["previous_name"] = row.name
                row.extra = extra
                row.name = defaults["name"]
                row.save(update_fields=["name", "extra", "updated_at"])
        return row

    def _remember_drive_file(self, user: User, row: DriveFile) -> None:
        from memory.models import AssistantMemory, MemorySource, MemoryType

        key = "drive_recent_files"
        mem = AssistantMemory.objects.filter(user=user, key=key).first()
        value = (mem.value if mem and isinstance(mem.value, dict) else {}) or {}
        files = [f for f in value.get("files", []) if f.get("name") != row.name]
        files.insert(
            0,
            {
                "name": row.name,
                "company": (row.document.company if row.document_id else "") or "",
                "view_count": row.view_count,
                "updated_at": timezone.now().isoformat(),
            },
        )
        payload = {"files": files[:12]}
        if mem:
            mem.value = payload
            mem.save(update_fields=["value", "updated_at"])
        else:
            AssistantMemory.objects.create(
                user=user,
                memory_type=MemoryType.CONTEXT,
                key=key,
                value=payload,
                source=MemorySource.CONVERSATION,
                confidence=1.0,
            )
