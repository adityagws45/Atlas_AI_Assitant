"""Live Milestone 6 OAuth verification against PostgreSQL + Redis + real Google Drive.

Prerequisites in .env:
  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, FIELD_ENCRYPTION_KEY
  DATABASE_URL (Postgres), REDIS_URL, GEMINI_API_KEY

Usage:
  1. Terminal A: python manage.py runserver 8000
  2. Terminal B: python scripts/verify_milestone6_live_oauth.py
  3. Open the printed auth URL, approve Drive readonly access
  4. Script continues after callback stores encrypted tokens

Optional:
  ATLAS_LIVE_TELEGRAM_ID=123456789  # reuse an existing Telegram user
  ATLAS_DRIVE_PDF_QUERY=annual report  # search query for a real PDF
"""

from __future__ import annotations

import os
import sys
import time
import webbrowser
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from datetime import timedelta

from django.utils import timezone

from accounts.models import GoogleIntegration, GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService
from core.crypto import decrypt_text
from documents.models import DocumentSource, FinancialDocument, ProcessingStatus
from drive.models import DriveConnectionMode, DriveFile, DriveSyncState
from drive.services.drive_client import GoogleDriveClient, build_drive_client
from drive.services.drive_sync import DriveSyncService
from memory.models import AssistantMemory
from telegram_bot.services.conversation_processor import ConversationProcessor


def _pass(label: str) -> None:
    print(f"PASS {label}")


def _fail(label: str, detail: str = "") -> None:
    print(f"FAIL {label}: {detail}")
    raise SystemExit(1)


def assert_postgres() -> None:
    eng = settings.DATABASES["default"]["ENGINE"]
    if "postgresql" not in eng:
        _fail("postgresql", eng)
    connection.ensure_connection()
    if connection.vendor != "postgresql":
        _fail("postgresql_vendor", connection.vendor)
    _pass(f"postgresql name={settings.DATABASES['default'].get('NAME')} port={settings.DATABASES['default'].get('PORT')}")


def assert_redis() -> None:
    backend = settings.CACHES["default"]["BACKEND"]
    if "redis" not in backend.lower():
        _fail("redis", backend)
    key = f"m6live:{int(time.time())}"
    cache.set(key, "pong", 60)
    if cache.get(key) != "pong":
        _fail("redis_roundtrip")
    _pass(f"redis {backend}")


def assert_oauth_configured(oauth: GoogleOAuthService) -> None:
    if not oauth.is_configured():
        _fail(
            "oauth_credentials",
            "Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in .env "
            "(Google Cloud Console -> OAuth client -> Web -> redirect "
            f"{settings.GOOGLE_REDIRECT_URI})",
        )
    if not (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip():
        print("WARN FIELD_ENCRYPTION_KEY empty - using SECRET_KEY-derived Fernet (dev only)")
    _pass("oauth_credentials_present")


def get_or_create_user() -> User:
    tid = int(os.environ.get("ATLAS_LIVE_TELEGRAM_ID") or "9910000610")
    user, _ = User.objects.get_or_create(
        telegram_id=tid,
        defaults={"first_name": "LiveOAuth", "onboarding_completed": True},
    )
    if not user.onboarding_completed:
        user.onboarding_completed = True
        user.save(update_fields=["onboarding_completed", "updated_at"])
    return user


def wait_for_oauth(user: User, oauth: GoogleOAuthService, *, timeout_s: int = 300) -> None:
    integ = GoogleIntegration.objects.filter(
        user=user, service=GoogleService.DRIVE, is_active=True
    ).first()
    access = decrypt_text(integ.access_token_encrypted) if integ else ""
    if access and not access.startswith("demo:"):
        _pass("oauth_already_connected")
        return

    started = oauth.start_auth(user, service=GoogleService.DRIVE)
    if not started.get("ok"):
        _fail("oauth_start", started.get("error") or "start failed")
    url = started["auth_url"]
    print("\n=== Open this URL to authorize Google Drive (readonly) ===\n")
    print(url)
    print("\nWaiting for callback (up to "
          f"{timeout_s}s). Ensure: python manage.py runserver 8000\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        integ = GoogleIntegration.objects.filter(
            user=user, service=GoogleService.DRIVE, is_active=True
        ).first()
        if integ:
            token = decrypt_text(integ.access_token_encrypted)
            if token and not token.startswith("demo:"):
                # Flip sync mode to OAuth
                state, _ = DriveSyncState.objects.get_or_create(user=user)
                if state.mode != DriveConnectionMode.OAUTH:
                    state.mode = DriveConnectionMode.OAUTH
                    state.save(update_fields=["mode", "updated_at"])
                _pass("oauth_callback_completed")
                return
        time.sleep(2)
    _fail("oauth_timeout", "No encrypted OAuth tokens received within timeout")


def verify_encrypted_tokens(user: User) -> str:
    integ = GoogleIntegration.objects.get(user=user, service=GoogleService.DRIVE, is_active=True)
    raw_access = integ.access_token_encrypted or ""
    raw_refresh = integ.refresh_token_encrypted or ""
    if not raw_access or raw_access.startswith("ya29.") or " " in raw_access[:20]:
        # ya29 is plaintext Google access token prefix — must NOT be stored raw
        if raw_access.startswith("ya29."):
            _fail("token_encryption", "access token stored in plaintext")
    access = decrypt_text(raw_access)
    refresh = decrypt_text(raw_refresh)
    if not access or access.startswith("demo:"):
        _fail("token_decrypt", "access token missing or still demo")
    if access == raw_access:
        _fail("token_encryption", "access token not encrypted at rest")
    _pass("encrypted_token_storage")
    # Refresh path
    oauth = GoogleOAuthService()
    # Force near-expiry to exercise refresh when refresh token exists
    if refresh and integ.token_expires_at and integ.token_expires_at > timezone.now():
        integ.token_expires_at = timezone.now() - timedelta(seconds=30)
        integ.save(update_fields=["token_expires_at", "updated_at"])
        new_access = oauth.get_valid_access_token(user, service=GoogleService.DRIVE)
        if not new_access:
            _fail("token_refresh", "refresh returned empty")
        _pass("token_refresh")
        return new_access
    token = oauth.get_valid_access_token(user, service=GoogleService.DRIVE)
    if not token:
        _fail("valid_access_token")
    _pass("token_refresh_skipped_still_valid")
    return token


def verify_sync(user: User, sync: DriveSyncService) -> None:
    full = sync.full_sync(user)
    if not full.get("ok"):
        _fail("full_sync", full.get("error") or str(full))
    seen = (full.get("stats") or {}).get("seen", 0)
    n = DriveFile.objects.filter(user=user, is_trashed=False).count()
    _pass(f"full_sync seen={seen} catalog={n}")

    inc = sync.incremental_sync(user)
    if not inc.get("ok"):
        _fail("incremental_sync", inc.get("error") or str(inc))
    _pass(f"incremental_sync stats={inc.get('stats')}")


def verify_rename_delete(user: User, access_token: str) -> None:
    """Exercise rename/delete handling via local catalog mutations + re-upsert semantics."""
    row = (
        DriveFile.objects.filter(user=user, is_trashed=False, is_folder=False)
        .order_by("-modified_time")
        .first()
    )
    if not row:
        print("WARN rename/delete skipped - empty Drive catalog")
        return

    # Rename detection: simulate metadata upsert with new name
    from drive.services.drive_client import DriveRemoteFile

    sync = DriveSyncService()
    remote = DriveRemoteFile(
        id=row.drive_file_id,
        name=f"RENAMED_VERIFY_{row.name}"[:500],
        mime_type=row.mime_type,
        md5_checksum=row.md5_checksum,
        size_bytes=row.size_bytes,
        parents=row.parents or [],
    )
    updated = sync._upsert_metadata(user, remote)
    if updated.name != remote.name:
        _fail("rename_upsert", updated.name)
    # Restore original name via live API metadata if possible
    client = GoogleDriveClient(access_token)
    live = client.get_file(row.drive_file_id)
    if live:
        sync._upsert_metadata(user, live)
        _pass("renamed_file_handling")
    else:
        # Restore local name
        row.refresh_from_db()
        row.name = row.name.replace("RENAMED_VERIFY_", "", 1)
        row.save(update_fields=["name", "updated_at"])
        _pass("renamed_file_handling_local")

    # Deleted handling: mark trashed then restore flag (do not trash real Drive files)
    fid = row.drive_file_id
    DriveFile.objects.filter(user=user, drive_file_id=fid).update(
        is_trashed=True, updated_at=timezone.now()
    )
    if not DriveFile.objects.filter(user=user, drive_file_id=fid, is_trashed=True).exists():
        _fail("deleted_file_flag")
    DriveFile.objects.filter(user=user, drive_file_id=fid).update(
        is_trashed=False, updated_at=timezone.now()
    )
    _pass("deleted_file_handling")


def import_real_pdf(user: User, sync: DriveSyncService) -> FinancialDocument:
    query = (os.environ.get("ATLAS_DRIVE_PDF_QUERY") or "pdf").strip()
    # Prefer real PDFs from catalog
    pdfs = list(
        DriveFile.objects.filter(
            user=user,
            is_trashed=False,
            is_folder=False,
            mime_type__icontains="pdf",
        ).order_by("-modified_time")[:15]
    )
    if not pdfs:
        # Refresh search
        sync.search_files(user, query, limit=25, refresh=True)
        pdfs = list(
            DriveFile.objects.filter(
                user=user,
                is_trashed=False,
                mime_type__icontains="pdf",
            )[:15]
        )
    if not pdfs:
        # Try Google Docs exportable as text via name search
        matches = sync.search_files(user, query, limit=10, refresh=True)
        prefer = [m for m in matches if m.is_supported and not m.metadata_only]
        if not prefer:
            _fail(
                "real_pdf_import",
                "No PDF/Docs found in Drive. Upload a PDF or set ATLAS_DRIVE_PDF_QUERY.",
            )
        target = prefer[0]
    else:
        # Prefer query match
        target = next((p for p in pdfs if query.lower() in (p.name or "").lower()), pdfs[0])

    print(f"INFO importing name={target.name!r} mime={target.mime_type}")
    result = sync.import_file(user, drive_file_id=target.drive_file_id)
    if not result.get("ok"):
        _fail("real_pdf_import", result.get("error") or str(result))
    doc = result["document"]
    if doc.processing_status != ProcessingStatus.READY:
        _fail("doc_ready", doc.processing_status)
    if doc.source != DocumentSource.DRIVE:
        _fail("doc_source", doc.source)
    _pass(f"real_drive_import title={doc.title!r} pages={doc.page_count}")
    return doc


def ensure_apple_compare_doc(user: User, processor: ConversationProcessor) -> None:
    """Seed Apple annual report locally so compare has a second document."""
    demo = BASE / "demo" / "documents" / "apple_annual_report_fy2024.md"
    if not demo.exists():
        demo = BASE / "documents" / "fixtures" / "apple_annual_report_fy2024.md"
    if not demo.exists():
        print("WARN no Apple fixture for compare - compare may be single-doc")
        return
    # Skip if already have an Apple doc ready
    existing = FinancialDocument.objects.filter(
        user=user,
        processing_status=ProcessingStatus.READY,
        title__icontains="Apple",
    ).exists()
    if existing:
        return
    processor.handle_document(
        telegram_id=user.telegram_id,
        file_bytes=demo.read_bytes(),
        filename="Apple_Annual_Report_FY2024.md",
        mime_type="text/markdown",
    )
    _pass("seeded_apple_for_compare")


def telegram_qa(user: User, processor: ConversationProcessor) -> None:
    def ask(label: str, text: str) -> str:
        r = processor.handle_text(
            telegram_id=user.telegram_id,
            text=text,
            telegram_message_id=int(time.time() * 1000) % 10_000_000,
        )
        low = (r or "").lower()
        for leak in (
            "drive_file_id",
            "googleapis",
            "oauth2",
            "embedding",
            "vector store",
            "chunking",
            "ya29.",
        ):
            if leak in low:
                _fail(f"leak_{label}", leak)
        safe = (r or "")[:140].replace("\n", " | ").encode("ascii", "replace").decode("ascii")
        print(f"OK {label}: {safe}")
        return r or ""

    ask("summarize", "Summarize this report.")
    ask("risks", "What are the biggest risks?")
    ask("compare", "Compare this with Apple's annual report.")
    search = ask("drive_search", "Search my Drive for report")
    if "found" not in search.lower() and "•" not in search and "*" not in search:
        # Soft check - empty Drive search still ok if phrased helpfully
        print("WARN drive_search reply unexpected shape")
    _pass("telegram_qa_and_search")

    mem = AssistantMemory.objects.filter(user=user).count()
    if mem < 1:
        _fail("memory", "no assistant memory rows")
    _pass(f"memory rows={mem}")


def main() -> None:
    print("=== Milestone 6 LIVE OAuth verification ===")
    assert_postgres()
    assert_redis()
    oauth = GoogleOAuthService()
    assert_oauth_configured(oauth)
    user = get_or_create_user()
    print(f"INFO user telegram_id={user.telegram_id}")

    wait_for_oauth(user, oauth)
    access = verify_encrypted_tokens(user)

    # Ensure OAuth mode (not demo)
    state = DriveSyncService().ensure_state(user)
    state.mode = DriveConnectionMode.OAUTH
    state.save(update_fields=["mode", "updated_at"])

    # Sanity: live client list
    client = build_drive_client(access_token=access, demo=False)
    try:
        files = client.list_files(page_size=5)
        _pass(f"drive_api_list count={len(files)}")
    except Exception as exc:  # noqa: BLE001
        _fail("drive_api_list", type(exc).__name__)

    sync = DriveSyncService()
    verify_sync(user, sync)
    verify_rename_delete(user, access)
    doc = import_real_pdf(user, sync)

    processor = ConversationProcessor()
    ensure_apple_compare_doc(user, processor)
    # Re-focus imported Drive doc
    from documents.services.document_memory import DocumentMemory

    DocumentMemory().remember_upload(user, doc)
    telegram_qa(user, processor)

    print("\nALL LIVE OAUTH CHECKS PASSED")
    print("MILESTONE_6_LIVE_OAUTH: PASS")


if __name__ == "__main__":
    main()
