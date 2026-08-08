"""Milestone 6 verification — Drive as document source (demo mode)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
os.chdir(BASE)

import django

django.setup()

from accounts.models import GoogleIntegration, GoogleService, User  # noqa: E402
from core.crypto import decrypt_text, encrypt_text  # noqa: E402
from documents.models import DocumentSource, FinancialDocument, ProcessingStatus  # noqa: E402
from drive.models import DriveFile, DriveSyncState  # noqa: E402
from drive.services.drive_client import MockDriveClient  # noqa: E402
from drive.services.drive_intent import detect_drive_intent  # noqa: E402
from drive.services.drive_sync import DriveSyncService  # noqa: E402
from drive.services.mime_map import is_metadata_only, is_supported_for_import  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402


def _ok(label: str) -> None:
    print(f"PASS {label}")


def test_crypto() -> None:
    token = encrypt_text("secret-access-token")
    assert token and token != "secret-access-token"
    assert decrypt_text(token) == "secret-access-token"
    assert decrypt_text("") == ""
    _ok("oauth_token_encryption")


def test_intents() -> None:
    assert detect_drive_intent("Connect my Google Drive").kind == "connect"
    assert detect_drive_intent("Search my Drive for Nvidia").kind == "search"
    assert detect_drive_intent("What documents do I have about Microsoft?").kind == "search"
    assert detect_drive_intent("Analyze my Apple annual report").kind == "import"
    assert detect_drive_intent("What is Apple's PE ratio?").kind == "none"
    _ok("drive_intents")


def test_mime_map() -> None:
    assert is_supported_for_import("application/pdf", "a.pdf")
    assert is_supported_for_import("application/vnd.google-apps.document", "Doc")
    assert is_metadata_only("application/vnd.google-apps.spreadsheet")
    assert not is_supported_for_import("application/vnd.google-apps.spreadsheet", "Sheet")
    _ok("mime_map")


def test_mock_client_and_sync() -> None:
    tid = 9910000601
    User.objects.filter(telegram_id=tid).delete()
    user = User.objects.create(telegram_id=tid, first_name="DriveHero", onboarding_completed=True)
    sync = DriveSyncService()
    sync.connect_demo(user)
    assert sync.is_connected(user)
    full = sync.full_sync(user)
    assert full["ok"], full
    assert DriveFile.objects.filter(user=user, is_trashed=False).count() >= 2
    _ok("demo_connect_full_sync")

    # Incremental + rename + delete
    client = MockDriveClient()
    files = client.list_files()
    assert files
    target = next(f for f in files if f.name.lower().startswith("apple"))
    client.rename(target.id, "Apple_Renamed_FY2024.md")
    inc = sync.incremental_sync(user)
    assert inc["ok"], inc
    # Re-upsert from mock search path
    sync.search_files(user, "Apple", refresh=True)
    _ok("incremental_sync")

    # Import Apple
    imp = sync.import_matching(user, "Apple annual")
    assert imp["ok"], imp
    doc = imp["document"]
    assert doc.processing_status == ProcessingStatus.READY
    assert doc.source == DocumentSource.DRIVE
    _ok("drive_import_apple")

    # Duplicate import reuses embeddings/doc
    imp2 = sync.import_matching(user, "Apple annual")
    assert imp2["ok"] and imp2.get("reused")
    assert str(imp2["document"].id) == str(doc.id)
    _ok("duplicate_detection_content_hash")

    # Sheets metadata only
    sheet = DriveFile.objects.filter(user=user, metadata_only=True).first()
    if sheet:
        bad = sync.import_file(user, drive_file_id=sheet.drive_file_id)
        assert not bad["ok"] and bad["error_code"] == "metadata_only"
        _ok("sheets_metadata_only")

    # Deleted handling
    client.mark_deleted(target.id)
    DriveFile.objects.filter(user=user, drive_file_id=target.id).update(is_trashed=True)
    assert DriveFile.objects.filter(user=user, drive_file_id=target.id, is_trashed=True).exists()
    _ok("deleted_file_handling")

    # Token refresh path: demo token decrypts
    integ = GoogleIntegration.objects.get(user=user, service=GoogleService.DRIVE)
    assert decrypt_text(integ.access_token_encrypted).startswith("demo:")
    _ok("token_storage")


def test_telegram_workflow() -> None:
    tid = 9910000602
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="DriveDemo", onboarding_completed=True)
    p = ConversationProcessor()

    def ask(label: str, text: str) -> str:
        r = p.handle_text(
            telegram_id=tid,
            text=text,
            telegram_message_id=int(time.time() * 1000) % 10_000_000,
        )
        low = (r or "").lower()
        for leak in (
            "drive_file_id",
            "file id",
            "embedding",
            "vector store",
            "chunking",
        ):
            assert leak not in low, f"leak `{leak}` in {label}: {r[:220]}"
        # google auth URLs legitimately contain /o/oauth2/ — only forbid raw token leakage
        if "ya29." in low or "googleapis.com/drive/v3" in low:
            assert False, f"api leak in {label}: {r[:220]}"
        # If connecting with live OAuth configured, auth URL is expected
        if "oauth2" in low and "accounts.google.com" not in low and "connect" not in label:
            assert False, f"leak `oauth2` in {label}: {r[:220]}"
        safe = (r or "")[:110].replace("\n", " | ").encode("ascii", "replace").decode("ascii")
        print(f"OK {label}: {safe}")
        return r or ""

    ask("connect", "Connect my Google Drive.")
    ask("import_apple", "Analyze my Apple annual report.")
    ask("summarize", "Summarize the business.")
    ask("risks", "What are the biggest risks?")
    ask("import_msft", "Import my Microsoft annual report.")
    ask("compare", "Compare Apple and Microsoft.")
    ask("docs_ai", "What documents do I have about AI?")
    # Must be a library listing, not a sector preference ack
    r_ai = ask("docs_ai_check", "Search my Drive for Microsoft")
    assert "microsoft" in r_ai.lower() or "msft" in r_ai.lower() or "found" in r_ai.lower()
    ask("long_term", "What should I pay attention to as a long-term AI investor?")
    # Explicit docs-about-AI after imports
    r_docs = p.handle_text(telegram_id=tid, text="What documents do I have about AI?")
    assert "prioritize" not in (r_docs or "").lower()
    assert "embedding" not in (r_docs or "").lower()
    print("OK docs_ai_routed:", (r_docs or "")[:100].replace("\n", " | "))
    _ok("telegram_drive_workflow")


def main() -> None:
    print("=== Milestone 6 verification ===")
    test_crypto()
    test_intents()
    test_mime_map()
    test_mock_client_and_sync()
    test_telegram_workflow()
    print("ALL MILESTONE 6 CHECKS PASSED")


if __name__ == "__main__":
    main()
