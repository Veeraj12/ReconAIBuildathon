import random

from django.test import SimpleTestCase

from ...context_builder import PatternContext
from ...knowledge.schema import CauseKnowledge, FeatureCondition
from ...retriever import CandidateCause
from ...scoring import CauseScoringEngine
from ..feature_vector import build_feature_vector
from ..learned_scorer import LearnedCauseScoringEngine
from ..schema import FeedbackRecord
from ..trainer import CauseScorerTrainer

_ENTRY = CauseKnowledge(
    cause_id="BANK_SERVICE_CHARGE",
    name="Recurring bank/service charge",
    applicable_pattern_types=("RECURRING_AMOUNT_DIFFERENCE",),
    required_features=("average_absolute_amount_difference",),
    supporting_conditions=(
        FeatureCondition("reference_match_rate", ">=", 0.90),
        FeatureCondition("common_date_offset_days", "==", 0),
    ),
    contradicting_conditions=(FeatureCondition("reference_match_rate", "<", 0.5),),
    explanation_template="A recurring bank charge may explain this.",
    investigation_template="Check the bank's fee schedule.",
)

_UNTRAINED_ENTRY = CauseKnowledge(
    cause_id="UNSEEN_CAUSE", name="Unseen cause",
    applicable_pattern_types=("RECURRING_AMOUNT_DIFFERENCE",),
    supporting_conditions=(FeatureCondition("reference_match_rate", ">=", 0.9),),
    explanation_template="x", investigation_template="y",
)


def _context(reference_match_rate=0.95, date_offset=0, pattern_confidence=0.95):
    return PatternContext(
        pattern_id="PATTERN_001", pattern_type="RECURRING_AMOUNT_DIFFERENCE",
        exception_type="AMOUNT_MISMATCH", record_count=100, total_financial_impact=25100.0,
        pattern_confidence=pattern_confidence,
        aggregate_stats={
            "average_absolute_amount_difference": 251.0,
            "reference_match_rate": reference_match_rate,
            "common_date_offset_days": date_offset,
        },
    )


def _trained_models(n=150, seed=7):
    rng = random.Random(seed)
    records = []
    for _ in range(n):
        ref_rate = rng.choice([0.95, 0.92, 0.60, 0.40, 0.98])
        offset = rng.choice([0, 0, 1])
        ctx = _context(ref_rate, offset, pattern_confidence=rng.uniform(0.5, 0.99))
        truth = ref_rate >= 0.9 and offset == 0
        confirmed = truth if rng.random() > 0.05 else (not truth)
        records.append(FeedbackRecord(
            cause_id="BANK_SERVICE_CHARGE", pattern_type="RECURRING_AMOUNT_DIFFERENCE",
            exception_type="AMOUNT_MISMATCH", features=build_feature_vector(ctx, _ENTRY),
            confirmed=confirmed, recorded_at="2026-01-01T00:00:00Z",
        ))
    return CauseScorerTrainer().fit_all(records)


class ColdStartFallbackTests(SimpleTestCase):
    """With no trained model at all, LearnedCauseScoringEngine must behave
    identically to the untouched rule-based engine — this is the
    guarantee that lets it ship as the default with zero feedback collected."""

    def test_empty_model_dict_matches_rule_based_engine_exactly(self):
        context = _context()
        candidate = CandidateCause(knowledge=_ENTRY)

        rule_based = CauseScoringEngine().score_all(context, [candidate])
        learned = LearnedCauseScoringEngine(trained_models={}).score_all(context, [candidate])

        self.assertEqual(len(rule_based), len(learned))
        self.assertEqual(rule_based[0].score, learned[0].score)
        self.assertEqual(rule_based[0].confidence_level, learned[0].confidence_level)
        self.assertEqual(rule_based[0].supporting_evidence, learned[0].supporting_evidence)

    def test_untrained_cause_falls_back_even_when_other_causes_are_trained(self):
        trained = _trained_models()
        context = _context()
        untrained_candidate = CandidateCause(knowledge=_UNTRAINED_ENTRY)

        rule_based = CauseScoringEngine().score_all(context, [untrained_candidate])
        learned = LearnedCauseScoringEngine(trained_models=trained).score_all(context, [untrained_candidate])

        self.assertEqual(rule_based[0].score, learned[0].score)


class LearnedScoringTests(SimpleTestCase):
    def test_trained_cause_produces_a_valid_scored_cause(self):
        trained = _trained_models()
        context = _context(reference_match_rate=0.98, date_offset=0)
        candidate = CandidateCause(knowledge=_ENTRY)

        scored = LearnedCauseScoringEngine(trained_models=trained).score_all(context, [candidate])

        self.assertEqual(len(scored), 1)
        self.assertGreaterEqual(scored[0].score, 0.0)
        self.assertLessEqual(scored[0].score, 1.0)
        self.assertIn(scored[0].confidence_level, ("high", "medium", "low"))

    def test_weak_evidence_can_still_be_dropped_below_retain_threshold(self):
        trained = _trained_models()
        context = _context(reference_match_rate=0.1, date_offset=1)
        candidate = CandidateCause(knowledge=_ENTRY)

        scored = LearnedCauseScoringEngine(trained_models=trained).score_all(context, [candidate])
        self.assertEqual(scored, [])

    def test_evidence_text_still_only_cites_declared_conditions(self):
        trained = _trained_models()
        context = _context(reference_match_rate=0.98, date_offset=0)
        candidate = CandidateCause(knowledge=_ENTRY)

        scored = LearnedCauseScoringEngine(trained_models=trained).score_all(context, [candidate])[0]
        cited_features = {e.feature for e in scored.supporting_evidence + scored.contradicting_evidence}
        declared_features = {c.feature for c in _ENTRY.supporting_conditions + _ENTRY.contradicting_conditions}
        self.assertTrue(cited_features.issubset(declared_features))

    def test_deterministic_tie_break_and_ordering_preserved(self):
        # Two candidates for the same context — result must still be
        # sorted score desc, cause_id asc, exactly like the rule-based
        # engine's own contract.
        trained = _trained_models()
        context = _context(reference_match_rate=0.98, date_offset=0)
        candidates = [CandidateCause(knowledge=_ENTRY), CandidateCause(knowledge=_UNTRAINED_ENTRY)]

        scored = LearnedCauseScoringEngine(trained_models=trained).score_all(context, candidates)
        scores_and_ids = [(s.score, s.cause_id) for s in scored]
        self.assertEqual(scores_and_ids, sorted(scores_and_ids, key=lambda t: (-t[0], t[1])))
