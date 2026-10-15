from django.test import SimpleTestCase

from ...context_builder import PatternContext
from ...knowledge.schema import CauseKnowledge, FeatureCondition
from ..feature_vector import build_feature_vector, feature_names_for_cause, vector_to_row


def _context(pattern_confidence=0.9, **stats):
    return PatternContext(
        pattern_id="PATTERN_001", pattern_type="RECURRING_AMOUNT_DIFFERENCE",
        exception_type="AMOUNT_MISMATCH", record_count=10, total_financial_impact=100.0,
        pattern_confidence=pattern_confidence, aggregate_stats=stats,
    )


def _entry(**kwargs):
    defaults = dict(
        cause_id="TEST_CAUSE", name="Test cause", applicable_pattern_types=("RECURRING_AMOUNT_DIFFERENCE",),
        explanation_template="x", investigation_template="y",
    )
    defaults.update(kwargs)
    return CauseKnowledge(**defaults)


class BuildFeatureVectorTests(SimpleTestCase):
    def test_pattern_confidence_always_present(self):
        entry = _entry()
        vector = build_feature_vector(_context(pattern_confidence=0.73), entry)
        self.assertEqual(vector["pattern_confidence"], 0.73)

    def test_satisfied_supporting_condition_is_one(self):
        entry = _entry(supporting_conditions=(FeatureCondition("reference_match_rate", ">=", 0.9),))
        vector = build_feature_vector(_context(reference_match_rate=0.95), entry)
        self.assertEqual(vector["support::reference_match_rate::>=::0.9"], 1.0)

    def test_unsatisfied_supporting_condition_is_zero(self):
        entry = _entry(supporting_conditions=(FeatureCondition("reference_match_rate", ">=", 0.9),))
        vector = build_feature_vector(_context(reference_match_rate=0.2), entry)
        self.assertEqual(vector["support::reference_match_rate::>=::0.9"], 0.0)

    def test_missing_feature_is_zero_not_dropped(self):
        # feature not present in aggregate_stats at all -> FeatureCondition.evaluate
        # returns None -> must be encoded as 0.0, never omitted and never 1.0.
        entry = _entry(supporting_conditions=(FeatureCondition("reference_match_rate", ">=", 0.9),))
        vector = build_feature_vector(_context(), entry)  # no reference_match_rate in stats
        self.assertIn("support::reference_match_rate::>=::0.9", vector)
        self.assertEqual(vector["support::reference_match_rate::>=::0.9"], 0.0)

    def test_contradicting_condition_uses_its_own_prefix(self):
        entry = _entry(contradicting_conditions=(FeatureCondition("reference_match_rate", "<", 0.5),))
        vector = build_feature_vector(_context(reference_match_rate=0.1), entry)
        self.assertEqual(vector["contradict::reference_match_rate::<::0.5"], 1.0)


class FeatureNamesForCauseTests(SimpleTestCase):
    def test_deterministic_sorted_order(self):
        entry = _entry(
            supporting_conditions=(
                FeatureCondition("b_feature", ">=", 1),
                FeatureCondition("a_feature", ">=", 1),
            ),
        )
        names = feature_names_for_cause(entry)
        self.assertEqual(names, sorted(names))

    def test_matches_keys_actually_produced_by_build_feature_vector(self):
        entry = _entry(
            supporting_conditions=(FeatureCondition("reference_match_rate", ">=", 0.9),),
            contradicting_conditions=(FeatureCondition("reference_match_rate", "<", 0.5),),
        )
        names = feature_names_for_cause(entry)
        vector = build_feature_vector(_context(reference_match_rate=0.95), entry)
        self.assertEqual(set(names), set(vector.keys()))


class VectorToRowTests(SimpleTestCase):
    def test_missing_name_fills_zero(self):
        row = vector_to_row({"a": 1.0}, ["a", "b"])
        self.assertEqual(row, [1.0, 0.0])

    def test_preserves_requested_order_regardless_of_dict_order(self):
        row = vector_to_row({"b": 2.0, "a": 1.0}, ["a", "b"])
        self.assertEqual(row, [1.0, 2.0])
