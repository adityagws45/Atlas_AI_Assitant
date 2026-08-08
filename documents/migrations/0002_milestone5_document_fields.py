# Generated manually for Milestone 5

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="financialdocument",
            name="content_hash",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="financialdocument",
            name="error_message",
            field=models.CharField(blank=True, default="", max_length=512),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="financialdocument",
            name="file_size_bytes",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="financialdocument",
            name="original_filename",
            field=models.CharField(blank=True, default="", max_length=512),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="financialdocument",
            name="source",
            field=models.CharField(
                choices=[
                    ("telegram_upload", "Telegram Upload"),
                    ("drive", "Google Drive"),
                    ("sec_edgar", "SEC EDGAR"),
                    ("local", "Local / Test"),
                ],
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="documentchunk",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="documentchunk",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
        migrations.AddField(
            model_name="documentchunk",
            name="embedding",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="documentchunk",
            name="metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="documentchunk",
            name="page_end",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentchunk",
            name="page_start",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="documentchunk",
            name="section",
            field=models.CharField(blank=True, default="", max_length=256),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="documentchunk",
            name="token_estimate",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name="financialdocument",
            index=models.Index(
                fields=["user", "processing_status"],
                name="financial_d_user_id_proc_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="financialdocument",
            index=models.Index(
                fields=["user", "content_hash"],
                name="financial_d_user_id_hash_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="documentchunk",
            index=models.Index(
                fields=["document", "page_start"],
                name="document_ch_documen_page_idx",
            ),
        ),
    ]
