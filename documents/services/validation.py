"""Upload validation — size, type, filename sanitization."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from django.conf import settings


ALLOWED_MIME = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/octet-stream",  # Telegram often sends this for PDFs
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/msword",
}


@dataclass
class ValidationResult:
    ok: bool
    error: str = ""
    extension: str = ""
    safe_filename: str = ""


def sanitize_filename(name: str) -> str:
    base = os.path.basename(name or "document")
    base = re.sub(r"[^\w.\- ()]+", "_", base, flags=re.UNICODE).strip("._ ")
    if not base:
        base = "document"
    return base[:180]


def validate_upload(
    *,
    filename: str,
    file_size: int,
    mime_type: str = "",
) -> ValidationResult:
    safe = sanitize_filename(filename)
    ext = os.path.splitext(safe)[1].lower()
    allowed = tuple(getattr(settings, "DOCUMENT_ALLOWED_EXTENSIONS", (".pdf", ".txt", ".md")))
    max_mb = int(getattr(settings, "DOCUMENT_MAX_UPLOAD_MB", 25))
    max_bytes = max_mb * 1024 * 1024

    if file_size <= 0:
        return ValidationResult(ok=False, error="That file looks empty — try another copy.")
    if file_size > max_bytes:
        return ValidationResult(
            ok=False,
            error=(
                f"That file is over the {max_mb} MB limit I can process reliably right now. "
                f"Split it into a smaller section, compress it, or export a text/Markdown excerpt "
                f"(under {max_mb} MB) and I'll dig in."
            ),
        )
    if ext not in allowed:
        return ValidationResult(
            ok=False,
            error="I can read PDF, TXT, Markdown, DOCX, and PPTX for now. Send one of those and I'll dig in.",
        )
    mime = (mime_type or "").lower().strip()
    if mime and mime not in ALLOWED_MIME and not mime.startswith("text/"):
        # Soft check — Telegram mime can be odd; extension is authoritative
        if ext == ".pdf" and "pdf" not in mime and mime != "application/octet-stream":
            return ValidationResult(
                ok=False,
                error="That doesn't look like a readable PDF/TXT/Markdown file.",
            )
    return ValidationResult(ok=True, extension=ext, safe_filename=safe)
