"""Abstract AI provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ai.types import ProviderMessage, ProviderResponse


class BaseAIProvider(ABC):
    """Swap Gemini for OpenAI/Claude by implementing this interface."""

    name: str = "base"

    @abstractmethod
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
        """Generate a completion. Must never raise uncaught SDK errors upstream."""

    def generate_text(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        response_json: bool = False,
        model: str | None = None,
    ) -> ProviderResponse:
        return self.generate(
            system=system,
            messages=[ProviderMessage(role="user", content=user)],
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            response_json=response_json,
            model=model,
        )

    def health_check(self) -> dict[str, Any]:
        return {"provider": self.name, "ok": True}
