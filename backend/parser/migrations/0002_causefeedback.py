# Hand-written to match the style of 0001_initial.py — this is what
# `python manage.py makemigrations` would generate from the
# CauseFeedback model added to models.py. If you're applying this to
# a real project, prefer regenerating it with makemigrations against
# your actual models.py to be safe; this file is provided so the
# feedback table and the rest of this change are usable together
# immediately.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("parser", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CauseFeedback",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cause_id", models.CharField(db_index=True, max_length=100)),
                ("pattern_type", models.CharField(max_length=100)),
                ("exception_type", models.CharField(max_length=100)),
                ("features", models.JSONField()),
                ("confirmed", models.BooleanField()),
                ("reconciliation_reference", models.CharField(blank=True, max_length=255, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "submitted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cause_feedback",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="causefeedback",
            index=models.Index(fields=["cause_id", "created_at"], name="parser_caus_cause_i_5f4a1e_idx"),
        ),
    ]
