from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authn", "0004_apikey_is_dashboard_test_key"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenant",
            name="artifact_retention_days",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Overrides ARTIFACT_RETENTION_DAYS. Use 0 for unlimited retention.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="document_retention_days",
            field=models.PositiveIntegerField(
                blank=True,
                help_text="Overrides DOCUMENT_RETENTION_DAYS. Use 0 for unlimited retention.",
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="tenant",
            name="infected_quarantine_retention_days",
            field=models.PositiveIntegerField(
                blank=True,
                help_text=(
                    "Overrides INFECTED_QUARANTINE_RETENTION_DAYS. "
                    "Use 0 to keep infected quarantine files indefinitely."
                ),
                null=True,
            ),
        ),
    ]
