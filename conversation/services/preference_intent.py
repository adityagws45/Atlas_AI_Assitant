"""Preference / profile intent routing — always before AI research."""

from __future__ import annotations

import re
from typing import Any

from accounts.models import User, UserRole
from conversation.services.onboarding_service import (
    BRIEFING_HINT,
    ROLE_ALIASES,
    _extract_symbols,
    _infer_sectors,
    _normalize,
)
from conversation.services.personalization import (
    build_sector_follow_reply,
    infer_sectors_from_text,
    seed_tickers_for_sectors,
)
from memory.models import UserPreference, Watchlist
from notifications.models import NotificationPreference

# Clear research asks — never treat these as preference updates
RESEARCH_BLOCKLIST = re.compile(
    r"\b(tell me about|what about|why is|why did|compare|summarize|"
    r"market[- ]moving|earnings for|earnings of|what should i watch|"
    r"tell me everything|stock price|how is .+ (doing|trading)|"
    r"latest news|pull up|look up|pay attention|what should i pay|"
    r"biggest risks?|ai strategy|how did revenue|"
    r"documents? (do i have|about|on)|files? (do i have|about|on)|"
    r"search( my)? (google )?drive|my (google )?drive|"
    r"analyze my |import my |connect( my)? (google )?drive|"
    r"show( me)?( my)? (spreadsheets?|sheets?)|open( my)? portfolio|"
    r"my portfolio|what stands out|which holdings|connect( my)? (google )?sheets?|"
    r"check( my)? (e)?mail|check( my)? (gmail|inbox)|"
    r"what needs (my )?attention|unread (e)?mails?|"
    r"search( my)? (e)?mails?|find( my)? (e)?mails?|"
    r"draft( a)? reply|summarize( the| that)? attachment|"
    r"connect( my)? (google )?((e)?mail|gmail)|"
    r"what does my day look like|meetings? (do i have )?today|"
    r"when am i free|schedule( a)? (meeting|review)|"
    r"connect( my)? (google )?calendar|any conflicts?"
    r")\b",
    re.IGNORECASE,
)

ROLE_STATEMENT = re.compile(
    r"\b(i'?m|i am|i work as|working as|my role is|i'?m a|i am a)\b"
    r".{0,40}\b("
    r"investor|analyst|founder|student|"
    r"finance\s+pro(?:fessional)?|equity\s+research|"
    r"research\s+analyst|portfolio\s+manager|trader"
    r")\b",
    re.IGNORECASE,
)

# Investing style / horizon — preference, never research
STYLE_STATEMENT = re.compile(
    r"\b("
    r"i invest|i'?m investing|invest for|investing for|"
    r"buy and hold|value investor|growth investor|"
    r"my (investing )?style|risk tolerance|"
    r"i prefer (growth|value|long|short)|horizon is|"
    r"i('?m| am) (a )?(long|short)[- ]term investor"
    r")\b",
    re.IGNORECASE,
)

SECTOR_STATEMENT = re.compile(
    r"\b(i\s+)?(cover|follow|focus(?:\s+on)?|interested\s+in|into|"
    r"mostly(?:\s+follow)?|primarily\s+cover|specialize\s+in|"
    r"work\s+in|care\s+about|watch(?:list)?\s+is|mainly follow)\b",
    re.IGNORECASE,
)

WATCHLIST_STATEMENT = re.compile(
    r"\b(my watchlist|watchlist is|add to (my )?watchlist|"
    r"change my watchlist|update my watchlist|put on my watchlist|"
    r"add .+ to (my )?list)\b",
    re.IGNORECASE,
)

# Bare clock like "8:00 AM" / "8am" / "9:30"
BARE_CLOCK = re.compile(
    r"^\s*([01]?\d|2[0-3])(?::([0-5]\d))?\s*(am|pm)?\.?\s*$",
    re.IGNORECASE,
)

ROLE_LABELS = {
    UserRole.INVESTOR: "an investor",
    UserRole.ANALYST: "an equity analyst",
    UserRole.FOUNDER: "a founder",
    UserRole.STUDENT: "a student",
    UserRole.FINANCE_PRO: "a finance professional",
    UserRole.OTHER: "your role",
}


def is_research_question(text: str) -> bool:
    return bool(RESEARCH_BLOCKLIST.search(text or ""))


def detect_preference_intent(text: str) -> str | None:
    """Return intent kind or None. Preference beats research only when not a clear finance Q."""
    raw = (text or "").strip()
    if not raw or is_research_question(raw):
        return None

    lower = _normalize(raw)

    if WATCHLIST_STATEMENT.search(lower) or (
        any(p in lower for p in ("add ", "track ")) and _extract_symbols(raw)
    ):
        return "watchlist"

    # Bare briefing time: "8:00 AM" / "8am"
    if BARE_CLOCK.match(raw):
        return "briefing"

    if BRIEFING_HINT.search(raw) and any(
        p in lower
        for p in (
            "briefing",
            "morning",
            "daily update",
            "should arrive",
            "prefer",
            "schedule",
            "send at",
            "arrive at",
            "at ",
        )
    ):
        if "briefing" in lower or "morning" in lower or "daily" in lower or "arrive" in lower:
            return "briefing"
        if re.search(r"\b([01]?\d|2[0-3])(?::[0-5]\d)?\s*(am|pm)\b", lower):
            if any(p in lower for p in ("prefer", "want", "set", "change", "update", "my")):
                return "briefing"

    if ROLE_STATEMENT.search(raw):
        return "role"

    if STYLE_STATEMENT.search(raw):
        return "style"

    if SECTOR_STATEMENT.search(raw) and infer_sectors_from_text(raw):
        return "sector"

    # Short sector dumps after analyst onboarding: "banking and fintech"
    sectors = infer_sectors_from_text(raw)
    if sectors and len(raw.split()) <= 8 and not _extract_symbols(raw):
        return "sector"

    return None


def apply_preference_update(user: User, text: str, *, onboarding_svc: Any = None) -> str | None:
    """
    Persist profile/preference and return a natural acknowledgment.
    Returns None if this is not a preference update.
    """
    kind = detect_preference_intent(text)
    if not kind:
        return None

    if kind == "role":
        return _apply_role(user, text)
    if kind == "style":
        return _apply_style(user, text, onboarding_svc=onboarding_svc)
    if kind == "sector":
        return _apply_sector(user, text, onboarding_svc=onboarding_svc)
    if kind == "briefing":
        return _apply_briefing(user, text, onboarding_svc=onboarding_svc)
    if kind == "watchlist":
        return _apply_watchlist(user, text, onboarding_svc=onboarding_svc)
    return None


def _resolve_role(text: str) -> str:
    normalized = _normalize(text)
    # Prefer more specific phrases first
    ordered = sorted(ROLE_ALIASES.items(), key=lambda x: -len(x[0]))
    for alias, value in ordered:
        if alias in normalized:
            return value
    if "equity research" in normalized or "research analyst" in normalized:
        return UserRole.ANALYST
    if "portfolio manager" in normalized or "trader" in normalized:
        return UserRole.FINANCE_PRO
    return UserRole.OTHER


def _apply_role(user: User, text: str) -> str:
    role = _resolve_role(text)
    user.role = role
    user.save(update_fields=["role", "updated_at"])
    label = ROLE_LABELS.get(role, "your role")
    if role == UserRole.ANALYST:
        return (
            "Perfect.\n\n"
            "I'll tailor research with an equity analyst's perspective.\n\n"
            "Which sectors do you primarily cover?"
        )
    if role == UserRole.INVESTOR:
        return (
            "Perfect.\n\n"
            "I'll keep an investor lens — catalysts, risk, and what moves price.\n\n"
            "What should I pay closest attention to — companies, sectors, or themes?"
        )
    return (
        f"Got it — I'll treat you as {label}.\n\n"
        "What sectors or companies should I prioritize?"
    )


def _apply_style(user: User, text: str, *, onboarding_svc: Any = None) -> str:
    if onboarding_svc is not None:
        onboarding_svc._save_depth(user, text)
    else:
        prefs, _ = UserPreference.objects.get_or_create(user=user)
        tag = f"invest_style:{text.strip()[:80]}"
        prefs.insight_types = list(
            dict.fromkeys((prefs.insight_types or []) + [tag])
        )[:20]
        prefs.save()
    lower = text.lower()
    if "long" in lower:
        lens = "a long-term"
    elif "short" in lower:
        lens = "a shorter-term"
    elif "value" in lower:
        lens = "a value"
    elif "growth" in lower:
        lens = "a growth"
    else:
        lens = "your"
    return (
        f"Got it — I'll weigh things with {lens} lens when it fits.\n\n"
        "Want to set a morning briefing time (like 8:00 AM), or jump into a company?"
    )


def _apply_sector(user: User, text: str, *, onboarding_svc: Any = None) -> str:
    sectors = infer_sectors_from_text(text)
    if not sectors:
        return (
            "Happy to lock that in — which sector or theme should I prioritize?"
        )
    seeded = seed_tickers_for_sectors(sectors)
    if onboarding_svc is not None:
        onboarding_svc._persist_focus(user, text, seeded, sectors)
    else:
        _persist_focus_local(user, text, seeded, sectors)

    if len(sectors) == 1:
        return build_sector_follow_reply(sectors[0], seeded)

    names = ", ".join(seeded[:5]) if seeded else "the leaders there"
    joined = " and ".join(sectors) if len(sectors) == 2 else ", ".join(sectors[:-1]) + f", and {sectors[-1]}"
    return (
        f"Got it — I'll prioritize {joined}. "
        f"Names like {names} will stay on my radar for research and briefings.\n\n"
        "If you want specific tickers weighted higher, just name them."
    )


def _apply_briefing(user: User, text: str, *, onboarding_svc: Any = None) -> str:
    if onboarding_svc is not None:
        onboarding_svc._save_briefing(user, text)
    else:
        _save_briefing_local(user, text)
    prefs = UserPreference.objects.filter(user=user).first()
    when = ""
    if prefs and prefs.preferred_briefing_time:
        when = prefs.preferred_briefing_time.strftime("%H:%M")
        tz = prefs.briefing_timezone or "UTC"
        when = f" around {when} {tz}"
    return (
        f"Locked in{when}.\n\n"
        "I'll keep your morning briefing on that schedule. "
        "Say the word anytime if you want to change it."
    )


def _apply_watchlist(user: User, text: str, *, onboarding_svc: Any = None) -> str:
    symbols = _extract_symbols(text)
    if not symbols:
        return (
            "Sure — which companies should I add or focus on?\n\n"
            "Tickers or names both work."
        )
    sectors = _infer_sectors(text, symbols)
    if onboarding_svc is not None:
        onboarding_svc._persist_focus(user, text, symbols, sectors)
    else:
        _persist_focus_local(user, text, symbols, sectors)
    if sectors:
        return (
            f"I'll keep {', '.join(symbols[:4])} on the list "
            f"and stay close to {sectors[0]}."
        )
    return f"I'll keep {', '.join(symbols[:4])} on my radar."


def _persist_focus_local(
    user: User,
    text: str,
    symbols: list[str],
    sectors: list[str],
) -> None:
    prefs, _ = UserPreference.objects.get_or_create(user=user)
    if sectors:
        prefs.sectors_of_interest = list(
            dict.fromkeys((prefs.sectors_of_interest or []) + sectors)
        )[:20]
    if text.strip() and not symbols:
        prefs.markets_of_interest = list(
            dict.fromkeys((prefs.markets_of_interest or []) + [text.strip()[:80]])
        )[:20]
    prefs.save()
    for sym in symbols[:10]:
        Watchlist.objects.update_or_create(
            user=user,
            symbol=sym,
            defaults={"notes": ""},
        )


def _save_briefing_local(user: User, text: str) -> None:
    from datetime import time

    prefs, _ = UserPreference.objects.get_or_create(user=user)
    match = re.search(
        r"\b([01]?\d|2[0-3])(?::([0-5]\d))?\s*(am|pm)?\b",
        text,
        re.IGNORECASE,
    )
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        prefs.preferred_briefing_time = time(hour=hour, minute=minute)
    tz_match = re.search(
        r"\b(UTC|GMT|[A-Za-z]+/[A-Za-z_]+|IST|EST|PST|CET)\b",
        text,
        re.IGNORECASE,
    )
    if tz_match:
        prefs.briefing_timezone = tz_match.group(1)
    prefs.save()
    notif, _ = NotificationPreference.objects.get_or_create(user=user)
    notif.daily_briefing_enabled = True
    notif.save()
