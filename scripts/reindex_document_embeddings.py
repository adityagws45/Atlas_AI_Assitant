"""Generic re-index of document chunk embeddings into one coherent vector space.

Works for ANY FinancialDocument (any company/file/page-count) — not Apple-specific.

Usage:
  python scripts/reindex_document_embeddings.py --all-ready
  python scripts/reindex_document_embeddings.py --doc-id <uuid>
  python scripts/reindex_document_embeddings.py --page-count 80   # optional filter only
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
os.chdir(BASE)

import django

django.setup()

from django.core.cache import cache

from documents.models import DocumentChunk, FinancialDocument, ProcessingStatus
from documents.services.document_pipeline import DocumentPipeline
from documents.services.metadata import extract_metadata


def _fix_year_metadata(doc: FinancialDocument) -> None:
    meta = extract_metadata(
        filename=doc.original_filename or doc.title,
        text=doc.extracted_text or "",
        page_count=doc.page_count or 0,
    )
    doc.metadata = {**(doc.metadata or {}), **meta}
    if meta.get("suggested_title"):
        doc.title = meta["suggested_title"]
    doc.save(update_fields=["metadata", "title", "updated_at"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-id", default="")
    parser.add_argument("--page-count", type=int, default=0)
    parser.add_argument("--all-ready", action="store_true")
    args = parser.parse_args()

    qs = FinancialDocument.objects.filter(processing_status=ProcessingStatus.READY)
    if args.doc_id:
        qs = qs.filter(id=args.doc_id)
    elif args.page_count:
        qs = qs.filter(page_count=args.page_count)
    elif not args.all_ready:
        qs = qs.filter(page_count=80)

    pipe = DocumentPipeline()
    docs = list(qs.order_by("-created_at"))
    print(f"reindex targets: {len(docs)}")
    for doc in docs:
        _fix_year_metadata(doc)
        before = {
            len(c.embedding or [])
            for c in DocumentChunk.objects.filter(document=doc).iterator()
        }
        print(f"  doc={doc.id} title={doc.title!r} year={(doc.metadata or {}).get('year')} dims_before={before}")
        pipe.reembed_chunks(doc)
        after = {
            len(c.embedding or [])
            for c in DocumentChunk.objects.filter(document=doc).iterator()
        }
        print(
            f"  -> backend={(doc.metadata or {}).get('embedding_backend')} "
            f"dim={(doc.metadata or {}).get('embedding_dim')} dims_after={after}"
        )
    cache.clear()
    print("DONE reindex + cache clear")


if __name__ == "__main__":
    main()
