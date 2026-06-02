# Generated manually for Docling dashboard runtime metadata.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0007_rename_documents_i_comparison_274fbb_idx_documents_i_compari_451f04_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="ingestionjob",
            name="docling_core_version",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="ingestionjob",
            name="docling_parse_version",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AddField(
            model_name="ingestionjob",
            name="runtime_json",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="ingestionjob",
            name="result_metrics_json",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
