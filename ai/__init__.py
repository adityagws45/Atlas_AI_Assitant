"""AI package — provider-agnostic orchestration."""

__all__ = ["AIService", "AITurnResult", "ConversationContext", "ToolRequest"]


def __getattr__(name: str):
    if name == "AIService":
        from ai.services.ai_service import AIService

        return AIService
    if name in {"AITurnResult", "ConversationContext", "ToolRequest"}:
        from ai import types as _types

        return getattr(_types, name)
    raise AttributeError(name)
