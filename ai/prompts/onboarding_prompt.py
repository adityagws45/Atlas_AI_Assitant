"""Onboarding prompt helpers — conversational onboarding remains Milestone 2 logic."""

ONBOARDING_TONE = """
If the user is mid-onboarding, stay warm and brief.
Do not restart the questionnaire. Defer to the onboarding state provided.
""".strip()


def build_onboarding_prompt(*, onboarding_state: dict) -> str:
    step = onboarding_state.get("step") or ""
    completed = bool(onboarding_state.get("completed"))
    return (
        f"{ONBOARDING_TONE}\n"
        f"completed={completed} step={step or 'n/a'}"
    )
