"""Gemini provider — only place that talks to the Google Generative AI SDK."""

from __future__ import annotations

import logging
import time
from typing import Any

from django.conf import settings

from ai.providers.base import BaseAIProvider
from ai.types import (
    ProviderConfigError,
    ProviderMessage,
    ProviderResponse,
    ProviderRetryExhausted,
    ProviderTimeoutError,
)

logger = logging.getLogger("atlas.ai.gemini")


class GeminiProvider(BaseAIProvider):
    """Isolated Gemini client with timeout, retries, and structured responses."""

    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
        default_temperature: float | None = None,
    ) -> None:
        raw_key = api_key if api_key is not None else getattr(settings, "GEMINI_API_KEY", "")
        self.api_key = (raw_key or "").strip()
        configured_model = (
            model if model is not None else getattr(settings, "GEMINI_MODEL", "gemini-3.5-flash")
        )
        self.model = (configured_model or "").strip() or "gemini-3.5-flash"
        self._model_resolved = False
        self._model_switch_reason = ""
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "GEMINI_TIMEOUT_SECONDS", 30)
        )
        self.max_retries = int(
            max_retries
            if max_retries is not None
            else getattr(settings, "GEMINI_MAX_RETRIES", 3)
        )
        self.default_temperature = float(
            default_temperature
            if default_temperature is not None
            else getattr(settings, "GEMINI_TEMPERATURE", 0.4)
        )
        self._client_ready = False

    def _ensure_client(self) -> Any:
        if not self.api_key.strip():
            raise ProviderConfigError(
                "GEMINI_API_KEY is not configured. Set it in .env to enable AI replies."
            )
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise ProviderConfigError(
                "google-generativeai is not installed"
            ) from exc

        if not self._client_ready:
            genai.configure(api_key=self.api_key)
            self._client_ready = True
        self._resolve_model_if_needed(genai)
        return genai

    def _resolve_model_if_needed(self, genai: Any) -> None:
        """Validate configured model; auto-switch to a working production Flash if needed."""
        if self._model_resolved:
            return
        from ai.providers.model_resolve import LIGHT_FLASH_FALLBACKS, resolve_model

        light = (getattr(settings, "GEMINI_LIGHT_MODEL", "") or "").strip()
        # Resolve main model
        resolved, switched, reason = resolve_model(
            genai, self.model, smoke=True, api_key=self.api_key
        )
        if switched:
            logger.warning(
                "event=gemini_model_switched from=%s to=%s reason=%s",
                self.model,
                resolved,
                reason,
            )
            self.model = resolved
            self._model_switch_reason = reason
        else:
            logger.info("event=gemini_model_validated model=%s", self.model)

        # Resolve light model used by memory/summary (store on settings-like attr)
        try:
            light_resolved, light_switched, light_reason = resolve_model(
                genai,
                light or self.model,
                fallbacks=LIGHT_FLASH_FALLBACKS,
                smoke=True,
                api_key=self.api_key,
            )
            self.light_model = light_resolved
            if light_switched:
                logger.warning(
                    "event=gemini_light_model_switched from=%s to=%s reason=%s",
                    light,
                    light_resolved,
                    light_reason,
                )
        except Exception as exc:  # noqa: BLE001
            self.light_model = self.model
            logger.warning(
                "event=gemini_light_model_fallback model=%s err=%s",
                self.model,
                type(exc).__name__,
            )
        self._model_resolved = True

    def generate(
        self,
        *,
        system: str,
        messages: list[ProviderMessage],
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_json: bool = False,
        model: str | None = None,
    ) -> ProviderResponse:
        genai = self._ensure_client()
        model_name = (model or self.model).strip()
        temp = self.default_temperature if temperature is None else temperature
        max_tokens = max_output_tokens or getattr(settings, "GEMINI_MAX_OUTPUT_TOKENS", 2048)
        # Gemini 3.x thinking can burn output tokens before visible text appears.
        if model_name.startswith("gemini-3"):
            floor = 1024 if response_json else 512
            max_tokens = max(int(max_tokens), floor)

        generation_config: dict[str, Any] = {
            "temperature": temp,
            "max_output_tokens": max_tokens,
        }
        if response_json:
            generation_config["response_mime_type"] = "application/json"

        history, last_user = self._to_gemini_contents(messages)
        last_error: Exception | None = None
        attempts = max(1, self.max_retries)

        for attempt in range(1, attempts + 1):
            started = time.monotonic()
            try:
                gemini_model = genai.GenerativeModel(
                    model_name=model_name,
                    system_instruction=system or None,
                    generation_config=generation_config,
                )
                # Prefer chat when we have prior turns; otherwise single generate.
                if history:
                    chat = gemini_model.start_chat(history=history)
                    response = self._call_with_timeout(
                        lambda: chat.send_message(last_user)
                    )
                else:
                    response = self._call_with_timeout(
                        lambda: gemini_model.generate_content(last_user)
                    )

                text = self._extract_text(response)
                if not text:
                    raise RuntimeError(
                        f"Gemini returned empty content (finish_reason="
                        f"{self._finish_reason(response)})"
                    )
                latency_ms = int((time.monotonic() - started) * 1000)
                usage = self._extract_usage(response)
                logger.info(
                    "event=gemini_ok model=%s attempt=%s latency_ms=%s chars=%s json=%s",
                    model_name,
                    attempt,
                    latency_ms,
                    len(text),
                    response_json,
                )
                return ProviderResponse(
                    text=text,
                    raw=response,
                    model=model_name,
                    finish_reason=self._finish_reason(response),
                    usage=usage,
                    latency_ms=latency_ms,
                )
            except ProviderTimeoutError:
                last_error = ProviderTimeoutError(
                    f"Gemini timed out after {self.timeout_seconds}s"
                )
                logger.warning(
                    "event=gemini_timeout model=%s attempt=%s/%s",
                    model_name,
                    attempt,
                    attempts,
                )
            except ProviderConfigError:
                raise
            except Exception as exc:  # noqa: BLE001 — normalize SDK errors
                last_error = exc
                retryable = self._is_retryable(exc)
                logger.warning(
                    "event=gemini_error model=%s attempt=%s/%s retryable=%s err=%s",
                    model_name,
                    attempt,
                    attempts,
                    retryable,
                    type(exc).__name__,
                )
                # Sticky broken model (404 NotFound) must not poison the process
                if self._is_model_unavailable(exc):
                    from ai.providers.model_resolve import mark_model_failed

                    mark_model_failed(model_name)
                    self._model_resolved = False
                    try:
                        self._resolve_model_if_needed(genai)
                        if self.model != model_name:
                            model_name = self.model
                            logger.warning(
                                "event=gemini_model_recovered to=%s",
                                model_name,
                            )
                            retryable = True
                    except Exception as recover_exc:  # noqa: BLE001
                        logger.warning(
                            "event=gemini_model_recover_failed err=%s",
                            type(recover_exc).__name__,
                        )
                if not retryable:
                    break
            if attempt < attempts:
                # Longer pause on rate/quota pressure so compare/QA recover under load
                if last_error is not None and self._is_rate_limited(last_error):
                    time.sleep(min(2 ** attempt, 8))
                else:
                    time.sleep(min(2 ** (attempt - 1), 4))

        raise ProviderRetryExhausted(
            f"Gemini failed after {attempts} attempt(s): {last_error}"
        ) from last_error

    @staticmethod
    def _is_rate_limited(exc: Exception) -> bool:
        err_name = exc.__class__.__name__.lower()
        msg = str(exc).lower()
        return (
            "resourceexhausted" in err_name
            or "429" in msg
            or "rate" in msg
            or "quota" in msg
        )

    def _call_with_timeout(self, fn):
        """Run sync SDK call; enforce soft timeout via elapsed check after return.

        google-generativeai is sync; we wrap with a simple wall-clock guard using
        a worker thread when timeout is configured.
        """
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn)
            try:
                return future.result(timeout=self.timeout_seconds)
            except concurrent.futures.TimeoutError as exc:
                future.cancel()
                raise ProviderTimeoutError(
                    f"Gemini call exceeded {self.timeout_seconds}s"
                ) from exc

    @staticmethod
    def _to_gemini_contents(messages: list[ProviderMessage]) -> tuple[list[dict], str]:
        if not messages:
            return [], ""

        history: list[dict] = []
        for msg in messages[:-1]:
            role = "user" if msg.role in {"user", "system"} else "model"
            history.append({"role": role, "parts": [msg.content]})

        last = messages[-1]
        return history, last.content

    @staticmethod
    def _extract_text(response: Any) -> str:
        try:
            text = (response.text or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            pass
        # Fallback: stitch candidates
        parts: list[str] = []
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", None) or []:
                chunk = getattr(part, "text", None)
                if chunk:
                    parts.append(chunk)
        return "\n".join(parts).strip()

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, int]:
        meta = getattr(response, "usage_metadata", None)
        if not meta:
            return {}
        return {
            "prompt_tokens": int(getattr(meta, "prompt_token_count", 0) or 0),
            "completion_tokens": int(getattr(meta, "candidates_token_count", 0) or 0),
            "total_tokens": int(getattr(meta, "total_token_count", 0) or 0),
        }

    @staticmethod
    def _finish_reason(response: Any) -> str:
        candidates = getattr(response, "candidates", None) or []
        if not candidates:
            return ""
        reason = getattr(candidates[0], "finish_reason", "")
        return str(reason)

    @staticmethod
    def _is_model_unavailable(exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        msg = str(exc).lower()
        return "notfound" in name or "404" in msg or "no longer available" in msg or (
            "not found" in msg and "model" in msg
        )

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        msg = str(exc).lower()
        retry_markers = (
            "timeout",
            "timed out",
            "429",
            "rate",
            "quota",
            "503",
            "500",
            "temporarily",
            "unavailable",
            "connection",
            "reset",
            "empty content",
            "finish_reason",
            "404",
            "not found",
            "no longer available",
        )
        if any(m in name for m in ("timeout", "unavailable", "internal", "notfound")):
            return True
        return any(m in msg for m in retry_markers)

    def health_check(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "light_model": getattr(self, "light_model", ""),
            "model_resolved": self._model_resolved,
            "model_switch_reason": self._model_switch_reason,
            "configured": bool(self.api_key.strip()),
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
        }
