from django.test import TestCase

from ..db_bridge import load_resolved_cases
from .....models import CauseFeedback


class LoadResolvedCasesTests(TestCase):
    """Django TestCase (not SimpleTestCase) — the one file in this
    package that touches the database, mirroring
    learning/tests/test_db_bridge.py's convention exactly."""

    def test_rows_without_raw_stats_are_excluded(self):
        CauseFeedback.objects.create(
            cause_id="NO_STATS", pattern_type="P", exception_type="E",
            features={}, raw_stats={}, confirmed=True,
        )
        cases = load_resolved_cases()
        self.assertEqual(cases, [])

    def test_rows_with_raw_stats_are_included(self):
        CauseFeedback.objects.create(
            cause_id="HAS_STATS", pattern_type="RECURRING_AMOUNT_DIFFERENCE", exception_type="AMOUNT_MISMATCH",
            features={}, raw_stats={"reference_match_rate": 0.9}, confirmed=True,
        )
        cases = load_resolved_cases()
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].cause_id, "HAS_STATS")
        self.assertEqual(cases[0].raw_stats, {"reference_match_rate": 0.9})

    def test_only_cause_ids_filter_narrows_query(self):
        CauseFeedback.objects.create(cause_id="A", pattern_type="P", exception_type="E",
                                      features={}, raw_stats={"x": 1}, confirmed=True)
        CauseFeedback.objects.create(cause_id="B", pattern_type="P", exception_type="E",
                                      features={}, raw_stats={"x": 1}, confirmed=True)
        cases = load_resolved_cases(only_cause_ids=["A"])
        self.assertEqual({c.cause_id for c in cases}, {"A"})

    def test_empty_table_returns_empty_list(self):
        self.assertEqual(load_resolved_cases(), [])
