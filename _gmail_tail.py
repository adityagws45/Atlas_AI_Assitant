    def draft_reply(
        self, user: User, *, instruction: str = "", tone: str = "polite"
    ) -> dict[str, Any]:
        msg = self._resolve_active(user)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Open or search an email first, then ask me to draft a reply.",
            }
        draft = self.drafts.draft(
            subject=msg.subject,
            from_name=msg.from_name or "there",
            body_context=msg.snippet or msg.body_text[:400],
            instruction=instruction,
            tone=tone,
        )
        draft["message_pk"] = str(msg.id)
        self.memory.save_draft(user, draft)
        self.memory.remember_open(user, self._row_dict(msg))
        return {
            "ok": True,
            "handled": True,
            "reply": self.drafts.format_draft_reply(draft),
            "draft": draft,
        }

    def confirm_send(self, user: User, text: str) -> dict[str, Any]:
        draft = self.memory.get_draft(user)
        if not draft:
            return {
                "ok": False,
                "handled": True,
                "reply": "There’s no draft waiting. Ask me to “draft a reply” first.",
            }
        low = (text or "").strip().lower()
        if low in {"send", "send it"} and not self.memory.is_pending_send(user):
            self.memory.mark_pending_send(user)
            return {
                "ok": True,
                "handled": True,
                "reply": self.drafts.format_draft_reply(draft, pending_send=True),
            }
        # Confirmed
        self.memory.clear_draft(user)
        # Read-only OAuth: never silently call send API
        return {
            "ok": True,
            "handled": True,
            "reply": (
                "Confirmed — in this setup I keep send gated (read-only inbox by default).\n\n"
                "Your approved draft:\n\n"
                f"{draft.get('body')}\n\n"
                "Paste it into your mail client, or reconnect later with send access if you want Atlas to deliver it."
            ),
        }

    def archive_active(self, user: User) -> dict[str, Any]:
        msg = self._resolve_active(user)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Nothing active to archive. Open an email first.",
            }
        try:
            client = self._client_for(user)
            client.archive(msg.message_id)
        except Exception:  # noqa: BLE001
            pass
        msg.is_archived = True
        msg.is_unread = False
        msg.save(update_fields=["is_archived", "is_unread", "updated_at"])
        return {
            "ok": True,
            "handled": True,
            "reply": f"Archived *{msg.subject or 'that email'}*. What’s next?",
        }

    def mark_active_read(self, user: User) -> dict[str, Any]:
        msg = self._resolve_active(user)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Nothing active to mark read. Open an email first.",
            }
        try:
            client = self._client_for(user)
            client.mark_read(msg.message_id)
        except Exception:  # noqa: BLE001
            pass
        msg.is_unread = False
        msg.save(update_fields=["is_unread", "updated_at"])
        return {
            "ok": True,
            "handled": True,
            "reply": f"Marked *{msg.subject or 'that email'}* as read.",
        }

    def summarize_attachment(self, user: User) -> dict[str, Any]:
        msg = self._resolve_active(user)
        if msg is None:
            return {
                "ok": False,
                "handled": True,
                "reply": "Open an email with an attachment first.",
            }
        atts = msg.attachments or []
        if not atts:
            return {
                "ok": False,
                "handled": True,
                "reply": f"*{msg.subject or 'That email'}* doesn’t have an attachment I can read.",
            }
        att = atts[0]
        filename = att.get("filename") or "attachment"
        mime = att.get("mime_type") or ""
        # Only supported types via DocumentPipeline
        lower = filename.lower()
        if not any(lower.endswith(ext) for ext in (".pdf", ".txt", ".md", ".markdown", ".docx")):
            # Demo PDF may be labeled application/pdf with .pdf — ok
            if "pdf" not in mime and "text" not in mime:
                return {
                    "ok": False,
                    "handled": True,
                    "reply": (
                        f"I see *{filename}*, but that format isn’t supported yet. "
                        "PDF, TXT, DOCX, or Markdown work best."
                    ),
                }
        try:
            client = self._client_for(user)
            data = client.get_attachment_bytes(msg.message_id, att.get("id") or filename)
        except Exception:  # noqa: BLE001
            data = None
        if not data:
            # Demo text fallback embedded in attachment metadata
            demo_text = att.get("demo_text") or ""
            if demo_text:
                data = demo_text.encode("utf-8")
                if not lower.endswith((".txt", ".md", ".pdf")):
                    filename = filename.rsplit(".", 1)[0] + ".txt"
                    mime = "text/plain"
            else:
                return {
                    "ok": False,
                    "handled": True,
                    "reply": f"I couldn’t download *{filename}*. It may have been removed.",
                }

        # Force text path for demo PDF bytes that are actually text
        ingest_name = filename
        ingest_mime = mime
        if filename.lower().endswith(".pdf") and data[:4] != b"%PDF":
            ingest_name = filename.rsplit(".", 1)[0] + ".txt"
            ingest_mime = "text/plain"

        try:
            doc = self.docs.ingest_bytes(
                user,
                data=data,
                filename=ingest_name,
                mime_type=ingest_mime or "text/plain",
                source=DocumentSource.TELEGRAM,
                title_override=f"{msg.subject[:80]} — {filename}"[:200],
                extra_metadata={"origin": "gmail_attachment", "email_subject": msg.subject},
            )
        except ValueError as exc:
            return {"ok": False, "handled": True, "reply": str(exc) or "Couldn’t process that attachment."}
        except Exception:  # noqa: BLE001
            return {
                "ok": False,
                "handled": True,
                "reply": f"I hit a snag reading *{filename}*. Try again in a moment.",
            }

        qa = self.doc_qa.answer(
            user,
            f"Summarize this attachment for a busy investor. File: {filename}",
            document_ids=[str(doc.id)],
            compare=False,
        )
        reply = qa.get("reply") if qa.get("ok") else None
        if not reply:
            # Deterministic fallback from demo text / snippet
            text_preview = data.decode("utf-8", errors="replace")[:600]
            reply = (
                f"*Attachment summary* — *{filename}*\n\n"
                f"{text_preview}\n\n"
                "Ask a follow-up about risks, numbers, or what to reply."
            )
        self.memory.remember_open(user, self._row_dict(msg))
        return {"ok": True, "handled": True, "reply": reply, "document": doc}

    def _resolve_active(self, user: User, query: str = "") -> GmailMessage | None:
        mid = self.memory.active_message_id(user)
        if mid:
            msg = GmailMessage.objects.filter(user=user, id=mid, is_archived=False).first()
            if msg:
                return msg
            msg = GmailMessage.objects.filter(
                user=user, message_id=mid, is_archived=False
            ).first()
            if msg:
                return msg
        return (
            GmailMessage.objects.filter(user=user, is_archived=False)
            .order_by("-priority_score", "-received_at")
            .first()
        )

    def _row_dict(self, row: GmailMessage) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "message_id": row.message_id,  # internal only — strip before user logs
            "subject": row.subject,
            "from_name": row.from_name,
            "from_email": row.from_email,
            "snippet": row.snippet,
            "body_text": row.body_text,
            "is_unread": row.is_unread,
            "has_attachment": row.has_attachment,
            "attachments": row.attachments or [],
            "companies": row.companies or [],
            "tickers": row.tickers or [],
            "people": row.people or [],
            "categories": row.categories or [],
            "priority_score": row.priority_score,
            "why": (row.extra or {}).get("why") or "",
        }
