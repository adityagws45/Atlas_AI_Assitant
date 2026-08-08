"""Groq Whisper speech-to-text — voice input only (does not replace Gemini)."""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

import httpx
from django.conf import settings

logger = logging.getLogger("atlas.voice.transcription")

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


class VoiceTranscriptionError(Exception):
    """Typed STT failure — never expose stack traces to Telegram users."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.message = message or code
        super().__init__(self.message)


@dataclass
class TranscriptionResult:
    text: str
    model: str
    duration_hint: float | None = None


class VoiceTranscriptionService:
    """Speech → text via Groq Whisper Large v3 Turbo."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else getattr(settings, "GROQ_API_KEY", "") or "").strip()
        self.model = (
            model
            or getattr(settings, "GROQ_WHISPER_MODEL", "")
            or "whisper-large-v3-turbo"
        ).strip()
        self.timeout = float(
            timeout
            if timeout is not None
            else getattr(settings, "GROQ_WHISPER_TIMEOUT_SECONDS", 60) or 60
        )

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def max_duration_seconds(self) -> int:
        return int(getattr(settings, "MAX_VOICE_DURATION_SECONDS", 120) or 120)

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        *,
        filename: str = "voice.ogg",
        mime_type: str = "audio/ogg",
        duration_seconds: float | None = None,
    ) -> TranscriptionResult:
        if duration_seconds is not None and duration_seconds > self.max_duration_seconds():
            raise VoiceTranscriptionError(
                "too_long",
                f"Voice longer than {self.max_duration_seconds()}s",
            )
        if not audio_bytes:
            raise VoiceTranscriptionError("empty_audio", "No audio data")
        if not self.is_configured():
            raise VoiceTranscriptionError("not_configured", "GROQ_API_KEY missing")

        suffix = Path(filename).suffix or ".ogg"
        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(prefix="atlas_voice_", suffix=suffix)
            os.close(fd)
            with open(tmp_path, "wb") as fh:
                fh.write(audio_bytes)
            return self.transcribe_file(
                tmp_path,
                filename=filename,
                mime_type=mime_type,
                duration_seconds=duration_seconds,
            )
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.info("event=voice_temp_cleanup_failed")

    def transcribe_file(
        self,
        path: str | Path,
        *,
        filename: str | None = None,
        mime_type: str = "audio/ogg",
        duration_seconds: float | None = None,
    ) -> TranscriptionResult:
        if duration_seconds is not None and duration_seconds > self.max_duration_seconds():
            raise VoiceTranscriptionError(
                "too_long",
                f"Voice longer than {self.max_duration_seconds()}s",
            )
        if not self.is_configured():
            raise VoiceTranscriptionError("not_configured", "GROQ_API_KEY missing")

        path = Path(path)
        if not path.is_file():
            raise VoiceTranscriptionError("download_failed", "Audio file missing")
        name = filename or path.name or "voice.ogg"

        try:
            with path.open("rb") as fh:
                return self._call_groq(fh, filename=name, mime_type=mime_type)
        except VoiceTranscriptionError:
            raise
        except httpx.TimeoutException as exc:
            logger.warning("event=groq_whisper_timeout")
            raise VoiceTranscriptionError("timeout", "Groq timeout") from exc
        except httpx.HTTPError as exc:
            logger.warning("event=groq_whisper_http err=%s", type(exc).__name__)
            raise VoiceTranscriptionError("network", "Groq network error") from exc

    def _call_groq(
        self, fh: BinaryIO, *, filename: str, mime_type: str
    ) -> TranscriptionResult:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": (filename, fh, mime_type or "application/octet-stream")}
        data = {
            "model": self.model,
            "response_format": "json",
            "temperature": "0",
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    GROQ_TRANSCRIBE_URL,
                    headers=headers,
                    files=files,
                    data=data,
                )
        except httpx.TimeoutException as exc:
            raise VoiceTranscriptionError("timeout", "Groq timeout") from exc

        if resp.status_code == 401:
            raise VoiceTranscriptionError("invalid_key", "Invalid Groq API key")
        if resp.status_code == 429:
            raise VoiceTranscriptionError("rate_limit", "Groq rate limit")
        if resp.status_code == 413:
            raise VoiceTranscriptionError("too_long", "Audio too large")
        if resp.status_code >= 400:
            # Do not log response body (may contain snippets); status only
            logger.warning(
                "event=groq_whisper_api_error status=%s",
                resp.status_code,
            )
            detail = (resp.text or "")[:200].lower()
            if "format" in detail or "unsupported" in detail:
                raise VoiceTranscriptionError("unsupported_format", "Unsupported audio")
            raise VoiceTranscriptionError("api_error", f"Groq HTTP {resp.status_code}")

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise VoiceTranscriptionError("malformed", "Bad Groq response") from exc

        text = str(payload.get("text") or "").strip()
        if not text:
            raise VoiceTranscriptionError("empty_transcript", "Empty transcription")

        logger.info(
            "event=groq_whisper_ok model=%s chars=%s",
            self.model,
            len(text),
        )
        return TranscriptionResult(text=text, model=self.model)


def user_facing_voice_error(exc: Exception) -> str:
    """Map STT failures to friendly Telegram copy."""
    if isinstance(exc, VoiceTranscriptionError):
        code = exc.code
    else:
        code = "api_error"
    mapping = {
        "too_long": (
            "🎙️ That voice message is too long. Please send a shorter message."
        ),
        "empty_transcript": (
            "🎙️ I couldn't understand that voice message. "
            "Please try again or type your question."
        ),
        "empty_audio": (
            "🎙️ I couldn't process that voice message. Please try again."
        ),
        "download_failed": (
            "🎙️ I couldn't process that voice message. Please try again."
        ),
        "unsupported_format": (
            "🎙️ I couldn't understand that voice message. "
            "Please try again or type your question."
        ),
        "rate_limit": (
            "🎙️ Voice transcription is busy right now. "
            "Please try again in a moment or type your question."
        ),
        "timeout": (
            "🎙️ Voice transcription is temporarily unavailable. "
            "Please try again or type your question."
        ),
        "network": (
            "🎙️ Voice transcription is temporarily unavailable. "
            "Please try again or type your question."
        ),
        "invalid_key": (
            "🎙️ Voice transcription is temporarily unavailable. "
            "Please try again or type your question."
        ),
        "not_configured": (
            "🎙️ Voice transcription is not configured on this server yet. "
            "Please type your question for now."
        ),
        "api_error": (
            "🎙️ Voice transcription is temporarily unavailable. "
            "Please try again or type your question."
        ),
        "malformed": (
            "🎙️ I couldn't understand that voice message. "
            "Please try again or type your question."
        ),
    }
    return mapping.get(
        code,
        "🎙️ Voice transcription is temporarily unavailable. "
        "Please try again or type your question.",
    )
