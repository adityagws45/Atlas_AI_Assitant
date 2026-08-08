"""Natural-language Calendar intents — never expose API jargon."""

from __future__ import annotations

import re
from dataclasses import dataclass


CONNECT = re.compile(
    r"\b("
    r"connect( my)? (google )?calendar|"
    r"link( my)? (google )?calendar|"
    r"enable( my)? (google )?calendar|"
    r"set up( my)? (google )?calendar"
    r")\b",
    re.IGNORECASE,
)

TODAY = re.compile(
    r"\b("
    r"what does my day look like|my day look like|"
    r"what('?s| is) on (my )?(calendar|schedule)( today)?|"
    r"today'?s (meetings?|schedule|calendar)|"
    r"what meetings do i have today|"
    r"show( me)? today'?s (calendar|meetings?|schedule)|"
    r"meetings? (do i have )?today|"
    r"summary of today'?s schedule|"
    r"how busy am i today|"
    r"important meetings? today|"
    r"(any|do i have)( any)? meetings? this afternoon|"
    r"do i have anything (today|this afternoon)|"
    r"what do i have today"
    r")\b",
    re.IGNORECASE,
)

TOMORROW = re.compile(
    r"\b("
    r"what (do i have|meetings? do i have) tomorrow|"
    r"what('?s| is) (on )?(my )?(calendar|schedule) tomorrow|"
    r"tomorrow'?s (meetings?|schedule|calendar)|"
    r"show( me)? tomorrow|"
    r"what about tomorrow|"
    r"do i have (a )?conflict tomorrow|"
    r"am i free tomorrow"
    r")\b",
    re.IGNORECASE,
)

NEXT = re.compile(
    r"\b("
    r"what('?s| is) my next meeting|"
    r"next meeting|"
    r"what'?s next on my (calendar|schedule)|"
    r"when is my next meeting"
    r")\b",
    re.IGNORECASE,
)

WEEK = re.compile(
    r"\b("
    r"show( me)? (my )?calendar for (this|next) week|"
    r"(this|next) week('?s)? (calendar|schedule|meetings?)|"
    r"what('?s| is) (on )?(my )?(calendar|schedule) (for )?(this|next) week|"
    r"my schedule this week|"
    r"what('?s| is) my schedule this week"
    r")\b",
    re.IGNORECASE,
)

FREE = re.compile(
    r"\b("
    r"when am i free|find( me)?( a)? free( \w+)?( slot| hour| time)?|"
    r"free one-?hour|open slot|availability|"
    r"when can i (review|meet|schedule)|"
    r"am i free at|"
    r"do i have anything between|"
    r"free (slot|time|hour)"
    r")\b",
    re.IGNORECASE,
)

CONFLICTS = re.compile(
    r"\b("
    r"any conflicts?|scheduling conflicts?|double[- ]booked|overlaps?|"
    r"overlapping meetings?|do i have (any )?(a )?conflict|"
    r"is there a conflict"
    r")\b",
    re.IGNORECASE,
)

DEADLINES = re.compile(
    r"\b(what deadlines|upcoming deadlines|deadlines? (are )?coming|any deadlines)\b",
    re.IGNORECASE,
)

CREATE = re.compile(
    r"\b("
    r"schedule( a)? (meeting|call|review|block|time)|"
    r"block( \w+)?( hours?| time)?( for)?|"
    r"add( a)? (meeting|event|reminder)|"
    r"set( up)?( a)? (meeting|reminder)|"
    r"remind me (before|about|to)|"
    r"find time for"
    r")\b",
    re.IGNORECASE,
)

UPDATE = re.compile(
    r"\b("
    r"move( my| the| that)? (meeting|event|it)|"
    r"reschedule( my| the| that)?|"
    r"move it|push( it| my meeting)|"
    r"what about friday"
    r")\b",
    re.IGNORECASE,
)

CANCEL = re.compile(
    r"\b("
    r"cancel( my| the| that| tomorrow'?s)? (meeting|event|it)?|"
    r"delete( my| the| that)? (meeting|event)|"
    r"cancel that|cancel it"
    r")\b",
    re.IGNORECASE,
)

CONFIRM = re.compile(
    r"^\s*(yes|confirm|do it|go ahead|yes,?\s*(please|move|cancel|schedule)?)\s*$",
    re.IGNORECASE,
)

SEARCH = re.compile(
    r"\b("
    r"(find|show|search).{0,40}(meeting|event|interview|earnings)|"
    r"meetings? (do i have )?with|"
    r"what meetings? .{0,30}with"
    r")\b",
    re.IGNORECASE,
)

# Follow-ups after an active calendar answer
FOLLOWUP = re.compile(
    r"\b("
    r"which (one|meeting) is (the )?longest|"
    r"longest( one|meeting)?|"
    r"when am i free|"
    r"is there a conflict|"
    r"any conflicts?|"
    r"what about tomorrow|"
    r"and tomorrow|"
    r"how busy|"
    r"which is (most )?important|"
    r"summarize( (them|that|it))?"
    r")\b",
    re.IGNORECASE,
)

BUSY = re.compile(
    r"\b(how busy am i|busy( day| today| tomorrow)?|packed (day|schedule))\b",
    re.IGNORECASE,
)

# Ambiguous "task" questions — clarify vs Sheets when calendar was recent
TASK_AMBIGUOUS = re.compile(
    r"^\s*(do i have |any |what )?(any )?(tasks?|to-?dos?|reminders?)( today| tomorrow)?\??\s*$",
    re.IGNORECASE,
)


@dataclass
class CalendarIntent:
    kind: str
    query: str = ""
    when: str = ""


def detect_calendar_intent(
    text: str,
    *,
    has_calendar_context: bool = False,
) -> CalendarIntent:
    raw = (text or "").strip()
    if not raw:
        return CalendarIntent(kind="none")
    if CONNECT.search(raw):
        return CalendarIntent(kind="connect")
    if CONFIRM.search(raw):
        return CalendarIntent(kind="confirm")
    if CONFLICTS.search(raw):
        return CalendarIntent(kind="conflicts", query=raw)
    if DEADLINES.search(raw):
        return CalendarIntent(kind="deadlines")
    if BUSY.search(raw):
        return CalendarIntent(kind="busy", query=raw)
    if FREE.search(raw):
        return CalendarIntent(kind="free", query=raw)
    if NEXT.search(raw):
        return CalendarIntent(kind="next")
    if WEEK.search(raw):
        return CalendarIntent(kind="week")
    if TOMORROW.search(raw):
        return CalendarIntent(kind="tomorrow", query=raw)
    if TODAY.search(raw):
        return CalendarIntent(kind="today", query=raw)
    # After Calendar conversation, "do I have any task?" is not a Sheets question
    if has_calendar_context and TASK_AMBIGUOUS.search(raw):
        return CalendarIntent(kind="clarify_task", query=raw)
    if CANCEL.search(raw):
        return CalendarIntent(kind="cancel", query=raw)
    if UPDATE.search(raw):
        return CalendarIntent(kind="update", query=raw, when=raw)
    if CREATE.search(raw):
        return CalendarIntent(kind="create", query=raw, when=raw)
    if SEARCH.search(raw):
        return CalendarIntent(kind="search", query=raw)
    if re.fullmatch(r"what about friday\??", raw, re.I):
        return CalendarIntent(kind="update", query=raw, when=raw)
    # Soft calendar cues
    if re.search(r"\b(my )?(calendar|schedule|meetings?)\b", raw, re.I) and re.search(
        r"\b(today|tomorrow|week|afternoon|morning|free|conflict|busy|next)\b",
        raw,
        re.I,
    ):
        if "tomorrow" in raw.lower():
            return CalendarIntent(kind="tomorrow", query=raw)
        if "week" in raw.lower():
            return CalendarIntent(kind="week", query=raw)
        return CalendarIntent(kind="today", query=raw)
    if has_calendar_context and FOLLOWUP.search(raw):
        low = raw.lower()
        if "conflict" in low or "overlap" in low:
            return CalendarIntent(kind="conflicts", query=raw)
        if "free" in low:
            return CalendarIntent(kind="free", query=raw)
        if "tomorrow" in low:
            return CalendarIntent(kind="tomorrow", query=raw)
        if "longest" in low or "important" in low or "summarize" in low or "busy" in low:
            return CalendarIntent(kind="followup", query=raw)
        return CalendarIntent(kind="followup", query=raw)
    if has_calendar_context and len(raw.split()) <= 12 and re.search(
        r"\b(why|longest|conflict|free|tomorrow|busy|important)\b", raw, re.I
    ):
        return CalendarIntent(kind="followup", query=raw)
    return CalendarIntent(kind="none")
