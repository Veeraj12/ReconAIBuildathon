from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("parser", "0002_causefeedback"),
    ]

    operations = [
        migrations.AddField(
            model_name="causefeedback",
            name="raw_stats",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
