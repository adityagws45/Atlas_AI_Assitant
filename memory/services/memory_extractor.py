"""Post-turn memory extraction via lightweight AI prompt."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from django.conf import settings

from accounts.models import User
from ai.prompts.prompt_manager import PromptManager
from ai.providers.base import BaseAIProvider
from ai.types import MemoryCandidate, ProviderConfigError, ProviderError
from memory.models import AssistantMemory, MemorySource, MemoryType
from memory.services.memory_retriever import MemoryRetriever

logger = logging.getLogger("atlas.memory.extractor")

MIN_CONFIDENCE = 0.65
ALLOWED_TYPES = {c.value for c in MemoryType}


class MemoryExtractor:
    """Ask whether the turn produced durable memory; save structured rows."""

    def __init__(
        self,
        provider: BaseAIProvider,
        *,
        prompts: PromptManager | None = None,
        retriever: MemoryRetriever | None = None,
    ) -> None:
        self.provider = provider
        self.prompts = prompts or PromptManager()
        self.retriever = retriever or MemoryRetriever()

    def extract_and_save(
        self,
        user: User,
        *,
        user_message: str,
        assistant_message: str,
    ) -> list[str]:
        if not (user_message or "").strip():
            return []

        # Skip pure clarification-only short loops with no preference signal
        if len(user_message.strip()) < 3:
            return []

        try:
            candidates = self._ask_model(user, user_message, assistant_message)
        except (ProviderConfigError, ProviderError) as exc:
            logger.warning("event=memory_extract_skipped err=%s", type(exc).__name__)
            return []
        except Exception:
            logger.exception("event=memory_extract_error telegram_id=%s", user.telegram_id)
            return []

        saved_keys: list[str] = []
        for cand in candidates:
            if cand.confidence < MIN_CONFIDENCE:
                continue
            if not self._is_durable(cand, user_message):
                continue
            key = self._normalize_key(cand.key)
            if not key:
                continue
            memory_type = cand.memory_type if cand.memory_type in ALLOWED_TYPES else MemoryType.PREFERENCE
            existing = (
                AssistantMemory.objects.filter(user=user, key=key)
                .order_by("-updated_at")
                .first()
            )
            if existing:
                existing.memory_type = memory_type
                existing.value = cand.value
                existing.source = MemorySource.CONVERSATION
                existing.confidence = float(cand.confidence)
                existing.save(
                    update_fields=[
                        "memory_type",
                        "value",
                        "source",
                        "confidence",
                        "updated_at",
                    ]
                )
                obj = existing
                created = False
            else:
                obj = AssistantMemory.objects.create(
                    user=user,
                    key=key,
                    memory_type=memory_type,
                    value=cand.value,
                    source=MemorySource.CONVERSATION,
                    confidence=float(cand.confidence),
                )
                created = True
            saved_keys.append(obj.key)
            logger.info(
                "event=memory_saved telegram_id=%s key=%s created=%s",
                user.telegram_id,
                obj.key,
                created,
            )
        return saved_keys

    def _ask_model(
        self, user: User, user_message: str, assistant_message: str
    ) -> list[MemoryCandidate]:
        system, prompt = self.prompts.compose_memory_extraction(
            user_message=user_message,
            assistant_message=assistant_message,
            existing_keys=self.retriever.existing_keys(user),
        )
        light_model = None
        try:
            light_model = getattr(self.provider, "light_model", None) or getattr(
                settings, "GEMINI_LIGHT_MODEL", None
            )
        except Exception:  # noqa: BLE001
            light_model = None

        response = self.provider.generate_text(
            system=system,
            user=prompt,
            temperature=0.1,
            max_output_tokens=512,
            response_json=True,
            model=light_model,
        )
        return self._parse_candidates(response.text)

    def _parse_candidates(self, text: str) -> list[MemoryCandidate]:
        data = _safe_json(text)
        if not data or not data.get("should_save"):
            return []
        out: list[MemoryCandidate] = []
        for item in data.get("memories") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            try:
                conf = float(item.get("confidence", 0.8))
            except (TypeError, ValueError):
                conf = 0.8
            out.append(
                MemoryCandidate(
                    memory_type=str(item.get("memory_type") or MemoryType.PREFERENCE),
                    key=key,
                    value=item.get("value"),
                    confidence=conf,
                    reason=str(item.get("reason") or ""),
                )
            )
        return out

    @staticmethod
    def _normalize_key(key: str) -> str:
        key = re.sub(r"[^a-z0-9_]+", "_", key.strip().lower())
        return key.strip("_")[:128]

    @staticmethod
    def _is_durable(cand: MemoryCandidate, user_message: str) -> bool:
        """Heuristic gate: ignore temporary market chatter."""
        ephemeral = (
            "today",
            "right now",
            "this minute",
            "current price",
            "just curious",
        )
        lower = user_message.lower()
        if any(p in lower for p in ephemeral) and cand.memory_type == "context":
            # allow preference keys even if message has "today"
            if not any(
                k in cand.key
                for k in ("prefer", "favorite", "style", "interest", "sector", "watch")
            ):
                return False
        if cand.value in (None, "", [], {}):
            return False
        return True


def _safe_json(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
