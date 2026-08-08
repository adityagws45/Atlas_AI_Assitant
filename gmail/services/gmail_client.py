"""Gmail API client — live Gmail + local demo backend."""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

from django.conf import settings
from django.core.cache import cache

from gmail.services.demo_data import DEMO_MESSAGES

logger = logging.getLogger("atlas.gmail.client")

META_TTL = int(getattr(settings, "CACHE_TTL_GMAIL_META", 120) or 120)
BODY_TTL = int(getattr(settings, "CACHE_TTL_GMAIL_BODY", 180) or 180)


@dataclass
class RemoteMessage:
    id: str
    thread_id: str
    subject: str
    from_name: str
    from_email: str
    snippet: str
    body_text: str
    received_at: datetime | None
    unread: bool
    labels: list[str] = field(default_factory=list)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    tickers: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)


class GmailClientProtocol(Protocol):
    def list_messages(self, *, query: str = "", max_results: int = 25) -> list[RemoteMessage]: ...

    def get_message(self, message_id: str) -> RemoteMessage | None: ...

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes | None: ...

    def mark_read(self, message_id: str) -> bool: ...

    def archive(self, message_id: str) -> bool: ...


def _parse_from(header: str) -> tuple[str, str]:
    raw = (header or "").strip()
    if "<" in raw and ">" in raw:
        name = raw.split("<", 1)[0].strip().strip('"')
        email = raw.split("<", 1)[1].split(">", 1)[0].strip()
        return name or email, email
    return raw, raw


class MockGmailClient:
    """Local demo inbox — no Google API calls."""

    def __init__(self) -> None:
        self._msgs = {m["id"]: dict(m) for m in DEMO_MESSAGES}
        self._archived: set[str] = set()

    def list_messages(self, *, query: str = "", max_results: int = 25) -> list[RemoteMessage]:
        q = (query or "").strip().lower()
        out: list[RemoteMessage] = []
        for raw in self._msgs.values():
            if raw["id"] in self._archived:
                continue
            blob = " ".join(
                [
                    raw.get("subject", ""),
                    raw.get("snippet", ""),
                    raw.get("body", ""),
                    raw.get("from_name", ""),
                    raw.get("from_email", ""),
                    " ".join(raw.get("companies") or []),
                    " ".join(raw.get("tickers") or []),
                    " ".join(raw.get("categories") or []),
                ]
            ).lower()
            if q:
                # Gmail-ish operators
                if q.startswith("from:"):
                    needle = q[5:].strip().strip('"')
                    if needle not in (raw.get("from_email") or "").lower() and needle not in (
                        raw.get("from_name") or ""
                    ).lower():
                        continue
                elif q in {"is:unread", "unread"}:
                    if not raw.get("unread"):
                        continue
                elif "is:unread" in q and "or" not in q and not raw.get("unread"):
                    continue
                elif q.startswith("subject:"):
                    if q[8:].strip().strip('"') not in (raw.get("subject") or "").lower():
                        continue
                elif q.startswith("in:inbox") or q.startswith("newer_than:"):
                    pass  # demo corpus is already an inbox snapshot
                elif q not in blob and not all(
                    tok in blob
                    for tok in q.replace("(", " ").replace(")", " ").split()
                    if tok and tok not in {"or", "and"} and not tok.startswith(("is:", "in:", "newer_than:", "older_than:", "subject:"))
                ):
                    continue
            out.append(self._to_remote(raw))
        out.sort(key=lambda m: m.received_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        return out[:max_results]

    def get_message(self, message_id: str) -> RemoteMessage | None:
        raw = self._msgs.get(message_id)
        if not raw or message_id in self._archived:
            return None
        return self._to_remote(raw)

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes | None:
        raw = self._msgs.get(message_id)
        if not raw:
            return None
        for i, att in enumerate(raw.get("attachments") or []):
            aid = att.get("id") or f"{message_id}:att:{i}"
            if aid == attachment_id or att.get("filename") == attachment_id:
                text = att.get("demo_text") or f"Attachment: {att.get('filename')}"
                return text.encode("utf-8")
        return None

    def mark_read(self, message_id: str) -> bool:
        raw = self._msgs.get(message_id)
        if not raw:
            return False
        raw["unread"] = False
        return True

    def archive(self, message_id: str) -> bool:
        if message_id not in self._msgs:
            return False
        self._archived.add(message_id)
        return True

    def _to_remote(self, raw: dict) -> RemoteMessage:
        received = None
        try:
            received = datetime.fromisoformat(raw["received_at"])
        except Exception:  # noqa: BLE001
            received = datetime.now(tz=timezone.utc)
        atts = []
        for i, att in enumerate(raw.get("attachments") or []):
            atts.append(
                {
                    "id": att.get("id") or f"{raw['id']}:att:{i}",
                    "filename": att.get("filename") or f"attachment-{i}",
                    "mime_type": att.get("mime_type") or "application/octet-stream",
                    "size": att.get("size") or 0,
                    "demo_text": att.get("demo_text") or "",
                }
            )
        return RemoteMessage(
            id=raw["id"],
            thread_id=raw.get("thread_id") or raw["id"],
            subject=raw.get("subject") or "",
            from_name=raw.get("from_name") or "",
            from_email=raw.get("from_email") or "",
            snippet=raw.get("snippet") or "",
            body_text=raw.get("body") or "",
            received_at=received,
            unread=bool(raw.get("unread")),
            labels=list(raw.get("labels") or []),
            attachments=atts,
            companies=list(raw.get("companies") or []),
            tickers=list(raw.get("tickers") or []),
            people=list(raw.get("people") or []),
            categories=list(raw.get("categories") or []),
        )


class GoogleGmailClient:
    """Live Gmail API (readonly by default)."""

    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self._svc = None

    def _service(self):
        if self._svc is not None:
            return self._svc
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        creds = Credentials(token=self.access_token)
        self._svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._svc

    def list_messages(self, *, query: str = "", max_results: int = 25) -> list[RemoteMessage]:
        cache_key = (
            "gmail:list:"
            + hashlib.sha1((self.access_token[:12] + "|" + query + "|" + str(max_results)).encode()).hexdigest()
        )
        cached = cache.get(cache_key)
        if isinstance(cached, list):
            return cached
        try:
            svc = self._service()
            resp = (
                svc.users()
                .messages()
                .list(userId="me", q=query or "", maxResults=max_results)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            logger.warning("event=gmail_list_failed err=%s detail=%s", type(exc).__name__, str(exc)[:240])
            if "401" in msg or "invalid_grant" in msg or "unauthorized" in msg:
                raise PermissionError("auth_required") from exc
            if (
                "has not been used" in msg
                or "it is disabled" in msg
                or "accessnotconfigured" in msg.replace(" ", "")
                or ("access not configured" in msg)
            ):
                raise RuntimeError("api_disabled") from exc
            if "403" in msg or "forbidden" in msg or "insufficient" in msg:
                raise PermissionError("permission_denied") from exc
            raise
        out: list[RemoteMessage] = []
        for item in resp.get("messages") or []:
            mid = item.get("id")
            if not mid:
                continue
            msg = self.get_message(mid)
            if msg:
                out.append(msg)
        cache.set(cache_key, out, META_TTL)
        return out

    def get_message(self, message_id: str) -> RemoteMessage | None:
        cache_key = "gmail:msg:" + hashlib.sha1(
            (self.access_token[:12] + "|" + message_id).encode()
        ).hexdigest()
        cached = cache.get(cache_key)
        if isinstance(cached, RemoteMessage):
            return cached
        try:
            svc = self._service()
            raw = (
                svc.users()
                .messages()
                .get(userId="me", id=message_id, format="full")
                .execute()
            )
        except Exception:  # noqa: BLE001
            logger.info("event=gmail_get_failed")
            return None
        msg = self._parse_api_message(raw)
        cache.set(cache_key, msg, BODY_TTL)
        return msg

    def get_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes | None:
        try:
            svc = self._service()
            raw = (
                svc.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            data = raw.get("data") or ""
            return base64.urlsafe_b64decode(data.encode("utf-8"))
        except Exception:  # noqa: BLE001
            logger.info("event=gmail_attachment_failed")
            return None

    def mark_read(self, message_id: str) -> bool:
        try:
            svc = self._service()
            svc.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            ).execute()
            return True
        except Exception:  # noqa: BLE001
            return False

    def archive(self, message_id: str) -> bool:
        try:
            svc = self._service()
            svc.users().messages().modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["INBOX"]},
            ).execute()
            return True
        except Exception:  # noqa: BLE001
            return False

    def _parse_api_message(self, raw: dict) -> RemoteMessage:
        headers = {
            (h.get("name") or "").lower(): (h.get("value") or "")
            for h in (raw.get("payload") or {}).get("headers") or []
        }
        from_name, from_email = _parse_from(headers.get("from", ""))
        subject = headers.get("subject") or ""
        received = None
        if headers.get("date"):
            try:
                received = parsedate_to_datetime(headers["date"])
            except Exception:  # noqa: BLE001
                received = None
        label_ids = list(raw.get("labelIds") or [])
        body, atts = self._walk_parts(raw.get("payload") or {})
        return RemoteMessage(
            id=raw.get("id") or "",
            thread_id=raw.get("threadId") or "",
            subject=subject,
            from_name=from_name,
            from_email=from_email,
            snippet=(raw.get("snippet") or "")[:500],
            body_text=(body or "")[:8000],
            received_at=received,
            unread="UNREAD" in label_ids,
            labels=label_ids,
            attachments=atts,
        )

    def _walk_parts(self, payload: dict) -> tuple[str, list[dict]]:
        body_chunks: list[str] = []
        atts: list[dict] = []

        def walk(part: dict) -> None:
            mime = (part.get("mimeType") or "").lower()
            filename = part.get("filename") or ""
            body = part.get("body") or {}
            data = body.get("data")
            att_id = body.get("attachmentId")
            if filename and att_id:
                atts.append(
                    {
                        "id": att_id,
                        "filename": filename,
                        "mime_type": mime,
                        "size": body.get("size") or 0,
                    }
                )
            elif data and mime.startswith("text/"):
                try:
                    body_chunks.append(
                        base64.urlsafe_b64decode(data.encode("utf-8")).decode(
                            "utf-8", errors="replace"
                        )
                    )
                except Exception:  # noqa: BLE001
                    pass
            for child in part.get("parts") or []:
                walk(child)

        walk(payload)
        return "\n".join(body_chunks).strip(), atts


def build_gmail_client(*, access_token: str, demo: bool) -> GmailClientProtocol:
    if demo or (access_token or "").startswith("demo:"):
        return MockGmailClient()
    return GoogleGmailClient(access_token)
