"""Thin Telegram send helpers — transport only."""

from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message as TgMessage
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger("atlas.telegram.adapter")

TELEGRAM_MAX_LENGTH = 3500  # leave headroom under Telegram's 4096 hard cap

# Legacy Markdown: renders *bold* and bullets without requiring the heavy
# escaping of MarkdownV2. Safe for AI-generated prose that contains periods,
# dashes, parens, and exclamation marks.
#
# Important: Telegram legacy Markdown does NOT understand CommonMark **bold**.
# AI replies often use **Summary**; we normalize those to *Summary* before send.
PARSE_MODE = ParseMode.MARKDOWN

_OAUTH_URL_RE = re.compile(
    r"https://accounts\.google\.com/o/oauth2/[^\s<>\")\]]+",
    re.IGNORECASE,
)


def prepare_telegram_markdown(text: str) -> str:
    """Normalize CommonMark-ish AI output to Telegram legacy Markdown.

    - ``**bold**`` → ``*bold*`` (Telegram bold)
    - ``* **Title:** rest`` / ``- item`` list lines → ``• …`` so a leading
      asterisk is not mistaken for an open bold span
    - Bare URLs and ``[label](url)`` links are left intact (clickable)
    """
    if not text:
        return text

    out = text.replace("\r\n", "\n")

    # List items that mix a markdown bullet with CommonMark bold
    out = re.sub(
        r"(?m)^[\-\*]\s+\*\*(.+?)\*\*",
        r"• *\1*",
        out,
    )
    # Remaining plain markdown bullets (- / *) → unicode bullet
    out = re.sub(r"(?m)^[\-\*]\s+", "• ", out)
    # CommonMark / GFM bold → Telegram legacy bold
    out = re.sub(r"\*\*(.+?)\*\*", r"*\1*", out)

    return out


def strip_telegram_markup(text: str) -> str:
    """Best-effort plain text when parse_mode is rejected."""
    if not text:
        return text
    out = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    out = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", out)
    out = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", out)
    out = re.sub(r"`([^`]+)`", r"\1", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", out)
    return out


def extract_google_oauth_url(text: str) -> str | None:
    """Find a Google OAuth authorization URL in assistant text."""
    if not text:
        return None
    # Prefer explicit markdown link target
    m = re.search(
        r"\[Connect Google\]\((https://accounts\.google\.com/o/oauth2/[^)\s]+)\)",
        text,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = _OAUTH_URL_RE.search(text)
    return m.group(0) if m else None


def scrub_oauth_urls_for_display(text: str) -> str:
    """Remove raw Google OAuth URLs from user-visible text (keep button only)."""
    if not text:
        return text
    out = text
    out = re.sub(
        r"\[Connect Google\]\(https://accounts\.google\.com/o/oauth2/[^)\s]+\)",
        "Connect Google",
        out,
        flags=re.IGNORECASE,
    )
    out = _OAUTH_URL_RE.sub("", out)
    out = re.sub(
        r"(?i)\(?\s*or open this link:?\s*\)?",
        "",
        out,
    )
    out = re.sub(r"(?i)open this link to authorize[^\n]*\n?", "", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    cleaned = out.strip()
    if cleaned and "connect google" not in cleaned.lower():
        cleaned = cleaned.rstrip() + "\n\nTap *Connect Google* below."
    return cleaned


def _oauth_keyboard(auth_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔗 Connect Google", url=auth_url)]]
    )


def _markdown_balanced(chunk: str) -> str:
    """Ensure a split chunk does not begin/end inside a *bold* span.

    Telegram's legacy Markdown mode fails to deliver a message (400 error) if
    a chunk starts or ends with an unbalanced bold marker. Trim a stray '*'
    so each chunk is self-consistent and deliverable.
    """
    if not chunk:
        return chunk
    # Odd count of asterisks → an unbalanced span is left over.
    if chunk.count("*") % 2 == 1:
        if chunk.endswith("*"):
            return chunk.rstrip("*").rstrip() or chunk
        if chunk.startswith("*"):
            return chunk.lstrip("*").lstrip() or chunk
    return chunk


def split_message(text: str, limit: int = TELEGRAM_MAX_LENGTH) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            pieces = remaining
        else:
            # Prefer paragraph, then sentence, then hard cut
            split_at = remaining.rfind("\n\n", 0, limit)
            if split_at < limit // 3:
                split_at = remaining.rfind("\n", 0, limit)
            if split_at < limit // 3:
                split_at = remaining.rfind(". ", 0, limit)
                if split_at > 0:
                    split_at += 1
            if split_at < limit // 3:
                split_at = limit
            pieces = remaining[:split_at]
        pieces = _markdown_balanced(pieces.rstrip())
        chunks.append(pieces)
        remaining = remaining[len(pieces) :].lstrip()
    return [c for c in chunks if c]


class TelegramAdapter:
    @staticmethod
    async def send_typing(update_message: TgMessage) -> None:
        await update_message.chat.send_action(ChatAction.TYPING)

    @staticmethod
    async def reply_text(
        update_message: TgMessage,
        text: str,
        *,
        context: ContextTypes.DEFAULT_TYPE | None = None,
    ) -> list[TgMessage]:
        sent: list[TgMessage] = []
        raw = text or ""
        auth_url = extract_google_oauth_url(raw)
        # Never show giant OAuth URLs in the chat — button carries the URL.
        display = scrub_oauth_urls_for_display(raw) if auth_url else raw
        prepared = prepare_telegram_markdown(display)
        keyboard = _oauth_keyboard(auth_url) if auth_url else None
        chunks = split_message(prepared)
        for i, chunk in enumerate(chunks):
            # Attach Connect Google on the first chunk only
            markup = keyboard if (keyboard and i == 0) else None
            try:
                msg = await update_message.reply_text(
                    chunk, parse_mode=PARSE_MODE, reply_markup=markup
                )
            except BadRequest as exc:
                logger.warning(
                    "event=telegram_markdown_fallback err=%s chars=%s",
                    type(exc).__name__,
                    len(chunk),
                )
                msg = await update_message.reply_text(
                    strip_telegram_markup(chunk),
                    parse_mode=None,
                    reply_markup=markup,
                )
            sent.append(msg)
            logger.info(
                "Sent telegram_message_id=%s chat_id=%s chars=%s oauth_btn=%s",
                msg.message_id,
                msg.chat_id,
                len(chunk),
                bool(markup),
            )
        return sent
