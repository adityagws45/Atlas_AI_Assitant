"""AI service package exports."""

__all__ = ["AIService", "ClarificationEngine"]


def __getattr__(name: str):
    if name == "AIService":
        from ai.services.ai_service import AIService

        return AIService
    if name == "ClarificationEngine":
        from ai.services.clarification_engine import ClarificationEngine

        return ClarificationEngine
    raise AttributeError(name)
