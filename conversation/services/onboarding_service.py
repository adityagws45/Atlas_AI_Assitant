"""
Progressive, listening-first onboarding for Atlas.

Ask only a few questions up front. Infer from answers (e.g. NVDA → semiconductors).
Skip steps the user already answered implicitly. Learn the rest over time.
"""

from __future__ import annotations

import logging
import re
from datetime import time
from typing import Any

from accounts.models import User, UserRole
from memory.models import UserPreference, Watchlist
from notifications.models import NotificationPreference

logger = logging.getLogger("atlas.onboarding")

STEP_ROLE = "role"
STEP_FOCUS = "focus"  # companies / sectors — open-ended
STEP_DEPTH = "depth"  # role-specific one follow-up
STEP_BRIEFING = "briefing"
STEP_DONE = "done"

# Progressive: role + focus + one depth question + optional briefing = ≤4 turns
ROLE_DEPTH_QUESTION: dict[str, str] = {
    UserRole.INVESTOR: (
        "One more thing — how do you usually invest?\n\n"
        "Growth, value, long-term, or a mix? Helps me weigh risk the way you would. "
        "Skip if you're still figuring that out."
    ),
    UserRole.ANALYST: (
        "When you're digging in, what should I bring first?\n\n"
        "Earnings, filings, comps, or news? Skip if you'd rather decide in the moment."
    ),
    UserRole.FOUNDER: (
        "Anything fundraising or competitor-related I should keep an eye on?\n\n"
        "A name, a round stage, or just say skip — we'll pick it up later."
    ),
    UserRole.STUDENT: (
        "What are you trying to get sharper at right now?\n\n"
        "Valuation, markets, specific companies — whatever helps. Or skip."
    ),
    UserRole.FINANCE_PRO: (
        "Where could I save you the most time day-to-day?\n\n"
        "Meeting prep, scanning news, or working through reports? Skip if not sure yet."
    ),
    UserRole.OTHER: (
        "When something moves, what should I surface first?\n\n"
        "News, earnings, filings, or macro? Skip works too."
    ),
    "": (
        "When something moves, what should I surface first?\n\n"
        "News, earnings, filings, or macro? Skip works too."
    ),
}

SKIP_PHRASES = {
    "skip", "skip this", "skip for now", "later", "not now", "pass", "next",
    "no", "nah", "nope", "n/a", "none", "nothing", "i'll skip", "ill skip",
    "continue", "whatever", "doesn't matter", "doesnt matter", "no thanks",
    "not really", "maybe later",
}

ROLE_ALIASES: dict[str, str] = {
    "equity research analyst": UserRole.ANALYST,
    "research analyst": UserRole.ANALYST,
    "equity research": UserRole.ANALYST,
    "finance professional": UserRole.FINANCE_PRO,
    "finance pro": UserRole.FINANCE_PRO,
    "investor": UserRole.INVESTOR,
    "analyst": UserRole.ANALYST,
    "founder": UserRole.FOUNDER,
    "student": UserRole.STUDENT,
    "finance": UserRole.FINANCE_PRO,
    "professional": UserRole.FINANCE_PRO,
    "other": UserRole.OTHER,
}

# Common name → ticker (listening layer before Gemini)
COMPANY_ALIASES: dict[str, str] = {
    "nvidia": "NVDA",
    "tsmc": "TSM",
    "taiwan semiconductor": "TSM",
    "apple": "AAPL",
    "microsoft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "amazon": "AMZN",
    "meta": "META",
    "facebook": "META",
    "tesla": "TSLA",
    "amd": "AMD",
    "intel": "INTC",
    "broadcom": "AVGO",
    "jpmorgan": "JPM",
    "jp morgan": "JPM",
    "berkshire": "BRK.B",
}

TICKER_SECTORS: dict[str, str] = {
    "NVDA": "semiconductors",
    "TSM": "semiconductors",
    "AMD": "semiconductors",
    "INTC": "semiconductors",
    "AVGO": "semiconductors",
    "AAPL": "technology",
    "MSFT": "technology",
    "GOOGL": "technology",
    "AMZN": "technology",
    "META": "technology",
    "TSLA": "auto / EV",
    "JPM": "financials",
}

SECTOR_KEYWORDS: dict[str, str] = {
    "semiconductor": "semiconductors",
    "semiconductors": "semiconductors",
    "chip": "semiconductors",
    "ai": "AI",
    "cloud computing": "cloud computing",
    "cloud": "cloud computing",
    "healthcare": "healthcare",
    "biotech": "biotech",
    "fintech": "fintech",
    "banking": "banking",
    "banks": "banking",
    "bank": "banking",
    "financials": "financials",
    "crypto": "crypto",
    "energy": "energy",
    "retail": "retail",
}

BRIEFING_HINT = re.compile(
    r"\b(briefing|morning update|daily update)\b|\b([01]?\d|2[0-3])(?::[0-5]\d)?\s*(am|pm)\b",
    re.IGNORECASE,
)

RESEARCH_HINT = re.compile(
    r"\b(tell me about|what about|why is|why did|compare|summarize|"
    r"market[- ]moving|earnings for|earnings of|"
    r"what should i watch|tell me everything|"
    r"research (?!analyst)\w+)\b",
    re.IGNORECASE,
)

FOCUS_QUESTION = (
    "What should I pay closest attention to for you?\n\n"
    "Companies, sectors, or themes — however you think about it. "
    "Skip if you'd rather show me as we go."
)

BRIEFING_QUESTION = (
    "Last one for now — when do you want a short morning briefing?\n\n"
    "Something like 8:00 or 9:30 works (UTC unless you say otherwise). Skip to set later."
)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def is_skip(text: str) -> bool:
    n = _normalize(text)
    return n in SKIP_PHRASES or n.startswith("skip ")


def is_emoji_only(text: str) -> bool:
    stripped = re.sub(
        r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F600-\U0001F64F"
        r"\U0001F680-\U0001F6FF\s]+",
        "",
        text,
    )
    return bool(text.strip()) and not stripped.strip()


def _extract_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    lower = text.lower()
    for name, ticker in sorted(COMPANY_ALIASES.items(), key=lambda x: -len(x[0])):
        if name in lower and ticker not in symbols:
            symbols.append(ticker)

    noise = {
        "AND", "OR", "THE", "FOR", "STOCK", "STOCKS", "FOLLOW", "MAINLY",
        "TRACK", "WATCH", "LIKE", "WITH", "FROM", "COMPANY", "YES", "NO",
        "I", "AM", "AN", "A", "MY", "ME", "TO", "ON", "IN", "ALSO", "ANY",
        "OTHER", "KEEP", "EYE", "ADD", "WANT", "TSMC",  # mapped via alias → TSM
    }
    # Explicit tickers typed in caps (NVDA) or known map keys
    for raw in re.findall(r"\b[A-Z]{1,5}\b", text):
        token = raw.upper()
        if token in noise or token in symbols:
            continue
        if token in TICKER_SECTORS:
            symbols.append(token)

    # Also accept lowercase known tickers typed as nvda
    for token in re.findall(r"\b[A-Za-z]{1,5}\b", text):
        up = token.upper()
        if up in TICKER_SECTORS and up not in symbols and up not in noise:
            symbols.append(up)

    return symbols[:10]


def _infer_sectors(text: str, symbols: list[str]) -> list[str]:
    found: list[str] = []
    lower = text.lower()
    for key, sector in SECTOR_KEYWORDS.items():
        if re.search(rf"\b{re.escape(key)}\b", lower) and sector not in found:
            found.append(sector)
    for sym in symbols:
        sector = TICKER_SECTORS.get(sym)
        if sector and sector not in found:
            found.append(sector)
    # Prefer company-inferred sectors first when present
    symbol_sectors = [TICKER_SECTORS[s] for s in symbols if s in TICKER_SECTORS]
    ordered: list[str] = []
    for s in symbol_sectors + found:
        if s not in ordered:
            ordered.append(s)
    return ordered


class OnboardingService:
    """Short, adaptive onboarding that listens and fills gaps over time."""

    COMPLETION = (
        "Perfect — I've got enough to get started.\n\n"
        "As we chat I'll keep learning your workflow so I can get more useful over time."
    )

    COMPLETION_SKIPPED = (
        "Understood — we can learn as we go.\n\n"
        "I'm ready whenever you are. Ask about a company or whatever's on your mind."
    )

    PLACEHOLDER = (
        "I'll keep that in mind.\n\n"
        "Full research answers arrive in the next milestone — for now I'm listening and learning."
    )

    def start(self, user: User, *, force_reset: bool = False) -> str:
        """
        /start behavior:
        - Returning onboarded user → welcome back (unless force_reset)
        - New / incomplete → progressive intro
        """
        if user.onboarding_completed and not force_reset:
            return self.welcome_back(user)

        user.onboarding_completed = False
        user.onboarding_step = STEP_ROLE
        user.save(update_fields=["onboarding_completed", "onboarding_step", "updated_at"])
        logger.info("event=onboarding_start telegram_id=%s", user.telegram_id)

        name = (user.first_name or "").strip()
        hi = f"Hi {name}!" if name else "Hi!"
        return (
            f"{hi} I'm Atlas.\n\n"
            "I'll help you stay on top of the companies and markets that matter most to you.\n\n"
            "A few quick things so I can tailor research and briefings — "
            "we can skip anything and fill in the rest later.\n\n"
            "What best describes you?\n"
            "Investor, analyst, founder, finance professional, student — or something else?"
        )

    def welcome_back(self, user: User) -> str:
        prefs = UserPreference.objects.filter(user=user).first()
        symbols = list(
            Watchlist.objects.filter(user=user).values_list("symbol", flat=True)[:5]
        )
        sectors = list((prefs.sectors_of_interest if prefs else []) or [])[:3]
        name = (user.first_name or "").strip() or "there"

        if symbols and sectors:
            focus = f"{', '.join(symbols)} and {sectors[0]}"
        elif symbols:
            focus = ", ".join(symbols)
        elif sectors:
            focus = f"{sectors[0]} names"
        elif user.role:
            focus = user.get_role_display().lower() if hasattr(user, "get_role_display") else user.role
            return (
                f"Welcome back, {name}. Still covering you as a {focus}.\n\n"
                "What should we look at today? "
                "Or say restart to redo the intro."
            )
        else:
            return (
                f"Welcome back, {name}.\n\n"
                "What should we look at today? "
                "Or say restart to redo the intro."
            )

        return (
            f"Welcome back, {name}. Still tracking {focus}.\n\n"
            "What should we dig into?"
        )

    def is_in_progress(self, user: User) -> bool:
        if user.onboarding_completed:
            return False
        return bool(user.onboarding_step) and user.onboarding_step != STEP_DONE

    def handle(self, user: User, text: str) -> str:
        step = user.onboarding_step or STEP_ROLE
        skipped = is_skip(text)

        # Mid-onboarding: user dumps companies / changes topic → absorb and continue smartly
        if not skipped and step != STEP_ROLE:
            diverted = self._maybe_absorb_focus_dump(user, text, current_step=step)
            if diverted:
                return diverted

        logger.info(
            "event=onboarding_step step=%s skip=%s telegram_id=%s",
            step,
            skipped,
            user.telegram_id,
        )

        if step == STEP_ROLE:
            if not skipped:
                # Role answer might also include companies ("I'm an investor focused on NVDA")
                self._save_role(user, text)
                symbols = _extract_symbols(text)
                sectors = _infer_sectors(text, symbols)
                if symbols or sectors:
                    self._persist_focus(user, text, symbols, sectors)
                    # Skip focus question — already listening
                    return self._after_focus(user, text, symbols, sectors, skipped=False)
            else:
                user.role = ""
                user.save(update_fields=["role", "updated_at"])
            return self._ask(user, STEP_FOCUS, preamble="That helps.")

        if step == STEP_FOCUS:
            if skipped:
                return self._ask(user, STEP_DEPTH, preamble="Understood — we'll learn it in conversation.")
            symbols = _extract_symbols(text)
            sectors = _infer_sectors(text, symbols)
            self._persist_focus(user, text, symbols, sectors)
            return self._after_focus(user, text, symbols, sectors, skipped=False)

        if step == STEP_DEPTH:
            if skipped:
                return self._ask(user, STEP_BRIEFING, preamble="Understood.")

            # Cross-question: user answered briefing time while we asked style
            if BRIEFING_HINT.search(text):
                self._save_briefing(user, text)
                user.onboarding_step = "style_catchup"
                user.save(update_fields=["onboarding_step", "updated_at"])
                return (
                    "Got it — I'll aim for a morning briefing around that time.\n\n"
                    "Before we wrap, what's your investing style — "
                    "growth, value, long-term, or a mix? Skip if you'd rather decide later."
                )

            self._save_depth(user, text)
            return self._ask(user, STEP_BRIEFING, preamble="Perfect — I'll keep that in mind.")

        if step == "style_catchup":
            if not skipped:
                self._save_depth(user, text)
            return self._complete(user)

        if step == STEP_BRIEFING:
            if skipped:
                return self._complete(user)
            # Cross-question: research request mid-briefing → finish onboarding, hand off
            if RESEARCH_HINT.search(text) and not BRIEFING_HINT.search(text):
                self._soft_complete(user)
                return ""  # signal handoff via process_message
            if BRIEFING_HINT.search(text) or re.search(r"\b\d{1,2}\b", text):
                self._save_briefing(user, text)
            return self._complete(user)

        return self._complete(user)

    def _after_focus(
        self,
        user: User,
        text: str,
        symbols: list[str],
        sectors: list[str],
        *,
        skipped: bool,
    ) -> str:
        """Acknowledge what we heard; optionally ask if there are more names."""
        if symbols and sectors:
            sector = sectors[0]
            names = ", ".join(symbols[:4])
            ack = (
                f"Got it. It sounds like you're focused on {sector}. "
                f"I'll prioritize that space — news, earnings, and major announcements around {names}.\n\n"
                "Any other companies you'd like me to keep an eye on? "
                "Or skip and we'll move on."
            )
            # Stay on a lightweight "more companies?" beat via depth only if needed —
            # treat as still FOCUS follow-up: set step to depth with custom question
            user.onboarding_step = STEP_DEPTH
            user.save(update_fields=["onboarding_step", "updated_at"])
            # Use depth slot for "any others?" then briefing — but depth question is role-specific.
            # Better: go to a mini follow-up stored as depth with override via asking briefing next
            # after one more turn. For progressive UX, ask "any others" THEN role depth is too many.
            # Spec example ends with "any other companies?" — that IS the next question.
            # Then we should go to briefing (skip role depth if we already have rich focus).
            user.onboarding_step = "more_names"
            user.save(update_fields=["onboarding_step", "updated_at"])
            return ack

        if symbols:
            names = ", ".join(symbols[:4])
            user.onboarding_step = "more_names"
            user.save(update_fields=["onboarding_step", "updated_at"])
            return (
                f"I'll keep {names} on my radar.\n\n"
                "Any others, or a sector I should widen into? Skip to continue."
            )

        if sectors:
            # Seed representative names so memory / watch today works immediately
            from conversation.services.personalization import seed_tickers_for_sectors

            seeded = seed_tickers_for_sectors(sectors)
            if seeded:
                self._persist_focus(user, text, seeded, sectors)
            sector = sectors[0]
            names = ", ".join(seeded[:5]) if seeded else "the leaders there"
            user.onboarding_step = STEP_DEPTH
            user.save(update_fields=["onboarding_step", "updated_at"])
            q = ROLE_DEPTH_QUESTION.get(user.role or "", ROLE_DEPTH_QUESTION[""])
            return (
                f"Great. I'll prioritize {sector} — names like {names} — "
                f"in future research.\n\n{q}"
            )

        # Free text without clear tickers
        prefs = self._prefs(user)
        prefs.sectors_of_interest = list(
            dict.fromkeys((prefs.sectors_of_interest or []) + [text.strip()[:80]])
        )[:20]
        prefs.save()
        return self._ask(user, STEP_DEPTH, preamble="That helps.")

    def _maybe_absorb_focus_dump(self, user: User, text: str, *, current_step: str) -> str | None:
        """If user changes topic mid-flow with companies, absorb without getting stuck."""
        # Don't divert the open focus question — let listening inference handle it
        if current_step == STEP_FOCUS:
            return None

        if current_step == "more_names":
            if is_skip(text):
                return self._ask(
                    user,
                    STEP_BRIEFING,
                    preamble="Perfect — I've got enough on the watchlist for now.",
                )
            symbols = _extract_symbols(text)
            sectors = _infer_sectors(text, symbols)
            self._persist_focus(user, text, symbols, sectors)
            if symbols:
                return self._ask(
                    user,
                    STEP_BRIEFING,
                    preamble=f"Added {', '.join(symbols[:4])}. I'll watch those too.",
                )
            return self._ask(user, STEP_BRIEFING, preamble="I'll keep that in mind.")

        # Mid depth/briefing: explicit watchlist edits only (avoid matching "I follow X")
        lower = _normalize(text)
        explicit_watchlist = any(
            p in lower
            for p in (
                "also watch",
                "add to watchlist",
                "change my watchlist",
                "update my watchlist",
                "add ",
            )
        )
        if explicit_watchlist:
            symbols = _extract_symbols(text)
            if symbols:
                self._persist_focus(user, text, symbols, _infer_sectors(text, symbols))
                if current_step == STEP_BRIEFING:
                    return (
                        f"I'll add {', '.join(symbols[:4])} to your list.\n\n"
                        "Want to set a morning briefing time, or skip?"
                    )
                return self._ask(
                    user,
                    STEP_BRIEFING,
                    preamble=f"I'll add {', '.join(symbols[:4])} to your list.",
                )
        return None

    def _ask(self, user: User, step: str, *, preamble: str) -> str:
        user.onboarding_step = step
        user.save(update_fields=["onboarding_step", "updated_at"])
        if step == STEP_FOCUS:
            body = FOCUS_QUESTION
        elif step == STEP_DEPTH:
            body = ROLE_DEPTH_QUESTION.get(user.role or "", ROLE_DEPTH_QUESTION[""])
        elif step == STEP_BRIEFING:
            body = BRIEFING_QUESTION
        else:
            body = FOCUS_QUESTION
        return f"{preamble}\n\n{body}"

    def _soft_complete(self, user: User) -> None:
        user.onboarding_completed = True
        user.onboarding_step = STEP_DONE
        user.save(update_fields=["onboarding_completed", "onboarding_step", "updated_at"])
        logger.info("event=onboarding_soft_complete telegram_id=%s", user.telegram_id)

    def _complete(self, user: User) -> str:
        user.onboarding_completed = True
        user.onboarding_step = STEP_DONE
        user.save(update_fields=["onboarding_completed", "onboarding_step", "updated_at"])
        logger.info("event=onboarding_complete telegram_id=%s", user.telegram_id)
        return self.COMPLETION if self._answered_anything(user) else self.COMPLETION_SKIPPED

    def _answered_anything(self, user: User) -> bool:
        user.refresh_from_db()
        if user.role:
            return True
        prefs = UserPreference.objects.filter(user=user).first()
        if prefs and (
            prefs.sectors_of_interest
            or prefs.markets_of_interest
            or prefs.insight_types
            or prefs.additional_verticals
            or prefs.preferred_briefing_time
        ):
            return True
        return Watchlist.objects.filter(user=user).exists()

    def _prefs(self, user: User) -> UserPreference:
        prefs, _ = UserPreference.objects.get_or_create(user=user)
        return prefs

    def _persist_focus(
        self, user: User, text: str, symbols: list[str], sectors: list[str]
    ) -> None:
        for symbol in symbols:
            Watchlist.objects.get_or_create(
                user=user, symbol=symbol, defaults={"company_name": ""}
            )
        prefs = self._prefs(user)
        merged = list(dict.fromkeys((prefs.sectors_of_interest or []) + sectors))
        if not symbols and not sectors and text.strip():
            merged = list(dict.fromkeys(merged + [text.strip()[:80]]))
        prefs.sectors_of_interest = merged[:20]
        prefs.save()
        logger.info(
            "event=focus_saved telegram_id=%s symbols=%s sectors=%s",
            user.telegram_id,
            symbols,
            sectors,
        )

    def _save_role(self, user: User, text: str) -> str:
        normalized = _normalize(text)
        role = UserRole.OTHER
        for alias, value in ROLE_ALIASES.items():
            if alias in normalized:
                role = value
                break
        user.role = role
        user.save(update_fields=["role", "updated_at"])
        logger.info("event=role_saved role=%s telegram_id=%s", role, user.telegram_id)
        return role

    def _save_depth(self, user: User, text: str) -> None:
        prefs = self._prefs(user)
        role = user.role or ""
        if role in {UserRole.INVESTOR}:
            tag = f"invest_style:{text.strip()[:80]}"
        elif role in {UserRole.ANALYST, UserRole.OTHER, ""}:
            prefs.insight_types = [p.strip() for p in re.split(r"[,;/]", text) if p.strip()][:20]
            prefs.save()
            return
        elif role == UserRole.FOUNDER:
            tag = f"founder_focus:{text.strip()[:80]}"
            symbols = _extract_symbols(text)
            if symbols:
                self._persist_focus(user, text, symbols, _infer_sectors(text, symbols))
        elif role == UserRole.STUDENT:
            tag = f"learning:{text.strip()[:80]}"
        elif role == UserRole.FINANCE_PRO:
            tag = f"workflow:{text.strip()[:80]}"
        else:
            tag = f"note:{text.strip()[:80]}"
        tags = list(prefs.additional_verticals or [])
        if tag not in tags:
            tags.append(tag)
        prefs.additional_verticals = tags[:30]
        prefs.save()

    def _save_briefing(self, user: User, text: str) -> None:
        prefs = self._prefs(user)
        match = re.search(
            r"\b([01]?\d|2[0-3])(?::([0-5]\d))?\s*(am|pm)?\b",
            text.lower(),
        )
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            meridiem = match.group(3)
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

    def handle_preference_change(self, user: User, text: str) -> str | None:
        """Post-onboarding profile/preference updates — never research."""
        from conversation.services.preference_intent import apply_preference_update

        return apply_preference_update(user, text, onboarding_svc=self)

    def process_message(self, user: User, text: str) -> dict[str, Any]:
        if is_emoji_only(text):
            return {
                "reply": "I saw that — want to put it in a quick sentence so I know how to help?",
                "onboarding": self.is_in_progress(user),
                "delegate_to_ai": False,
            }

        if self.is_in_progress(user):
            step = user.onboarding_step or STEP_ROLE
            if (
                step in {STEP_BRIEFING, STEP_DEPTH, "more_names", "style_catchup"}
                and RESEARCH_HINT.search(text)
                and not is_skip(text)
                and not BRIEFING_HINT.search(text)
            ):
                self._soft_complete(user)
                return {"reply": None, "onboarding": False, "delegate_to_ai": True}

            if user.onboarding_step == "more_names":
                diverted = self._maybe_absorb_focus_dump(user, text, current_step="more_names")
                if diverted:
                    return {"reply": diverted, "onboarding": True, "delegate_to_ai": False}

            reply = self.handle(user, text)
            if reply == "" and user.onboarding_completed:
                return {"reply": None, "onboarding": False, "delegate_to_ai": True}
            return {"reply": reply, "onboarding": True, "delegate_to_ai": False}

        lower = _normalize(text)
        from conversation.services.personalization import (
            build_tell_me_everything_reply,
            build_watch_today_reply,
        )
        from conversation.services.preference_intent import apply_preference_update

        # Preference / profile updates ALWAYS beat research orchestration
        pref_reply = apply_preference_update(user, text, onboarding_svc=self)
        if pref_reply:
            return {
                "reply": pref_reply,
                "onboarding": False,
                "delegate_to_ai": False,
                "preference_update": True,
            }

        if any(
            p in lower
            for p in (
                "what should i watch today",
                "what should i watch",
                "what do i watch today",
                "watch today",
            )
        ):
            return {
                "reply": build_watch_today_reply(user),
                "onboarding": False,
                "delegate_to_ai": False,
            }

        if "tell me everything" in lower or lower in {"catch me up", "brief me"}:
            return {
                "reply": build_tell_me_everything_reply(user),
                "onboarding": False,
                "delegate_to_ai": False,
            }

        return {
            "reply": None,
            "onboarding": False,
            "delegate_to_ai": True,
        }
