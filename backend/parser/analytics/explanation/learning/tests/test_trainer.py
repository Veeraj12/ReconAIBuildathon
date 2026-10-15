import random

from django.test import SimpleTestCase

from ...context_builder import PatternContext
from ...knowledge.schema import CauseKnowledge, FeatureCondition
from ..feature_vector import build_feature_vector
from ..schema import FeedbackRecord
from ..trainer import CauseScorerTrainer, InsufficientDataError, MIN_PER_CLASS, MIN_SAMPLES_PER_CAUSE, predict_raw_probability

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
    explanation_template="x", investigation_template="y",
)


def _context(reference_match_rate, date_offset, pattern_confidence=0.9):
    return PatternContext(
        pattern_id="PATTERN_001", pattern_type="RECURRING_AMOUNT_DIFFERENCE",
        exception_type="AMOUNT_MISMATCH", record_count=10, total_financial_impact=100.0,
        pattern_confidence=pattern_confidence,
        aggregate_stats={
            "average_absolute_amount_difference": 251.0,
            "reference_match_rate": reference_match_rate,
            "common_date_offset_days": date_offset,
        },
    )


def _synthetic_records(n=120, seed=7, cause_id="BANK_SERVICE_CHARGE", noise=0.10):
    rng = random.Random(seed)
    records = []
    for _ in range(n):
        ref_rate = rng.choice([0.95, 0.92, 0.60, 0.40, 0.98])
        offset = rng.choice([0, 0, 1])
        ctx = _context(ref_rate, offset, pattern_confidence=rng.uniform(0.5, 0.99))
        truth = ref_rate >= 0.9 and offset == 0
        confirmed = truth if rng.random() > noise else (not truth)
        records.append(FeedbackRecord(
            cause_id=cause_id, pattern_type="RECURRING_AMOUNT_DIFFERENCE",
            exception_type="AMOUNT_MISMATCH", features=build_feature_vector(ctx, _ENTRY),
            confirmed=confirmed, recorded_at="2026-01-01T00:00:00Z",
        ))
    return records


class InsufficientDataTests(SimpleTestCase):
    def test_below_min_samples_raises(self):
        records = _synthetic_records(n=MIN_SAMPLES_PER_CAUSE - 1)
        with self.assertRaises(InsufficientDataError):
            CauseScorerTrainer()._fit_one("BANK_SERVICE_CHARGE", records)

    def test_below_min_per_class_raises_even_with_enough_total(self):
        # Plenty of total records, but almost all one label.
        records = [
            FeedbackRecord(cause_id="X", pattern_type="P", exception_type="E", features={"a": 1.0}, confirmed=True,
                            recorded_at="2026-01-01T00:00:00Z")
            for _ in range(MIN_SAMPLES_PER_CAUSE + 10)
        ] + [
            FeedbackRecord(cause_id="X", pattern_type="P", exception_type="E", features={"a": 0.0}, confirmed=False,
                            recorded_at="2026-01-01T00:00:00Z")
            for _ in range(MIN_PER_CLASS - 1)
        ]
        with self.assertRaises(InsufficientDataError):
            CauseScorerTrainer()._fit_one("X", records)

    def test_fit_all_silently_skips_undertrained_causes(self):
        # A trainable cause plus an undertrained one in the same batch —
        # fit_all must return the trainable one and simply omit the other,
        # never raise.
        good = _synthetic_records(n=120, cause_id="TRAINABLE")
        few = _synthetic_records(n=3, cause_id="TOO_FEW")
        trained = CauseScorerTrainer().fit_all(good + few)
        self.assertIn("TRAINABLE", trained)
        self.assertNotIn("TOO_FEW", trained)


class FitOneTests(SimpleTestCase):
    def test_learns_positive_weight_for_a_genuinely_predictive_feature(self):
        records = _synthetic_records(n=150, noise=0.05)
        model = CauseScorerTrainer()._fit_one("BANK_SERVICE_CHARGE", records)
        weights = dict(zip(model.feature_names, model.coefficients))
        # The two conditions that actually determine ground truth in our
        # synthetic data should end up with clearly positive weight; the
        # contradicting condition should end up negative.
        self.assertGreater(weights["support::reference_match_rate::>=::0.9"], 0)
        self.assertGreater(weights["support::common_date_offset_days::==::0"], 0)
        self.assertLess(weights["contradict::reference_match_rate::<::0.5"], 0)

    def test_calibration_curve_is_attached_and_non_empty(self):
        records = _synthetic_records(n=120)
        model = CauseScorerTrainer()._fit_one("BANK_SERVICE_CHARGE", records)
        self.assertTrue(len(model.calibration_x) > 0)
        self.assertEqual(len(model.calibration_x), len(model.calibration_y))

    def test_sample_and_positive_counts_recorded_accurately(self):
        records = _synthetic_records(n=120)
        model = CauseScorerTrainer()._fit_one("BANK_SERVICE_CHARGE", records)
        self.assertEqual(model.sample_count, 120)
        self.assertEqual(model.positive_count, sum(r.confirmed for r in records))

    def test_deterministic_given_fixed_seed_input(self):
        records = _synthetic_records(n=120, seed=99)
        model_a = CauseScorerTrainer()._fit_one("BANK_SERVICE_CHARGE", records)
        model_b = CauseScorerTrainer()._fit_one("BANK_SERVICE_CHARGE", records)
        self.assertEqual(model_a.coefficients, model_b.coefficients)
        self.assertEqual(model_a.intercept, model_b.intercept)


class PredictRawProbabilityTests(SimpleTestCase):
    def test_higher_supporting_signal_yields_higher_probability(self):
        records = _synthetic_records(n=150, noise=0.05)
        model = CauseScorerTrainer()._fit_one("BANK_SERVICE_CHARGE", records)

        strong = build_feature_vector(_context(0.98, 0), _ENTRY)
        weak = build_feature_vector(_context(0.3, 1), _ENTRY)

        self.assertGreater(predict_raw_probability(model, strong), predict_raw_probability(model, weak))

    def test_probability_is_bounded(self):
        records = _synthetic_records(n=150)
        model = CauseScorerTrainer()._fit_one("BANK_SERVICE_CHARGE", records)
        vector = build_feature_vector(_context(0.99, 0), _ENTRY)
        p = predict_raw_probability(model, vector)
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)
