from django.contrib import admin

from gmail.models import GmailMessage, GmailSyncState


@admin.register(GmailSyncState)
class GmailSyncStateAdmin(admin.ModelAdmin):
    list_display = ("user", "mode", "status", "last_synced_at", "updated_at")
    list_filter = ("mode", "status")


@admin.register(GmailMessage)
class GmailMessageAdmin(admin.ModelAdmin):
    list_display = (
        "subject",
        "from_name",
        "user",
        "is_unread",
        "is_archived",
        "priority_score",
        "received_at",
    )
    list_filter = ("is_unread", "is_archived")
    search_fields = ("subject", "from_name", "from_email")
    readonly_fields = ("message_id", "thread_id", "body_text")
