from django.test import SimpleTestCase

from ...context_builder import PatternContext
from ...knowledge.schema import CauseKnowledge, FeatureCondition
from ...retriever import CandidateCause
from ...scoring import CauseScoringEngine
from ..feature_vector import build_feature_vector
from ..schema import FeedbackRecord
from ..similar_cases import CaseIndex, Neighbor, build_case_indexes, rule_based_raw_score, shrinkage_score

_ENTRY = CauseKnowledge(
    cause_id="TEST_CAUSE", name="Test cause", applicable_pattern_types=("RECURRING_AMOUNT_DIFFERENCE",),
    supporting_conditions=(
        FeatureCondition("reference_match_rate", ">=", 0.90),
        FeatureCondition("common_date_offset_days", "==", 0),
    ),
    contradicting_conditions=(FeatureCondition("reference_match_rate", "<", 0.5),),
    explanation_template="x", investigation_template="y",
)


def _context(reference_match_rate=0.95, date_offset=0, pattern_confidence=0.9):
    return PatternContext(
        pattern_id="P1", pattern_type="RECURRING_AMOUNT_DIFFERENCE", exception_type="AMOUNT_MISMATCH",
        record_count=10, total_financial_impact=100.0, pattern_confidence=pattern_confidence,
        aggregate_stats={"reference_match_rate": reference_match_rate, "common_date_offset_days": date_offset},
    )


def _record(confirmed, reference_match_rate=0.95, date_offset=0, cause_id="TEST_CAUSE", recorded_at="2026-01-01T00:00:00Z"):
    vector = build_feature_vector(_context(reference_match_rate, date_offset), _ENTRY)
    return FeedbackRecord(
        cause_id=cause_id, pattern_type="RECURRING_AMOUNT_DIFFERENCE", exception_type="AMOUNT_MISMATCH",
        features=vector, confirmed=confirmed, recorded_at=recorded_at,
    )


class RuleBasedRawScoreTests(SimpleTestCase):
    def test_matches_engine_score_when_above_retention_threshold(self):
        context = _context(reference_match_rate=0.98, date_offset=0, pattern_confidence=0.9)
        candidate = CandidateCause(knowledge=_ENTRY)
        engine_result = CauseScoringEngine().score_all(context, [candidate])
        raw = rule_based_raw_score(context, candidate)
        self.assertAlmostEqual(engine_result[0].score, round(raw, 4))

    def test_still_returns_a_number_when_engine_would_drop_the_candidate(self):
        context = _context(reference_match_rate=0.2, date_offset=1, pattern_confidence=0.2)
        candidate = CandidateCause(knowledge=_ENTRY)
        engine_result = CauseScoringEngine().score_all(context, [candidate])
        self.assertEqual(engine_result, [])  # dropped by the real engine
        raw = rule_based_raw_score(context, candidate)
        self.assertGreaterEqual(raw, 0.0)
        self.assertLess(raw, 0.25)

    def test_bounded_between_zero_and_one(self):
        context = _context(reference_match_rate=0.99, date_offset=0, pattern_confidence=0.99)
        candidate = CandidateCause(knowledge=_ENTRY)
        raw = rule_based_raw_score(context, candidate)
        self.assertGreaterEqual(raw, 0.0)
        self.assertLessEqual(raw, 1.0)


class CaseIndexTests(SimpleTestCase):
    def test_empty_index_returns_no_neighbors(self):
        index = CaseIndex([])
        self.assertEqual(index.nearest({"pattern_confidence": 0.9}), [])

    def test_returns_at_most_k_neighbors(self):
        records = [_record(True) for _ in range(10)]
        index = CaseIndex(records)
        neighbors = index.nearest(build_feature_vector(_context(), _ENTRY), k=3)
        self.assertEqual(len(neighbors), 3)

    def test_closest_neighbor_is_an_identical_record(self):
        exact_match_vector = build_feature_vector(_context(0.95, 0), _ENTRY)
        far_record = _record(True, reference_match_rate=0.1, date_offset=1)
        near_record = FeedbackRecord(
            cause_id="TEST_CAUSE", pattern_type="P", exception_type="E",
            features=exact_match_vector, confirmed=True, recorded_at="2026-01-01T00:00:00Z",
        )
        index = CaseIndex([far_record, near_record])
        neighbors = index.nearest(exact_match_vector, k=1)
        self.assertEqual(neighbors[0].distance, 0.0)
        self.assertIs(neighbors[0].record, near_record)

    def test_len_reflects_record_count(self):
        index = CaseIndex([_record(True), _record(False)])
        self.assertEqual(len(index), 2)


class BuildCaseIndexesTests(SimpleTestCase):
    def test_groups_by_cause_id(self):
        records = [_record(True, cause_id="A"), _record(False, cause_id="A"), _record(True, cause_id="B")]
        indexes = build_case_indexes(records)
        self.assertEqual(set(indexes.keys()), {"A", "B"})
        self.assertEqual(len(indexes["A"]), 2)
        self.assertEqual(len(indexes["B"]), 1)


class ShrinkageScoreTests(SimpleTestCase):
    def test_no_neighbors_collapses_to_the_prior_exactly(self):
        self.assertEqual(shrinkage_score([], prior=0.42), 0.42)

    def test_all_confirmed_neighbors_pull_score_above_prior(self):
        neighbors = [Neighbor(record=_record(True), distance=0.0) for _ in range(5)]
        score = shrinkage_score(neighbors, prior=0.3)
        self.assertGreater(score, 0.3)

    def test_all_rejected_neighbors_pull_score_below_prior(self):
        neighbors = [Neighbor(record=_record(False), distance=0.0) for _ in range(5)]
        score = shrinkage_score(neighbors, prior=0.7)
        self.assertLess(score, 0.7)

    def test_more_prior_strength_means_less_movement_for_the_same_neighbors(self):
        neighbors = [Neighbor(record=_record(True), distance=0.0) for _ in range(3)]
        weak_prior_trust = shrinkage_score(neighbors, prior=0.2, prior_strength=1.0)
        strong_prior_trust = shrinkage_score(neighbors, prior=0.2, prior_strength=10.0)
        # Same evidence, but a higher prior_strength should stay closer to the prior.
        self.assertLess(abs(strong_prior_trust - 0.2), abs(weak_prior_trust - 0.2))

    def test_score_bounded_zero_to_one_for_bounded_prior(self):
        neighbors = [Neighbor(record=_record(True), distance=0.0) for _ in range(5)]
        score = shrinkage_score(neighbors, prior=0.9)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
