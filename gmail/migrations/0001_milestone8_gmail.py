# Generated manually for Milestone 8

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GmailSyncState",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "mode",
                    models.CharField(
                        choices=[("oauth", "OAuth"), ("demo", "Local demo")],
                        default="demo",
                        max_length=16,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("idle", "Idle"), ("running", "Running"), ("failed", "Failed")],
                        default="idle",
                        max_length=16,
                    ),
                ),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.CharField(blank=True, max_length=512)),
                ("stats", models.JSONField(blank=True, default=dict)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="gmail_sync_state",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "gmail_sync_states"},
        ),
        migrations.CreateModel(
            name="GmailMessage",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("message_id", models.CharField(db_index=True, max_length=128)),
                ("thread_id", models.CharField(blank=True, db_index=True, max_length=128)),
                ("subject", models.CharField(blank=True, max_length=512)),
                ("from_name", models.CharField(blank=True, max_length=256)),
                ("from_email", models.CharField(blank=True, max_length=320)),
                ("snippet", models.CharField(blank=True, max_length=512)),
                ("body_text", models.TextField(blank=True)),
                ("received_at", models.DateTimeField(blank=True, null=True)),
                ("is_unread", models.BooleanField(default=True)),
                ("is_archived", models.BooleanField(default=False)),
                ("is_important", models.BooleanField(default=False)),
                ("labels", models.JSONField(blank=True, default=list)),
                ("companies", models.JSONField(blank=True, default=list)),
                ("tickers", models.JSONField(blank=True, default=list)),
                ("people", models.JSONField(blank=True, default=list)),
                ("categories", models.JSONField(blank=True, default=list)),
                ("priority_score", models.FloatField(default=0.0)),
                ("has_attachment", models.BooleanField(default=False)),
                ("attachments", models.JSONField(blank=True, default=list)),
                ("extra", models.JSONField(blank=True, default=dict)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="gmail_messages",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "gmail_messages"},
        ),
        migrations.AddConstraint(
            model_name="gmailmessage",
            constraint=models.UniqueConstraint(
                fields=("user", "message_id"), name="uniq_user_gmail_message"
            ),
        ),
        migrations.AddIndex(
            model_name="gmailmessage",
            index=models.Index(
                fields=["user", "is_unread", "is_archived"],
                name="gmail_messa_user_id_7c0a1a_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="gmailmessage",
            index=models.Index(
                fields=["user", "priority_score"],
                name="gmail_messa_user_id_prio_idx",
            ),
        ),
    ]
