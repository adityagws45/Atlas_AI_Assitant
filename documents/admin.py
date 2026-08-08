from django.contrib import admin

from documents.models import DocumentChunk, FinancialDocument


class DocumentChunkInline(admin.TabularInline):
    model = DocumentChunk
    extra = 0
    fields = ("chunk_index", "page_start", "page_end", "section", "content")
    readonly_fields = ("chunk_index", "page_start", "page_end", "section", "content")
    can_delete = False


@admin.register(FinancialDocument)
class FinancialDocumentAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "user",
        "source",
        "processing_status",
        "page_count",
        "created_at",
    )
    list_filter = ("source", "processing_status")
    search_fields = ("title", "original_filename", "content_hash")
    readonly_fields = ("content_hash", "file_size_bytes", "error_message")
    inlines = [DocumentChunkInline]
    exclude = ("extracted_text", "gemini_file_uri")
