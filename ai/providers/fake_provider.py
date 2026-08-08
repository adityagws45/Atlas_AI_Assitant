"""Fake AI provider for deterministic Milestone 3 verification."""

from __future__ import annotations

import json
from typing import Any, Callable

from ai.providers.base import BaseAIProvider
from ai.types import ProviderMessage, ProviderResponse, ProviderTimeoutError


class FakeProvider(BaseAIProvider):
    """Configurable stub — never hits the network."""

    name = "fake"

    def __init__(
        self,
        responder: Callable[..., str] | None = None,
        *,
        fail_times: int = 0,
        timeout_times: int = 0,
    ) -> None:
        self.responder = responder or self._default_responder
        self.fail_times = fail_times
        self.timeout_times = timeout_times
        self.calls: list[dict[str, Any]] = []
        self._attempt = 0

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
        self._attempt += 1
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "temperature": temperature,
                "response_json": response_json,
                "model": model,
            }
        )
        if self.timeout_times > 0:
            self.timeout_times -= 1
            raise ProviderTimeoutError("simulated timeout")
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("simulated provider failure")

        text = self.responder(
            system=system,
            messages=messages,
            response_json=response_json,
        )
        return ProviderResponse(text=text, model=model or "fake-model", latency_ms=1)

    @staticmethod
    def _default_responder(*, system: str, messages: list[ProviderMessage], response_json: bool) -> str:
        user_blob = " ".join(m.content for m in messages).lower()
        current = FakeProvider._current_user_message(user_blob)

        # Memory extraction
        if "should anything from this turn become long-term memory" in user_blob:
            if "i always prefer concise updates" in user_blob or "prefer concise" in user_blob:
                return json.dumps(
                    {
                        "should_save": True,
                        "memories": [
                            {
                                "memory_type": "preference",
                                "key": "communication_style",
                                "value": {"style": "concise"},
                                "confidence": 0.92,
                                "reason": "User stated lasting preference",
                            }
                        ],
                    }
                )
            if "favorite companies are nvidia and amd" in user_blob:
                return json.dumps(
                    {
                        "should_save": True,
                        "memories": [
                            {
                                "memory_type": "preference",
                                "key": "favorite_companies",
                                "value": ["NVDA", "AMD"],
                                "confidence": 0.9,
                                "reason": "Explicit favorite companies",
                            }
                        ],
                    }
                )
            return json.dumps({"should_save": False, "memories": []})

        # Summary
        if "update the rolling conversation summary" in user_blob:
            return "User discussed semiconductors and asked about NVDA catalysts."

        # Tool synthesis pass
        if (
            "live tool result" in user_blob
            or "tool used:" in user_blob
            or "live research packet" in user_blob
            or "internal analysis mode:" in user_blob
            or "write the telegram analyst reply" in user_blob
        ):
            return (
                "NVDA is trading near the latest print, with AI infrastructure demand "
                "still the main driver behind the move.\n\n"
                "Use that live level as context — watch the next demand/catalyst update, "
                "not just the tick."
            )

        # Clarification for Apple — match the live user message, not prompt examples
        if "tell me about apple" in current or (
            "clarification hint from rules engine" in user_blob and "apple" in current
        ):
            return json.dumps(
                {
                    "needs_clarification": True,
                    "clarification_question": (
                        "Happy to dig into Apple. Which angle is most useful?\n\n"
                        "• Company Overview\n"
                        "• Stock Performance\n"
                        "• Latest News\n"
                        "• Earnings\n"
                        "• Financial Analysis\n"
                        "• Products"
                    ),
                    "needs_tool": False,
                    "tool": None,
                    "answer": (
                        "Happy to dig into Apple. Which angle is most useful?\n\n"
                        "• Company Overview\n"
                        "• Stock Performance\n"
                        "• Latest News\n"
                        "• Earnings\n"
                        "• Financial Analysis\n"
                        "• Products"
                    ),
                    "confidence": 0.95,
                }
            )

        # Tool decision
        if "live price" in current or "current price of nvda" in current:
            return json.dumps(
                {
                    "needs_clarification": False,
                    "clarification_question": "",
                    "needs_tool": True,
                    "tool": {
                        "name": "stock_quote",
                        "arguments": {"symbol": "NVDA"},
                        "reason": "User asked for a live price",
                    },
                    "answer": (
                        "I'd pull a live NVDA quote for this — "
                        "price action matters for sizing any near-term view."
                    ),
                    "confidence": 0.9,
                }
            )

        # Default analyst reply that can reference context
        summary_note = ""
        if (
            "semiconductor" in user_blob
            or "supply chain" in user_blob
            or "nvda" in current
        ):
            summary_note = " Given your semiconductor focus, "
        return json.dumps(
            {
                "needs_clarification": False,
                "clarification_question": "",
                "needs_tool": False,
                "tool": None,
                "answer": (
                    f"Here's how I'd frame it.{summary_note}"
                    "The useful question is which driver actually changes your thesis — "
                    "demand, margins, or competition. Want me to dig into one of those?"
                ),
                "confidence": 0.85,
            }
        )

    @staticmethod
    def _current_user_message(user_blob: str) -> str:
        """Extract live user text from composed prompt JSON when present."""
        import re

        match = re.search(
            r'"current_user_message"\s*:\s*"((?:\\.|[^"\\])*)"',
            user_blob,
        )
        if match:
            return match.group(1).encode("utf-8").decode("unicode_escape").lower()
        return user_blob
