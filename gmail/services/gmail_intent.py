"""Natural-language Gmail intents — never expose API jargon."""

from __future__ import annotations

import re
from dataclasses import dataclass


CONNECT = re.compile(
    r"\b("
    r"connect( my)? (google )?((e)?mail|gmail|inbox)|"
    r"link( my)? (google )?((e)?mail|gmail)|"
    r"enable( my)? (google )?gmail|"
    r"set up( my)? (google )?((e)?mail|gmail)"
    r")\b",
    re.IGNORECASE,
)

LATEST = re.compile(
    r"\b("
    r"show( me)?( my)? (latest|recent) (e)?mails?|"
    r"(latest|recent) (e)?mails?|"
    r"my (latest|recent) (e)?mails?"
    r")\b",
    re.IGNORECASE,
)

CHECK = re.compile(
    r"\b("
    r"check( my)? (e)?mail|check( my)? (gmail|inbox)|"
    r"what'?s? in (my )?(inbox|email)|"
    r"look at (my )?(inbox|email|emails)|"
    r"show( me)?( my)? (inbox|emails?)"
    r")\b",
    re.IGNORECASE,
)

ATTENTION = re.compile(
    r"\b("
    r"what needs (my )?attention|any(thing)? important|"
    r"important (e)?mails?|priority (e)?mails?|"
    r"do i have anything important|"
    r"are any of (these|them|those) (important|urgent)|"
    r"what should i (read|reply|handle)"
    r")\b",
    re.IGNORECASE,
)

SUMMARY = re.compile(
    r"\b("
    r"summarize( (my|today'?s|the|these|those))? (latest |recent )?(\d+ )?(finance )?(e)?mails?|"
    r"summarize( the)? latest( \d+)?|"
    r"summarize( my)? (finance |latest finance )?inbox|inbox summary|"
    r"summarize( my)? (latest )?finance (e)?mails?|"
    r"summarize( the)? most important( one)?|"
    r"email summary|summarise( (my|today'?s|the|these))? (latest |recent )?(\d+ )?(e)?mails?|"
    r"summarize the (first|second|third|latest) (one|email)"
    r")\b",
    re.IGNORECASE,
)

UNREAD = re.compile(
    r"\b("
    r"show( me)? unread|unread (e)?mails?|"
    r"what'?s? unread|any unread|"
    r"do i have (any )?unread"
    r")\b",
    re.IGNORECASE,
)

FINANCE = re.compile(
    r"\b("
    r"show( me)?( my)? (latest |recent )?finance (e)?mails?|"
    r"^(my )?finance (e)?mails?\b|"
    r"finance( and)? business (e)?mails?|"
    r"^(financial (e)?mails?)\b"
    r")",
    re.IGNORECASE,
)

EARNINGS = re.compile(
    r"\b("
    r"(any |show( me)? )?(earnings|eps)[- ]?(related )?(e)?mails?|"
    r"earnings[- ]related|"
    r"quarterly results (e)?mails?"
    r")\b",
    re.IGNORECASE,
)

INVESTMENTS = re.compile(
    r"\b("
    r"(any |show( me)? )?investment (alerts?|emails?)|"
    r"(portfolio|stock|market) alerts?|"
    r"broker(age)? (alerts?|emails?)"
    r")\b",
    re.IGNORECASE,
)

UNREAD_FINANCE = re.compile(
    r"\b("
    r"unread finance|"
    r"finance.{0,20}unread|"
    r"unread.{0,20}finance|"
    r"any unread finance"
    r")\b",
    re.IGNORECASE,
)

HAS_ATTACHMENT = re.compile(
    r"\b("
    r"does (it|this|that) have (an )?attachment|"
    r"(any|has) attachment|"
    r"show( me)?( the)? attachment|"
    r"is there (an )?attachment"
    r")\b",
    re.IGNORECASE,
)

SEARCH = re.compile(
    r"\b("
    r"search( my)? (e)?mails?|"
    r"find( my)? (e)?mails?|"
    r"emails? (from|about|for)|"
    r"did anyone reply|"
    r"emails? from last (week|month)|"
    r"find (invoices?|resumes?|financial reports?|earnings)"
    r")\b",
    re.IGNORECASE,
)

THREAD = re.compile(
    r"\b("
    r"open( that| this| the)? (e)?mail|"
    r"show( me)?( that| this)? thread|"
    r"what (is|about) (this|that|the) (e)?mail|"
    r"what (is )?(this|that|the) (e)?mail about|"
    r"what about that (e)?mail|"
    r"the first one|the second one|the third one"
    r")\b",
    re.IGNORECASE,
)

ATTACHMENT = re.compile(
    r"\b("
    r"summarize( the| that| this)? attachment|"
    r"what'?s? in the attachment|"
    r"read( the)? attachment|"
    r"analyze( the)? attachment"
    r")\b",
    re.IGNORECASE,
)

MEETINGS = re.compile(
    r"\b("
    r"meetings? (were )?mentioned|"
    r"any meetings in (my )?(email|inbox)|"
    r"meetings? in (my )?(email|inbox)"
    r")\b",
    re.IGNORECASE,
)

DRAFT = re.compile(
    r"\b("
    r"draft( a)? reply|write( a)? reply|"
    r"reply (politely|formally|as (an )?analyst|as (a )?founder)|"
    r"rewrite( (it|the reply))?|"
    r"make (it|the reply) (shorter|polite|formal)|"
    r"shorten( the)? reply|"
    r"improve( the)? (tone|reply)|"
    r"what should i reply"
    r")\b",
    re.IGNORECASE,
)

SEND = re.compile(
    r"^\s*(send( it)?|yes(\s+send)?|confirm( send)?|yes,?\s*send)\s*$",
    re.IGNORECASE,
)

ARCHIVE = re.compile(
    r"\b(archive( (this|that|it))?|file (this|that) away)\b",
    re.IGNORECASE,
)

MARK_READ = re.compile(
    r"\b(mark( as)? read|mark( this| that)? (as )?read)\b",
    re.IGNORECASE,
)

ABOUT_COMPANY = re.compile(
    r"\bwhat did\s+([A-Za-z][A-Za-z0-9.& \-]{1,40}?)\s+say\??$|"
    r"\b(?:emails? (?:from|about)|search(?: my)? emails? for|find emails? (?:from|about)|did anyone reply about)\s+([A-Za-z][A-Za-z0-9.& \-]{1,40}?)\??$",
    re.IGNORECASE,
)

FOLLOWUP = re.compile(
    r"\b("
    r"summarize( (the|this|that|them|these|those|it|the first|the second|the most important))?|"
    r"are any (of )?(these|them|those) (important|urgent)|"
    r"what (is|about) (this|that)|"
    r"what (is|are) the important (number|numbers|figure|figures)|"
    r"the (first|second|third) one|"
    r"any (of )?(these|them) important|"
    r"more (detail|details)|"
    r"who (is|was) that from|"
    r"does (it|this|that) have|"
    r"attachment"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class GmailIntent:
    kind: str
    query: str = ""
    tone: str = "polite"


def detect_gmail_intent(
    text: str,
    *,
    has_gmail_context: bool = False,
) -> GmailIntent:
    raw = (text or "").strip()
    if not raw:
        return GmailIntent(kind="none")
    if CONNECT.search(raw):
        return GmailIntent(kind="connect")
    if SEND.search(raw):
        return GmailIntent(kind="send")
    if ATTACHMENT.search(raw):
        return GmailIntent(kind="attachment")
    if DRAFT.search(raw):
        tone = "polite"
        low = raw.lower()
        if "formal" in low:
            tone = "formal"
        elif "analyst" in low:
            tone = "analyst"
        elif "founder" in low:
            tone = "founder"
        elif "short" in low:
            tone = "short"
        return GmailIntent(kind="draft", query=raw, tone=tone)
    if ARCHIVE.search(raw):
        return GmailIntent(kind="archive")
    if MARK_READ.search(raw):
        return GmailIntent(kind="mark_read")
    if MEETINGS.search(raw):
        return GmailIntent(kind="meetings")
    if HAS_ATTACHMENT.search(raw):
        return GmailIntent(kind="has_attachment", query=raw)
    if SUMMARY.search(raw):
        return GmailIntent(kind="summary", query=raw)
    if UNREAD_FINANCE.search(raw):
        return GmailIntent(kind="unread_finance", query=raw)
    if EARNINGS.search(raw):
        return GmailIntent(kind="earnings", query=raw)
    if INVESTMENTS.search(raw):
        return GmailIntent(kind="investments", query=raw)
    if FINANCE.search(raw):
        return GmailIntent(kind="finance", query=raw)
    if UNREAD.search(raw):
        return GmailIntent(kind="unread")
    if ATTENTION.search(raw):
        return GmailIntent(kind="priority")
    if LATEST.search(raw):
        return GmailIntent(kind="latest", query=raw)
    if CHECK.search(raw):
        return GmailIntent(kind="check")

    m = ABOUT_COMPANY.search(raw)
    if m:
        company = next((g for g in m.groups() if g), "") or ""
        return GmailIntent(kind="search", query=company.strip())
    if THREAD.search(raw):
        return GmailIntent(kind="thread", query=raw)
    if SEARCH.search(raw):
        q = raw
        for pat in (
            r"^search( my)? (e)?mails?( for)?\s*",
            r"^find( my)? (e)?mails?( for| about| from)?\s*",
            r"^emails? (from|about|for)\s*",
            r"^find\s+",
        ):
            q = re.sub(pat, "", q, flags=re.I).strip(" .!?")
        return GmailIntent(kind="search", query=q or raw)
    if has_gmail_context and FOLLOWUP.search(raw):
        low = raw.lower()
        if "important" in low or "urgent" in low:
            return GmailIntent(kind="priority", query=raw)
        if "summarize" in low or "about" in low or "first" in low or "second" in low or "this" in low:
            return GmailIntent(kind="followup", query=raw)
        return GmailIntent(kind="followup", query=raw)
    return GmailIntent(kind="none")
