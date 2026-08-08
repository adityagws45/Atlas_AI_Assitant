"""Logging filters that prevent secrets from appearing in logs."""

from __future__ import annotations

import logging
import re


class RedactSecretsFilter(logging.Filter):
    """
    Strip Telegram bot tokens and common secret patterns from log records.
    httpx/PTB otherwise log full request URLs including the bot token.
    """

    _BOT_TOKEN_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+")
    _BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]+", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True

        redacted = self._BOT_TOKEN_RE.sub("bot***:REDACTED", message)
        redacted = self._BEARER_RE.sub(r"\1REDACTED", redacted)

        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True
