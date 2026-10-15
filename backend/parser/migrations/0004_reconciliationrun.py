import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("parser", "0003_causefeedback_raw_stats"),
    ]

    operations = [
        migrations.CreateModel(
            name="ReconciliationRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_type", models.CharField(choices=[("single", "Bank vs Tally"), ("multi", "Multi-source")], max_length=10)),
                ("result", models.JSONField()),
                ("summary_snapshot", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reconciliation_runs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="reconciliationrun",
            index=models.Index(fields=["user", "-created_at"], name="parser_reco_user_id_c1e4a2_idx"),
        ),
    ]
