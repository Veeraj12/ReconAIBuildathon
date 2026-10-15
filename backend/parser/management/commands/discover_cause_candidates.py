"""
python manage.py discover_cause_candidates

Mines accumulated CauseFeedback.raw_stats for candidate NEW supporting
conditions (or, for a cause_id an accountant free-typed with no
catalogue.py entry yet, a candidate brand-new cause) using association
rule mining — point (d). See analytics/explanation/discovery/README.md
for the full design.

This NEVER writes to catalogue.py. It prints/exports draft suggestions
for a human to review and, if they look right, copy by hand into
catalogue.py — see discovery/README.md §4 for why auto-insertion was
deliberately ruled out.
"""

import json

from django.core.management.base import BaseCommand

from parser.analytics.explanation.discovery.db_bridge import load_resolved_cases
from parser.analytics.explanation.discovery.discoverer import (
    DEFAULT_MIN_CONFIDENCE, DEFAULT_MIN_LIFT, DEFAULT_MIN_SAMPLE_COUNT, DEFAULT_MIN_SUPPORT,
    CauseCandidateDiscoverer,
)


class Command(BaseCommand):
    help = "Discover candidate new cause conditions from accumulated CauseFeedback via association rule mining."

    def add_arguments(self, parser):
        parser.add_argument("--cause-id", action="append", default=None,
                             help="Only mine these cause_id(s). Repeatable. Default: every cause_id with feedback.")
        parser.add_argument("--min-support", type=float, default=DEFAULT_MIN_SUPPORT)
        parser.add_argument("--min-confidence", type=float, default=DEFAULT_MIN_CONFIDENCE)
        parser.add_argument("--min-lift", type=float, default=DEFAULT_MIN_LIFT)
        parser.add_argument("--min-sample-count", type=int, default=DEFAULT_MIN_SAMPLE_COUNT)
        parser.add_argument("--output", type=str, default=None,
                             help="Write full JSON drafts to this file path, in addition to the console summary.")

    def handle(self, *args, **options):
        cases = load_resolved_cases(only_cause_ids=options.get("cause_id"))
        self.stdout.write(f"Loaded {len(cases)} resolved case(s) with raw_stats.")

        discoverer = CauseCandidateDiscoverer(
            min_support=options["min_support"],
            min_confidence=options["min_confidence"],
            min_lift=options["min_lift"],
            min_sample_count=options["min_sample_count"],
        )
        drafts = discoverer.discover(cases)

        if not drafts:
            self.stdout.write(self.style.WARNING(
                "No candidate conditions cleared the support/confidence/lift/sample-count thresholds. "
                "This is a normal outcome with little feedback yet, not necessarily an error — try "
                "lowering --min-sample-count or --min-support once more feedback accumulates."
            ))
            return

        self.stdout.write(self.style.SUCCESS(f"\nFound {len(drafts)} candidate draft(s):\n"))
        for i, draft in enumerate(drafts, start=1):
            new_or_existing = "NEW CAUSE" if not draft.is_new_condition_for_existing_cause else "new condition for existing cause"
            self.stdout.write(f"{i}. [{new_or_existing}] {draft.suggested_cause_id} ({draft.pattern_type})")
            self.stdout.write(f"   conditions: {draft.suggested_supporting_conditions}")
            self.stdout.write(
                f"   support={draft.support:.2f} confidence={draft.confidence:.2f} "
                f"lift={draft.lift:.2f} n={draft.sample_count}"
            )
            self.stdout.write(f"   {draft.notes}\n")

        if options.get("output"):
            with open(options["output"], "w", encoding="utf-8") as f:
                json.dump([d.to_dict() for d in drafts], f, indent=2)
            self.stdout.write(self.style.SUCCESS(f"Full JSON written to {options['output']}"))
