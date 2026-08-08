"""High-level retriever with document memory awareness."""

from __future__ import annotations

import re
from uuid import UUID

from documents.models import DocumentChunk
from documents.services.vector_store import (
    RetrievedChunk,
    VectorStore,
    _wants_financials,
    _wants_risks,
    serialize_chunks,
)


class DocumentRetriever:
    def __init__(self, store: VectorStore | None = None) -> None:
        self.store = store or VectorStore()

    def retrieve_for_question(
        self,
        *,
        user_id: UUID | str,
        question: str,
        document_ids: list[str] | None = None,
        company: str | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        hits = self.store.search(
            user_id=user_id,
            query=question,
            document_ids=document_ids,
            company=company,
            top_k=top_k,
        )
        # Pull neighboring pages when the seed hit is a section header —
        # Item 1A / Statements of Operations often span many short chunks.
        if hits and (_wants_risks(question) or _wants_financials(question)):
            hits = self._expand_neighbors(hits, question=question, limit=top_k or 10)
        return hits

    def _expand_neighbors(
        self,
        hits: list[RetrievedChunk],
        *,
        question: str,
        limit: int,
    ) -> list[RetrievedChunk]:
        seen = {h.chunk_id for h in hits}
        out = list(hits)
        seed = hits[0]
        page = int(seed.page_start or 0)
        if not page:
            return out[:limit]

        # Risk: expand forward through Item 1A body; financials: ops statement pages.
        if _wants_risks(question):
            page_lo, page_hi = max(1, page - 1), page + 6
        else:
            page_lo, page_hi = max(1, page - 1), page + 2

        neighbors = (
            DocumentChunk.objects.filter(
                document_id=seed.document_id,
                page_start__gte=page_lo,
                page_start__lte=page_hi,
            )
            .select_related("document")
            .order_by("chunk_index")[:24]
        )
        for ch in neighbors:
            cid = str(ch.id)
            if cid in seen:
                continue
            text = (ch.content or "").lower()
            if _wants_risks(question):
                useful = bool(
                    re.search(
                        r"risk|adversely|competitive|geopolit|tariff|supplier|"
                        r"macroeconomic|litigation|regulatory|cyber",
                        text,
                    )
                )
            else:
                useful = bool(
                    re.search(
                        r"net sales|net income|operations|revenue|in millions",
                        text,
                    )
                )
            if not useful:
                continue
            doc = ch.document
            out.append(
                RetrievedChunk(
                    chunk_id=cid,
                    document_id=str(doc.id),
                    document_title=doc.title,
                    content=ch.content,
                    page_start=ch.page_start,
                    page_end=ch.page_end,
                    section=ch.section or "",
                    score=float(seed.score) * 0.92,
                    company=doc.company,
                    metadata=dict(ch.metadata or {}),
                )
            )
            seen.add(cid)
            if len(out) >= max(limit, 12):
                break
        return out[: max(limit, 12)]

    def as_research_packet(self, chunks: list[RetrievedChunk]) -> dict:
        return {
            "ok": True,
            "passages": serialize_chunks(chunks),
            "passage_count": len(chunks),
        }
