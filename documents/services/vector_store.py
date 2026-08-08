"""In-DB vector store — cosine search over DocumentChunk embeddings."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from django.conf import settings
from django.core.cache import cache

from documents.models import DocumentChunk, FinancialDocument, ProcessingStatus
from documents.services.embeddings import LOCAL_DIM, cosine_similarity, embed_query

logger = logging.getLogger("atlas.documents.vector")


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_title: str
    content: str
    page_start: int | None
    page_end: int | None
    section: str
    score: float
    company: str
    metadata: dict


# Phrase → strong lexical signal for 10-K style filings
_PHRASE_GROUPS: tuple[tuple[tuple[str, ...], float], ...] = (
    (
        (
            "consolidated statements of operations",
            "consolidated statement of operations",
            "statements of operations",
            "net sales",
            "total net sales",
            "net income",
            "earnings per share",
            "earnings",
            "revenue",
        ),
        0.55,
    ),
    (
        (
            "item 1a",
            "item 1a.",
            "risk factors",
            "macroeconomic and industry risks",
            "business risks",
            "legal and regulatory risks",
        ),
        0.50,
    ),
    (
        (
            "consolidated balance sheets",
            "consolidated statements of cash flows",
            "management's discussion",
            "md&a",
        ),
        0.35,
    ),
)


class VectorStore:
    """Semantic retrieval over stored chunk embeddings (no external vector DB)."""

    def search(
        self,
        *,
        user_id: UUID | str,
        query: str,
        top_k: int | None = None,
        document_ids: list[str] | None = None,
        company: str | None = None,
        page: int | None = None,
    ) -> list[RetrievedChunk]:
        k = int(top_k or getattr(settings, "DOCUMENT_TOP_K", 6))
        cache_key = (
            f"docret:v4:{user_id}:{hash(query) & 0xFFFFFFFF}:"
            f"{','.join(document_ids or [])}:{company}:{page}:{k}"
        )
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        qs = DocumentChunk.objects.filter(
            document__user_id=user_id,
            document__processing_status=ProcessingStatus.READY,
        ).select_related("document")
        if document_ids:
            qs = qs.filter(document_id__in=document_ids)
        if company:
            qs = qs.filter(document__metadata__company__iexact=company)
        if page is not None:
            qs = qs.filter(page_start__lte=page, page_end__gte=page)

        # Cap candidates for latency on huge corpora
        chunks = list(qs.order_by("-document__updated_at", "chunk_index")[:1200])
        if not chunks:
            return []

        # Resolve ONE embedding space for this search. Never mix 384 and 3072.
        ref_dim, backend = _resolve_corpus_space(chunks)
        aligned = [ch for ch in chunks if len(ch.embedding or []) == ref_dim]
        skipped = len(chunks) - len(aligned)
        if skipped:
            logger.warning(
                "event=vector_skip_dim_mismatch user=%s kept=%s skipped=%s ref_dim=%s",
                user_id,
                len(aligned),
                skipped,
                ref_dim,
            )
        chunks = aligned
        if not chunks:
            return []

        qvec = embed_query(query, reference_dim=ref_dim, backend=backend)
        if len(qvec) != ref_dim:
            # Force local alignment as last resort
            qvec = embed_query(query, reference_dim=LOCAL_DIM, backend="local")
            chunks = [ch for ch in chunks if len(ch.embedding or []) == len(qvec)]
            if not chunks:
                return []
            ref_dim = len(qvec)
            backend = "local"

        scored: list[tuple[float, DocumentChunk]] = []
        for ch in chunks:
            emb = ch.embedding or []
            sem = cosine_similarity(qvec, emb) if emb else 0.0
            lex = _lexical_score(query, ch.content, ch.section or "")
            # Hybrid: semantic + lexical (lexical carries financial/risk intent)
            score = (0.65 * sem) + lex
            scored.append((score, ch))

        scored.sort(key=lambda x: -x[0])
        # Diversify: avoid returning 8 near-duplicate TOC pages
        results: list[RetrievedChunk] = []
        seen_pages: set[int] = set()
        page_counts: dict[int, int] = {}
        for score, ch in scored:
            page_key = int(ch.page_start or 0)
            if page_key:
                if page_counts.get(page_key, 0) >= 2 and len(results) >= max(3, k // 2):
                    continue
            results.append(_to_retrieved(ch, score))
            if page_key:
                seen_pages.add(page_key)
                page_counts[page_key] = page_counts.get(page_key, 0) + 1
            if len(results) >= k:
                break

        cache.set(
            cache_key,
            results,
            timeout=min(180, getattr(settings, "CACHE_TTL_DOCUMENT", 3600)),
        )
        logger.info(
            "event=vector_search user=%s candidates=%s returned=%s ref_dim=%s backend=%s top=%.4f",
            user_id,
            len(chunks),
            len(results),
            ref_dim,
            backend,
            results[0].score if results else 0.0,
        )
        return results


def _resolve_corpus_space(chunks: list[DocumentChunk]) -> tuple[int, str]:
    """Pick ONE embedding space for this search; never mix dims in scoring."""
    from collections import Counter

    dims = Counter(len(ch.embedding or []) for ch in chunks if ch.embedding)
    if not dims:
        return LOCAL_DIM, "local"

    # Dominant observed dim among candidates (authoritative for this query set)
    dim, _ = dims.most_common(1)[0]
    backend = "local" if dim == LOCAL_DIM else "gemini"

    # If every candidate document agrees on stamped metadata, prefer that label
    backends = {
        (ch.document.metadata or {}).get("embedding_backend")
        for ch in chunks
        if (ch.document.metadata or {}).get("embedding_dim") == dim
    }
    backends.discard(None)
    if len(backends) == 1:
        backend = next(iter(backends))  # type: ignore[assignment]
    return dim, str(backend)


def _to_retrieved(ch: DocumentChunk, score: float) -> RetrievedChunk:
    doc: FinancialDocument = ch.document
    return RetrievedChunk(
        chunk_id=str(ch.id),
        document_id=str(doc.id),
        document_title=doc.title,
        content=ch.content,
        page_start=ch.page_start,
        page_end=ch.page_end,
        section=ch.section or "",
        score=float(score),
        company=doc.company,
        metadata=dict(ch.metadata or {}),
    )


_RISK_BODY_HEADERS = (
    "macroeconomic and industry risks",
    "business risks",
    "legal and regulatory risks",
    "operational risks",
    "financial risks",
)


def _lexical_score(query: str, content: str, section: str) -> float:
    """Hybrid lexical score — critical when embeddings are weak or mismatched."""
    q = (query or "").lower()
    c = (content or "").lower()
    s = (section or "").lower()
    if not q or not c:
        return 0.0

    score = 0.0
    # Phrase groups — apply when the query matches the theme (risk intent
    # also unlocks the risk-factor phrase group even if the user only said "risks").
    for phrases, weight in _PHRASE_GROUPS:
        is_risk_group = weight == 0.50 and any("risk" in p for p in phrases)
        if not any(p in q for p in phrases) and not (is_risk_group and _wants_risks(q)):
            continue
        if any(p in c or p in s for p in phrases):
            score += weight

    # Intent-specific boosts
    if _wants_financials(q):
        if "consolidated statements of operations" in c or "statements of operations" in c:
            score += 0.65
        if re.search(r"\bnet income\b", c) and re.search(r"\d", c):
            score += 0.40
        if re.search(r"\b(total )?net sales\b", c) and re.search(r"\d{2,}", c):
            score += 0.35
        if "in millions" in c and re.search(r"\b20[1-3]\d\b", c):
            score += 0.10

    if _wants_risks(q):
        toc_like = _is_toc_like(c)
        body_header = any(h in c or h in s for h in _RISK_BODY_HEADERS)
        adverse = bool(
            re.search(
                r"could (have|result|adversely)|materially adversely|material adverse",
                c,
            )
        )
        item_header = "item 1a" in c[:240] or "item 1a. risk factors" in c or "risk factors" in s

        # Prefer substantive Item 1A body over TOC / cover listings
        if body_header:
            score += 0.85
        if adverse and not toc_like:
            score += 0.55
        if item_header and not toc_like:
            score += 0.35
        if "risk factors" in c[:500] and not toc_like and len(c) > 500:
            score += 0.20
        # Soft page prior for typical Item 1A body in long 10-Ks (set via caller metadata later)
        if toc_like:
            score -= 0.70
        elif re.search(r"item\s+1a\.?\s+risk factors\s+\d+", c) and "macroeconomic" not in c:
            score -= 0.45

    # Generic token overlap
    q_tokens = _query_tokens(q)
    if q_tokens:
        overlap = sum(1 for t in q_tokens if t in c)
        score += min(0.20, 0.03 * overlap)

    return score


def _is_toc_like(c: str) -> bool:
    """Heuristic: table-of-contents / cover index rows, not Item 1A prose."""
    item_hits = len(re.findall(r"\bitem\s+\d+[a-z]?\.", c))
    if item_hits >= 4:
        return True
    if re.search(r"item\s+1a\.?\s+risk factors\s+\d+", c) and "macroeconomic" not in c:
        # TOC line like "Item 1A. Risk Factors 4" without body section headers
        if not any(h in c for h in _RISK_BODY_HEADERS) and "the company" not in c[:200]:
            return True
    return False


def _wants_financials(q: str) -> bool:
    return bool(
        re.search(
            r"\b(revenue|net sales|net income|earnings|profit|financial statements?|"
            r"statements? of operations|income statement|how much (did|does)|figure|"
            r"which page)\b",
            q,
            re.I,
        )
    )


def _wants_risks(q: str) -> bool:
    return bool(
        re.search(
            r"\b(risks?|risk factors?|item\s*1a|biggest risks?|material risks?|"
            r"what could go wrong|threats?)\b",
            q,
            re.I,
        )
    )


def _query_tokens(q: str) -> list[str]:
    stop = {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "in",
        "to",
        "for",
        "was",
        "were",
        "is",
        "are",
        "what",
        "which",
        "this",
        "that",
        "with",
        "from",
        "about",
        "company",
        "companies",
        "report",
        "mentioned",
        "give",
        "me",
        "three",
        "3",
        "biggest",
    }
    return [t for t in re.findall(r"[a-z0-9]{3,}", q.lower()) if t not in stop]


def serialize_chunks(chunks: list[RetrievedChunk]) -> list[dict[str, Any]]:
    """Public research packet — never expose embeddings."""
    out = []
    for ch in chunks:
        cite = []
        if ch.section:
            cite.append(ch.section)
        if ch.page_start:
            page = f"Page {ch.page_start}"
            if ch.page_end and ch.page_end != ch.page_start:
                page = f"Pages {ch.page_start}–{ch.page_end}"
            cite.append(page)
        out.append(
            {
                "document": ch.document_title,
                "company": ch.company,
                "source": " — ".join(cite) if cite else ch.document_title,
                "excerpt": ch.content[:1600],
                "page_start": ch.page_start,
                "page_end": ch.page_end,
                "relevance": round(ch.score, 4),
            }
        )
    return out
