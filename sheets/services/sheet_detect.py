"""Automatic spreadsheet schema detection — finance-aware, no IDs exposed."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


TICKER_RE = re.compile(r"^[A-Z]{1,5}(\.[A-Z]{1,2})?$")
CURRENCY_RE = re.compile(r"^\$?\s?-?\d[\d,]*\.?\d*%?$|^-?\d[\d,]*\.?\d*$")
PCT_HINT = re.compile(r"%|pct|percent|return|p/?l|gain|weight|alloc", re.I)
DATE_HINT = re.compile(r"date|month|period|as of|day", re.I)
TICKER_HINT = re.compile(r"ticker|symbol|code", re.I)
SECTOR_HINT = re.compile(r"sector|industry|theme", re.I)
VALUE_HINT = re.compile(r"value|market|mv|position|notional|amount", re.I)
SHARES_HINT = re.compile(r"shares|qty|quantity|units", re.I)
COST_HINT = re.compile(r"cost|basis|avg|purchase", re.I)
PRICE_HINT = re.compile(r"price|last|close|px", re.I)
COMPANY_HINT = re.compile(r"company|name|holding|security", re.I)


@dataclass
class SheetSchema:
    sheet_name: str
    headers: list[str] = field(default_factory=list)
    kind: str = "unknown"  # portfolio|watchlist|timeseries|expense|budget|other
    columns: dict[str, str] = field(default_factory=dict)  # role -> header
    row_count: int = 0
    currency_like: bool = False
    has_tickers: bool = False
    missing_cells: int = 0
    notes: list[str] = field(default_factory=list)


def detect_workbook(values_by_sheet: dict[str, list[list[Any]]]) -> dict[str, Any]:
    schemas: list[dict[str, Any]] = []
    primary = "other"
    for name, rows in values_by_sheet.items():
        schema = detect_sheet(name, rows)
        schemas.append(_schema_dict(schema))
        if schema.kind == "portfolio":
            primary = "portfolio"
        elif schema.kind == "financials" and primary not in {"portfolio"}:
            primary = "financials"
        elif schema.kind == "watchlist" and primary not in {"portfolio", "financials"}:
            primary = "watchlist"
        elif schema.kind == "timeseries" and primary == "other":
            primary = "timeseries"
    return {
        "primary_kind": primary,
        "tabs": schemas,
        "tab_count": len(schemas),
    }


def detect_sheet(sheet_name: str, rows: list[list[Any]]) -> SheetSchema:
    if not rows:
        return SheetSchema(sheet_name=sheet_name, notes=["empty"])
    headers = [str(c).strip() for c in rows[0]]
    body = rows[1:]
    schema = SheetSchema(
        sheet_name=sheet_name,
        headers=headers,
        row_count=len(body),
    )
    roles: dict[str, str] = {}
    for h in headers:
        hl = h.lower()
        if TICKER_HINT.search(hl) and "ticker" not in roles:
            roles["ticker"] = h
        elif COMPANY_HINT.search(hl) and "company" not in roles:
            roles["company"] = h
        elif SECTOR_HINT.search(hl) and "sector" not in roles:
            roles["sector"] = h
        elif SHARES_HINT.search(hl) and "shares" not in roles:
            roles["shares"] = h
        elif COST_HINT.search(hl) and "cost" not in roles:
            roles["cost"] = h
        elif PRICE_HINT.search(hl) and "price" not in roles:
            roles["price"] = h
        elif VALUE_HINT.search(hl) and "value" not in roles:
            roles["value"] = h
        elif PCT_HINT.search(hl) and "pl" not in roles and ("p/l" in hl or "pl" in hl or "return" in hl):
            roles["pl"] = h
        elif PCT_HINT.search(hl) and ("weight" in hl or "alloc" in hl) and "weight" not in roles:
            roles["weight"] = h
        elif DATE_HINT.search(hl) and "date" not in roles:
            roles["date"] = h
    schema.columns = roles

    # Infer tickers from first column if unlabeled
    if "ticker" not in roles and body:
        col0 = [str(r[0]).strip().upper() for r in body if r]
        if col0 and sum(1 for t in col0 if TICKER_RE.match(t)) >= max(1, len(col0) // 2):
            roles["ticker"] = headers[0] if headers else "Ticker"
            schema.columns = roles
            schema.has_tickers = True
    if "ticker" in roles:
        schema.has_tickers = True

    missing = 0
    currency_hits = 0
    cells = 0
    for r in body:
        for c in r:
            cells += 1
            if c is None or str(c).strip() == "":
                missing += 1
            elif CURRENCY_RE.match(str(c).strip().replace(",", "")):
                currency_hits += 1
    schema.missing_cells = missing
    schema.currency_like = cells > 0 and currency_hits / max(1, cells) > 0.15

    title_l = sheet_name.lower()
    header_blob = " ".join(headers).lower()
    if {"ticker", "value"} <= set(roles) or {"ticker", "shares", "price"} <= set(roles):
        schema.kind = "portfolio"
    elif "ticker" in roles and ("thesis" in header_blob or "watch" in title_l):
        schema.kind = "watchlist"
    elif "date" in roles and any(k in roles for k in ("value", "pl")):
        schema.kind = "timeseries"
    elif any(x in title_l for x in ("expense", "budget", "spend")):
        schema.kind = "expense" if "expense" in title_l else "budget"
    elif (
        any(h in header_blob for h in ("metric", "revenue", "net income", "operating"))
        or any(re.fullmatch(r"20\d{2}|fy\s*20\d{2}", h.strip(), re.I) for h in headers[1:4])
    ):
        schema.kind = "financials"
    elif schema.has_tickers:
        schema.kind = "watchlist"
    else:
        schema.kind = "other"

    if missing:
        schema.notes.append(f"missing_cells={missing}")
    return schema


def _schema_dict(schema: SheetSchema) -> dict[str, Any]:
    return {
        "sheet_name": schema.sheet_name,
        "headers": schema.headers,
        "kind": schema.kind,
        "columns": schema.columns,
        "row_count": schema.row_count,
        "currency_like": schema.currency_like,
        "has_tickers": schema.has_tickers,
        "missing_cells": schema.missing_cells,
        "notes": schema.notes,
    }


def to_records(rows: list[list[Any]]) -> tuple[list[str], list[dict[str, Any]]]:
    if not rows:
        return [], []
    headers = [str(c).strip() or f"col_{i}" for i, c in enumerate(rows[0])]
    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        rec = {}
        for i, h in enumerate(headers):
            rec[h] = row[i] if i < len(row) else ""
        records.append(rec)
    return headers, records


def parse_number(value: Any) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "").replace("$", "").replace("%", "")
    if not s or s in {"-", "—", "n/a", "N/A"}:
        return None
    try:
        return float(s)
    except ValueError:
        return None
