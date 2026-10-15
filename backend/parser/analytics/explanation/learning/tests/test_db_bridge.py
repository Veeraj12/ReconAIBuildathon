from django.test import TestCase

from ..db_bridge import load_case_indexes
from ..similar_cases import MIN_CASES_FOR_KNN
from .....models import CauseFeedback


class LoadCaseIndexesTests(TestCase):
    """Uses Django's TestCase (not SimpleTestCase, unlike every other
    file in this package) because this is the one module that actually
    touches the database — CauseFeedback rows need a real (test)
    database to be created and queried against."""

    def _create_feedback(self, cause_id, confirmed, count):
        for i in range(count):
            CauseFeedback.objects.create(
                cause_id=cause_id,
                pattern_type="RECURRING_AMOUNT_DIFFERENCE",
                exception_type="AMOUNT_MISMATCH",
                features={"pattern_confidence": 0.9, "support::reference_match_rate::>=::0.9": 1.0 if confirmed else 0.0},
                confirmed=confirmed,
            )

    def test_below_min_cases_is_excluded(self):
        self._create_feedback("SPARSE_CAUSE", True, MIN_CASES_FOR_KNN - 1)
        indexes = load_case_indexes()
        self.assertNotIn("SPARSE_CAUSE", indexes)

    def test_at_or_above_min_cases_is_included(self):
        self._create_feedback("ENOUGH_CAUSE", True, MIN_CASES_FOR_KNN)
        indexes = load_case_indexes()
        self.assertIn("ENOUGH_CAUSE", indexes)
        self.assertEqual(len(indexes["ENOUGH_CAUSE"]), MIN_CASES_FOR_KNN)

    def test_only_cause_ids_filter_narrows_query(self):
        self._create_feedback("CAUSE_A", True, MIN_CASES_FOR_KNN)
        self._create_feedback("CAUSE_B", True, MIN_CASES_FOR_KNN)
        indexes = load_case_indexes(only_cause_ids=["CAUSE_A"])
        self.assertEqual(set(indexes.keys()), {"CAUSE_A"})

    def test_empty_table_returns_empty_dict(self):
        self.assertEqual(load_case_indexes(), {})

    def test_submitted_by_null_does_not_break_conversion(self):
        # submitted_by is nullable (SET_NULL on user deletion) — the
        # bridge/to_feedback_record conversion must not assume a user
        # is always attached.
        CauseFeedback.objects.create(
            cause_id="NO_USER_CAUSE", pattern_type="P", exception_type="E",
            features={"pattern_confidence": 0.5}, confirmed=True, submitted_by=None,
        )
        self._create_feedback("NO_USER_CAUSE", True, MIN_CASES_FOR_KNN - 1)
        indexes = load_case_indexes()
        self.assertIn("NO_USER_CAUSE", indexes)
