"""Document Q&A + comparison — equity-analyst synthesis over retrieved passages."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from django.conf import settings
from django.core.cache import cache

from accounts.models import User
from ai.types import ProviderError, ProviderMessage
from documents.models import FinancialDocument, ProcessingStatus
from documents.services.document_memory import DocumentMemory
from documents.services.retriever import DocumentRetriever
from memory.models import UserPreference

logger = logging.getLogger("atlas.documents.qa")

DOC_SYNTHESIS_SYSTEM = """
You are Atlas — an experienced equity research analyst on Telegram.

You were given curated excerpts from the user's financial document(s).
Answer the user's question using ONLY those excerpts. Never dump raw text.
Never mention embeddings, retrieval, chunks, vectors, or internal tooling.

Rules:
- Answer the actual question first.
- Use only numbers and facts present in the excerpts. Do not invent figures.
- When citing, include page references from the packet (e.g. Page 32).
- If the excerpts do not contain the answer, say clearly what is missing —
  do not guess from general knowledge.
- When asked which risk is "most important" / "biggest" and the filing does not
  formally rank risks, still give a reasoned pick from the risks present in the
  excerpts (e.g. lead Item 1A themes, severity language, breadth of impact),
  cite pages, and note that the document does not assign an official ranking.
- Shape the reply to the question (simple Q→simple A; risks→risk list;
  financials→figures with period labels). Do not force a fixed report template.
- Keep it Telegram-friendly: tight paragraphs, light structure, no emojis.
""".strip()

SYNTHESIS_CACHE_TTL = 900  # reuse answers under Gemini rate pressure


class DocumentQAService:
    def __init__(
        self,
        *,
        provider=None,
        retriever: DocumentRetriever | None = None,
        memory: DocumentMemory | None = None,
    ) -> None:
        if provider is None:
            from ai.providers.gemini_provider import GeminiProvider

            provider = GeminiProvider()
        self.provider = provider
        self.retriever = retriever or DocumentRetriever()
        self.memory = memory or DocumentMemory()

    def answer(
        self,
        user: User,
        question: str,
        *,
        document_ids: list[str] | None = None,
        compare: bool = False,
    ) -> dict[str, Any]:
        ids = document_ids or self.memory.active_document_ids(user)
        if not ids:
            return {
                "ok": False,
                "error_code": "no_document",
                "error": (
                    "I don't have a report loaded yet. "
                    "Upload a filing, or say “connect my Drive” / “analyze my …” "
                    "to pull one from your files."
                ),
                "reply": (
                    "I don't have a report loaded yet. "
                    "Upload a filing, or say “connect my Drive” / “analyze my …” "
                    "to pull one from your files."
                ),
            }

        docs = list(
            FinancialDocument.objects.filter(
                user=user,
                id__in=ids,
                processing_status=ProcessingStatus.READY,
            )
        )
        if not docs:
            return {
                "ok": False,
                "error_code": "not_ready",
                "error": "That document is still processing or failed — try uploading again.",
                "reply": "That document isn't ready yet. Try uploading it again.",
            }

        # Generic: upgrade any stale/local/mixed embedding spaces before search
        from documents.services.document_pipeline import DocumentPipeline

        docs = DocumentPipeline().ensure_embedding_space(docs)

        if compare and len(docs) < 2:
            extras = list(
                FinancialDocument.objects.filter(
                    user=user,
                    processing_status=ProcessingStatus.READY,
                )
                .exclude(id__in=[d.id for d in docs])
                .order_by("-created_at")[:3]
            )
            docs = docs + extras
            ids = [str(d.id) for d in docs]

        if compare and len(docs) < 2:
            return {
                "ok": False,
                "error_code": "need_two_docs",
                "error": "need_two",
                "reply": (
                    "I need two reports to compare. "
                    "Upload a second filing (another year, quarter, or company) and ask again."
                ),
            }

        top_k = 10 if compare else 8
        # Pull more passages for financial-statement / risk digs
        qlow = (question or "").lower()
        if any(
            t in qlow
            for t in (
                "revenue",
                "net income",
                "net sales",
                "risk",
                "item 1a",
                "financial statement",
                "which page",
            )
        ):
            top_k = max(top_k, 12)
        chunks = self.retriever.retrieve_for_question(
            user_id=user.id,
            question=question,
            document_ids=[str(d.id) for d in docs[:4]],
            top_k=top_k,
        )
        if not chunks:
            return {
                "ok": False,
                "error_code": "no_passages",
                "reply": (
                    "I couldn't find a relevant section for that. "
                    "Try asking about risks, revenue, guidance, or management commentary."
                ),
            }

        packet = self.retriever.as_research_packet(chunks)
        packet["documents"] = [
            {
                "title": d.title,
                "company": d.company,
                "kind": d.document_kind,
                "period": d.fiscal_period,
                "pages": d.page_count,
            }
            for d in docs[:4]
        ]
        packet["compare_mode"] = bool(compare)
        packet["user_lens"] = self._user_lens(user)

        reply = self._synthesize(question=question, packet=packet)
        return {
            "ok": True,
            "reply": reply,
            "document_ids": [str(d.id) for d in docs[:4]],
            "sources": [p.get("source") for p in packet.get("passages") or [] if p.get("source")],
            "tool": "document_compare" if compare else "document_qa",
        }

    def _user_lens(self, user: User) -> dict[str, Any]:
        prefs = UserPreference.objects.filter(user=user).first()
        return {
            "role": user.role or "",
            "sectors": list((prefs.sectors_of_interest if prefs else []) or [])[:8],
            "style_tags": list((prefs.insight_types if prefs else []) or [])[:6],
        }

    def _synth_cache_key(self, *, question: str, packet: dict[str, Any]) -> str:
        doc_ids = [str(d.get("id") or d.get("title") or "") for d in (packet.get("documents") or [])]
        raw = "|".join(
            [
                question.strip().lower()[:400],
                "1" if packet.get("compare_mode") else "0",
                ",".join(doc_ids),
                str(len(packet.get("passages") or [])),
            ]
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
        return f"docqa:synth:{digest}"

    def _synthesize(self, *, question: str, packet: dict[str, Any]) -> str:
        cache_key = self._synth_cache_key(question=question, packet=packet)
        cached = cache.get(cache_key)
        if isinstance(cached, str) and cached.strip():
            logger.info("event=doc_synth_cache_hit")
            return cached

        # Cap packet size under load to reduce token use / quota pressure
        lean_packet = dict(packet)
        trimmed = []
        for p in list(lean_packet.get("passages") or [])[:6]:
            row = dict(p)
            excerpt = str(row.get("excerpt") or "")
            if len(excerpt) > 1200:
                row["excerpt"] = excerpt[:1200] + "…"
            trimmed.append(row)
        lean_packet["passages"] = trimmed

        user_prompt = (
            f"User question:\n{question}\n\n"
            f"Document research packet (use facts; do not expose structure):\n"
            f"{json.dumps(lean_packet, ensure_ascii=False, default=str)}\n\n"
            "Write the Telegram analyst reply now."
        )
        # Prefer light Flash for synthesis — more reliable under quota pressure
        model = getattr(self.provider, "light_model", None) or getattr(self.provider, "model", None)
        try:
            # Ensure client/model resolution has run
            if hasattr(self.provider, "_ensure_client"):
                self.provider._ensure_client()
                model = getattr(self.provider, "light_model", None) or self.provider.model
            response = self.provider.generate(
                system=DOC_SYNTHESIS_SYSTEM,
                messages=[ProviderMessage(role="user", content=user_prompt)],
                temperature=0.35,
                response_json=False,
                model=model,
            )
            text = (response.text or "").strip()
        except ProviderError as exc:
            logger.warning("event=doc_synth_provider_error err=%s", type(exc).__name__)
            # One recovery attempt: clear sticky bad model and retry light
            try:
                from ai.providers.model_resolve import clear_resolve_cache, mark_model_failed

                if model:
                    mark_model_failed(str(model))
                clear_resolve_cache()
                if hasattr(self.provider, "_model_resolved"):
                    self.provider._model_resolved = False
                if hasattr(self.provider, "_ensure_client"):
                    self.provider._ensure_client()
                retry_model = getattr(self.provider, "light_model", None) or self.provider.model
                response = self.provider.generate(
                    system=DOC_SYNTHESIS_SYSTEM,
                    messages=[ProviderMessage(role="user", content=user_prompt)],
                    temperature=0.35,
                    response_json=False,
                    model=retry_model,
                )
                text = (response.text or "").strip()
            except Exception as recover_exc:  # noqa: BLE001
                logger.warning(
                    "event=doc_synth_recover_failed err=%s",
                    type(recover_exc).__name__,
                )
                return self._fallback_reply(question, packet)
        except Exception as exc:  # noqa: BLE001
            logger.warning("event=doc_synth_error err=%s", type(exc).__name__)
            return self._fallback_reply(question, packet)

        if not text:
            return self._fallback_reply(question, packet)
        text = re.sub(
            r"\b(embedding|vector store|chunk(?:s|ing)?|retriev\w+)\b",
            "",
            text,
            flags=re.I,
        )
        text = text.strip() or self._fallback_reply(question, packet)
        ttl = int(getattr(settings, "CACHE_TTL_DOCUMENT", SYNTHESIS_CACHE_TTL) or SYNTHESIS_CACHE_TTL)
        cache.set(cache_key, text, timeout=min(ttl, SYNTHESIS_CACHE_TTL))
        return text

    def _fallback_reply(self, question: str, packet: dict[str, Any]) -> str:
        passages = packet.get("passages") or []
        docs = packet.get("documents") or []
        title = docs[0]["title"] if docs else "the report"
        lines = [
            f"*Summary*\nFrom {title}, here's what I can pull together right now for: "
            f"{question.strip()[:120]}",
            "",
            "*Key Findings*",
        ]
        if passages:
            for p in passages[:4]:
                src = p.get("source") or title
                excerpt = re.sub(r"\s+", " ", (p.get("excerpt") or ""))[:220]
                lines.append(f"• {excerpt} ({src})")
        else:
            lines.append("• I still have the report loaded, but live synthesis is briefly constrained.")
        lines.extend(
            [
                "",
                "Ask again in a moment for a fuller take, or narrow to risks, revenue, "
                "AI strategy, or guidance.",
            ]
        )
        return "\n".join(lines)
