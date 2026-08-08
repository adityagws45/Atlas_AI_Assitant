"""Embedding generation — Gemini when available, local lexical fallback.

CRITICAL: document chunks and queries for a given corpus MUST share one
embedding space (same backend + dimensionality). Mixing 384-local with
3072-Gemini yields cosine similarity of 0.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import time
from typing import Sequence

from django.conf import settings

logger = logging.getLogger("atlas.documents.embeddings")

LOCAL_DIM = 384
GEMINI_DIM = 3072  # gemini-embedding-001 default output size
_LOCAL_DIM = LOCAL_DIM  # backward-compatible alias

_SPACE_LOCAL = "local"
_SPACE_GEMINI = "gemini"


def embed_texts(
    texts: list[str],
    *,
    model: str | None = None,
    task_type: str = "retrieval_document",
    force_local: bool = False,
) -> list[list[float]]:
    """Embed texts. Prefer Gemini unless ``force_local`` is set."""
    if not texts:
        return []
    results: list[list[float] | None] = [None] * len(texts)
    missing: list[tuple[int, str]] = []
    for i, t in enumerate(texts):
        key = _cache_key(t, model, task_type=task_type, force_local=force_local)
        cached = _cache_get(key)
        if cached is not None:
            results[i] = cached
        else:
            missing.append((i, t))

    if missing:
        vectors = _embed_batch(
            [t for _, t in missing],
            model=model,
            task_type=task_type,
            force_local=force_local,
        )
        for (i, t), vec in zip(missing, vectors):
            results[i] = vec
            _cache_set(
                _cache_key(t, model, task_type=task_type, force_local=force_local),
                vec,
            )

    return [r or _local_embed("") for r in results]


def embed_corpus(
    texts: list[str],
    *,
    model: str | None = None,
    task_type: str = "retrieval_document",
) -> tuple[list[list[float]], str, int]:
    """
    Embed an entire document corpus into ONE vector space.

    Tries Gemini (batched + retries). On failure, falls back to local for
    *every* text so a document never stores mixed dimensions.
    Returns ``(vectors, backend, dim)`` where backend is ``gemini`` or ``local``.
    """
    if not texts:
        return [], _SPACE_LOCAL, LOCAL_DIM

    force_local = bool(getattr(settings, "DOCUMENT_EMBEDDING_FORCE_LOCAL", False))
    if force_local:
        vecs = [_local_embed(t) for t in texts]
        return vecs, _SPACE_LOCAL, LOCAL_DIM

    api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not api_key:
        vecs = [_local_embed(t) for t in texts]
        return vecs, _SPACE_LOCAL, LOCAL_DIM

    try:
        vecs = _gemini_embed_corpus(texts, model=model, task_type=task_type)
        dims = {len(v) for v in vecs}
        if len(dims) != 1:
            raise RuntimeError(f"mixed gemini dims: {dims}")
        dim = next(iter(dims))
        logger.info(
            "event=embed_corpus_ok backend=gemini chunks=%s dim=%s",
            len(vecs),
            dim,
        )
        return vecs, _SPACE_GEMINI, dim
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "event=embed_corpus_fallback_local err=%s count=%s",
            type(exc).__name__,
            len(texts),
        )
        vecs = [_local_embed(t) for t in texts]
        return vecs, _SPACE_LOCAL, LOCAL_DIM


def embed_query(
    text: str,
    *,
    model: str | None = None,
    reference_dim: int | None = None,
    backend: str | None = None,
) -> list[float]:
    """
    Embed a search query in the SAME vector space as stored document chunks.

    Pass ``reference_dim`` / ``backend`` from the active corpus so cosine
    similarity is never silently zero across mismatched spaces.
    """
    force_local = False
    if backend == _SPACE_LOCAL or (reference_dim is not None and reference_dim == LOCAL_DIM):
        force_local = True
    elif backend == _SPACE_GEMINI:
        force_local = False
    elif reference_dim is not None and reference_dim == GEMINI_DIM:
        force_local = False

    return embed_texts(
        [text],
        model=model,
        task_type="retrieval_query",
        force_local=force_local,
    )[0]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        na += fx * fx
        nb += fy * fy
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / math.sqrt(na * nb)


def _embed_batch(
    texts: list[str],
    *,
    model: str | None,
    task_type: str,
    force_local: bool,
) -> list[list[float]]:
    if force_local:
        return [_local_embed(t) for t in texts]

    api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    model_name = (
        model or getattr(settings, "GEMINI_EMBEDDING_MODEL", "") or "gemini-embedding-001"
    ).strip()
    if api_key:
        try:
            return _gemini_embed(
                texts, api_key=api_key, model_name=model_name, task_type=task_type
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=embed_gemini_fallback err=%s count=%s task=%s",
                type(exc).__name__,
                len(texts),
                task_type,
            )
    return [_local_embed(t) for t in texts]


def _gemini_embed_corpus(
    texts: list[str],
    *,
    model: str | None,
    task_type: str,
) -> list[list[float]]:
    """Embed many texts via Gemini in small batches with retries. All-or-nothing."""
    api_key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    model_name = (
        model or getattr(settings, "GEMINI_EMBEDDING_MODEL", "") or "gemini-embedding-001"
    ).strip()
    batch_size = int(getattr(settings, "DOCUMENT_EMBEDDING_BATCH_SIZE", 8) or 8)
    out: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                part = _gemini_embed(
                    batch,
                    api_key=api_key,
                    model_name=model_name,
                    task_type=task_type,
                )
                # Reject silent local fallbacks inside batch — treat as failure
                if any(len(v) == LOCAL_DIM for v in part):
                    raise RuntimeError("gemini batch returned local-dim vector")
                out.extend(part)
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                sleep_s = min(20.0, 1.5 * (2**attempt))
                logger.warning(
                    "event=embed_gemini_retry attempt=%s err=%s sleep=%.1f",
                    attempt + 1,
                    type(exc).__name__,
                    sleep_s,
                )
                time.sleep(sleep_s)
        if last_err is not None:
            raise last_err
        # Gentle pacing between batches to avoid ResourceExhausted
        if start + batch_size < len(texts):
            time.sleep(0.35)
    return out


def _gemini_embed(
    texts: list[str],
    *,
    api_key: str,
    model_name: str,
    task_type: str = "retrieval_document",
) -> list[list[float]]:
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    out: list[list[float]] = []
    model_id = model_name if model_name.startswith("models/") else f"models/{model_name}"
    for text in texts:
        trimmed = (text or "")[:6000]
        result = genai.embed_content(
            model=model_id,
            content=trimmed,
            task_type=task_type,
        )
        emb = result.get("embedding") if isinstance(result, dict) else getattr(result, "embedding", None)
        if not emb:
            out.append(_local_embed(trimmed))
        else:
            out.append([float(x) for x in emb])
    return out


def _local_embed(text: str) -> list[float]:
    """Deterministic hashing embedder for offline / test / API-failure paths."""
    vec = [0.0] * LOCAL_DIM
    tokens = re.findall(r"[a-z0-9$%]{2,}", (text or "").lower())
    if not tokens:
        return vec
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        idx = int.from_bytes(h[:2], "big") % LOCAL_DIM
        sign = 1.0 if h[2] % 2 == 0 else -1.0
        weight = 1.0 + (h[3] / 255.0)
        vec[idx] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cache_key(
    text: str,
    model: str | None,
    *,
    task_type: str = "retrieval_document",
    force_local: bool = False,
) -> str:
    m = (model or getattr(settings, "GEMINI_EMBEDDING_MODEL", "local") or "local")[:40]
    digest = hashlib.sha256((text or "")[:4000].encode("utf-8")).hexdigest()[:24]
    space = "local" if force_local else "auto"
    return f"docemb:v2:{m}:{task_type}:{space}:{digest}"


def _cache_get(key: str):
    from django.core.cache import cache

    return cache.get(key)


def _cache_set(key: str, vec: list[float]) -> None:
    from django.core.cache import cache

    cache.set(key, vec, timeout=getattr(settings, "CACHE_TTL_DOCUMENT", 3600))
