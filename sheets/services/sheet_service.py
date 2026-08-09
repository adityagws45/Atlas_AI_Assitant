"""Sheets facade — connect / list / open / analyze for Telegram + tools."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from accounts.models import GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService
from core.crypto import encrypt_text
from sheets.models import SheetConnectionMode, SheetSyncState, SheetSyncStatus, SheetWorkbook
from sheets.services.public_sheets import SheetAccessError, load_public_workbook
from sheets.services.sheet_analyze import SheetAnalyzer
from sheets.services.sheet_client import build_sheets_client
from sheets.services.sheet_detect import detect_workbook
from sheets.services.sheet_intent import SheetIntent, detect_sheet_intent
from sheets.services.sheet_memory import SheetMemory
from sheets.services.sheet_qa_service import SheetQAService
from telegram_bot.adapters.oauth_ux import google_access_required_reply

logger = logging.getLogger("atlas.sheets.service")


class SheetService:
    def __init__(
        self,
        *,
        oauth: GoogleOAuthService | None = None,
        analyzer: SheetAnalyzer | None = None,
        memory: SheetMemory | None = None,
        qa: SheetQAService | None = None,
    ) -> None:
        self.oauth = oauth or GoogleOAuthService()
        self.analyzer = analyzer or SheetAnalyzer()
        self.memory = memory or SheetMemory()
        self.qa = qa or SheetQAService()

    def handle_intent(self, user: User, text: str) -> dict[str, Any] | None:
        has_active = bool(self.memory.active_workbook_id(user))
        intent = detect_sheet_intent(text, has_active_sheet=has_active)
        if intent.kind == "none":
            return None
        if intent.kind == "connect":
            return self.connect(user)
        if intent.kind == "list":
            return self.list_sheets(user, intent.query)
        if intent.kind == "open_url":
            return self.open_by_spreadsheet_id(user, intent.query)
        if intent.kind == "open":
            return self.open_sheet(user, intent.query or "portfolio")
        if intent.kind == "analyze":
            return self.analyze_active(user, question=text, mode=intent.mode, ticker=intent.query)
        return None

    def open_by_spreadsheet_id(self, user: User, spreadsheet_id: str) -> dict[str, Any]:
        """Open ANY spreadsheet by ID extracted from a Google Sheets URL (per Telegram user)."""
        sid = (spreadsheet_id or "").strip()
        if not sid:
            return {
                "ok": False,
                "handled": True,
                "reply": "I couldn't read a spreadsheet ID from that link. Paste a full Google Sheets URL.",
            }

        self.memory.remember_pending(user, sid)

        # Known local demo fixture IDs (dev/test only) — never invent data for other IDs
        demo_hit = build_sheets_client(access_token="demo:sheets", demo=True).load_workbook(sid)
        if demo_hit is not None:
            if not self.is_connected(user):
                self.connect_demo(user)
            else:
                state = self.ensure_state(user)
                if state.mode != SheetConnectionMode.DEMO and not self.is_live_connected(user):
                    self.connect_demo(user)
            return self._open_with_access(user, sid, access_mode="demo", after_oauth=False)

        # 1) Public / anyone-with-link — no OAuth
        public = load_public_workbook(sid)
        if public.payload is not None:
            state = self.ensure_state(user)
            state.mode = SheetConnectionMode.PUBLIC
            state.status = SheetSyncStatus.IDLE
            state.error_message = ""
            state.save(update_fields=["mode", "status", "error_message", "updated_at"])
            return self._open_with_access(
                user,
                sid,
                access_mode="public",
                after_oauth=False,
                preloaded=public.payload,
            )

        err = public.error
        code = err.code if err else "temporary"

        # 2) Private sheet + user already has live Google tokens
        if code in {"auth_required", "permission_denied"} and self.is_live_connected(user):
            return self._open_with_access(user, sid, access_mode="oauth", after_oauth=False)

        # 3) Auth required → Connect Google for THIS Telegram user
        if code in {"auth_required", "permission_denied"}:
            return self._oauth_prompt(user, sid, reason=code)

        # 4) Distinct failures — never claim "deleted" unless Google said not found
        if code == "not_found":
            return {
                "ok": False,
                "handled": True,
                "error_code": "not_found",
                "reply": (
                    "I couldn't find that Google Sheet — the link may be wrong "
                    "or the spreadsheet was deleted."
                ),
            }
        if code == "empty":
            return {
                "ok": False,
                "handled": True,
                "error_code": "empty",
                "reply": "That spreadsheet looks empty. Add some data, then paste the link again.",
            }
        return {
            "ok": False,
            "handled": True,
            "error_code": code or "temporary",
            "reply": (err.message if err else None)
            or "I couldn't read that spreadsheet right now. Please try again shortly.",
        }

    def _oauth_prompt(self, user: User, sid: str, *, reason: str) -> dict[str, Any]:
        if self.oauth.is_configured():
            started = self.oauth.start_auth(
                user,
                service=GoogleService.SHEETS,
                pending_spreadsheet_id=sid,
                pending_action="open_sheet",
            )
            if started.get("ok") and started.get("auth_url"):
                auth_url = started["auth_url"]
                return {
                    "ok": False,
                    "handled": True,
                    "needs_oauth": True,
                    "auth_url": auth_url,
                    "error_code": reason,
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
                or "I couldn't start Google authorization. Please try again shortly.",
            }
        return {
            "ok": False,
            "handled": True,
            "needs_oauth": True,
            "error_code": reason,
            "reply": (
                "📊 That Google Sheet needs authorization, but this server has no live "
                "Google OAuth configured.\n\n"
                "I won't pretend to analyze a different sheet. "
                "Set GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / PUBLIC_BASE_URL "
                "on the deployed backend."
            ),
        }

    def _open_with_access(
        self,
        user: User,
        spreadsheet_id: str,
        *,
        access_mode: str,
        after_oauth: bool,
        preloaded=None,
    ) -> dict[str, Any]:
        book, _ = SheetWorkbook.objects.update_or_create(
            user=user,
            spreadsheet_id=spreadsheet_id,
            defaults={
                "title": "Google Sheet",
                "is_trashed": False,
                "extra": {"access_mode": access_mode},
            },
        )
        # Always refresh access_mode on open
        extra = dict(book.extra or {})
        extra["access_mode"] = access_mode
        book.extra = extra
        book.save(update_fields=["extra", "updated_at"])

        loaded = self._load_and_index(user, book, preloaded=preloaded)
        if not loaded.get("ok"):
            return loaded
        book.refresh_from_db()
        self.memory.remember_open(user, book)
        if after_oauth:
            reply = (
                f"✅ Google connected. I've opened your spreadsheet (*{book.title}*). "
                "What would you like to know?"
            )
        elif access_mode == "public":
            reply = (
                "📊 Google Sheet detected.\n"
                "✅ I can access this spreadsheet.\n\n"
                f"Active sheet: *{book.title}*\n\n"
                "What would you like to know?"
            )
        else:
            reply = (
                "📊 Google Sheet detected. I've connected it and I'm ready to analyze the data.\n\n"
                f"Active sheet: *{book.title}*\n\n"
                "What would you like to know?"
            )
        return {
            "ok": True,
            "handled": True,
            "workbook": book,
            "findings": loaded.get("findings"),
            "access_mode": access_mode,
            "reply": reply,
        }

    def _open_live_spreadsheet(
        self, user: User, spreadsheet_id: str, *, after_oauth: bool
    ) -> dict[str, Any]:
        return self._open_with_access(
            user, spreadsheet_id, access_mode="oauth", after_oauth=after_oauth
        )

    def resume_pending_after_oauth(self, user: User, spreadsheet_id: str = "") -> dict[str, Any]:
        """Called from OAuth callback — open the sheet the user originally pasted."""
        state = self.ensure_state(user)
        state.mode = SheetConnectionMode.OAUTH
        state.save(update_fields=["mode", "updated_at"])
        sid = (spreadsheet_id or "").strip() or self.memory.pending_spreadsheet_id(user) or ""
        if not sid:
            return {
                "ok": True,
                "reply": (
                    "✅ Google connected. Return to Telegram and paste your Google Sheets URL, "
                    "or ask me to analyze your sheet."
                ),
            }
        return self._open_live_spreadsheet(user, sid, after_oauth=True)

    def ensure_state(self, user: User) -> SheetSyncState:
        state, _ = SheetSyncState.objects.get_or_create(user=user)
        return state

    def is_live_connected(self, user: User) -> bool:
        """True only when this Telegram user has real Google OAuth tokens (not demo)."""
        state = SheetSyncState.objects.filter(user=user).first()
        if state and state.mode == SheetConnectionMode.DEMO:
            # Demo mode never counts as live, even if a placeholder integration row exists
            token = self.oauth.get_valid_access_token(user, service=GoogleService.SHEETS)
            if not token:
                token = self.oauth.get_valid_access_token(user, service=GoogleService.DRIVE)
            return bool(token)
        return bool(
            self.oauth.get_valid_access_token(user, service=GoogleService.SHEETS)
            or self.oauth.get_valid_access_token(user, service=GoogleService.DRIVE)
        )

    def is_connected(self, user: User) -> bool:
        state = SheetSyncState.objects.filter(user=user).first()
        if state and state.mode in {SheetConnectionMode.DEMO, SheetConnectionMode.PUBLIC}:
            return True
        return self.is_live_connected(user)

    def connect_demo(self, user: User) -> SheetSyncState:
        integ, _ = user.google_integrations.get_or_create(
            service=GoogleService.SHEETS,
            defaults={
                "access_token_encrypted": encrypt_text("demo:sheets"),
                "refresh_token_encrypted": encrypt_text("demo:sheets"),
                "is_active": True,
                "scopes": ["spreadsheets.readonly.demo"],
            },
        )
        if not integ.is_active:
            integ.is_active = True
            integ.access_token_encrypted = encrypt_text("demo:sheets")
            integ.save(update_fields=["is_active", "access_token_encrypted", "updated_at"])
        state = self.ensure_state(user)
        state.mode = SheetConnectionMode.DEMO
        state.status = SheetSyncStatus.IDLE
        state.error_message = ""
        state.save(update_fields=["mode", "status", "error_message", "updated_at"])
        return state

    def connect(self, user: User) -> dict[str, Any]:
        if self.oauth.is_configured():
            started = self.oauth.start_auth(user, service=GoogleService.SHEETS)
            if started.get("ok") and started.get("auth_url"):
                auth_url = started["auth_url"]
                return {
                    "ok": True,
                    "handled": True,
                    "auth_url": auth_url,
                    "reply": google_access_required_reply(
                        auth_url,
                        purpose=(
                            "Connect Google once for Calendar, Gmail, Drive, and Sheets."
                        ),
                    ),
                }
        self.connect_demo(user)
        self.sync_catalog(user)
        return {
            "ok": True,
            "handled": True,
            "demo": True,
            "reply": (
                "Your spreadsheets are ready (local demo catalog).\n\n"
                "Try:\n"
                "• “Show my spreadsheets”\n"
                "• “Open my portfolio”\n"
                "• “What stands out?”"
            ),
        }

    def _client_for(self, user: User):
        state = self.ensure_state(user)
        # Prefer live tokens whenever available for this user
        token = self.oauth.get_valid_access_token(user, service=GoogleService.SHEETS)
        if not token:
            token = self.oauth.get_valid_access_token(user, service=GoogleService.DRIVE)
        if token:
            if state.mode != SheetConnectionMode.OAUTH:
                state.mode = SheetConnectionMode.OAUTH
                state.save(update_fields=["mode", "updated_at"])
            return build_sheets_client(access_token=token, demo=False)
        if state.mode == SheetConnectionMode.DEMO:
            return build_sheets_client(access_token="demo:sheets", demo=True)
        raise PermissionError("Sheets aren't connected.")

    def sync_catalog(self, user: User) -> dict[str, Any]:
        state = self.ensure_state(user)
        state.status = SheetSyncStatus.RUNNING
        state.save(update_fields=["status", "updated_at"])
        try:
            client = self._client_for(user)
            remotes = client.list_spreadsheets(page_size=40)
            for remote in remotes:
                SheetWorkbook.objects.update_or_create(
                    user=user,
                    spreadsheet_id=remote.id,
                    defaults={
                        "title": (remote.title or "Untitled")[:512],
                        "modified_time": remote.modified_time,
                        "sheet_names": remote.sheet_names or [],
                        "is_trashed": False,
                        "last_synced_at": timezone.now(),
                    },
                )
            state.status = SheetSyncStatus.IDLE
            state.last_synced_at = timezone.now()
            state.stats = {"listed": len(remotes)}
            state.save()
            return {"ok": True, "count": len(remotes)}
        except Exception as exc:  # noqa: BLE001
            state.status = SheetSyncStatus.FAILED
            state.error_message = type(exc).__name__[:200]
            state.save(update_fields=["status", "error_message", "updated_at"])
            return {"ok": False, "error": "I couldn't refresh your spreadsheets just now."}

    def list_sheets(self, user: User, query: str = "") -> dict[str, Any]:
        if not self.is_connected(user):
            self.connect_demo(user)
        self.sync_catalog(user)
        qs = SheetWorkbook.objects.filter(user=user, is_trashed=False)
        if query:
            qs = qs.filter(title__icontains=query)
        books = list(qs.order_by("-view_count", "-modified_time", "-updated_at")[:8])
        if not books:
            return {
                "ok": True,
                "handled": True,
                "reply": "I don't see any spreadsheets yet. Add one in Google Sheets, or use the demo portfolio.",
            }
        lines = [f"• *{b.title}*" for b in books]
        return {
            "ok": True,
            "handled": True,
            "reply": "Here are your spreadsheets:\n" + "\n".join(lines) + (
                "\n\nSay “open my portfolio” and I’ll dig in."
            ),
        }

    def open_sheet(self, user: User, query: str) -> dict[str, Any]:
        if not self.is_connected(user):
            self.connect_demo(user)
            self.sync_catalog(user)
        q = (query or "portfolio").strip()
        book = (
            SheetWorkbook.objects.filter(user=user, is_trashed=False, title__icontains=q)
            .order_by("-view_count", "-updated_at")
            .first()
        )
        if book is None:
            self.sync_catalog(user)
            book = (
                SheetWorkbook.objects.filter(user=user, is_trashed=False)
                .order_by("-view_count", "-updated_at")
                .first()
            )
        if book is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "I couldn't find a spreadsheet to open. Say “show my spreadsheets” first.",
            }
        loaded = self._load_and_index(user, book)
        if not loaded.get("ok"):
            return loaded
        book.refresh_from_db()
        self.memory.remember_open(user, book)
        analysis = loaded["findings"]
        reply = self.analyzer.format_analyst_reply(analysis, question=f"summarize {book.title}")
        return {
            "ok": True,
            "handled": True,
            "workbook": book,
            "findings": analysis,
            "reply": f"Opened *{book.title}*.\n\n" + reply,
        }

    def analyze_active(
        self,
        user: User,
        *,
        question: str,
        mode: str = "summary",
        ticker: str = "",
    ) -> dict[str, Any]:
        book = self._resolve_active(user)
        if book is None:
            return {
                "ok": False,
                "handled": True,
                "reply": (
                    "I don't have an active Google Sheet yet.\n\n"
                    "Paste a Google Sheets URL, or say “show my spreadsheets” / "
                    "“open my portfolio” so I know which data to use.\n\n"
                    "I won't guess with an unrelated demo spreadsheet."
                ),
            }
        assert book is not None
        loaded = self._load_and_index(user, book)
        if not loaded.get("ok"):
            return loaded
        findings = loaded["findings"]
        client_payload = loaded.get("payload")
        values = (
            client_payload.values_by_sheet
            if client_payload is not None
            else {}
        )

        # Keep legacy ticker shortcut when explicitly requested
        if mode == "ticker" and ticker:
            hits = [h for h in findings.get("holdings") or [] if h.get("ticker") == ticker.upper()]
            if not hits:
                return {
                    "ok": True,
                    "handled": True,
                    "reply": "I couldn't find that information in the spreadsheet.",
                    "findings": findings,
                }
            h = hits[0]
            reply = (
                f"*{h.get('ticker')}* in *{book.title}* — "
                f"{h.get('company') or 'holding'} ({h.get('sector') or 'n/a'}).\n"
                f"• Value: {self.analyzer._fmt_money(h.get('value'))}\n"
                f"• P/L: {self.analyzer._fmt_pct(h.get('pl_pct'))}\n"
                f"• Weight: {h.get('weight') or h.get('weight_calc') or 'n/a'}%"
            )
            return {"ok": True, "handled": True, "reply": reply, "findings": findings}

        if mode in {"best", "worst", "risks", "recs", "trends", "portfolio"} and mode != "qa":
            # Still allow structured modes, but prefer QA for open-ended wording
            qlow = (question or "").lower()
            open_ended = any(
                x in qlow
                for x in (
                    "why",
                    "explain",
                    "compare",
                    "percentage",
                    "how much",
                    "what should",
                    "unusual",
                    "simple terms",
                    "pay attention",
                    "which company",
                    "which metric",
                    "total",
                    "growth",
                )
            )
            if not open_ended:
                focused = {**findings, "mode": mode, "focus": mode}
                if mode == "recs":
                    extra = list(focused.get("recommendations") or [])
                    extra.insert(
                        0,
                        "Charts that help: sector pie, P/L bar by ticker, and portfolio value over time.",
                    )
                    focused["recommendations"] = extra[:5]
                reply = self.analyzer.format_analyst_reply(focused, question=question)
                self.memory.remember_open(user, book)
                return {"ok": True, "handled": True, "reply": reply, "findings": focused}

        qa = self.qa.answer(
            question=question,
            title=book.title,
            values_by_sheet=values,
            findings=findings,
        )
        self.memory.remember_open(user, book)
        return {
            "ok": True,
            "handled": True,
            "reply": qa.get("reply") or "I couldn't find that information in the spreadsheet.",
            "findings": findings,
            "qa_source": qa.get("source"),
        }

    def _resolve_active(self, user: User) -> SheetWorkbook | None:
        wid = self.memory.active_workbook_id(user)
        if wid:
            book = SheetWorkbook.objects.filter(user=user, id=wid, is_trashed=False).first()
            if book:
                return book
        return None

    def _load_and_index(
        self, user: User, book: SheetWorkbook, *, preloaded=None
    ) -> dict[str, Any]:
        access_mode = str((book.extra or {}).get("access_mode") or "").strip()
        payload = preloaded
        try:
            if payload is None and access_mode == "public":
                result = load_public_workbook(book.spreadsheet_id)
                if result.payload is not None:
                    payload = result.payload
                elif result.error and result.error.code == "auth_required" and self.is_live_connected(user):
                    access_mode = "oauth"
                    extra = dict(book.extra or {})
                    extra["access_mode"] = "oauth"
                    book.extra = extra
                    book.save(update_fields=["extra", "updated_at"])
                elif result.error:
                    return self._access_error_reply(book, result.error)

            if payload is None and access_mode != "public":
                try:
                    client = self._client_for(user)
                    payload = client.load_workbook(book.spreadsheet_id)
                except PermissionError:
                    result = load_public_workbook(book.spreadsheet_id)
                    if result.payload is not None:
                        payload = result.payload
                        extra = dict(book.extra or {})
                        extra["access_mode"] = "public"
                        book.extra = extra
                        book.save(update_fields=["extra", "updated_at"])
                    elif result.error:
                        return self._access_error_reply(book, result.error)
                    else:
                        return {
                            "ok": False,
                            "handled": True,
                            "reply": "Connect your spreadsheets first — say “connect my Sheets”.",
                        }
        except SheetAccessError as exc:
            return self._access_error_reply(book, exc)
        except Exception:  # noqa: BLE001
            return {
                "ok": False,
                "handled": True,
                "error_code": "temporary",
                "reply": f"I couldn't open *{book.title}* right now. Try again in a moment.",
            }

        if payload is None:
            # Last resort: try public once more before claiming revoked
            result = load_public_workbook(book.spreadsheet_id)
            if result.payload is not None:
                payload = result.payload
            elif result.error:
                return self._access_error_reply(book, result.error)
            else:
                return {
                    "ok": False,
                    "handled": True,
                    "error_code": "temporary",
                    "reply": (
                        f"I couldn't read *{book.title}* from Google right now. "
                        "Please try again shortly."
                    ),
                }

        if not any(payload.values_by_sheet.values()):
            return {
                "ok": False,
                "handled": True,
                "error_code": "empty",
                "reply": f"*{book.title}* looks empty. Add headers and a few rows, then ask again.",
            }

        detected = detect_workbook(payload.values_by_sheet)
        book.title = payload.title[:512] or book.title
        book.sheet_names = payload.sheet_names
        book.content_hash = payload.content_hash
        book.detected = detected
        book.view_count += 1
        book.last_synced_at = timezone.now()
        book.save()

        findings = self.analyzer.analyze(
            title=book.title,
            values_by_sheet=payload.values_by_sheet,
            content_hash=payload.content_hash,
            mode="summary",
        )
        return {"ok": True, "findings": findings, "payload": payload}

    def _access_error_reply(self, book: SheetWorkbook, err: SheetAccessError) -> dict[str, Any]:
        if err.code == "not_found":
            reply = (
                "I couldn't find that Google Sheet — it may have been deleted "
                "or the link is wrong."
            )
        elif err.code == "auth_required":
            reply = (
                f"*{book.title}* needs Google authorization. "
                "Say “connect my Sheets” or paste the link again to Connect Google."
            )
        elif err.code == "permission_denied":
            reply = (
                f"I don't have permission to read *{book.title}*. "
                "Ask the owner to share it with you, or Connect Google with an account that can."
            )
        elif err.code == "empty":
            reply = f"*{book.title}* looks empty. Add some data, then ask again."
        else:
            reply = (
                f"I couldn't read *{book.title}* right now (temporary Google issue). "
                "Please try again shortly."
            )
        return {
            "ok": False,
            "handled": True,
            "error_code": err.code,
            "reply": reply,
        }
