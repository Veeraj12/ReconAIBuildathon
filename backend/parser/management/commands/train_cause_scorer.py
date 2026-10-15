"""
python manage.py train_cause_scorer

Reads every CauseFeedback row, trains whichever cause_ids now have
enough data (see learning/trainer.py's MIN_SAMPLES_PER_CAUSE /
MIN_PER_CLASS), and writes the result to
analytics/explanation/learning/trained_weights.json.

MERGE, NOT REPLACE
────────────────────
A retrain only ever ADDS or UPDATES entries for cause_ids that had
enough feedback THIS time. A cause_id that was trainable last time but
has since fallen below the threshold (shouldn't normally happen, since
feedback only accumulates — but e.g. after a database cleanup) keeps
its last-known-good model rather than silently reverting to the
rule-based fallback. To force-remove a cause's learned model entirely,
delete its entry from trained_weights.json by hand (it's a plain JSON
file — see learning/store.py) and rerun this command.

This command deliberately does not schedule itself — run it by hand,
via cron, or via whatever job runner the deployment already uses.
Nothing in the request path (views.py, service.py) ever triggers
training; LearnedCauseScoringEngine only ever LOADS
trained_weights.json (see learned_scorer.py), it never writes to it.
"""

from django.core.management.base import BaseCommand

from parser.analytics.explanation.learning.store import DEFAULT_WEIGHTS_PATH, load_trained_models, save_trained_models
from parser.analytics.explanation.learning.trainer import CauseScorerTrainer
from parser.models import CauseFeedback


class Command(BaseCommand):
    help = "Retrain the learned cause scorer from accumulated CauseFeedback rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--min-cause-id",
            action="append",
            default=None,
            help="Only (re)train these cause_id(s). Repeatable. Default: consider every cause_id with feedback.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Train and report what WOULD be written, without touching trained_weights.json.",
        )

    def handle(self, *args, **options):
        queryset = CauseFeedback.objects.all()
        only_causes = options.get("min_cause_id")
        if only_causes:
            queryset = queryset.filter(cause_id__in=only_causes)

        records = [row.to_feedback_record() for row in queryset.iterator()]
        self.stdout.write(f"Loaded {len(records)} feedback records.")

        newly_trained = CauseScorerTrainer().fit_all(records)

        if not newly_trained:
            self.stdout.write(self.style.WARNING(
                "No cause_id has enough feedback yet to train (see MIN_SAMPLES_PER_CAUSE / "
                "MIN_PER_CLASS in learning/trainer.py). Nothing written."
            ))
            return

        existing = load_trained_models()
        merged = {**existing, **newly_trained}

        for cause_id, model in sorted(newly_trained.items()):
            self.stdout.write(
                f"  {cause_id}: {model.sample_count} samples "
                f"({model.positive_count} confirmed, {model.sample_count - model.positive_count} rejected)"
            )

        if options.get("dry_run"):
            self.stdout.write(self.style.WARNING(f"--dry-run: would write {len(merged)} cause model(s) to "
                                                   f"{DEFAULT_WEIGHTS_PATH} (not written)."))
            return

        save_trained_models(merged, DEFAULT_WEIGHTS_PATH)
        self.stdout.write(self.style.SUCCESS(
            f"Trained {len(newly_trained)} cause(s) this run; {len(merged)} total in {DEFAULT_WEIGHTS_PATH}."
        ))
