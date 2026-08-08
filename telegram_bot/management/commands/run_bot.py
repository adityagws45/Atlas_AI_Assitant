"""Run the Telegram bot in long-polling mode (local development)."""

import logging

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger("atlas.telegram.polling")


class Command(BaseCommand):
    help = "Run Atlas Telegram bot with long polling (Milestone 2)."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError(
                "TELEGRAM_BOT_TOKEN is missing. Add it to your .env and retry."
            )

        from telegram_bot.bot import build_application

        self.stdout.write(self.style.NOTICE("Starting Atlas bot in polling mode…"))
        logger.info("event=polling_start")
        app = build_application()
        try:
            app.run_polling(
                drop_pending_updates=False,
                allowed_updates=["message"],
                close_loop=False,
            )
        except Exception:
            logger.exception("event=polling_crash")
            raise
