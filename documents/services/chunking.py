"""Intelligent chunking — preserve headings, lists, tables, sentences."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

from documents.services.metadata import detect_section
from documents.services.parser import PageText


@dataclass
class ChunkDraft:
    content: str
    chunk_index: int
    page_start: int | None
    page_end: int | None
    section: str
    metadata: dict


def chunk_pages(
    pages: list[PageText],
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[ChunkDraft]:
    size = int(chunk_size or getattr(settings, "DOCUMENT_CHUNK_SIZE", 1100))
    ov = int(overlap or getattr(settings, "DOCUMENT_CHUNK_OVERLAP", 160))
    ov = max(0, min(ov, size // 3))

    units: list[tuple[int, str]] = []
    for page in pages:
        blocks = _split_preserving_structure(page.text or "")
        for block in blocks:
            if block.strip():
                units.append((page.page, block.strip()))

    if not units:
        return []

    drafts: list[ChunkDraft] = []
    buf: list[str] = []
    page_nums: list[int] = []
    buf_len = 0
    idx = 0

    def flush() -> None:
        nonlocal buf, page_nums, buf_len, idx
        if not buf:
            return
        content = "\n\n".join(buf).strip()
        if not content:
            buf, page_nums, buf_len = [], [], 0
            return
        section = detect_section(content)
        drafts.append(
            ChunkDraft(
                content=content,
                chunk_index=idx,
                page_start=min(page_nums) if page_nums else None,
                page_end=max(page_nums) if page_nums else None,
                section=section,
                metadata={"chars": len(content)},
            )
        )
        idx += 1
        if ov and content:
            # Keep trailing overlap as soft context for next chunk
            tail = content[-ov:]
            # Prefer starting at sentence/newline boundary
            cut = max(tail.find(". "), tail.find("\n"))
            if cut > 20:
                tail = tail[cut + 1 :].lstrip()
            buf = [tail] if tail else []
            buf_len = len(tail)
            page_nums = [page_nums[-1]] if page_nums else []
        else:
            buf, page_nums, buf_len = [], [], 0

    for page_no, block in units:
        block_len = len(block) + (2 if buf else 0)
        # Never split tables / bullet lists mid-block if they fit
        if buf_len + block_len > size and buf:
            flush()
        if len(block) > size * 1.5:
            # Oversized block: split on sentences only
            for piece in _sentence_slices(block, size):
                if buf_len + len(piece) > size and buf:
                    flush()
                buf.append(piece)
                page_nums.append(page_no)
                buf_len += len(piece) + 2
            continue
        buf.append(block)
        page_nums.append(page_no)
        buf_len += block_len

    flush()
    return drafts


def _split_preserving_structure(text: str) -> list[str]:
    if not text.strip():
        return []
    # Split on blank lines into paragraphs/blocks
    parts = re.split(r"\n\s*\n", text)
    out: list[str] = []
    for part in parts:
        p = part.strip()
        if not p:
            continue
        # Keep bullet lists / tables together
        if _looks_like_list_or_table(p):
            out.append(p)
            continue
        # Heading + following paragraph stay together when short
        lines = p.split("\n")
        if len(lines) >= 2 and _looks_like_heading(lines[0]) and len(p) < 900:
            out.append(p)
            continue
        out.append(p)
    return out


def _looks_like_heading(line: str) -> bool:
    s = line.strip()
    if len(s) > 120:
        return False
    if re.match(r"^(item\s+\d+|risk factors|md&a|note\s+\d+)", s, re.IGNORECASE):
        return True
    return s.isupper() and len(s.split()) <= 12


def _looks_like_list_or_table(text: str) -> bool:
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if len(lines) < 2:
        return False
    bullets = sum(1 for ln in lines if re.match(r"^(\-|\*|•|\d+[\.\)])\s+", ln.strip()))
    if bullets >= max(2, len(lines) // 2):
        return True
    pipes = sum(1 for ln in lines if ln.count("|") >= 2)
    return pipes >= 2


def _sentence_slices(text: str, size: int) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    cur = ""
    for sent in sentences:
        if not sent:
            continue
        if len(cur) + len(sent) + 1 <= size:
            cur = f"{cur} {sent}".strip()
        else:
            if cur:
                chunks.append(cur)
            if len(sent) > size:
                for i in range(0, len(sent), size):
                    chunks.append(sent[i : i + size])
                cur = ""
            else:
                cur = sent
    if cur:
        chunks.append(cur)
    return chunks
