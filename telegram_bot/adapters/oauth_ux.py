"""Unified Google connect copy for Telegram (URL only on the button)."""

from __future__ import annotations


def google_access_required_reply(auth_url: str, *, purpose: str = "") -> str:
    """
    User-facing OAuth prompt.

    The auth URL is embedded as a markdown link so TelegramAdapter can attach an
    inline button and scrub the URL from visible text.
    """
    url = (auth_url or "").strip()
    purpose = (purpose or "").strip()
    lines = ["*Google access is required.*"]
    if purpose:
        lines.append(purpose)
    lines.append("")
    lines.append("Tap *Connect Google* below.")
    lines.append("")
    lines.append(
        "If Google says the app is *unverified*, tap *Advanced* → "
        "*Go to Atlas (unsafe)* → *Allow* (normal for new apps)."
    )
    if url:
        lines.append(f"\n[Connect Google]({url})")
    return "\n".join(lines)


def google_connected_prefix(*, action: str = "") -> str:
    action = (action or "").strip()
    if action:
        return f"Google connected ✓\n{action}\n\n"
    return "Google connected ✓\n\n"
