from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("documents", "0009_document_created_by_user_document_created_via_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="infected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
