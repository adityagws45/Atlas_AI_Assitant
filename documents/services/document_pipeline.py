"""End-to-end document processing pipeline."""

from __future__ import annotations

import hashlib
import logging
from typing import BinaryIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction

from accounts.models import User
from documents.models import DocumentChunk, DocumentSource, FinancialDocument, ProcessingStatus
from documents.services.chunking import chunk_pages
from documents.services.embeddings import LOCAL_DIM, embed_corpus
from documents.services.metadata import extract_metadata
from documents.services.parser import parse_bytes
from documents.services.validation import validate_upload

logger = logging.getLogger("atlas.documents.pipeline")


class DocumentPipeline:
    """Upload → validate → parse → clean → metadata → chunk → embed → store."""

    def ingest_bytes(
        self,
        user: User,
        *,
        data: bytes,
        filename: str,
        mime_type: str = "",
        source: str = DocumentSource.TELEGRAM,
        title_override: str = "",
        extra_metadata: dict | None = None,
    ) -> FinancialDocument:
        validation = validate_upload(
            filename=filename,
            file_size=len(data or b""),
            mime_type=mime_type,
        )
        if not validation.ok:
            raise ValueError(validation.error)

        content_hash = hashlib.sha256(data).hexdigest()
        existing = (
            FinancialDocument.objects.filter(
                user=user,
                content_hash=content_hash,
                processing_status=ProcessingStatus.READY,
            )
            .order_by("-created_at")
            .first()
        )
        if existing:
            if extra_metadata:
                existing.metadata = {**(existing.metadata or {}), **extra_metadata}
                existing.save(update_fields=["metadata", "updated_at"])
            # Refresh title/year heuristics if an older buggy metadata pass stored
            # bond maturities (e.g. "Notes due 2031") as the fiscal year.
            try:
                meta = extract_metadata(
                    filename=validation.safe_filename or existing.original_filename,
                    text=existing.extracted_text or existing.title,
                    page_count=existing.page_count or 0,
                )
                changed = False
                existing.metadata = {**(existing.metadata or {}), **meta}
                if meta.get("suggested_title") and existing.title != meta["suggested_title"]:
                    existing.title = meta["suggested_title"]
                    changed = True
                existing.save(update_fields=["metadata", "title", "updated_at"])
                # Keep embedding space coherent for re-uploads of the same file.
                if self._needs_reembed(existing, allow_local_upgrade=True):
                    self.reembed_chunks(existing)
                    changed = True
                logger.info(
                    "event=doc_duplicate_reuse telegram_id=%s doc_id=%s refreshed=%s",
                    user.telegram_id,
                    existing.id,
                    changed,
                )
            except Exception:  # noqa: BLE001
                logger.info(
                    "event=doc_duplicate_reuse telegram_id=%s doc_id=%s",
                    user.telegram_id,
                    existing.id,
                )
            return existing

        doc = FinancialDocument(
            user=user,
            source=source,
            title=title_override or validation.safe_filename,
            original_filename=validation.safe_filename,
            mime_type=mime_type or "",
            file_size_bytes=len(data),
            content_hash=content_hash,
            processing_status=ProcessingStatus.PROCESSING,
            metadata=dict(extra_metadata or {}),
        )
        doc.file.save(validation.safe_filename, ContentFile(data), save=False)
        doc.save()

        try:
            self._process(doc, data=data, filename=validation.safe_filename, mime_type=mime_type)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "event=doc_pipeline_failed telegram_id=%s doc_id=%s",
                user.telegram_id,
                doc.id,
            )
            doc.processing_status = ProcessingStatus.FAILED
            doc.error_message = type(exc).__name__[:200]
            doc.save(update_fields=["processing_status", "error_message", "updated_at"])
            raise

        return doc

    def reprocess(self, document: FinancialDocument) -> FinancialDocument:
        if not document.file:
            raise ValueError("Document has no file to reprocess.")
        document.file.open("rb")
        try:
            data = document.file.read()
        finally:
            document.file.close()
        document.processing_status = ProcessingStatus.PROCESSING
        document.error_message = ""
        document.save(update_fields=["processing_status", "error_message", "updated_at"])
        DocumentChunk.objects.filter(document=document).delete()
        self._process(
            document,
            data=data,
            filename=document.original_filename or document.title,
            mime_type=document.mime_type,
        )
        return document

    def reembed_chunks(self, document: FinancialDocument) -> FinancialDocument:
        """Re-embed all chunks for a document into one coherent vector space."""
        chunks = list(
            DocumentChunk.objects.filter(document=document).order_by("chunk_index")
        )
        if not chunks:
            return document
        vectors, backend, dim = embed_corpus([c.content for c in chunks])
        for ch, emb in zip(chunks, vectors):
            ch.embedding = emb
        DocumentChunk.objects.bulk_update(chunks, ["embedding"], batch_size=100)
        meta = dict(document.metadata or {})
        meta["embedding_backend"] = backend
        meta["embedding_dim"] = dim
        document.metadata = meta
        document.save(update_fields=["metadata", "updated_at"])
        logger.info(
            "event=doc_reembed telegram_id=%s doc_id=%s chunks=%s backend=%s dim=%s",
            document.user.telegram_id,
            document.id,
            len(chunks),
            backend,
            dim,
        )
        return document

    def ensure_embedding_space(
        self, documents: list[FinancialDocument] | FinancialDocument
    ) -> list[FinancialDocument]:
        """
        Generic migration helper — any ready document whose chunk vectors are
        missing, mixed, or stuck in the local fallback space (while Gemini is
        configured) gets re-embedded into the current corpus space.
        """
        docs = documents if isinstance(documents, list) else [documents]
        out: list[FinancialDocument] = []
        for doc in docs:
            if doc.processing_status != ProcessingStatus.READY:
                out.append(doc)
                continue
            if self._needs_reembed(doc):
                self.reembed_chunks(doc)
                doc.refresh_from_db()
            out.append(doc)
        return out

    @staticmethod
    def _needs_reembed(
        document: FinancialDocument,
        *,
        allow_local_upgrade: bool = False,
    ) -> bool:
        chunks = list(DocumentChunk.objects.filter(document=document).only("embedding")[:80])
        if not chunks:
            return False
        dims = {len(c.embedding or []) for c in chunks}
        if len(dims) != 1:
            return True
        dim = next(iter(dims))
        meta = document.metadata or {}
        meta_dim = meta.get("embedding_dim")
        meta_backend = meta.get("embedding_backend")
        if meta_dim and int(meta_dim) != dim:
            return True
        # Legacy docs ingested before embedding_backend was stamped — migrate once.
        if not meta_backend:
            return True
        # On explicit re-upload, retry upgrading an intentional local fallback
        # when Gemini embeddings are available again.
        if allow_local_upgrade and meta_backend == "local":
            api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
            force_local = bool(getattr(settings, "DOCUMENT_EMBEDDING_FORCE_LOCAL", False))
            if api_key and not force_local and dim == LOCAL_DIM:
                return True
        return False

    def _process(
        self,
        doc: FinancialDocument,
        *,
        data: bytes,
        filename: str,
        mime_type: str,
    ) -> None:
        parsed = parse_bytes(data=data, filename=filename, mime_type=mime_type)
        if parsed.error:
            friendly = {
                "empty": "I couldn't find readable text in that file.",
                "corrupt_pdf": "That PDF looks corrupted or unreadable.",
                "scanned_or_empty": (
                    "This looks like a scanned or image-only PDF with little extractable text. "
                    "I can't run OCR in this build — upload a text-based PDF, or export to "
                    "TXT/Markdown and I'll analyze that."
                ),
                "undecodable": "I couldn't decode that text file.",
                "pdf_backend_missing": "PDF support isn't available in this environment.",
                "docx_backend_missing": "DOCX support isn't available in this environment.",
                "pptx_backend_missing": "PowerPoint text extraction isn't available here.",
                "corrupt_docx": "That Word file looks corrupted or unreadable.",
                "corrupt_pptx": "That PowerPoint file looks corrupted or unreadable.",
            }.get(parsed.error, "I couldn't process that document.")
            raise ValueError(friendly)

        meta = extract_metadata(
            filename=filename,
            text=parsed.full_text,
            page_count=parsed.page_count,
        )
        drafts = chunk_pages(parsed.pages)
        if not drafts:
            raise ValueError("I couldn't find enough readable content to analyze.")

        # Cap extremely large docs for hackathon latency (still supports 300+ pages via chunking)
        max_chunks = 400
        if len(drafts) > max_chunks:
            drafts = drafts[:max_chunks]
            meta["truncated_chunks"] = True

        embeddings, backend, dim = embed_corpus([d.content for d in drafts])
        meta["embedding_backend"] = backend
        meta["embedding_dim"] = dim
        if backend == "local" and (getattr(settings, "GEMINI_API_KEY", "") or "").strip():
            logger.warning(
                "event=embed_local_corpus telegram_id=%s chunks=%s dim=%s",
                doc.user.telegram_id,
                len(embeddings),
                dim,
            )

        with transaction.atomic():
            DocumentChunk.objects.filter(document=doc).delete()
            rows = []
            for draft, emb in zip(drafts, embeddings):
                rows.append(
                    DocumentChunk(
                        document=doc,
                        chunk_index=draft.chunk_index,
                        content=draft.content,
                        page_start=draft.page_start,
                        page_end=draft.page_end,
                        section=draft.section,
                        token_estimate=max(1, len(draft.content) // 4),
                        embedding=emb,
                        metadata=draft.metadata,
                    )
                )
            DocumentChunk.objects.bulk_create(rows, batch_size=100)

            # Store a bounded excerpt for debugging — never log full text
            excerpt = (parsed.full_text or "")[:20000]
            doc.extracted_text = excerpt
            doc.page_count = parsed.page_count
            doc.metadata = {**(doc.metadata or {}), **meta}
            if meta.get("suggested_title"):
                doc.title = meta["suggested_title"]
            doc.processing_status = ProcessingStatus.READY
            doc.error_message = ""
            doc.save(
                update_fields=[
                    "extracted_text",
                    "page_count",
                    "metadata",
                    "title",
                    "processing_status",
                    "error_message",
                    "updated_at",
                ]
            )

        logger.info(
            "event=doc_ready telegram_id=%s doc_id=%s pages=%s chunks=%s backend=%s dim=%s",
            doc.user.telegram_id,
            doc.id,
            doc.page_count,
            len(drafts),
            backend,
            dim,
        )
