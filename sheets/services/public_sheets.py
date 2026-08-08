"""Anonymous / public Google Sheets access (no OAuth).

Uses the spreadsheet export endpoint. Works when the sheet is shared as
"Anyone with the link" (viewer) or published to the web.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

from sheets.services.sheet_client import WorkbookPayload, _hash_values

logger = logging.getLogger("atlas.sheets.public")

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_MAX_SHEETS = 8
_MAX_ROWS = 200
_MAX_COLS = 26


class SheetAccessError(Exception):
    """Typed failure opening a Google Sheet."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # auth_required|not_found|permission_denied|temporary|empty|invalid
        self.message = message


@dataclass
class PublicLoadResult:
    payload: WorkbookPayload | None = None
    error: SheetAccessError | None = None


def load_public_workbook(spreadsheet_id: str) -> PublicLoadResult:
    """Try to read a spreadsheet without user OAuth."""
    sid = (spreadsheet_id or "").strip()
    if not sid or len(sid) < 20:
        return PublicLoadResult(
            error=SheetAccessError("invalid", "That doesn't look like a valid spreadsheet ID.")
        )
    try:
        payload = _fetch_xlsx(sid)
        if payload is None:
            payload = _fetch_csv_fallback(sid)
        if payload is None:
            return PublicLoadResult(
                error=SheetAccessError(
                    "auth_required",
                    "This spreadsheet needs Google authorization to read.",
                )
            )
        if not any(payload.values_by_sheet.values()):
            return PublicLoadResult(
                error=SheetAccessError("empty", "This spreadsheet looks empty.")
            )
        logger.info(
            "event=public_sheet_ok spreadsheet_id=%s sheets=%s",
            sid[:12],
            len(payload.sheet_names),
        )
        return PublicLoadResult(payload=payload)
    except SheetAccessError as exc:
        return PublicLoadResult(error=exc)
    except httpx.TimeoutException:
        return PublicLoadResult(
            error=SheetAccessError(
                "temporary",
                "Google Sheets timed out. Please try again in a moment.",
            )
        )
    except httpx.HTTPError:
        return PublicLoadResult(
            error=SheetAccessError(
                "temporary",
                "I couldn't reach Google Sheets just now. Please try again shortly.",
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("event=public_sheet_failed err=%s", type(exc).__name__)
        return PublicLoadResult(
            error=SheetAccessError(
                "temporary",
                "Something went wrong reading that spreadsheet. Please try again.",
            )
        )


def _fetch_xlsx(sid: str) -> WorkbookPayload | None:
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=xlsx"
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        resp = client.get(url)
    return _interpret_export_response(sid, resp, expect="xlsx")


def _fetch_csv_fallback(sid: str) -> WorkbookPayload | None:
    url = f"https://docs.google.com/spreadsheets/d/{sid}/export?format=csv"
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        resp = client.get(url)
    payload = _interpret_export_response(sid, resp, expect="csv")
    return payload


def _interpret_export_response(
    sid: str, resp: httpx.Response, *, expect: str
) -> WorkbookPayload | None:
    status = resp.status_code
    ct = (resp.headers.get("content-type") or "").lower()
    text_head = (resp.text or "")[:800] if "text" in ct or "html" in ct else ""
    final_url = str(resp.url)

    if status == 404:
        raise SheetAccessError(
            "not_found",
            "I couldn't find that Google Sheet — it may have been deleted or the link is wrong.",
        )
    if status in {401, 403}:
        raise SheetAccessError(
            "permission_denied",
            "I don't have permission to read that spreadsheet.",
        )
    if status >= 500:
        raise SheetAccessError(
            "temporary",
            "Google Sheets had a temporary error. Please try again shortly.",
        )

    # Login wall / private sheet
    if (
        "accounts.google.com" in final_url
        or "ServiceLogin" in text_head
        or "signin" in final_url.lower()
        or ("text/html" in ct and ("Sign in" in text_head or "Google Accounts" in text_head))
    ):
        raise SheetAccessError(
            "auth_required",
            "This spreadsheet is private. Connect Google so Atlas can read it with your account.",
        )

    if status != 200:
        raise SheetAccessError(
            "temporary",
            f"Google Sheets returned an unexpected response ({status}).",
        )

    if expect == "xlsx":
        if "spreadsheetml" in ct or "officedocument" in ct or resp.content[:2] == b"PK":
            return _parse_xlsx(sid, resp.content)
        if "text/html" in ct:
            raise SheetAccessError(
                "auth_required",
                "This spreadsheet is private. Connect Google so Atlas can read it with your account.",
            )
        return None

    # csv
    if "text/csv" in ct or "application/csv" in ct or (
        "text/plain" in ct and "," in (resp.text or "")[:200]
    ):
        rows = list(csv.reader(io.StringIO(resp.text)))
        rows = [r[:_MAX_COLS] for r in rows[:_MAX_ROWS]]
        values = {"Sheet1": rows}
        return WorkbookPayload(
            id=sid,
            title="Google Sheet",
            sheet_names=["Sheet1"],
            values_by_sheet=values,
            content_hash=_hash_values(values),
        )
    if "text/html" in ct:
        raise SheetAccessError(
            "auth_required",
            "This spreadsheet is private. Connect Google so Atlas can read it with your account.",
        )
    return None


def _parse_xlsx(sid: str, content: bytes) -> WorkbookPayload:
    z = zipfile.ZipFile(io.BytesIO(content))
    shared = _shared_strings(z)
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets_meta: list[tuple[str, str]] = []
    for sheet in wb.findall("m:sheets/m:sheet", _NS):
        name = sheet.attrib.get("name") or "Sheet"
        rid = sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        if rid:
            sheets_meta.append((name, rid))

    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target: dict[str, str] = {}
    for rel in rels:
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if rid and target:
            rid_to_target[rid] = target.lstrip("/")

    values_by_sheet: dict[str, list[list[Any]]] = {}
    title = "Google Sheet"
    # Try doc props for title
    if "docProps/core.xml" in z.namelist():
        try:
            core = ET.fromstring(z.read("docProps/core.xml"))
            for el in core.iter():
                if el.tag.endswith("title") and (el.text or "").strip():
                    title = el.text.strip()[:512]
                    break
        except Exception:  # noqa: BLE001
            pass

    for name, rid in sheets_meta[:_MAX_SHEETS]:
        target = rid_to_target.get(rid) or ""
        path = target if target.startswith("xl/") else f"xl/{target}"
        if path not in z.namelist():
            continue
        rows = _parse_sheet_xml(z.read(path), shared)
        values_by_sheet[name] = rows
        if title == "Google Sheet" and name:
            title = name

    if not values_by_sheet and "xl/worksheets/sheet1.xml" in z.namelist():
        values_by_sheet["Sheet1"] = _parse_sheet_xml(z.read("xl/worksheets/sheet1.xml"), shared)

    if not values_by_sheet:
        raise SheetAccessError("empty", "This spreadsheet looks empty.")

    # Prefer workbook-level title from first non-empty sheet name when doc title missing
    if title in {"Google Sheet", sheets_meta[0][0] if sheets_meta else "Google Sheet"}:
        # Keep sheet tab name only if no better title; Class Data sample uses tab as identity
        if sheets_meta:
            title = sheets_meta[0][0]

    return WorkbookPayload(
        id=sid,
        title=title[:512],
        sheet_names=list(values_by_sheet.keys()),
        values_by_sheet=values_by_sheet,
        content_hash=_hash_values(values_by_sheet),
    )


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall("m:si", _NS):
        texts = [t.text or "" for t in si.findall(".//m:t", _NS)]
        out.append("".join(texts))
    return out


def _parse_sheet_xml(raw: bytes, shared: list[str]) -> list[list[Any]]:
    root = ET.fromstring(raw)
    rows_out: list[list[Any]] = []
    for row in root.findall("m:sheetData/m:row", _NS)[:_MAX_ROWS]:
        cells: dict[int, Any] = {}
        max_idx = 0
        for c in row.findall("m:c", _NS):
            ref = c.attrib.get("r") or "A1"
            col = _col_index(ref)
            max_idx = max(max_idx, col)
            t = c.attrib.get("t")
            v = c.find("m:v", _NS)
            if v is None or v.text is None:
                cells[col] = ""
                continue
            if t == "s":
                try:
                    cells[col] = shared[int(v.text)]
                except (ValueError, IndexError):
                    cells[col] = v.text
            elif t == "inlineStr":
                is_el = c.find("m:is", _NS)
                texts = [t.text or "" for t in (is_el.findall(".//m:t", _NS) if is_el is not None else [])]
                cells[col] = "".join(texts)
            else:
                cells[col] = v.text
        if max_idx >= _MAX_COLS:
            max_idx = _MAX_COLS - 1
        line = [cells.get(i, "") for i in range(max_idx + 1)]
        rows_out.append(line)
    return rows_out


_COL_RE = re.compile(r"^([A-Z]+)")


def _col_index(cell_ref: str) -> int:
    m = _COL_RE.match(cell_ref.upper())
    if not m:
        return 0
    letters = m.group(1)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return max(0, n - 1)
