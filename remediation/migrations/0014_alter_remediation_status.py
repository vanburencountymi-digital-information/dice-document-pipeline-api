from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("remediation", "0013_remediationcallback"),
    ]

    operations = [
        migrations.AlterField(
            model_name="remediation",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("compliant", "Compliant"),
                    ("noncompliant", "Noncompliant"),
                    ("error", "Error"),
                    ("skipped", "Skipped"),
                ],
                default="queued",
                max_length=20,
            ),
        ),
    ]
