# Generated manually for Milestone 9

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CalendarSyncState",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
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
                ("timezone", models.CharField(default="UTC", max_length=64)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("error_message", models.CharField(blank=True, max_length=512)),
                ("stats", models.JSONField(blank=True, default=dict)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="calendar_sync_state",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "calendar_sync_states"},
        ),
        migrations.CreateModel(
            name="CalendarEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("event_id", models.CharField(db_index=True, max_length=128)),
                ("calendar_id", models.CharField(default="primary", max_length=128)),
                ("title", models.CharField(max_length=512)),
                ("description", models.TextField(blank=True)),
                ("location", models.CharField(blank=True, max_length=512)),
                ("start_at", models.DateTimeField(db_index=True)),
                ("end_at", models.DateTimeField()),
                ("all_day", models.BooleanField(default=False)),
                ("status", models.CharField(default="confirmed", max_length=32)),
                ("is_recurring", models.BooleanField(default=False)),
                ("categories", models.JSONField(blank=True, default=list)),
                ("companies", models.JSONField(blank=True, default=list)),
                ("tickers", models.JSONField(blank=True, default=list)),
                ("importance", models.FloatField(default=0.0)),
                ("extra", models.JSONField(blank=True, default=dict)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="calendar_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "calendar_events"},
        ),
        migrations.AddConstraint(
            model_name="calendarevent",
            constraint=models.UniqueConstraint(
                fields=("user", "event_id"), name="uniq_user_calendar_event"
            ),
        ),
        migrations.AddIndex(
            model_name="calendarevent",
            index=models.Index(
                fields=["user", "start_at", "status"],
                name="calendar_ev_user_start_idx",
            ),
        ),
    ]
