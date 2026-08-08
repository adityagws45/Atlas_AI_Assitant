from django.db import models

from accounts.models import User
from core.models import TimeStampedModel, UUIDModel


class DocumentSource(models.TextChoices):
    TELEGRAM = "telegram_upload", "Telegram Upload"
    DRIVE = "drive", "Google Drive"
    SEC_EDGAR = "sec_edgar", "SEC EDGAR"
    LOCAL = "local", "Local / Test"


class DocumentKind(models.TextChoices):
    ANNUAL_REPORT = "10-k", "Annual Report (10-K)"
    QUARTERLY_REPORT = "10-q", "Quarterly Report (10-Q)"
    EARNINGS = "earnings", "Earnings Report"
    TRANSCRIPT = "transcript", "Earnings Call Transcript"
    PRESENTATION = "presentation", "Investor Presentation"
    SEC_FILING = "sec_filing", "SEC Filing"
    OTHER = "other", "Financial Document"


class ProcessingStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class FinancialDocument(UUIDModel, TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="documents")
    source = models.CharField(max_length=32, choices=DocumentSource.choices)
    title = models.CharField(max_length=512)
    original_filename = models.CharField(max_length=512, blank=True)
    file = models.FileField(upload_to="documents/%Y/%m/", blank=True)
    mime_type = models.CharField(max_length=128, blank=True)
    file_size_bytes = models.PositiveIntegerField(null=True, blank=True)
    content_hash = models.CharField(max_length=64, blank=True, db_index=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    extracted_text = models.TextField(blank=True)
    gemini_file_uri = models.CharField(max_length=512, blank=True)
    processing_status = models.CharField(
        max_length=16, choices=ProcessingStatus.choices, default=ProcessingStatus.PENDING
    )
    error_message = models.CharField(max_length=512, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "financial_documents"
        indexes = [
            models.Index(fields=["user", "processing_status"]),
            models.Index(fields=["user", "content_hash"]),
        ]

    def __str__(self):
        return self.title

    @property
    def company(self) -> str:
        return str((self.metadata or {}).get("company") or "")

    @property
    def document_kind(self) -> str:
        return str((self.metadata or {}).get("kind") or DocumentKind.OTHER)

    @property
    def fiscal_period(self) -> str:
        return str((self.metadata or {}).get("fiscal_period") or "")


class DocumentChunk(UUIDModel, TimeStampedModel):
    document = models.ForeignKey(
        FinancialDocument, on_delete=models.CASCADE, related_name="chunks"
    )
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    page_start = models.PositiveIntegerField(null=True, blank=True)
    page_end = models.PositiveIntegerField(null=True, blank=True)
    section = models.CharField(max_length=256, blank=True)
    token_estimate = models.PositiveIntegerField(default=0)
    embedding = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "document_chunks"
        ordering = ["chunk_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"],
                name="uniq_document_chunk_index",
            ),
        ]
        indexes = [
            models.Index(fields=["document", "page_start"]),
        ]

    def __str__(self):
        return f"{self.document_id}#{self.chunk_index}"
