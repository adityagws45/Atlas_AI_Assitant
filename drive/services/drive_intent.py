"""Natural-language Drive intents — never expose API jargon to users."""

from __future__ import annotations

import re
from dataclasses import dataclass


CONNECT = re.compile(
    r"\b("
    r"connect( my)? (google )?drive|link( my)? (google )?drive|"
    r"authorize( my)? (google )?drive|enable( my)? (google )?drive|"
    r"set up( my)? (google )?drive|add( my)? (google )?drive"
    r")\b",
    re.IGNORECASE,
)

DISCONNECT = re.compile(
    r"\b(disconnect|unlink|revoke).{0,20}(google )?drive\b",
    re.IGNORECASE,
)

SEARCH = re.compile(
    r"\b("
    r"search( my)? (google )?drive|find( in)?( my)? (google )?drive|"
    r"look( in)?( my)? (google )?drive|what( files| documents)? do i have|"
    r"which( files| documents)? do i have|documents? (about|on|for)|"
    r"files? (about|on|for)|show( me)?( my)? (files|documents|drive)"
    r")\b",
    re.IGNORECASE,
)

IMPORT = re.compile(
    r"\b("
    r"import|pull in|load|open|analyze|summarize|read"
    r").{0,40}\b("
    r"from (my )?(google )?drive|my (apple|microsoft|nvidia|amazon|google|meta|tesla).{0,30}"
    r"(report|filing|notes|deck|document)|"
    r"(annual|investor|earnings) (report|notes|filing)"
    r")\b",
    re.IGNORECASE,
)

ANALYZE_MY = re.compile(
    r"\b("
    r"analyze my .{3,80}|"
    r"summarize my .{3,80}|"
    r"import my .{3,80}|"
    r"read my .{3,80}|"
    r"open my .{3,80}"
    r")\b",
    re.IGNORECASE,
)

SYNC = re.compile(
    r"\b(sync|refresh|update).{0,20}(my )?(google )?drive\b",
    re.IGNORECASE,
)

TOPIC_ABOUT = re.compile(
    r"(?:documents?|files?|reports?)\s+(?:about|on|for)\s+(.+)$|"
    r"what (?:documents?|files?)\s+(?:do i have|have i).{0,20}(?:about|on|for)\s+(.+)$|"
    r"search(?: my)?(?: google)? drive(?: for)?\s+(.+)$|"
    r"find(?: in)?(?: my)?(?: google)? drive(?: for)?\s+(.+)$",
    re.IGNORECASE,
)

IMPORT_TARGET = re.compile(
    r"(?:analyze|summarize|import|load|pull in|read|open)\s+my\s+(.+?)(?:\s+from\s+.*)?$",
    re.IGNORECASE,
)


@dataclass
class DriveIntent:
    kind: str  # connect|disconnect|search|import|sync|none
    query: str = ""


def detect_drive_intent(text: str) -> DriveIntent:
    raw = (text or "").strip()
    if not raw:
        return DriveIntent(kind="none")
    if CONNECT.search(raw):
        return DriveIntent(kind="connect")
    if DISCONNECT.search(raw):
        return DriveIntent(kind="disconnect")
    if SYNC.search(raw):
        return DriveIntent(kind="sync")
    if ANALYZE_MY.search(raw) or IMPORT.search(raw):
        # Portfolio / spreadsheet phrasing belongs to Sheets (M7), not Drive import
        if re.search(
            r"\b(portfolio|spreadsheet|watchlist|holdings|sheets?)\b",
            raw,
            re.IGNORECASE,
        ) and not re.search(
            r"\b(drive|pdf|document|filing|deck|notes)\b",
            raw,
            re.IGNORECASE,
        ):
            return DriveIntent(kind="none")
        m = IMPORT_TARGET.search(raw)
        q = (m.group(1) if m else "").strip(" .!?")
        if not q:
            # fallback: strip leading verb
            q = re.sub(
                r"^(analyze|summarize|import|load|pull in|read|open)\s+my\s+",
                "",
                raw,
                flags=re.IGNORECASE,
            ).strip()
        return DriveIntent(kind="import", query=q)
    if SEARCH.search(raw):
        m = TOPIC_ABOUT.search(raw)
        q = ""
        if m:
            q = next((g for g in m.groups() if g), "") or ""
        q = q.strip(" .!?\"'")
        return DriveIntent(kind="search", query=q)
    return DriveIntent(kind="none")
