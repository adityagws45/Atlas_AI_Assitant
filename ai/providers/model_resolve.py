"""Resolve a working Gemini Flash model against the installed SDK."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("atlas.ai.model_resolve")

# Preference order for production Flash when configured model is unusable.
PRODUCTION_FLASH_FALLBACKS = (
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-flash-latest",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    # gemini-2.5-flash often listed but returns 404 for new API keys — keep last
    "gemini-2.5-flash",
)

LIGHT_FLASH_FALLBACKS = (
    "gemini-3.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash-lite",
)

# Process-level cache: (api_key_suffix, configured, fallback_key) -> result
_RESOLVE_CACHE: dict[tuple[str, str, str], tuple[str, bool, str]] = {}
# Models that failed live smoke / generate in this process
_FAILED_MODELS: set[str] = set()


def clear_resolve_cache() -> None:
    _RESOLVE_CACHE.clear()


def mark_model_failed(model_name: str) -> None:
    name = (model_name or "").strip()
    if name:
        _FAILED_MODELS.add(name)
        # Drop cache entries that may have selected this model
        doomed = [k for k, v in _RESOLVE_CACHE.items() if v[0] == name]
        for k in doomed:
            _RESOLVE_CACHE.pop(k, None)
        logger.warning("event=model_marked_failed model=%s", name)


def list_generate_content_models(genai: Any) -> list[str]:
    names: list[str] = []
    for model in genai.list_models():
        methods = getattr(model, "supported_generation_methods", None) or []
        if "generateContent" not in methods:
            continue
        raw = getattr(model, "name", "") or ""
        names.append(raw.replace("models/", ""))
    return names


def model_supported(model_name: str, available: list[str]) -> bool:
    name = (model_name or "").strip()
    if not name:
        return False
    if name in _FAILED_MODELS:
        return False
    if name in available:
        return True
    return f"models/{name}" in available


def smoke_generate(genai: Any, model_name: str) -> bool:
    """Tiny live generate to confirm the model actually returns text."""
    try:
        # Gemini 3.x thinking models can consume many output tokens before visible text.
        max_tokens = 512 if model_name.startswith("gemini-3") else 64
        model = genai.GenerativeModel(model_name)
        response = model.generate_content(
            "Reply with exactly: OK",
            generation_config={
                "temperature": 0,
                "max_output_tokens": max_tokens,
            },
        )
        try:
            text = (response.text or "").strip()
        except Exception:
            parts: list[str] = []
            for candidate in getattr(response, "candidates", None) or []:
                content = getattr(candidate, "content", None)
                for part in getattr(content, "parts", None) or []:
                    chunk = getattr(part, "text", None)
                    if chunk:
                        parts.append(chunk)
            text = "\n".join(parts).strip()
        ok = bool(text)
        if not ok:
            mark_model_failed(model_name)
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "event=model_smoke_fail model=%s err=%s",
            model_name,
            type(exc).__name__,
        )
        mark_model_failed(model_name)
        return False


def resolve_model(
    genai: Any,
    configured: str,
    *,
    fallbacks: tuple[str, ...] = PRODUCTION_FLASH_FALLBACKS,
    smoke: bool = True,
    api_key: str = "",
) -> tuple[str, bool, str]:
    """
    Return (model_name, switched, reason).

    switched=True when we replaced the configured model.
    Never returns a model previously marked failed in this process.
    """
    configured = (configured or "").strip()
    cache_key = (api_key[-8:] if api_key else "", configured, ",".join(fallbacks[:3]))
    if cache_key in _RESOLVE_CACHE:
        cached = _RESOLVE_CACHE[cache_key]
        if cached[0] not in _FAILED_MODELS:
            return cached
        _RESOLVE_CACHE.pop(cache_key, None)

    available = list_generate_content_models(genai)

    result: tuple[str, bool, str] | None = None
    if configured and model_supported(configured, available):
        if not smoke or smoke_generate(genai, configured):
            result = (configured, False, "configured_model_ok")
        else:
            logger.warning(
                "event=model_listed_but_unusable model=%s — selecting fallback",
                configured,
            )
    elif configured:
        logger.warning(
            "event=model_not_in_sdk model=%s available_flash_sample=%s",
            configured,
            [n for n in available if "flash" in n.lower()][:8],
        )

    if result is None:
        for candidate in fallbacks:
            if not model_supported(candidate, available):
                continue
            if smoke and not smoke_generate(genai, candidate):
                continue
            reason = f"switched_from={configured or 'empty'} to={candidate}"
            logger.info("event=model_auto_switched %s", reason)
            result = (candidate, True, reason)
            break

    if result is None:
        # Last resort: first listed flash that has NOT failed smoke
        for name in available:
            if name in _FAILED_MODELS:
                continue
            lower = name.lower()
            if (
                "flash" in lower
                and "embed" not in lower
                and "image" not in lower
                and "tts" not in lower
            ):
                # Still smoke-test last-resort picks when enabled
                if smoke and not smoke_generate(genai, name):
                    continue
                result = (
                    name,
                    True,
                    f"switched_from={configured or 'empty'} to={name} (first_flash)",
                )
                break

    if result is None:
        raise RuntimeError(
            f"No usable Gemini Flash model found. configured={configured!r} "
            f"failed={sorted(_FAILED_MODELS)[:10]} available={available[:15]}"
        )

    _RESOLVE_CACHE[cache_key] = result
    return result
