"""Production M5 verification on PostgreSQL + Redis + full demo workflow."""

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

from django.conf import settings
from django.core.cache import cache
from django.db import connection

from accounts.models import User
from documents.models import DocumentChunk, FinancialDocument, ProcessingStatus
from memory.models import AssistantMemory
from telegram_bot.services.conversation_processor import ConversationProcessor

DEMO = BASE / "demo" / "documents"
if not DEMO.exists():
    DEMO = BASE / "documents" / "fixtures"


def assert_postgres() -> None:
    assert "postgresql" in settings.DATABASES["default"]["ENGINE"], settings.DATABASES["default"]
    connection.ensure_connection()
    assert connection.vendor == "postgresql"
    print("PASS postgresql", settings.DATABASES["default"].get("NAME"), settings.DATABASES["default"].get("PORT"))


def assert_redis() -> None:
    backend = settings.CACHES["default"]["BACKEND"]
    assert "redis" in backend.lower(), backend
    key = f"m5prod:{int(time.time())}"
    cache.set(key, "pong", 60)
    assert cache.get(key) == "pong"
    print("PASS redis", backend)


def run_workflow() -> None:
    tid = 9910000701
    User.objects.filter(telegram_id=tid).delete()
    User.objects.create(telegram_id=tid, first_name="ProdHero", onboarding_completed=True)
    p = ConversationProcessor()

    apple = (DEMO / "apple_annual_report_fy2024.md").read_bytes()
    msft = (DEMO / "microsoft_annual_report_fy2024.md").read_bytes()

    def ask(label: str, text: str | None = None, upload: bytes | None = None, name: str = "") -> str:
        if upload is not None:
            r = p.handle_document(
                telegram_id=tid,
                file_bytes=upload,
                filename=name,
                mime_type="text/markdown",
            )
        else:
            r = p.handle_text(telegram_id=tid, text=text or "", telegram_message_id=int(time.time() * 1000) % 10_000_000)
        low = (r or "").lower()
        leaks = ("embedding", "vector store", "chunking", "retrieval pipeline")
        assert not any(x in low for x in leaks), f"leak in {label}: {r[:200]}"
        assert "research brain" not in low, f"ai fail {label}: {r[:200]}"
        safe = (r or "")[:120].replace("\n", " | ").encode("ascii", "replace").decode("ascii")
        print(f"OK {label}: {safe}")
        return r or ""

    ask("upload_apple", upload=apple, name="Apple_Annual_Report_FY2024.md")
    ask("summarize_business", "Summarize the business.")
    ask("biggest_risks", "What are the biggest risks?")
    ask("revenue_change", "How did revenue change?")
    ask("ai_strategy", "Summarize management's AI strategy.")
    ask("upload_msft", upload=msft, name="Microsoft_Annual_Report_FY2024.md")
    ask("compare", "Compare Apple and Microsoft.")
    ask("ai_investor", "What should I pay attention to as a long-term AI investor?")

    user = User.objects.get(telegram_id=tid)
    docs = FinancialDocument.objects.filter(user=user, processing_status=ProcessingStatus.READY)
    assert docs.count() >= 2, docs.count()
    assert DocumentChunk.objects.filter(document__user=user).count() >= 4
    mem = AssistantMemory.objects.filter(user=user, key="active_documents").first()
    assert mem and isinstance(mem.value, dict) and mem.value.get("documents"), mem
    print("PASS persistence_and_memory docs=", docs.count(), "chunks=", DocumentChunk.objects.filter(document__user=user).count())


def main() -> None:
    assert_postgres()
    assert_redis()
    run_workflow()
    print("\nM5_PRODUCTION_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
