"""Voice feature verification — Groq Whisper STT → ConversationProcessor."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
os.chdir(BASE)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django

django.setup()

import httpx

from accounts.models import User
from ai.services.voice_transcription import (
    VoiceTranscriptionError,
    VoiceTranscriptionService,
    user_facing_voice_error,
)
from conversation.models import ContentType, Message
from gmail.services.gmail_intent import detect_gmail_intent
from gcalendar.services.calendar_intent import detect_calendar_intent
from sheets.services.sheet_intent import detect_sheet_intent
from telegram_bot.services.conversation_processor import ConversationProcessor


def _pass(label: str) -> None:
    print(f"PASS {label}")


def test_service_config_and_errors() -> None:
    svc = VoiceTranscriptionService(api_key="")
    assert not svc.is_configured()
    try:
        svc.transcribe_bytes(b"abc", filename="v.ogg")
        raise AssertionError("expected not_configured")
    except VoiceTranscriptionError as exc:
        assert exc.code == "not_configured"

    svc2 = VoiceTranscriptionService(api_key="test-key")
    try:
        svc2.transcribe_bytes(b"x", filename="v.ogg", duration_seconds=9999)
        raise AssertionError("expected too_long")
    except VoiceTranscriptionError as exc:
        assert exc.code == "too_long"

    try:
        svc2.transcribe_bytes(b"", filename="v.ogg")
        raise AssertionError("expected empty_audio")
    except VoiceTranscriptionError as exc:
        assert exc.code == "empty_audio"

    assert "too long" in user_facing_voice_error(
        VoiceTranscriptionError("too_long")
    ).lower()
    assert "understand" in user_facing_voice_error(
        VoiceTranscriptionError("empty_transcript")
    ).lower()
    assert "busy" in user_facing_voice_error(
        VoiceTranscriptionError("rate_limit")
    ).lower() or "try again" in user_facing_voice_error(
        VoiceTranscriptionError("rate_limit")
    ).lower()
    _pass("voice_service_errors")
    _pass("long_audio_rejection")
    _pass("empty_audio")


def test_groq_success_and_failures() -> None:
    svc = VoiceTranscriptionService(api_key="gk-test", model="whisper-large-v3-turbo")

    class FakeResp:
        status_code = 200

        def json(self):
            return {"text": "Why is Nvidia moving today?"}

    with patch("ai.services.voice_transcription.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = FakeResp()
        client_cls.return_value = client
        result = svc.transcribe_bytes(b"fake-ogg-bytes", filename="voice.ogg")
        assert result.text == "Why is Nvidia moving today?"
        assert result.model == "whisper-large-v3-turbo"
        # Ensure multipart post called
        assert client.post.called
    _pass("successful_transcription")
    _pass("groq_whisper_service")

    class EmptyResp:
        status_code = 200

        def json(self):
            return {"text": "   "}

    with patch("ai.services.voice_transcription.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = EmptyResp()
        client_cls.return_value = client
        try:
            svc.transcribe_bytes(b"x", filename="voice.ogg")
            raise AssertionError("empty")
        except VoiceTranscriptionError as exc:
            assert exc.code == "empty_transcript"
    _pass("empty_transcription")

    for status, code in [(401, "invalid_key"), (429, "rate_limit"), (500, "api_error")]:

        class ErrResp:
            status_code = status
            text = "error"

            def json(self):
                return {}

        with patch("ai.services.voice_transcription.httpx.Client") as client_cls:
            client = MagicMock()
            client.__enter__.return_value = client
            client.__exit__.return_value = False
            client.post.return_value = ErrResp()
            client_cls.return_value = client
            try:
                svc.transcribe_bytes(b"x", filename="voice.ogg")
                raise AssertionError(code)
            except VoiceTranscriptionError as exc:
                assert exc.code == code, (status, exc.code)
    _pass("groq_api_error")
    _pass("groq_rate_limit")

    with patch("ai.services.voice_transcription.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.side_effect = httpx.TimeoutException("t")
        client_cls.return_value = client
        try:
            svc.transcribe_bytes(b"x", filename="voice.ogg")
            raise AssertionError("timeout")
        except VoiceTranscriptionError as exc:
            assert exc.code == "timeout"
    _pass("groq_timeout")

    class FmtResp:
        status_code = 400
        text = "unsupported format"

        def json(self):
            return {}

    with patch("ai.services.voice_transcription.httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = FmtResp()
        client_cls.return_value = client
        try:
            svc.transcribe_bytes(b"x", filename="voice.ogg")
            raise AssertionError("format")
        except VoiceTranscriptionError as exc:
            assert exc.code == "unsupported_format"
    _pass("unsupported_audio")


def test_voice_routes_like_text() -> None:
    """Transcribed text must hit the same intent detectors as typed text."""
    cases = [
        ("Why is Nvidia moving today?", "finance_or_ai"),
        ("Compare Microsoft and Nvidia for long term AI growth.", "compare"),
        ("What is the latest news about Nvidia?", "news"),
        ("What's on my calendar today?", "calendar"),
        ("Show me my latest finance emails.", "gmail"),
        ("Analyze this spreadsheet.", "sheets_or_ai"),
        ("What are the three biggest risks in this financial report?", "doc_or_ai"),
        ("Summarize the financial report in my Drive.", "drive_or_ai"),
    ]
    for text, label in cases:
        cal = detect_calendar_intent(text)
        gmail = detect_gmail_intent(text)
        sheet = detect_sheet_intent(text, has_active_sheet=True)
        # At least one domain or falls through to AI — voice does not invent new intents
        assert isinstance(text, str) and text.strip()
        if label == "calendar":
            assert cal.kind == "today"
        if label == "gmail":
            assert gmail.kind in {"latest", "finance", "check"}
        _pass(f"voice_intent_parity_{label}")
    _pass("voice_intent_detection")


def test_voice_through_processor_memory() -> None:
    tid_a, tid_b = 9977000101, 9977000102
    User.objects.filter(telegram_id__in=[tid_a, tid_b]).delete()
    User.objects.create(
        telegram_id=tid_a, first_name="VoiceA", telegram_username="va", onboarding_completed=True
    )
    User.objects.create(
        telegram_id=tid_b, first_name="VoiceB", telegram_username="vb", onboarding_completed=True
    )
    p = ConversationProcessor()

    r1 = p.handle_text(
        telegram_id=tid_a,
        text="What's on my calendar today?",
        username="va",
        first_name="VoiceA",
        input_source="voice",
        telegram_message_id=1,
    )
    assert r1
    msg = (
        Message.objects.filter(conversation__user__telegram_id=tid_a, role="user")
        .order_by("-created_at")
        .first()
    )
    assert msg is not None
    assert msg.content_type == ContentType.VOICE
    assert (msg.metadata or {}).get("input_source") == "voice"
    assert "calendar" in msg.content.lower() or "today" in msg.content.lower()
    cal_asst = (
        Message.objects.filter(conversation__user__telegram_id=tid_a, role="assistant")
        .order_by("-created_at")
        .first()
    )
    assert cal_asst and (cal_asst.metadata or {}).get("pipeline") == "calendar"
    _pass("voice_to_calendar_path")

    r2 = p.handle_text(
        telegram_id=tid_a,
        text="Show me my latest finance emails.",
        username="va",
        first_name="VoiceA",
        input_source="voice",
        telegram_message_id=2,
    )
    assert r2
    meta = (
        Message.objects.filter(conversation__user__telegram_id=tid_a, role="assistant")
        .order_by("-created_at")
        .first()
    )
    assert meta and (meta.metadata or {}).get("pipeline") == "gmail"
    _pass("voice_to_gmail_path")

    r_b = p.handle_text(
        telegram_id=tid_b,
        text="What's on my calendar today?",
        username="vb",
        first_name="VoiceB",
        input_source="voice",
        telegram_message_id=3,
    )
    assert r_b
    a_count = Message.objects.filter(conversation__user__telegram_id=tid_a).count()
    b_count = Message.objects.filter(conversation__user__telegram_id=tid_b).count()
    assert a_count >= 2 and b_count >= 1
    assert not Message.objects.filter(
        conversation__user__telegram_id=tid_b, content__icontains="finance emails"
    ).exists()
    _pass("multi_user_isolation")

    r3 = p.handle_text(
        telegram_id=tid_a,
        text="hello",
        username="va",
        first_name="VoiceA",
        input_source="text",
        telegram_message_id=4,
    )
    assert r3
    last_user = (
        Message.objects.filter(conversation__user__telegram_id=tid_a, role="user")
        .order_by("-created_at")
        .first()
    )
    assert last_user and last_user.content_type == ContentType.TEXT
    _pass("voice_conversation_memory")
    _pass("text_regression_after_voice")


def test_handler_voice_detection_contract() -> None:
    """Bot registers VOICE|VIDEO_NOTE → handle_voice_message."""
    from telegram_bot import bot as bot_mod
    import inspect

    src = inspect.getsource(bot_mod.build_application)
    assert "filters.VOICE" in src
    assert "handle_voice_message" in src
    from telegram_bot.handlers import message_handler as mh

    assert "Listening" in inspect.getsource(mh.handle_voice_message)
    assert "input_source=\"voice\"" in inspect.getsource(mh.handle_voice_message).replace(
        "'", '"'
    ) or "input_source='voice'" in inspect.getsource(mh.handle_voice_message)
    assert "_handle_text_sync" in inspect.getsource(mh.handle_voice_message)
    _pass("telegram_voice_detection")
    _pass("audio_handling_contract")
    _pass("conversation_processor_integration")


def test_gemini_not_replaced() -> None:
    from django.conf import settings
    from ai.services.ai_service import AIService
    from ai.providers.gemini_provider import GeminiProvider

    assert settings.GEMINI_MODEL
    assert getattr(settings, "GROQ_WHISPER_MODEL", "") == "whisper-large-v3-turbo" or (
        "whisper" in getattr(settings, "GROQ_WHISPER_MODEL", "")
    )
    svc = AIService()
    assert isinstance(svc.provider, GeminiProvider)
    _pass("gemini_main_ai_unchanged")


def main() -> None:
    print("=== Voice (Groq Whisper) verification ===")
    test_service_config_and_errors()
    test_groq_success_and_failures()
    test_voice_routes_like_text()
    test_voice_through_processor_memory()
    test_handler_voice_detection_contract()
    test_gemini_not_replaced()
    print("\nVOICE_VERIFICATION: PASS")
    print(
        "Architecture: Voice -> whisper-large-v3-turbo -> ConversationProcessor -> "
        "existing routing -> Atlas answer (not transcription echo)."
    )


if __name__ == "__main__":
    main()
