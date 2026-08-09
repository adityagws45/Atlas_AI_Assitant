"""Sync conversation processor used by Telegram handlers."""

from __future__ import annotations

import logging
import re

from django.db import OperationalError

from accounts.services.user_service import UserService
from conversation.services.message_service import MessageService
from conversation.services.onboarding_service import OnboardingService, is_emoji_only
from conversation.services.orchestrator import ConversationOrchestrator
from documents.models import DocumentSource
from documents.services.document_intent import is_document_compare, is_document_question
from documents.services.document_memory import DocumentMemory
from documents.services.document_pipeline import DocumentPipeline
from documents.services.document_qa_service import DocumentQAService
from drive.services.drive_intent import detect_drive_intent
from drive.services.drive_service import DriveService
from gcalendar.services.calendar_intent import detect_calendar_intent
from gcalendar.services.calendar_service import CalendarService
from gmail.services.gmail_intent import detect_gmail_intent
from gmail.services.gmail_service import GmailService
from sheets.services.sheet_intent import detect_sheet_intent
from sheets.services.sheet_service import SheetService

logger = logging.getLogger("atlas.telegram.processor")

FRIENDLY_ERROR = (
    "I'm having trouble pulling that right now. Try again in a moment — "
    "or send /start if you want a fresh intro."
)

RESTART_PHRASES = {
    "restart",
    "start over",
    "reset",
    "redo intro",
    "redo onboarding",
}

MAX_STORE_CHARS = 4000


class ConversationProcessor:
    """Telegram brain: user → messages → onboarding / prefs / docs / Drive / Sheets / AI."""

    def __init__(self, orchestrator: ConversationOrchestrator | None = None) -> None:
        self.onboarding = OnboardingService()
        self.orchestrator = orchestrator or ConversationOrchestrator()
        self.doc_pipeline = DocumentPipeline()
        self.doc_memory = DocumentMemory()
        self.doc_qa = DocumentQAService()
        self.drive = DriveService()
        self.sheets = SheetService()
        self.gmail = GmailService()
        self.calendar = CalendarService()

    def handle_start(
        self,
        *,
        telegram_id: int,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        telegram_message_id: int | None = None,
        force_reset: bool = False,
    ) -> str:
        try:
            user, created = UserService.get_or_create_from_telegram(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            conversation = MessageService.get_or_create_active_conversation(user)
            if user.onboarding_completed and not force_reset and not created:
                reply = self.onboarding.welcome_back(user)
                event = "welcome_back"
            else:
                reply = self.onboarding.start(user, force_reset=True)
                event = "onboarding_start"
            MessageService.save_user_message(
                conversation,
                "/start",
                telegram_message_id=telegram_message_id,
                metadata={"event": event},
            )
            MessageService.save_assistant_message(
                conversation,
                reply,
                metadata={"event": event},
            )
            logger.info(
                "event=start_ok telegram_id=%s new_user=%s mode=%s",
                telegram_id,
                created,
                event,
            )
            return reply
        except OperationalError:
            logger.exception("event=start_db_error telegram_id=%s", telegram_id)
            return FRIENDLY_ERROR
        except Exception:
            logger.exception("event=start_error telegram_id=%s", telegram_id)
            return FRIENDLY_ERROR

    def handle_document(
        self,
        *,
        telegram_id: int,
        file_bytes: bytes,
        filename: str,
        mime_type: str = "",
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        telegram_message_id: int | None = None,
    ) -> str:
        try:
            user, _ = UserService.get_or_create_from_telegram(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            conversation = MessageService.get_or_create_active_conversation(user)
            MessageService.save_user_message(
                conversation,
                f"[uploaded: {filename}]",
                telegram_message_id=telegram_message_id,
                metadata={"upload": True, "filename": filename[:180]},
            )
            try:
                doc = self.doc_pipeline.ingest_bytes(
                    user,
                    data=file_bytes,
                    filename=filename,
                    mime_type=mime_type,
                    source=DocumentSource.TELEGRAM,
                )
            except ValueError as exc:
                raw = str(exc).strip()
                reply = raw if raw.startswith("📄") else f"📄 {raw}"
                MessageService.save_assistant_message(
                    conversation,
                    reply,
                    metadata={"pipeline": "document_upload", "ok": False},
                )
                return reply

            self.doc_memory.remember_upload(user, doc)
            # Answer a question queued while the PDF was still processing
            pending_q = self.doc_memory.pop_pending_question(user)
            if pending_q and is_document_question(pending_q):
                qa = self.doc_qa.answer(
                    user,
                    pending_q,
                    document_ids=[str(doc.id)],
                    compare=False,
                )
                if qa.get("ok") and qa.get("reply"):
                    reply = self.orchestrator.formatter.format(
                        "Document ready.\n\n" + str(qa["reply"])
                    )
                else:
                    reply = _document_ready_reply(doc)
            else:
                reply = _document_ready_reply(doc)
            MessageService.save_assistant_message(
                conversation,
                reply,
                metadata={
                    "pipeline": "document_upload",
                    "ok": True,
                    "document_id": str(doc.id),
                    "answered_pending": bool(pending_q),
                },
            )
            from conversation.services.entity_context import EntityContext

            EntityContext().remember(
                user,
                document_id=str(doc.id),
                company=(doc.company or "") or None,
                topic="document",
            )
            logger.info(
                "event=document_upload_ok telegram_id=%s doc_id=%s",
                telegram_id,
                doc.id,
            )
            return reply
        except OperationalError:
            logger.exception("event=document_db_error telegram_id=%s", telegram_id)
            return FRIENDLY_ERROR
        except Exception:
            logger.exception("event=document_error telegram_id=%s", telegram_id)
            lower = (filename or "").lower()
            if lower.endswith(".pdf") or "pdf" in (mime_type or "").lower():
                return (
                    "📄 I received the document, but I couldn't process this PDF. "
                    "Please try another PDF."
                )
            return (
                "📄 I received the document, but I couldn't process it. "
                "Try a text-based PDF, TXT, or Markdown export."
            )

    def handle_text(
        self,
        *,
        telegram_id: int,
        text: str,
        username: str = "",
        first_name: str = "",
        last_name: str = "",
        telegram_message_id: int | None = None,
        input_source: str = "text",
    ) -> str:
        text = (text or "").strip()
        if not text:
            return "I didn't catch that — mind sending it again in a line or two?"

        if len(text) > MAX_STORE_CHARS:
            text = text[:MAX_STORE_CHARS]

        if _normalize_phrase(text) in RESTART_PHRASES:
            return self.handle_start(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
                telegram_message_id=telegram_message_id,
                force_reset=True,
            )

        try:
            user, _ = UserService.get_or_create_from_telegram(
                telegram_id=telegram_id,
                username=username,
                first_name=first_name,
                last_name=last_name,
            )
            conversation = MessageService.get_or_create_active_conversation(user)
            from conversation.models import ContentType

            content_type = (
                ContentType.VOICE
                if input_source == "voice"
                else ContentType.TEXT
            )
            MessageService.save_user_message(
                conversation,
                text,
                content_type=content_type,
                telegram_message_id=telegram_message_id,
                metadata={
                    "emoji_only": is_emoji_only(text),
                    "input_source": input_source or "text",
                },
            )

            # Calendar BEFORE Sheets — schedule questions must never hit Sheets/finance.
            has_cal_ctx = bool(
                user.onboarding_completed and self.calendar.memory.has_recent_context(user)
            )
            cal_intent = (
                detect_calendar_intent(text, has_calendar_context=has_cal_ctx)
                if user.onboarding_completed
                else None
            )
            if cal_intent is not None and cal_intent.kind != "none":
                cal_result = self.calendar.handle_intent(user, text)
                if cal_result is not None:
                    reply = self.orchestrator.formatter.format(
                        cal_result.get("reply")
                        or "I couldn't complete that calendar request."
                    )
                    metadata = {
                        "onboarding": False,
                        "pipeline": "calendar",
                        "ok": bool(cal_result.get("ok")),
                        "calendar_intent": cal_intent.kind,
                        "needs_oauth": bool(cal_result.get("needs_oauth")),
                    }
                    MessageService.save_assistant_message(
                        conversation,
                        reply,
                        metadata=metadata,
                    )
                    logger.info(
                        "event=text_ok telegram_id=%s onboarding=%s ai=%s chars=%s",
                        telegram_id,
                        False,
                        True,
                        len(text),
                    )
                    return reply

            # Gmail BEFORE Sheets — inbox questions / email follow-ups must not hit
            # Sheets catch-all or finance routing.
            has_gmail_ctx = bool(
                user.onboarding_completed and self.gmail.memory.has_recent_context(user)
            )
            gmail_intent = (
                detect_gmail_intent(text, has_gmail_context=has_gmail_ctx)
                if user.onboarding_completed
                else None
            )
            if gmail_intent is not None and gmail_intent.kind != "none":
                gmail_result = self.gmail.handle_intent(user, text) or {}
                reply = self.orchestrator.formatter.format(
                    gmail_result.get("reply")
                    or "I couldn't complete that email request."
                )
                metadata = {
                    "onboarding": False,
                    "pipeline": "gmail",
                    "ok": bool(gmail_result.get("ok")),
                    "gmail_intent": gmail_intent.kind,
                    "needs_oauth": bool(gmail_result.get("needs_oauth")),
                }
                MessageService.save_assistant_message(
                    conversation,
                    reply,
                    metadata=metadata,
                )
                logger.info(
                    "event=text_ok telegram_id=%s onboarding=%s ai=%s chars=%s",
                    telegram_id,
                    False,
                    True,
                    len(text),
                )
                return reply

            # Sheets / portfolio before Drive — "open my portfolio" is finance data,
            # not a Drive file import. One assistant, two sources; sheets win on overlap.
            # Exception: ambiguous "biggest risks" / summary must not auto-open a demo
            # portfolio over an active filing (demo doc Q&A path).
            has_active_sheet = bool(
                user.onboarding_completed and self.sheets.memory.active_workbook_id(user)
            )
            sheet_intent = (
                detect_sheet_intent(text, has_active_sheet=has_active_sheet)
                if user.onboarding_completed
                else None
            )
            if sheet_intent is not None and sheet_intent.kind != "none":
                if sheet_intent.kind != "open_url" and _should_defer_sheet_to_documents(
                    user=user,
                    text=text,
                    sheet_intent=sheet_intent,
                    doc_memory=self.doc_memory,
                    sheet_memory=self.sheets.memory,
                ):
                    sheet_intent = None
            if sheet_intent is not None and sheet_intent.kind != "none":
                sheet_result = self.sheets.handle_intent(user, text) or {}
                reply = self.orchestrator.formatter.format(
                    sheet_result.get("reply")
                    or "I couldn't complete that spreadsheet request."
                )
                metadata: dict = {
                    "onboarding": False,
                    "pipeline": "sheets",
                    "ok": bool(sheet_result.get("ok")),
                    "sheet_intent": sheet_intent.kind,
                    "needs_oauth": bool(sheet_result.get("needs_oauth")),
                }
                MessageService.save_assistant_message(
                    conversation,
                    reply,
                    metadata=metadata,
                )
                logger.info(
                    "event=text_ok telegram_id=%s onboarding=%s ai=%s chars=%s",
                    telegram_id,
                    False,
                    True,
                    len(text),
                )
                return reply

            # Drive library intents beat preference/sector short-circuits
            drive_intent = (
                detect_drive_intent(text)
                if user.onboarding_completed
                else None
            )
            if drive_intent is not None and drive_intent.kind != "none":
                drive_result = self.drive.handle_intent(user, text) or {}
                reply = self.orchestrator.formatter.format(
                    drive_result.get("reply")
                    or "I couldn't complete that file request."
                )
                metadata = {
                    "onboarding": False,
                    "pipeline": "drive",
                    "ok": bool(drive_result.get("ok")),
                    "drive_intent": drive_intent.kind,
                    "needs_oauth": bool(drive_result.get("needs_oauth")),
                }
                if (
                    drive_intent.kind == "import"
                    and drive_result.get("ok")
                    and drive_result.get("document")
                    and is_document_question(text)
                ):
                    qa = self.doc_qa.answer(
                        user,
                        text,
                        document_ids=[str(drive_result["document"].id)],
                        compare=False,
                    )
                    if qa.get("ok") and qa.get("reply"):
                        reply = self.orchestrator.formatter.format(qa["reply"])
                        metadata["pipeline"] = "drive_import_qa"
                MessageService.save_assistant_message(
                    conversation,
                    reply,
                    metadata=metadata,
                )
                logger.info(
                    "event=text_ok telegram_id=%s onboarding=%s ai=%s chars=%s",
                    telegram_id,
                    False,
                    True,
                    len(text),
                )
                return reply

            result = self.onboarding.process_message(user, text)

            metadata = {"onboarding": result.get("onboarding", False)}
            if result.get("delegate_to_ai"):
                from conversation.services.entity_context import EntityContext
                from conversation.services.finance_fast_path import (
                    try_finance_fast_answer,
                )
                from conversation.services.market_fast_path import (
                    try_market_move_fast_answer,
                )
                from finance.utils.ticker_resolve import resolve_symbol, resolve_symbols

                entities = EntityContext()
                # If a PDF is still processing, queue the question
                if self.doc_memory.processing_document_ids(user) and (
                    is_document_question(text)
                    or is_document_compare(text)
                    or _looks_like_doc_followup(text)
                ):
                    self.doc_memory.remember_pending_question(user, text)
                    reply = (
                        "Still processing the report — I'll answer as soon as it's ready."
                    )
                    metadata.update(
                        {"pipeline": "document_pending", "ok": True, "queued": True}
                    )
                else:
                    active_ids = self.doc_memory.active_document_ids(user)
                    if active_ids and (
                        is_document_question(text)
                        or is_document_compare(text)
                        or _looks_like_doc_followup(text)
                    ):
                        qa = self.doc_qa.answer(
                            user,
                            text,
                            document_ids=active_ids,
                            compare=is_document_compare(text),
                        )
                        reply = self.orchestrator.formatter.format(
                            qa.get("reply")
                            or "I couldn't pull a clean take from the report."
                        )
                        metadata.update(
                            {
                                "pipeline": "document_qa",
                                "ok": bool(qa.get("ok")),
                                "document_ids": qa.get("document_ids") or active_ids,
                            }
                        )
                        entities.remember(
                            user,
                            document_id=str(active_ids[0]),
                            topic="document",
                        )
                    else:
                        default_sym = entities.resolve_symbol(user, text)
                        clarified = False
                        if (
                            default_sym is None
                            and re.search(
                                r"\b(its|it'?s|their|the company|the stock)\b",
                                text,
                                re.IGNORECASE,
                            )
                            and not resolve_symbol(text)
                        ):
                            amb = entities.ambiguity_prompt(user)
                            if amb:
                                reply = amb
                                metadata.update(
                                    {"pipeline": "entity_clarify", "ok": True}
                                )
                                clarified = True

                        if not clarified:
                            market = try_market_move_fast_answer(
                                text, default_symbol=default_sym
                            )
                            if market:
                                reply = self.orchestrator.formatter.format(
                                    market["reply"]
                                )
                                metadata.update(market.get("metadata") or {})
                                sym = (market.get("metadata") or {}).get("symbol")
                                if sym:
                                    entities.remember(
                                        user, symbol=str(sym), topic="market"
                                    )
                            else:
                                fast = try_finance_fast_answer(
                                    text, default_symbol=default_sym
                                )
                                if fast:
                                    reply = self.orchestrator.formatter.format(
                                        fast["reply"]
                                    )
                                    metadata.update(fast.get("metadata") or {})
                                    sym = (fast.get("metadata") or {}).get("symbol")
                                    if sym:
                                        entities.remember(
                                            user, symbol=str(sym), topic="finance"
                                        )
                                else:
                                    ai_result = self.orchestrator.process(
                                        user, conversation, text
                                    )
                                    reply = ai_result["reply"]
                                    metadata.update(ai_result.get("metadata") or {})
                                    named = resolve_symbols(text) or []
                                    one = resolve_symbol(text)
                                    if not named and one:
                                        named = [one]
                                    if len(named) >= 2:
                                        entities.remember(
                                            user,
                                            symbol=named[0],
                                            alt_symbols=named[1:],
                                            topic="compare",
                                        )
                                    elif named:
                                        entities.remember(
                                            user, symbol=named[0], topic="research"
                                        )
            else:
                reply = self.orchestrator.formatter.format(result.get("reply") or "")

            MessageService.save_assistant_message(
                conversation,
                reply,
                metadata=metadata,
            )
            logger.info(
                "event=text_ok telegram_id=%s onboarding=%s ai=%s chars=%s",
                telegram_id,
                result.get("onboarding"),
                bool(result.get("delegate_to_ai")),
                len(text),
            )
            return (reply or "").strip() or FRIENDLY_ERROR
        except OperationalError:
            logger.exception("event=text_db_error telegram_id=%s", telegram_id)
            return FRIENDLY_ERROR
        except Exception:
            logger.exception("event=text_error telegram_id=%s", telegram_id)
            return FRIENDLY_ERROR


def _normalize_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _document_ready_reply(doc) -> str:
    """Telegram-ready completion message after successful ingestion."""
    from documents.models import DocumentKind

    meta = doc.metadata or {}
    company = (doc.company or meta.get("company") or "").strip()
    kind_raw = str(doc.document_kind or meta.get("kind") or "").strip()
    kind_label = str(meta.get("kind_label") or "").strip()
    if not kind_label and kind_raw and kind_raw != DocumentKind.OTHER.value:
        try:
            kind_label = DocumentKind(kind_raw).label
        except Exception:
            kind_label = kind_raw
    if kind_label.lower() in {"", "other", "financial document"}:
        kind_label = ""
    fiscal = (doc.fiscal_period or meta.get("fiscal_period") or "").strip()
    pages = doc.page_count
    title = (doc.title or doc.original_filename or "Financial report").strip()

    overview: list[str] = []
    if company:
        overview.append(f"• Company: {company}")
    overview.append(f"• Document: {title}")
    if kind_label:
        bit = kind_label
        if fiscal and fiscal not in bit:
            bit = f"{bit} ({fiscal})"
        overview.append(f"• Type: {bit}")
    elif fiscal:
        overview.append(f"• Period: {fiscal}")
    if pages:
        overview.append(f"• Pages: {pages}")

    return (
        "📄 *Document ready*\n"
        + "\n".join(overview)
        + "\n\nAsk me anything about it — revenue, risks, strategy, or a short summary."
    )


_DOC_CONTEXT_CUES = re.compile(
    r"\b("
    r"filing|10[\s\-]?k|10[\s\-]?q|financial report|"
    r"this (report|document|filing|deck|pdf)|"
    r"the (report|document|filing|pdf)|"
    r"in (this|the) (report|filing|document)|"
    r"according to the (report|filing)|"
    r"from the (report|filing|document)|"
    r"uploaded (report|document|pdf|file)|"
    r"summarize (this|the) (report|document|filing|pdf)"
    r")\b",
    re.IGNORECASE,
)

_SHEET_EXPLICIT = re.compile(
    r"\b(portfolio|spreadsheet|workbook|holdings? sheet|my sheet|google sheets?|"
    r"this sheet|the sheet|that sheet|active sheet)\b",
    re.IGNORECASE,
)


def _should_defer_sheet_to_documents(
    *,
    user,
    text: str,
    sheet_intent,
    doc_memory,
    sheet_memory,
) -> bool:
    """
    Ambiguous risk/summary phrasing matches both Sheets and Documents.
    Prefer an already-active filing when the user is talking about the report,
    or when no portfolio sheet has been opened yet.

    Never defer an explicit Sheets URL. Prefer the ACTIVE sheet when one is set
    unless the user clearly references a filing/document.
    """
    if getattr(sheet_intent, "kind", "") == "open_url":
        return False
    if sheet_intent.kind != "analyze" or sheet_intent.mode not in {
        "risks",
        "summary",
        "best",
        "worst",
        "recs",
        "analysis",
        "qa",
    }:
        return False
    if _SHEET_EXPLICIT.search(text or ""):
        return False
    if sheet_memory.active_workbook_id(user) and not (
        _DOC_CONTEXT_CUES.search(text or "") or _looks_like_doc_followup(text)
    ):
        # Active sheet owns follow-ups unless the user is clearly asking about a filing
        return False
    active_docs = doc_memory.active_document_ids(user)
    if not active_docs:
        return False
    if not (
        is_document_question(text)
        or _DOC_CONTEXT_CUES.search(text or "")
        or _looks_like_doc_followup(text)
    ):
        return False
    if _DOC_CONTEXT_CUES.search(text or "") or _looks_like_doc_followup(text):
        return True
    return not sheet_memory.active_workbook_id(user)


def _looks_like_doc_followup(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        p in lower
        for p in (
            "what about",
            "and the",
            "from the report",
            "in the filing",
            "that section",
            "go deeper",
            "more detail",
            "elaborate",
            "which of those",
            "which of these",
            "those risks",
            "these risks",
            "that risk",
            "most important",
            "the biggest one",
            "net income",
            "this report",
            "the report",
            "this document",
            "this pdf",
            "this filing",
        )
    )
