"""Telegram update handlers — text + voice share ConversationProcessor."""

from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from ai.services.voice_transcription import (
    VoiceTranscriptionError,
    VoiceTranscriptionService,
    user_facing_voice_error,
)
from telegram_bot.adapters.telegram_adapter import TelegramAdapter
from telegram_bot.services.conversation_processor import ConversationProcessor

logger = logging.getLogger("atlas.telegram.handlers")

_processor = ConversationProcessor()
_voice_stt = VoiceTranscriptionService()


def _user_kwargs(update: Update) -> dict:
    tg_user = update.effective_user
    assert tg_user is not None
    return {
        "telegram_id": tg_user.id,
        "username": tg_user.username or "",
        "first_name": tg_user.first_name or "",
        "last_name": tg_user.last_name or "",
    }


@sync_to_async
def _handle_start_sync(**kwargs) -> str:
    return _processor.handle_start(**kwargs)


@sync_to_async
def _handle_text_sync(**kwargs) -> str:
    return _processor.handle_text(**kwargs)


@sync_to_async
def _transcribe_sync(**kwargs):
    return _voice_stt.transcribe_bytes(**kwargs)


async def _safe_reply(update: Update, text: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    try:
        await TelegramAdapter.reply_text(update.message, text, context=context)
    except TelegramError:
        logger.exception(
            "event=telegram_send_failed chat_id=%s",
            getattr(update.effective_chat, "id", None),
        )


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    logger.info("event=incoming_start telegram_id=%s", update.effective_user.id)
    try:
        await TelegramAdapter.send_typing(update.message)
        reply = await _handle_start_sync(
            **_user_kwargs(update),
            telegram_message_id=update.message.message_id,
        )
        await _safe_reply(update, reply, context)
    except Exception:
        logger.exception("event=handler_start_error telegram_id=%s", update.effective_user.id)
        await _safe_reply(
            update,
            "Something glitched on my side. Try /start once more in a few seconds.",
            context,
        )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_user or not update.message:
        return

    text = (update.message.text or "").strip()
    if not text:
        await _safe_reply(
            update,
            "I didn't catch that — mind typing it out?",
            context,
        )
        return

    if text.startswith("/") and not text.lower().startswith("/start"):
        logger.info(
            "event=unsupported_command command=%s telegram_id=%s",
            text.split()[0][:32],
            update.effective_user.id,
        )
        await _safe_reply(
            update,
            "No commands needed — just talk normally.\n"
            "Send /start anytime if you want to redo the intro.",
            context,
        )
        return

    logger.info(
        "event=incoming_text telegram_id=%s chars=%s",
        update.effective_user.id,
        len(text),
    )
    try:
        await TelegramAdapter.send_typing(update.message)
        reply = await _handle_text_sync(
            **_user_kwargs(update),
            text=text,
            telegram_message_id=update.message.message_id,
            input_source="text",
        )
        await _safe_reply(update, reply, context)
    except Exception:
        logger.exception("event=handler_text_error telegram_id=%s", update.effective_user.id)
        await _safe_reply(
            update,
            "Something glitched on my side. Try again in a moment.",
            context,
        )


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Voice → Groq Whisper → existing ConversationProcessor (same as typed text)."""
    if not update.effective_user or not update.message:
        return

    voice = update.message.voice or update.message.video_note
    if voice is None:
        await _safe_reply(
            update,
            "🎙️ I couldn't process that voice message. Please try again.",
            context,
        )
        return

    duration = float(getattr(voice, "duration", 0) or 0)
    max_dur = int(getattr(settings, "MAX_VOICE_DURATION_SECONDS", 120) or 120)
    telegram_id = update.effective_user.id

    logger.info(
        "event=incoming_voice telegram_id=%s duration=%s",
        telegram_id,
        duration,
    )

    # Immediate UX ack (then typing while STT + Atlas run)
    await _safe_reply(update, "🎙️ Listening…", context)

    if duration > max_dur:
        await _safe_reply(
            update,
            "🎙️ That voice message is too long. Please send a shorter message.",
            context,
        )
        return

    try:
        await TelegramAdapter.send_typing(update.message)
        try:
            tg_file = await context.bot.get_file(voice.file_id)
            audio_bytes = bytes(await tg_file.download_as_bytearray())
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "event=voice_download_failed telegram_id=%s err=%s",
                telegram_id,
                type(exc).__name__,
            )
            await _safe_reply(
                update,
                "🎙️ I couldn't process that voice message. Please try again.",
                context,
            )
            return

        if not audio_bytes:
            await _safe_reply(
                update,
                "🎙️ I couldn't process that voice message. Please try again.",
                context,
            )
            return

        is_video_note = bool(update.message.video_note)
        filename = "voice.ogg" if not is_video_note else "video_note.mp4"
        mime = "audio/ogg" if not is_video_note else "video/mp4"

        try:
            result = await _transcribe_sync(
                audio_bytes=audio_bytes,
                filename=filename,
                mime_type=mime,
                duration_seconds=duration,
            )
        except VoiceTranscriptionError as exc:
            logger.info(
                "event=voice_stt_failed telegram_id=%s code=%s",
                telegram_id,
                exc.code,
            )
            await _safe_reply(update, user_facing_voice_error(exc), context)
            return
        except Exception:
            logger.exception("event=voice_stt_unexpected telegram_id=%s", telegram_id)
            await _safe_reply(
                update,
                user_facing_voice_error(VoiceTranscriptionError("api_error")),
                context,
            )
            return

        transcript = (result.text or "").strip()
        if not transcript:
            await _safe_reply(
                update,
                user_facing_voice_error(VoiceTranscriptionError("empty_transcript")),
                context,
            )
            return

        logger.info(
            "event=voice_transcribed telegram_id=%s chars=%s",
            telegram_id,
            len(transcript),
        )

        # Same path as typed text — answer the spoken question (not echo transcript)
        await TelegramAdapter.send_typing(update.message)
        reply = await _handle_text_sync(
            **_user_kwargs(update),
            text=transcript,
            telegram_message_id=update.message.message_id,
            input_source="voice",
        )
        await _safe_reply(update, reply, context)
    except Exception:
        logger.exception("event=handler_voice_error telegram_id=%s", telegram_id)
        await _safe_reply(
            update,
            "🎙️ Something glitched while handling that voice note. "
            "Please try again or type your question.",
            context,
        )


async def handle_photo_or_document(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if not update.effective_user or not update.message:
        return

    # Photos are not supported for financial document analysis in M5
    if update.message.photo and not update.message.document:
        logger.info(
            "event=incoming_photo telegram_id=%s deferred=1",
            update.effective_user.id,
        )
        await _safe_reply(
            update,
            "I analyze financial documents as PDF/TXT/Markdown for now — "
            "send the filing as a file and I'll dig in.",
            context,
        )
        return

    doc = update.message.document
    if not doc:
        await _safe_reply(
            update,
            "Send a PDF, TXT, or Markdown filing and I'll analyze it.",
            context,
        )
        return

    filename = doc.file_name or "document.pdf"
    logger.info(
        "event=incoming_document telegram_id=%s name=%s size=%s",
        update.effective_user.id,
        filename[:80],
        doc.file_size,
    )
    # UX: never leave the user hanging — acknowledge immediately, then process.
    await _safe_reply(
        update,
        "📄 Financial report received.\nI'm analyzing the document now…",
        context,
    )
    try:
        await TelegramAdapter.send_typing(update.message)
        tg_file = await context.bot.get_file(doc.file_id)
        file_bytes = bytes(await tg_file.download_as_bytearray())
        reply = await _handle_document_sync(
            **_user_kwargs(update),
            file_bytes=file_bytes,
            filename=filename,
            mime_type=doc.mime_type or "",
            telegram_message_id=update.message.message_id,
        )
        await TelegramAdapter.send_typing(update.message)
        await _safe_reply(update, reply, context)
    except Exception:
        logger.exception(
            "event=handler_document_error telegram_id=%s",
            update.effective_user.id,
        )
        lower_name = filename.lower()
        if lower_name.endswith(".pdf") or "pdf" in (doc.mime_type or "").lower():
            err = (
                "📄 I received the document, but I couldn't process this PDF. "
                "Please try another PDF."
            )
        else:
            err = (
                "📄 I received the document, but I couldn't process it. "
                "Try a text-based PDF, TXT, or Markdown export."
            )
        await _safe_reply(update, err, context)


@sync_to_async
def _handle_document_sync(**kwargs) -> str:
    return _processor.handle_document(**kwargs)


async def handle_unsupported(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stickers, animations, contacts, locations, etc."""
    if not update.message:
        return
    logger.info(
        "event=incoming_unsupported telegram_id=%s",
        update.effective_user.id if update.effective_user else "-",
    )
    await _safe_reply(
        update,
        "I work best with text or a short voice note. "
        "Tell me what you need in a sentence or two.",
        context,
    )
