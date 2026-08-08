"""Email drafting — never auto-send; confirmation required."""

from __future__ import annotations

import re
from typing import Any


TONES = {
    "polite": "warm, concise, respectful",
    "formal": "formal, precise, board-ready",
    "analyst": "equity-analyst tone — clear, evidence-led",
    "founder": "founder tone — direct, decisive, grateful",
    "short": "as short as possible while remaining complete",
}


class GmailDraftService:
    def draft(
        self,
        *,
        subject: str,
        from_name: str,
        body_context: str,
        instruction: str = "",
        tone: str = "polite",
    ) -> dict[str, Any]:
        tone_key = (tone or "polite").lower()
        if "formal" in (instruction or "").lower() or "formal" in tone_key:
            tone_key = "formal"
        elif "analyst" in (instruction or "").lower() or "analyst" in tone_key:
            tone_key = "analyst"
        elif "founder" in (instruction or "").lower() or "founder" in tone_key:
            tone_key = "founder"
        elif "short" in (instruction or "").lower() or "shorten" in (instruction or "").lower():
            tone_key = "short"
        elif "polite" in (instruction or "").lower() or "rewrite" in (instruction or "").lower():
            tone_key = "polite"

        first = (from_name or "there").split()[0]
        ctx = (body_context or "").strip()
        ctx_bit = ""
        if ctx:
            # Pull a short cue without dumping the email
            cue = re.split(r"(?<=[.!?])\s+", ctx)[0][:120]
            ctx_bit = f" Thanks for the note regarding “{cue.rstrip('.')}.”"

        if tone_key == "formal":
            text = (
                f"Dear {first},\n\n"
                f"Thank you for your message.{ctx_bit} "
                "I have reviewed the details and will follow up with a clear next step shortly.\n\n"
                "Kind regards"
            )
        elif tone_key == "analyst":
            text = (
                f"Hi {first},\n\n"
                f"Appreciate the update.{ctx_bit} "
                "From my side: the key questions are timing, magnitude, and what changes for allocation. "
                "Happy to align on those points.\n\n"
                "Best"
            )
        elif tone_key == "founder":
            text = (
                f"Hi {first},\n\n"
                f"Thanks — this is helpful.{ctx_bit} "
                "Let’s lock the next step and keep momentum. I’ll confirm shortly.\n\n"
                "Thanks"
            )
        elif tone_key == "short":
            text = f"Hi {first} — thanks, noted.{ctx_bit} I’ll confirm next steps soon.\n\nBest"
        else:
            text = (
                f"Hi {first},\n\n"
                f"Thank you for reaching out.{ctx_bit} "
                "I’ll review and get back to you with a clear response.\n\n"
                "Best regards"
            )

        if instruction and "improve" in instruction.lower():
            text = text.replace("I’ll review and get back to you with a clear response.", 
                                "I’ve reviewed this and can share a concrete recommendation on request.")

        return {
            "tone": tone_key,
            "subject": f"Re: {subject}" if subject and not subject.lower().startswith("re:") else subject,
            "body": text.strip(),
            "awaiting_send_confirm": True,
        }

    def format_draft_reply(self, draft: dict[str, Any], *, pending_send: bool = False) -> str:
        body = draft.get("body") or ""
        subj = draft.get("subject") or ""
        if pending_send:
            return (
                "*Ready to send*\n"
                f"Subject: {subj}\n\n"
                f"{body}\n\n"
                "Reply *YES* to confirm. I won’t send without that."
            )
        return (
            "*Draft reply*\n"
            f"Subject: {subj}\n\n"
            f"{body}\n\n"
            "Nothing sent yet. Say “rewrite politely”, “make it shorter”, "
            "or “send it” when you’re ready."
        )
