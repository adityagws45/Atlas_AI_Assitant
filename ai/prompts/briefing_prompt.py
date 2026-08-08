"""Daily briefing prompt scaffold — execution in a later milestone."""

BRIEFING_SYSTEM = """
You draft a morning market briefing for Telegram.
Lead with what matters for this user's watchlist and sectors.
Keep it scannable: movers, catalysts, risks, one clear "watch today" line.
""".strip()


def build_briefing_prompt(*, context: dict) -> str:
    return (
        f"{BRIEFING_SYSTEM}\n\n"
        "User context will be supplied by the orchestrator when briefings go live.\n"
        f"Stub context keys: {sorted(context.keys())}"
    )
