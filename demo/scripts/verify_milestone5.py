"""Milestone 5 verification — document pipeline, retrieval, Q&A, edge cases."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Prefer local sqlite when Postgres isn't up
os.environ.setdefault(
    "DATABASE_URL",
    os.environ.get("DATABASE_URL") or f"sqlite:///{Path(__file__).resolve().parents[1] / 'test_m5.sqlite3'}",
)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

import django

django.setup()

from accounts.models import User  # noqa: E402
from documents.models import DocumentChunk, DocumentSource, FinancialDocument, ProcessingStatus  # noqa: E402
from documents.services.chunking import chunk_pages  # noqa: E402
from documents.services.document_memory import DocumentMemory  # noqa: E402
from documents.services.document_pipeline import DocumentPipeline  # noqa: E402
from documents.services.document_qa_service import DocumentQAService  # noqa: E402
from documents.services.embeddings import cosine_similarity, embed_texts  # noqa: E402
from documents.services.metadata import extract_metadata  # noqa: E402
from documents.services.parser import parse_bytes  # noqa: E402
from documents.services.retriever import DocumentRetriever  # noqa: E402
from documents.services.validation import validate_upload  # noqa: E402
from telegram_bot.services.conversation_processor import ConversationProcessor  # noqa: E402

FIXTURES = BASE / "documents" / "fixtures"


def _user(tid: int = 9900000601) -> User:
    User.objects.filter(telegram_id=tid).delete()
    return User.objects.create(telegram_id=tid, first_name="Demo", onboarding_completed=True)


def test_validation() -> None:
    bad = validate_upload(filename="x.exe", file_size=100, mime_type="application/octet-stream")
    assert not bad.ok
    ok = validate_upload(filename="aapl_10k.pdf", file_size=1000, mime_type="application/pdf")
    assert ok.ok
    empty = validate_upload(filename="a.txt", file_size=0)
    assert not empty.ok
    print("PASS validation")


def test_parse_txt_md() -> None:
    data = (FIXTURES / "apple_annual_report_fy2024.md").read_bytes()
    parsed = parse_bytes(data=data, filename="apple.md")
    assert parsed.page_count == 1
    assert "Risk Factors" in parsed.full_text
    assert not parsed.error
    print("PASS parse_markdown")


def test_parse_corrupt_empty() -> None:
    empty = parse_bytes(data=b"", filename="x.pdf")
    assert empty.error
    corrupt = parse_bytes(data=b"%PDF-1.4 not-a-real-pdf", filename="bad.pdf")
    assert corrupt.error in {"corrupt_pdf", "scanned_or_empty", "empty"}
    print("PASS parse_edge_cases")


def test_chunking_and_metadata() -> None:
    data = (FIXTURES / "apple_annual_report_fy2024.md").read_bytes()
    parsed = parse_bytes(data=data, filename="Apple_10K_2024.md")
    meta = extract_metadata(filename="Apple_10K_2024.md", text=parsed.full_text, page_count=1)
    assert meta["company"] == "Apple"
    drafts = chunk_pages(parsed.pages)
    assert len(drafts) >= 2
    # bullet / table blocks should remain intact somewhere
    joined = "\n".join(d.content for d in drafts)
    assert "Gross margin" in joined or "Risk Factors" in joined
    print(f"PASS chunking_metadata chunks={len(drafts)}")


def test_embeddings_and_search() -> None:
    vecs = embed_texts(["Apple AI strategy privacy", "banking credit risk capital ratios"])
    assert len(vecs) == 2 and len(vecs[0]) > 10
    assert cosine_similarity(vecs[0], vecs[0]) > 0.99
    print("PASS embeddings")


def test_pipeline_upload_duplicate() -> None:
    user = _user(9900000602)
    pipe = DocumentPipeline()
    data = (FIXTURES / "apple_annual_report_fy2024.md").read_bytes()
    doc1 = pipe.ingest_bytes(
        user, data=data, filename="Apple_Annual_Report_FY2024.md", source=DocumentSource.LOCAL
    )
    assert doc1.processing_status == ProcessingStatus.READY
    assert DocumentChunk.objects.filter(document=doc1).count() >= 2
    doc2 = pipe.ingest_bytes(
        user, data=data, filename="Apple_Annual_Report_FY2024.md", source=DocumentSource.LOCAL
    )
    assert doc1.id == doc2.id  # duplicate reuse
    print("PASS pipeline_duplicate")


def test_retrieval_quality() -> None:
    user = User.objects.get(telegram_id=9900000602)
    doc = FinancialDocument.objects.filter(user=user).first()
    retriever = DocumentRetriever()
    chunks = retriever.retrieve_for_question(
        user_id=user.id,
        question="What are the biggest risk factors?",
        document_ids=[str(doc.id)],
        top_k=4,
    )
    assert chunks, "expected risk-related passages"
    blob = " ".join(c.content.lower() for c in chunks)
    assert "risk" in blob
    print("PASS retrieval_quality")


def test_memory_and_qa() -> None:
    user = User.objects.get(telegram_id=9900000602)
    doc = FinancialDocument.objects.filter(user=user).first()
    mem = DocumentMemory()
    mem.remember_upload(user, doc)
    assert str(doc.id) in mem.active_document_ids(user)

    qa = DocumentQAService()
    # Force local fallback synthesis if Gemini exhausted — still must return structure
    result = qa.answer(user, "Summarize the business.", document_ids=[str(doc.id)])
    assert result.get("reply")
    assert "embedding" not in result["reply"].lower()
    assert "vector" not in result["reply"].lower()
    print("PASS memory_and_qa")


def test_compare_and_telegram_flow() -> None:
    tid = 9900000603
    User.objects.filter(telegram_id=tid).delete()
    p = ConversationProcessor()
    # Skip onboarding
    from accounts.models import User as U

    u = U.objects.create(telegram_id=tid, first_name="Hero", onboarding_completed=True)

    apple = (FIXTURES / "apple_annual_report_fy2024.md").read_bytes()
    msft = (FIXTURES / "microsoft_annual_report_fy2024.md").read_bytes()
    apple_prior = (FIXTURES / "apple_annual_report_fy2023.md").read_bytes()

    r1 = p.handle_document(
        telegram_id=tid,
        file_bytes=apple,
        filename="Apple_Annual_Report_FY2024.md",
        mime_type="text/markdown",
    )
    assert "loaded" in r1.lower() or "Got it" in r1
    assert "embedding" not in r1.lower()

    r2 = p.handle_text(telegram_id=tid, text="Summarize the business.", telegram_message_id=2)
    assert r2 and "research brain" not in r2.lower()
    assert "chunk" not in r2.lower()

    r3 = p.handle_text(telegram_id=tid, text="What are the biggest risks?", telegram_message_id=3)
    assert "risk" in r3.lower()

    r4 = p.handle_text(
        telegram_id=tid,
        text="Summarize management's AI strategy.",
        telegram_message_id=4,
    )
    assert "ai" in r4.lower() or "intelligence" in r4.lower()

    # Upload prior year + ask what changed
    p.handle_document(
        telegram_id=tid,
        file_bytes=apple_prior,
        filename="Apple_Annual_Report_FY2023.md",
        mime_type="text/markdown",
    )
    r5 = p.handle_text(
        telegram_id=tid,
        text="What changed compared to last year?",
        telegram_message_id=6,
    )
    assert r5

    # Microsoft for cross-company compare
    p.handle_document(
        telegram_id=tid,
        file_bytes=msft,
        filename="Microsoft_Annual_Report_FY2024.md",
        mime_type="text/markdown",
    )
    r6 = p.handle_text(
        telegram_id=tid,
        text="Compare Apple and Microsoft annual reports.",
        telegram_message_id=8,
    )
    assert r6 and "embedding" not in r6.lower()

    r7 = p.handle_text(
        telegram_id=tid,
        text="What should I pay attention to as a long-term AI investor?",
        telegram_message_id=9,
    )
    assert r7

    # Question before upload (fresh user)
    tid2 = 9900000604
    User.objects.filter(telegram_id=tid2).delete()
    U.objects.create(telegram_id=tid2, first_name="New", onboarding_completed=True)
    r8 = p.handle_text(
        telegram_id=tid2,
        text="What are the biggest risks?",
        telegram_message_id=1,
    )
    # Without docs this may go to market AI OR preference — should not crash
    assert r8

    print("PASS telegram_demo_flow")


def test_retrieval_speed() -> None:
    user = User.objects.filter(telegram_id=9900000603).first()
    if not user:
        print("SKIP retrieval_speed")
        return
    retriever = DocumentRetriever()
    t0 = time.monotonic()
    retriever.retrieve_for_question(
        user_id=user.id,
        question="AI strategy capital allocation risks",
        top_k=6,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 15.0, elapsed
    print(f"PASS retrieval_speed elapsed={elapsed:.2f}s")


def test_unsupported_and_recovery() -> None:
    tid = 9900000605
    User.objects.filter(telegram_id=tid).delete()
    from accounts.models import User as U

    U.objects.create(telegram_id=tid, first_name="Edge", onboarding_completed=True)
    p = ConversationProcessor()
    r = p.handle_document(
        telegram_id=tid,
        file_bytes=b"MZ fake exe",
        filename="malware.exe",
        mime_type="application/octet-stream",
    )
    assert "PDF" in r or "Markdown" in r or "TXT" in r
    # Recovery: next message still works
    r2 = p.handle_text(telegram_id=tid, text="I'm an investor.", telegram_message_id=2)
    assert "research brain" not in r2.lower()
    print("PASS unsupported_and_recovery")


def main() -> None:
    test_validation()
    test_parse_txt_md()
    test_parse_corrupt_empty()
    test_chunking_and_metadata()
    test_embeddings_and_search()
    test_pipeline_upload_duplicate()
    test_retrieval_quality()
    test_memory_and_qa()
    test_compare_and_telegram_flow()
    test_retrieval_speed()
    test_unsupported_and_recovery()
    print("\nMILESTONE_5_VERIFICATION: PASS")


if __name__ == "__main__":
    main()
