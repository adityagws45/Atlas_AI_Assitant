"""Build the python-telegram-bot Application (polling or webhook)."""

from __future__ import annotations

import logging

from django.conf import settings
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from telegram_bot.handlers.message_handler import (
    handle_photo_or_document,
    handle_start,
    handle_text_message,
    handle_unsupported,
    handle_voice_message,
)

logger = logging.getLogger("atlas.telegram.bot")


def build_application() -> Application:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN is not configured. Set it in your .env file."
        )

    app = (
        Application.builder()
        .token(token)
        .concurrent_updates(True)
        .build()
    )

    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.COMMAND, handle_text_message))
    app.add_handler(MessageHandler(filters.VOICE | filters.VIDEO_NOTE, handle_voice_message))
    app.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.ALL, handle_photo_or_document)
    )
    app.add_handler(
        MessageHandler(
            filters.Sticker.ALL
            | filters.ANIMATION
            | filters.LOCATION
            | filters.CONTACT
            | filters.POLL,
            handle_unsupported,
        )
    )

    logger.info("event=bot_built handlers=registered")
    return app
