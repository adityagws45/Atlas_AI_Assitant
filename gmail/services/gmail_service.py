"""Gmail facade — connect / inbox / search / draft for Telegram + tools."""

from __future__ import annotations

import logging
import re
from typing import Any

from django.utils import timezone

from accounts.models import GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService
from telegram_bot.adapters.oauth_ux import (
    google_access_required_reply,
    google_connected_prefix,
)
from core.crypto import encrypt_text
from documents.models import DocumentSource
from documents.services.document_pipeline import DocumentPipeline
from documents.services.document_qa_service import DocumentQAService
from gmail.models import GmailConnectionMode, GmailMessage, GmailSyncState, GmailSyncStatus
from gmail.services.gmail_client import build_gmail_client
from gmail.services.gmail_draft import GmailDraftService
from gmail.services.gmail_intel import enrich_message, format_inbox_digest, format_thread_summary
from gmail.services.gmail_intent import detect_gmail_intent
from gmail.services.gmail_memory import GmailMemory
from gmail.services.gmail_query import build_gmail_query
from gmail.services.gmail_relevance import (
    FINANCE_THRESHOLD,
    finance_search_query,
    partition_by_finance,
    score_finance_relevance,
)

logger = logging.getLogger("atlas.gmail.service")

_API_DISABLED_MSG = (
    "Gmail API is not enabled on this project's Google Cloud account "
    "(project 73169070550).\n\n"
    "Enable it here, wait 1-2 minutes, then ask again "
    "(OAuth is already done — just enable the API):\n"
    "https://console.developers.google.com/apis/api/gmail.googleapis.com/overview?project=73169070550"
)


class GmailService:
    def __init__(
        self,
        *,
        oauth: GoogleOAuthService | None = None,
        memory: GmailMemory | None = None,
        drafts: GmailDraftService | None = None,
        docs: DocumentPipeline | None = None,
        doc_qa: DocumentQAService | None = None,
    ) -> None:
        self.oauth = oauth or GoogleOAuthService()
        self.memory = memory or GmailMemory()
        self.drafts = drafts or GmailDraftService()
        self.docs = docs or DocumentPipeline()
        self.doc_qa = doc_qa or DocumentQAService()

    def handle_intent(self, user: User, text: str) -> dict[str, Any] | None:
        intent = detect_gmail_intent(
            text, has_gmail_context=self.memory.has_recent_context(user)
        )
        if intent.kind == "none":
            return None
        handlers = {
            "connect": lambda: self.connect(user),
            "check": lambda: self.inbox_digest(user, mode="check", question=text),
            "latest": lambda: self.inbox_digest(user, mode="latest", question=text),
            "priority": lambda: self.inbox_digest(user, mode="priority", question=text),
            "finance": lambda: self.inbox_digest(user, mode="finance", question=text),
            "earnings": lambda: self.inbox_digest(user, mode="earnings", question=text),
            "investments": lambda: self.inbox_digest(user, mode="investments", question=text),
            "unread_finance": lambda: self.inbox_digest(
                user, mode="unread_finance", question=text
            ),
            "summary": lambda: self.summarize(user, text),
            "unread": lambda: self.inbox_digest(user, mode="unread", question=text),
            "search": lambda: self.search(user, intent.query or text, question=text),
            "thread": lambda: self.open_thread(user, intent.query),
            "followup": lambda: self.followup(user, text),
            "has_attachment": lambda: self.attachment_status(user, text),
            "attachment": lambda: self.summarize_attachment(user),
            "meetings": lambda: self.find_meetings(user),
            "draft": lambda: self.draft_reply(user, instruction=intent.query, tone=intent.tone),
            "send": lambda: self.confirm_send(user, text),
            "archive": lambda: self.archive_active(user),
            "mark_read": lambda: self.mark_active_read(user),
        }
        handler = handlers.get(intent.kind)
        return handler() if handler else None

    def ensure_state(self, user: User) -> GmailSyncState:
        state, _ = GmailSyncState.objects.get_or_create(user=user)
        return state

    def is_live_connected(self, user: User) -> bool:
        token = self.oauth.get_valid_access_token(user, service=GoogleService.GMAIL)
        if not token or str(token).startswith("demo:"):
            return False
        if not self.oauth.token_has_required_scopes(user, service=GoogleService.GMAIL):
            self.oauth.disconnect(user, service=GoogleService.GMAIL)
            return False
        return True

    def is_connected(self, user: User) -> bool:
        state = GmailSyncState.objects.filter(user=user).first()
        if state and state.mode == GmailConnectionMode.DEMO:
            return True
        return self.is_live_connected(user)

    def connect_demo(self, user: User) -> GmailSyncState:
        integ, _ = user.google_integrations.get_or_create(
            service=GoogleService.GMAIL,
            defaults={
                "access_token_encrypted": encrypt_text("demo:gmail"),
                "refresh_token_encrypted": encrypt_text("demo:gmail"),
                "is_active": True,
                "scopes": ["gmail.readonly.demo"],
            },
        )
        if not integ.is_active:
            integ.is_active = True
            integ.access_token_encrypted = encrypt_text("demo:gmail")
            integ.save(update_fields=["is_active", "access_token_encrypted", "updated_at"])
        state = self.ensure_state(user)
        state.mode = GmailConnectionMode.DEMO
        state.status = GmailSyncStatus.IDLE
        state.error_message = ""
        state.save(update_fields=["mode", "status", "error_message", "updated_at"])
        return state

    def connect(self, user: User, *, pending_question: str = "") -> dict[str, Any]:
        if pending_question:
            self.memory.remember_pending_question(user, pending_question)
        if self.oauth.is_configured():
            started = self.oauth.start_auth(user, service=GoogleService.GMAIL)
            if started.get("ok") and started.get("auth_url"):
                auth_url = started["auth_url"]
                return {
                    "ok": False,
                    "handled": True,
                    "needs_oauth": True,
                    "auth_url": auth_url,
                    "reply": google_access_required_reply(
                        auth_url,
                        purpose=(
                            "Connect Google once for Calendar, Gmail, Drive, and Sheets."
                        ),
                    ),
                }
            return {
                "ok": False,
                "handled": True,
                "needs_oauth": True,
                "reply": started.get("error")
                or "I couldn't start Gmail authorization. Please try again.",
            }
        self.connect_demo(user)
        self.sync_inbox(user)
        return {
            "ok": True,
            "handled": True,
            "demo": True,
            "reply": (
                "Your inbox is ready (local demo mail — live Google OAuth is not "
                "configured on this server).\n\n"
                "Try:\n"
                "• “Show me my latest emails”\n"
                "• “Do I have any unread emails?”\n"
                "• “Find emails from Microsoft”"
            ),
        }

    def ensure_ready(self, user: User, *, question: str = "") -> dict[str, Any] | None:
        if self.is_live_connected(user):
            state = self.ensure_state(user)
            if state.mode != GmailConnectionMode.OAUTH:
                state.mode = GmailConnectionMode.OAUTH
                state.save(update_fields=["mode", "updated_at"])
            return None
        state = GmailSyncState.objects.filter(user=user).first()
        if state and state.mode == GmailConnectionMode.DEMO:
            return None
        if self.oauth.is_configured():
            return self.connect(user, pending_question=question)
        self.connect_demo(user)
        return None

    def verify_gmail_access(self, user: User) -> dict[str, Any]:
        token = self.oauth.get_valid_access_token(user, service=GoogleService.GMAIL)
        if not token:
            return {
                "ok": False,
                "error_code": "auth_required",
                "error": "Gmail authorization required. Please connect Google.",
            }
        if not self.oauth.token_has_required_scopes(user, service=GoogleService.GMAIL):
            self.oauth.disconnect(user, service=GoogleService.GMAIL)
            return {
                "ok": False,
                "error_code": "permission_denied",
                "error": (
                    "I don't have Gmail permission yet. "
                    "Please reconnect Google and allow Gmail access."
                ),
            }
        try:
            client = build_gmail_client(access_token=token, demo=False)
            client.list_messages(query="in:inbox", max_results=1)
            return {"ok": True}
        except PermissionError as exc:
            code = str(exc) or "permission_denied"
            self.oauth.disconnect(user, service=GoogleService.GMAIL)
            return {
                "ok": False,
                "error_code": code
                if code in {"auth_required", "permission_denied"}
                else "permission_denied",
                "error": (
                    "I don't have permission to read your Gmail. "
                    "Please reconnect Google and allow Gmail access."
                ),
            }
        except RuntimeError as exc:
            if str(exc) == "api_disabled":
                return {"ok": False, "error_code": "api_disabled", "error": _API_DISABLED_MSG}
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=gmail_verify_failed err=%s", type(exc).__name__)
            return {
                "ok": False,
                "error_code": "temporary",
                "error": "I couldn't reach Gmail right now. Please try again.",
            }

    def resume_after_oauth(self, user: User) -> dict[str, Any]:
        verified = self.verify_gmail_access(user)
        if not verified.get("ok"):
            question = self.memory.pop_pending_question(user)
            if question:
                self.memory.remember_pending_question(user, question)
            if verified.get("error_code") == "api_disabled":
                return {
                    "ok": False,
                    "error_code": "api_disabled",
                    "reply": verified.get("error") or _API_DISABLED_MSG,
                }
            reconnect = self.connect(user, pending_question=question or "")
            return {
                "ok": False,
                "error_code": verified.get("error_code") or "permission_denied",
                "needs_oauth": True,
                "auth_url": reconnect.get("auth_url"),
                "reply": (verified.get("error") or "") + "\n\n" + (reconnect.get("reply") or ""),
            }

        state = self.ensure_state(user)
        state.mode = GmailConnectionMode.OAUTH
        state.error_message = ""
        state.save(update_fields=["mode", "error_message", "updated_at"])

        question = self.memory.pop_pending_question(user)
        prefix = google_connected_prefix(action="Checking your inbox…")
        synced = self.sync_inbox(user)
        if not synced.get("ok"):
            if question:
                self.memory.remember_pending_question(user, question)
            return {
                "ok": False,
                "error_code": synced.get("error_code"),
                "reply": synced.get("error") or "I couldn't load your inbox just yet.",
            }
        if question:
            result = self.handle_intent(user, question) or {}
            body = result.get("reply") or "Ask me about your emails."
            if result.get("needs_oauth") or result.get("error_code") in {
                "permission_denied",
                "auth_required",
                "temporary",
                "api_disabled",
            }:
                return {
                    "ok": bool(result.get("ok")),
                    "error_code": result.get("error_code"),
                    "needs_oauth": bool(result.get("needs_oauth")),
                    "auth_url": result.get("auth_url"),
                    "reply": body,
                }
            return {"ok": True, "reply": prefix + body}
        digest = self.inbox_digest(user, mode="latest")
        return {"ok": True, "reply": prefix + (digest.get("reply") or "")}

    def _watchlist_context(self, user: User) -> tuple[list[str], list[str]]:
        symbols: list[str] = []
        names: list[str] = []
        try:
            from memory.models import Watchlist

            for row in Watchlist.objects.filter(user=user).only("symbol", "company_name")[:40]:
                if row.symbol:
                    symbols.append(str(row.symbol).upper())
                if row.company_name:
                    names.append(str(row.company_name))
        except Exception:  # noqa: BLE001
            pass
        try:
            prefs = getattr(user, "preferences", None)
            if prefs is None:
                from memory.models import UserPreference

                prefs = UserPreference.objects.filter(user=user).first()
            if prefs:
                for s in list(prefs.sectors_of_interest or [])[:20]:
                    if s:
                        names.append(str(s))
        except Exception:  # noqa: BLE001
            pass
        return symbols, names

    def _client_for(self, user: User):
        state = self.ensure_state(user)
        token = self.oauth.get_valid_access_token(user, service=GoogleService.GMAIL)
        if token and not str(token).startswith("demo:"):
            if state.mode != GmailConnectionMode.OAUTH:
                state.mode = GmailConnectionMode.OAUTH
                state.save(update_fields=["mode", "updated_at"])
            return build_gmail_client(access_token=token, demo=False)
        if state.mode == GmailConnectionMode.OAUTH:
            raise PermissionError("auth_required")
        if state.mode == GmailConnectionMode.DEMO:
            return build_gmail_client(access_token="demo:gmail", demo=True)
        raise PermissionError("auth_required")

    def sync_inbox(self, user: User, *, query: str = "", max_results: int = 30) -> dict[str, Any]:
        state = self.ensure_state(user)
        state.status = GmailSyncStatus.RUNNING
        state.save(update_fields=["status", "updated_at"])
        try:
            client = self._client_for(user)
            is_demo = (
                state.mode == GmailConnectionMode.DEMO
                or type(client).__name__ == "MockGmailClient"
            )
            if state.mode == GmailConnectionMode.OAUTH and is_demo:
                raise PermissionError("auth_required")
            remotes = client.list_messages(query=query, max_results=max_results)
            remote_ids: list[str] = []
            wl_syms, wl_names = self._watchlist_context(user)
            for remote in remotes:
                enriched = enrich_message(
                    remote,
                    watchlist_symbols=wl_syms,
                    watchlist_names=wl_names,
                )
                remote_ids.append(remote.id)
                GmailMessage.objects.update_or_create(
                    user=user,
                    message_id=remote.id,
                    defaults={
                        "thread_id": remote.thread_id or "",
                        "subject": (remote.subject or "")[:512],
                        "from_name": (remote.from_name or "")[:256],
                        "from_email": (remote.from_email or "")[:320],
                        "snippet": (remote.snippet or "")[:512],
                        "body_text": (remote.body_text or "")[:8000],
                        "received_at": remote.received_at,
                        "is_unread": remote.unread,
                        "is_archived": False,
                        "is_important": "IMPORTANT" in (remote.labels or []),
                        "labels": remote.labels or [],
                        "companies": enriched["companies"],
                        "tickers": enriched["tickers"],
                        "people": enriched["people"],
                        "categories": enriched["categories"],
                        "priority_score": enriched["priority_score"],
                        "has_attachment": bool(remote.attachments),
                        "attachments": remote.attachments or [],
                        "extra": {
                            "why": enriched["why"],
                            "finance_score": enriched["finance_score"],
                            "finance_band": enriched["finance_band"],
                            "is_finance": enriched["is_finance"],
                            "is_noise": enriched["is_noise"],
                            "has_finance_attachment": enriched["has_finance_attachment"],
                        },
                    },
                )
            stale_deleted = 0
            if not (query or "").strip() and state.mode == GmailConnectionMode.OAUTH:
                qs = GmailMessage.objects.filter(user=user)
                if remote_ids:
                    qs = qs.exclude(message_id__in=remote_ids)
                stale_deleted, _ = qs.delete()
            logger.info(
                "event=gmail_sync_ok telegram_id=%s source=%s listed=%s query=%s stale_deleted=%s",
                user.telegram_id,
                "demo" if is_demo else "gmail_api",
                len(remotes),
                (query or "")[:80],
                stale_deleted,
            )
            state.status = GmailSyncStatus.IDLE
            state.last_synced_at = timezone.now()
            state.stats = {
                "listed": len(remotes),
                "source": "demo" if is_demo else "gmail_api",
                "stale_deleted": stale_deleted,
                "query": (query or "")[:120],
            }
            state.error_message = ""
            state.save()
            return {
                "ok": True,
                "count": len(remotes),
                "message_ids": remote_ids,
                "source": "demo" if is_demo else "gmail_api",
            }
        except PermissionError as exc:
            state.status = GmailSyncStatus.FAILED
            code = str(exc) or "auth_required"
            state.error_message = code[:200]
            state.save(update_fields=["status", "error_message", "updated_at"])
            if code == "permission_denied":
                self.oauth.disconnect(user, service=GoogleService.GMAIL)
                return {
                    "ok": False,
                    "error_code": "permission_denied",
                    "error": (
                        "Gmail permission denied. Please reconnect Google and allow Gmail access."
                    ),
                }
            return {
                "ok": False,
                "error_code": "auth_required",
                "error": "Gmail authorization required. Please connect Google.",
            }
        except RuntimeError as exc:
            if str(exc) == "api_disabled":
                state.status = GmailSyncStatus.FAILED
                state.error_message = "api_disabled"
                state.save(update_fields=["status", "error_message", "updated_at"])
                return {"ok": False, "error_code": "api_disabled", "error": _API_DISABLED_MSG}
            raise
        except Exception as exc:  # noqa: BLE001
            state.status = GmailSyncStatus.FAILED
            state.error_message = exc.__class__.__name__[:200]
            state.save(update_fields=["status", "error_message", "updated_at"])
            logger.warning("event=gmail_sync_failed err=%s", type(exc).__name__)
            return {
                "ok": False,
                "error_code": "temporary",
                "error": "Gmail is unavailable right now. Please try again.",
            }

    def _fail_or_oauth(
        self, user: User, synced: dict[str, Any], *, question: str = ""
    ) -> dict[str, Any]:
        code = synced.get("error_code") or "temporary"
        if code in {"auth_required", "permission_denied"}:
            linked = self.connect(user, pending_question=question)
            linked["error_code"] = code
            linked["reply"] = (synced.get("error") or "") + "\n\n" + (linked.get("reply") or "")
            return linked
        return {
            "ok": False,
            "handled": True,
            "error_code": code,
            "reply": synced.get("error") or "I couldn't access your Gmail right now.",
        }

    def inbox_digest(
        self, user: User, *, mode: str = "check", question: str = ""
    ) -> dict[str, Any]:
        gate = self.ensure_ready(user, question=question or "Show me my latest emails")
        if gate:
            return gate

        # Candidate pulls: recent inbox + finance-boosted search (merged & ranked)
        primary_kind = {
            "unread": "unread",
            "unread_finance": "unread_finance",
            "finance": "finance",
            "earnings": "earnings",
            "investments": "investments",
            "priority": "priority",
            "latest": "latest",
            "check": "latest",
        }.get(mode, "latest")
        gmail_q = build_gmail_query(question or mode, kind=primary_kind)
        synced = self.sync_inbox(user, query=gmail_q, max_results=30)
        if not synced.get("ok"):
            return self._fail_or_oauth(user, synced, question=question)

        message_ids: list[str] = list(synced.get("message_ids") or [])

        # For default latest/unread/priority views, also pull finance candidates
        if mode in {"latest", "check", "unread", "priority"}:
            fin_q = finance_search_query(
                "unread_finance" if mode == "unread" else "finance"
            )
            if fin_q != gmail_q:
                extra = self.sync_inbox(user, query=fin_q, max_results=20)
                if extra.get("ok"):
                    for mid in extra.get("message_ids") or []:
                        if mid not in message_ids:
                            message_ids.append(mid)

        if message_ids:
            rows = list(GmailMessage.objects.filter(user=user, message_id__in=message_ids))
        else:
            rows = []

        # Re-score with current watchlist (extra may have been stored earlier)
        wl_syms, wl_names = self._watchlist_context(user)
        payload: list[dict[str, Any]] = []
        for r in rows:
            data = self._row_dict(r, watchlist_symbols=wl_syms, watchlist_names=wl_names)
            if mode in {"unread", "unread_finance"} and not data.get("is_unread"):
                continue
            payload.append(data)

        # Rank by finance relevance (never chronological-first for latest)
        payload.sort(
            key=lambda m: (
                float(m.get("finance_score") or 0),
                1 if m.get("is_unread") else 0,
                float(m.get("priority_score") or 0),
            ),
            reverse=True,
        )
        scanned = len(payload)

        if mode in {"finance", "earnings", "investments", "unread_finance"}:
            # Strong finance filter for explicit finance asks
            finance_only = [m for m in payload if m.get("is_finance")]
            if mode == "earnings":
                finance_only = [
                    m
                    for m in finance_only
                    if "earnings" in (m.get("categories") or [])
                    or re.search(
                        r"\bearnings?|eps|quarterly results|guidance\b",
                        f"{m.get('subject','')} {m.get('snippet','')}",
                        re.I,
                    )
                ] or finance_only
            display = finance_only[:8] if finance_only else payload[:6]
            reply = format_inbox_digest(
                display if finance_only else payload,
                mode=mode if mode != "unread_finance" else "finance",
                total_scanned=scanned,
            )
            remember = finance_only[:8] if finance_only else display[:6]
        elif mode == "priority":
            important = [
                m
                for m in payload
                if m.get("is_finance")
                or float(m.get("finance_score") or 0) >= FINANCE_THRESHOLD
                or float(m.get("priority_score") or 0) >= 20
            ][:8]
            display = important or payload[:5]
            reply = format_inbox_digest(display, mode="priority", total_scanned=scanned)
            remember = display
        else:
            # latest / check / unread — two-layer finance-first
            finance, other = partition_by_finance(payload, finance_limit=6, other_limit=4)
            display = finance + other
            reply = format_inbox_digest(payload, mode=mode, total_scanned=scanned)
            remember = display

        if not payload:
            empty = (
                "No unread emails."
                if mode in {"unread", "unread_finance"}
                else "No emails found."
            )
            self.memory.remember_results(user, messages=[], label=mode, gmail_query=gmail_q)
            return {
                "ok": True,
                "handled": True,
                "reply": empty,
                "messages": [],
                "source": synced.get("source"),
            }

        self.memory.remember_results(
            user, messages=remember, label=mode, gmail_query=gmail_q
        )
        return {
            "ok": True,
            "handled": True,
            "reply": reply,
            "messages": remember,
            "source": synced.get("source"),
            "scanned": scanned,
        }

    def search(self, user: User, query: str, *, question: str = "") -> dict[str, Any]:
        gate = self.ensure_ready(
            user, question=question or f"Find emails {query}".strip()
        )
        if gate:
            return gate
        q = (query or question or "").strip()
        # Prefer full user phrasing so "from X" / "about Y" survive intent stripping
        gmail_q = build_gmail_query(question or q, kind="search")
        if not gmail_q or gmail_q == "in:inbox newer_than:14d":
            gmail_q = build_gmail_query(q, kind="search")
        synced = self.sync_inbox(user, query=gmail_q, max_results=20)
        if not synced.get("ok"):
            return self._fail_or_oauth(user, synced, question=question or q)

        ids = synced.get("message_ids") or []
        if not ids:
            self.memory.remember_results(
                user, messages=[], label=f"Emails: {q}", gmail_query=gmail_q
            )
            return {
                "ok": True,
                "handled": True,
                "error_code": "empty_results",
                "reply": f"No emails found for *{q or 'that search'}*.",
                "messages": [],
                "source": synced.get("source"),
            }
        id_order = {mid: i for i, mid in enumerate(ids)}
        rows = list(GmailMessage.objects.filter(user=user, message_id__in=ids))
        rows.sort(key=lambda r: id_order.get(r.message_id, 999))
        payload = [self._row_dict(r) for r in rows[:12]]
        # Rank search hits by finance relevance when topic is company/market-ish
        payload.sort(
            key=lambda m: (
                float(m.get("finance_score") or 0),
                1 if m.get("is_unread") else 0,
            ),
            reverse=True,
        )
        payload = payload[:8]
        self.memory.remember_results(
            user, messages=payload, label=f"Emails: {q}", gmail_query=gmail_q
        )
        if len(payload) == 1:
            return {
                "ok": True,
                "handled": True,
                "reply": format_thread_summary(payload[0]),
                "messages": payload,
                "source": synced.get("source"),
            }
        return {
            "ok": True,
            "handled": True,
            "reply": format_inbox_digest(payload, title=f"Emails: {q}", mode="search"),
            "messages": payload,
            "source": synced.get("source"),
        }

    def summarize(self, user: User, text: str = "") -> dict[str, Any]:
        gate = self.ensure_ready(user, question=text or "Summarize my latest emails")
        if gate:
            return gate
        low = (text or "").lower()
        ctx = self.memory.get_results(user)
        messages = list(ctx.get("messages") or [])

        if re.search(r"\bmost important\b", low):
            if not messages:
                dig = self.inbox_digest(user, mode="priority", question=text)
                if not dig.get("ok"):
                    return dig
                messages = dig.get("messages") or []
            ranked = sorted(
                messages,
                key=lambda m: float(m.get("finance_score") or m.get("priority_score") or 0),
                reverse=True,
            )
            if not ranked:
                return {"ok": True, "handled": True, "reply": "No emails found to summarize."}
            top = ranked[0]
            mid = top.get("id") or top.get("message_id")
            row = None
            if mid:
                row = GmailMessage.objects.filter(user=user, id=mid).first() or (
                    GmailMessage.objects.filter(user=user, message_id=mid).first()
                )
            data = self._row_dict(row) if row else top
            self.memory.remember_open(user, data)
            return {
                "ok": True,
                "handled": True,
                "reply": format_thread_summary(data),
                "message": data,
            }

        if re.search(r"\b(first|second|third|this|that|the latest) (one|email)\b", low):
            idx = 0
            if "second" in low:
                idx = 1
            elif "third" in low:
                idx = 2
            if messages and idx < len(messages):
                mid = messages[idx].get("id") or messages[idx].get("message_id")
                row = None
                if mid:
                    row = GmailMessage.objects.filter(user=user, id=mid).first() or (
                        GmailMessage.objects.filter(user=user, message_id=mid).first()
                    )
                if row:
                    data = self._row_dict(row)
                    self.memory.remember_open(user, data)
                    return {
                        "ok": True,
                        "handled": True,
                        "reply": format_thread_summary(data),
                        "message": data,
                    }

        n = 5
        m_n = re.search(r"latest\s+(\d+)", low)
        if m_n:
            n = max(1, min(10, int(m_n.group(1))))

        want_finance = "finance" in low
        if not messages or want_finance:
            digest = self.inbox_digest(
                user,
                mode="finance" if want_finance else "latest",
                question=text,
            )
            if not digest.get("ok"):
                return digest
            messages = digest.get("messages") or []

        if want_finance:
            messages = [m for m in messages if m.get("is_finance")] or messages

        top = messages[:n]
        if not top:
            return {
                "ok": True,
                "handled": True,
                "reply": (
                    "No finance-related emails found to summarize."
                    if want_finance
                    else "No emails found to summarize."
                ),
            }
        lines = [
            f"*Summary of {'finance ' if want_finance else ''}latest {len(top)}*",
            "",
        ]
        for m in top:
            mid = m.get("id")
            row = GmailMessage.objects.filter(user=user, id=mid).first() if mid else None
            subj = (row.subject if row else m.get("subject")) or "(no subject)"
            frm = (row.from_name if row else m.get("from_name")) or "sender"
            snip = (row.snippet if row else m.get("snippet")) or ""
            why = m.get("why") or ((row.extra or {}).get("why") if row else "")
            lines.append(f"• *{subj}* — {frm}")
            if snip:
                lines.append(f"  {snip[:160]}")
            if why:
                lines.append(f"  _{why}_")
            if m.get("has_finance_attachment") or (
                row and (row.extra or {}).get("has_finance_attachment")
            ):
                lines.append("  📎 Financial attachment detected")
        self.memory.remember_results(
            user,
            messages=top,
            label="Summary",
            gmail_query=str(ctx.get("gmail_query") or ""),
        )
        return {"ok": True, "handled": True, "reply": "\n".join(lines), "messages": top}

    def followup(self, user: User, text: str) -> dict[str, Any]:
        low = (text or "").lower()
        if re.search(r"\battachment\b", low) or "does it have" in low:
            return self.attachment_status(user, text)
        if "important" in low or "urgent" in low:
            ctx = self.memory.get_results(user)
            messages = list(ctx.get("messages") or [])
            if not messages:
                return self.inbox_digest(user, mode="priority", question=text)
            scored = sorted(
                messages,
                key=lambda m: float(m.get("finance_score") or m.get("priority_score") or 0),
                reverse=True,
            )
            urgent = [
                m
                for m in scored
                if m.get("is_finance")
                or float(m.get("finance_score") or 0) >= FINANCE_THRESHOLD
                or float(m.get("priority_score") or 0) >= 20
                or m.get("is_unread")
            ][:5]
            if not urgent:
                return {
                    "ok": True,
                    "handled": True,
                    "reply": "Nothing in the current email set looks especially urgent for finance.",
                }
            return {
                "ok": True,
                "handled": True,
                "reply": format_inbox_digest(urgent, mode="priority"),
                "messages": urgent,
            }
        if "summarize" in low or "about" in low or "first" in low or "second" in low or "number" in low:
            return self.summarize(user, text)
        msg = self._resolve_active(user, query=text)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "I don't have an active email yet — say “show me my latest emails”.",
            }
        data = self._row_dict(msg)
        self.memory.remember_open(user, data)
        return {"ok": True, "handled": True, "reply": format_thread_summary(data), "message": data}

    def attachment_status(self, user: User, text: str = "") -> dict[str, Any]:
        msg = self._resolve_active(user, query=text)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "I don't have an active email yet — open one first.",
            }
        data = self._row_dict(msg)
        self.memory.remember_open(user, data)
        atts = data.get("attachments") or []
        if not data.get("has_attachment") and not atts:
            return {
                "ok": True,
                "handled": True,
                "reply": f"*{data.get('subject') or 'This email'}* does not appear to have an attachment.",
                "message": data,
            }
        names = [str(a.get("filename") or a.get("name") or "attachment") for a in atts] or [
            "attachment"
        ]
        lines = [
            f"Yes — *{data.get('subject') or 'this email'}* has "
            f"{len(names)} attachment{'s' if len(names) != 1 else ''}:",
            "",
        ]
        for n in names[:6]:
            lines.append(f"• {n}")
        if data.get("has_finance_attachment"):
            lines.extend(
                [
                    "",
                    "📎 Financial attachment detected",
                    "Say “summarize the attachment” and I’ll run it through the document pipeline.",
                ]
            )
        else:
            lines.extend(["", "Say “summarize the attachment” if you want a rundown."])
        return {"ok": True, "handled": True, "reply": "\n".join(lines), "message": data}

    def open_thread(self, user: User, query: str = "") -> dict[str, Any]:
        mm = re.search(r"what did\s+(.+?)\s+say", query or "", re.I)
        if mm:
            return self.search(user, mm.group(1).strip())
        msg = self._resolve_active(user, query=query)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "I don't have an active email yet — say “check my email” or search by company.",
            }
        data = self._row_dict(msg)
        self.memory.remember_open(user, data)
        return {"ok": True, "handled": True, "reply": format_thread_summary(data), "message": data}

    def find_meetings(self, user: User) -> dict[str, Any]:
        gate = self.ensure_ready(user, question="Any meetings in my email?")
        if gate:
            return gate
        synced = self.sync_inbox(
            user, query="newer_than:30d (meeting OR invite OR RSVP)", max_results=30
        )
        if not synced.get("ok"):
            return self._fail_or_oauth(user, synced, question="Any meetings in my email?")
        ids = synced.get("message_ids") or []
        rows = list(GmailMessage.objects.filter(user=user, message_id__in=ids)) if ids else []
        hits = [r for r in rows if "meeting" in (r.categories or [])] or rows[:5]
        if not hits:
            return {
                "ok": True,
                "handled": True,
                "reply": "No clear meeting invites in the recent set.",
            }
        lines = ["*Meetings mentioned*", ""]
        for r in hits[:5]:
            lines.append(f"• *{r.subject}* — {r.from_name or r.from_email}")
        payload = [self._row_dict(r) for r in hits[:5]]
        self.memory.remember_results(user, messages=payload, label="Meetings")
        return {"ok": True, "handled": True, "reply": "\n".join(lines)}

    def draft_reply(
        self, user: User, *, instruction: str = "", tone: str = "polite"
    ) -> dict[str, Any]:
        msg = self._resolve_active(user)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Open or search an email first, then ask me to draft a reply.",
            }
        draft = self.drafts.draft(
            subject=msg.subject,
            from_name=msg.from_name or "there",
            body_context=msg.snippet or msg.body_text[:400],
            instruction=instruction,
            tone=tone,
        )
        draft["message_pk"] = str(msg.id)
        self.memory.save_draft(user, draft)
        self.memory.remember_open(user, self._row_dict(msg))
        return {
            "ok": True,
            "handled": True,
            "reply": self.drafts.format_draft_reply(draft),
            "draft": draft,
        }

    def confirm_send(self, user: User, text: str) -> dict[str, Any]:
        draft = self.memory.get_draft(user)
        if not draft:
            return {
                "ok": False,
                "handled": True,
                "reply": "There’s no draft waiting. Ask me to “draft a reply” first.",
            }
        low = (text or "").strip().lower()
        if low in {"send", "send it"} and not self.memory.is_pending_send(user):
            self.memory.mark_pending_send(user)
            return {
                "ok": True,
                "handled": True,
                "reply": self.drafts.format_draft_reply(draft, pending_send=True),
            }
        # Confirmed
        self.memory.clear_draft(user)
        # Read-only OAuth: never silently call send API
        return {
            "ok": True,
            "handled": True,
            "reply": (
                "Confirmed — in this setup I keep send gated (read-only inbox by default).\n\n"
                "Your approved draft:\n\n"
                f"{draft.get('body')}\n\n"
                "Paste it into your mail client, or reconnect later with send access if you want Atlas to deliver it."
            ),
        }

    def archive_active(self, user: User) -> dict[str, Any]:
        msg = self._resolve_active(user)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Nothing active to archive. Open an email first.",
            }
        try:
            client = self._client_for(user)
            client.archive(msg.message_id)
        except Exception:  # noqa: BLE001
            pass
        msg.is_archived = True
        msg.is_unread = False
        msg.save(update_fields=["is_archived", "is_unread", "updated_at"])
        return {
            "ok": True,
            "handled": True,
            "reply": f"Archived *{msg.subject or 'that email'}*. What’s next?",
        }

    def mark_active_read(self, user: User) -> dict[str, Any]:
        msg = self._resolve_active(user)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Nothing active to mark read. Open an email first.",
            }
        try:
            client = self._client_for(user)
            client.mark_read(msg.message_id)
        except Exception:  # noqa: BLE001
            pass
        msg.is_unread = False
        msg.save(update_fields=["is_unread", "updated_at"])
        return {
            "ok": True,
            "handled": True,
            "reply": f"Marked *{msg.subject or 'that email'}* as read.",
        }

    def summarize_attachment(self, user: User) -> dict[str, Any]:
        msg = self._resolve_active(user)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Open an email with an attachment first.",
            }
        atts = msg.attachments or []
        if not atts:
            return {
                "ok": False,
                "handled": True,
                "reply": f"*{msg.subject or 'That email'}* doesn’t have an attachment I can read.",
            }
        att = atts[0]
        filename = att.get("filename") or "attachment"
        mime = att.get("mime_type") or ""
        # Only supported types via DocumentPipeline
        lower = filename.lower()
        if not any(lower.endswith(ext) for ext in (".pdf", ".txt", ".md", ".markdown", ".docx")):
            # Demo PDF may be labeled application/pdf with .pdf — ok
            if "pdf" not in mime and "text" not in mime:
                return {
                    "ok": False,
                    "handled": True,
                    "reply": (
                        f"I see *{filename}*, but that format isn’t supported yet. "
                        "PDF, TXT, DOCX, or Markdown work best."
                    ),
                }
        try:
            client = self._client_for(user)
            data = client.get_attachment_bytes(msg.message_id, att.get("id") or filename)
        except Exception:  # noqa: BLE001
            data = None
        if not data:
            # Demo text fallback embedded in attachment metadata
            demo_text = att.get("demo_text") or ""
            if demo_text:
                data = demo_text.encode("utf-8")
                if not lower.endswith((".txt", ".md", ".pdf")):
                    filename = filename.rsplit(".", 1)[0] + ".txt"
                    mime = "text/plain"
            else:
                return {
                    "ok": False,
                    "handled": True,
                    "reply": f"I couldn’t download *{filename}*. It may have been removed.",
                }

        # Force text path for demo PDF bytes that are actually text
        ingest_name = filename
        ingest_mime = mime
        if filename.lower().endswith(".pdf") and data[:4] != b"%PDF":
            ingest_name = filename.rsplit(".", 1)[0] + ".txt"
            ingest_mime = "text/plain"

        try:
            doc = self.docs.ingest_bytes(
                user,
                data=data,
                filename=ingest_name,
                mime_type=ingest_mime or "text/plain",
                source=DocumentSource.TELEGRAM,
                title_override=f"{msg.subject[:80]} — {filename}"[:200],
                extra_metadata={"origin": "gmail_attachment", "email_subject": msg.subject},
            )
        except ValueError as exc:
            return {"ok": False, "handled": True, "reply": str(exc) or "Couldn’t process that attachment."}
        except Exception:  # noqa: BLE001
            return {
                "ok": False,
                "handled": True,
                "reply": f"I hit a snag reading *{filename}*. Try again in a moment.",
            }

        qa = self.doc_qa.answer(
            user,
            f"Summarize this attachment for a busy investor. File: {filename}",
            document_ids=[str(doc.id)],
            compare=False,
        )
        reply = qa.get("reply") if qa.get("ok") else None
        if not reply:
            # Deterministic fallback from demo text / snippet
            text_preview = data.decode("utf-8", errors="replace")[:600]
            reply = (
                f"*Attachment summary* — *{filename}*\n\n"
                f"{text_preview}\n\n"
                "Ask a follow-up about risks, numbers, or what to reply."
            )
        self.memory.remember_open(user, self._row_dict(msg))
        return {"ok": True, "handled": True, "reply": reply, "document": doc}

    def _resolve_active(self, user: User, query: str = "") -> GmailMessage | None:
        mid = self.memory.active_message_id(user)
        if mid:
            msg = GmailMessage.objects.filter(user=user, id=mid, is_archived=False).first()
            if msg:
                return msg
            msg = GmailMessage.objects.filter(
                user=user, message_id=mid, is_archived=False
            ).first()
            if msg:
                return msg
        return (
            GmailMessage.objects.filter(user=user, is_archived=False)
            .order_by("-priority_score", "-received_at")
            .first()
        )

    def _row_dict(
        self,
        row: GmailMessage,
        *,
        watchlist_symbols: list[str] | None = None,
        watchlist_names: list[str] | None = None,
    ) -> dict[str, Any]:
        extra = dict(row.extra or {})
        # Refresh finance score from content when possible
        rel = score_finance_relevance(
            subject=row.subject or "",
            snippet=row.snippet or "",
            body=row.body_text or "",
            from_name=row.from_name or "",
            from_email=row.from_email or "",
            attachments=row.attachments or [],
            watchlist_symbols=watchlist_symbols,
            watchlist_names=watchlist_names,
            labels=row.labels or [],
            unread=bool(row.is_unread),
        )
        received_display = ""
        if row.received_at:
            try:
                received_display = row.received_at.astimezone(timezone.get_current_timezone()).strftime(
                    "%b %d, %Y %H:%M"
                )
            except Exception:  # noqa: BLE001
                received_display = str(row.received_at)[:19]
        return {
            "id": str(row.id),
            "message_id": row.message_id,
            "subject": row.subject,
            "from_name": row.from_name,
            "from_email": row.from_email,
            "snippet": row.snippet,
            "body_text": row.body_text,
            "is_unread": row.is_unread,
            "has_attachment": row.has_attachment,
            "attachments": row.attachments or [],
            "companies": row.companies or [],
            "tickers": row.tickers or [],
            "people": row.people or [],
            "categories": row.categories or [],
            "priority_score": max(float(row.priority_score or 0), float(rel.score)),
            "finance_score": float(rel.score),
            "finance_band": rel.band,
            "is_finance": rel.is_finance,
            "is_noise": rel.is_noise,
            "has_finance_attachment": rel.has_finance_attachment,
            "why": rel.why or extra.get("why") or "",
            "received_at": row.received_at.isoformat() if row.received_at else "",
            "received_display": received_display,
        }
