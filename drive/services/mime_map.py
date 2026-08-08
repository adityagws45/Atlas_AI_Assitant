"""Drive MIME helpers — supported import types."""

from __future__ import annotations

# Google Workspace → export MIME
GOOGLE_EXPORT = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}

# Binary downloads we can ingest
DOWNLOADABLE = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
}

# Metadata only (no content import in M6)
METADATA_ONLY = {
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}

FOLDER = "application/vnd.google-apps.folder"


def is_folder(mime: str) -> bool:
    return (mime or "") == FOLDER


def is_metadata_only(mime: str) -> bool:
    return (mime or "") in METADATA_ONLY


def is_supported_for_import(mime: str, name: str = "") -> bool:
    mime = mime or ""
    if is_folder(mime) or is_metadata_only(mime):
        return False
    if mime in GOOGLE_EXPORT or mime in DOWNLOADABLE:
        return True
    lower = (name or "").lower()
    return lower.endswith(
        (".pdf", ".txt", ".md", ".markdown", ".docx", ".pptx")
    )


def export_spec(mime: str) -> tuple[str, str] | None:
    return GOOGLE_EXPORT.get(mime)


def suggested_extension(mime: str, name: str = "") -> str:
    if mime in DOWNLOADABLE:
        return DOWNLOADABLE[mime]
    exp = GOOGLE_EXPORT.get(mime)
    if exp:
        return exp[1]
    lower = (name or "").lower()
    for ext in (".pdf", ".txt", ".md", ".markdown", ".docx", ".pptx"):
        if lower.endswith(ext):
            return ext
    return ".txt"
