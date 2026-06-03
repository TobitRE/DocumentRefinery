from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authn", "0003_apikey_allowed_upload_mime_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="apikey",
            name="is_dashboard_test_key",
            field=models.BooleanField(default=False),
        ),
    ]
