"""Drive conversation facade — connect / search / import for Telegram."""

from __future__ import annotations

import logging
from typing import Any

from accounts.models import GoogleService, User
from accounts.services.google_oauth_service import GoogleOAuthService
from drive.models import DriveConnectionMode
from drive.services.drive_intent import DriveIntent, detect_drive_intent
from drive.services.drive_sync import DriveSyncService
from memory.models import UserPreference

logger = logging.getLogger("atlas.drive.service")


class DriveService:
    """User-facing Drive operations without exposing API details."""

    def __init__(
        self,
        *,
        oauth: GoogleOAuthService | None = None,
        sync: DriveSyncService | None = None,
    ) -> None:
        self.oauth = oauth or GoogleOAuthService()
        self.sync = sync or DriveSyncService(oauth=self.oauth)

    def handle_intent(self, user: User, text: str) -> dict[str, Any] | None:
        intent = detect_drive_intent(text)
        if intent.kind == "none":
            return None
        if intent.kind == "connect":
            return self.connect(user)
        if intent.kind == "disconnect":
            return self.disconnect(user)
        if intent.kind == "sync":
            return self.sync_now(user)
        if intent.kind == "search":
            return self.search(user, intent.query)
        if intent.kind == "import":
            return self.import_and_ready(user, intent.query)
        return None

    def connect(self, user: User) -> dict[str, Any]:
        if self.oauth.is_configured():
            started = self.oauth.start_auth(user, service=GoogleService.DRIVE)
            if started.get("ok"):
                url = started["auth_url"]
                return {
                    "ok": True,
                    "handled": True,
                    "reply": (
                        "Let's connect your files.\n\n"
                        f"Open this link to authorize read-only access:\n{url}\n\n"
                        "When you're done, come back and say something like "
                        "“search my Drive for Apple” or “analyze my Apple annual report.”"
                    ),
                }
            # Fall through to demo if misconfigured mid-flight
        self.sync.connect_demo(user)
        sync_result = self.sync.full_sync(user)
        n = (sync_result.get("stats") or {}).get("seen", 0)
        return {
            "ok": True,
            "handled": True,
            "demo": True,
            "reply": (
                "Your files are ready (demo library with sample filings).\n\n"
                f"I indexed {n} item(s). Try:\n"
                "• “Search my Drive for Apple”\n"
                "• “Analyze my Apple annual report”\n"
                "• “What documents do I have about AI?”"
            ),
        }

    def disconnect(self, user: User) -> dict[str, Any]:
        self.oauth.disconnect(user, service=GoogleService.DRIVE)
        state = self.sync.ensure_state(user)
        state.mode = DriveConnectionMode.DEMO
        state.save(update_fields=["mode", "updated_at"])
        return {
            "ok": True,
            "handled": True,
            "reply": "Disconnected. Say “connect my Drive” anytime to link files again.",
        }

    def sync_now(self, user: User) -> dict[str, Any]:
        if not self.sync.is_connected(user):
            return {
                "ok": False,
                "handled": True,
                "reply": "Connect your Drive first — say “connect my Drive”.",
            }
        result = self.sync.background_sync_hook(user)
        if not result.get("ok"):
            return {
                "ok": False,
                "handled": True,
                "reply": result.get("error")
                or "I couldn't refresh your files just now. Try again shortly.",
            }
        return {
            "ok": True,
            "handled": True,
            "reply": "Your file library is up to date.",
        }

    def search(self, user: User, query: str) -> dict[str, Any]:
        if not self.sync.is_connected(user):
            return {
                "ok": False,
                "handled": True,
                "reply": (
                    "I don't have access to your files yet. "
                    "Say “connect my Drive” and I'll take it from there."
                ),
            }
        q = (query or "").strip()
        files = (
            self.sync.find_by_company_or_topic(user, q, limit=8)
            if q
            else self.sync.list_recent(user, limit=8)
        )
        files = self._prioritize_preferences(user, files)

        # Also surface already-imported library docs matching the topic
        from documents.models import FinancialDocument, ProcessingStatus
        from django.db.models import Q

        doc_lines: list[str] = []
        if q:
            docs = FinancialDocument.objects.filter(
                user=user,
                processing_status=ProcessingStatus.READY,
            ).filter(
                Q(title__icontains=q)
                | Q(original_filename__icontains=q)
                | Q(metadata__company__icontains=q)
                | Q(extracted_text__icontains=q)
            )[:6]
            for d in docs:
                doc_lines.append(f"• *{d.title}* — already loaded")

        if not files and not doc_lines:
            return {
                "ok": True,
                "handled": True,
                "reply": (
                    f"I didn't find files matching “{q}”."
                    if q
                    else "Your Drive library looks empty right now."
                ),
            }
        lines = []
        seen_names = set()
        for f in files[:6]:
            tag = ""
            if f.metadata_only:
                tag = " (spreadsheet — metadata only)"
            elif f.document_id:
                tag = " — already loaded"
            lines.append(f"• *{f.name}*{tag}")
            seen_names.add((f.name or "").lower())
        for line in doc_lines:
            # avoid duplicate titles already listed
            if any(n and n in line.lower() for n in seen_names):
                continue
            lines.append(line)
        header = (
            f"Here's what I found for “{q}”:"
            if q
            else "Here are some of your recent files:"
        )
        return {
            "ok": True,
            "handled": True,
            "files": files,
            "reply": header + "\n" + "\n".join(lines[:8]) + (
                "\n\nSay “analyze my …” with a filename and I’ll dig in."
            ),
        }

    def import_and_ready(self, user: User, query: str) -> dict[str, Any]:
        if not self.sync.is_connected(user):
            # Auto-enable demo for hackathon continuity when user asks to analyze Drive files
            self.sync.connect_demo(user)
            self.sync.full_sync(user)
        result = self.sync.import_matching(user, query)
        return {
            "ok": bool(result.get("ok")),
            "handled": True,
            "document": result.get("document"),
            "reply": result.get("reply") or result.get("error") or "Done.",
            "error_code": result.get("error_code"),
        }

    def _prioritize_preferences(self, user: User, files: list) -> list:
        pref = UserPreference.objects.filter(user=user).first()
        tokens: list[str] = []
        if pref:
            tokens.extend(str(x).lower() for x in (pref.sectors_of_interest or []))
            tokens.extend(str(x).lower() for x in (pref.additional_verticals or []))
        if not tokens:
            return files

        def score(f) -> int:
            name = (f.name or "").lower()
            return sum(1 for t in tokens if t and t in name)

        return sorted(files, key=score, reverse=True)
