from django.test import SimpleTestCase

from ..discretizer import compute_quantile_thresholds, discretize_case, discretize_pool
from ..schema import ConditionItem, ResolvedCase


def _case(**raw_stats):
    return ResolvedCase(cause_id="X", pattern_type="P", exception_type="E", confirmed=True,
                         recorded_at="t", raw_stats=raw_stats)


class ComputeQuantileThresholdsTests(SimpleTestCase):
    def test_empty_list_returns_empty(self):
        self.assertEqual(compute_quantile_thresholds([]), [])

    def test_few_points_falls_back_to_median(self):
        self.assertEqual(compute_quantile_thresholds([10.0]), [10.0])
        self.assertEqual(compute_quantile_thresholds([10.0, 20.0]), [15.0])

    def test_deterministic_given_same_input(self):
        values = [5, 15, 25, 35, 45, 55, 65, 75]
        self.assertEqual(compute_quantile_thresholds(values), compute_quantile_thresholds(values))

    def test_returns_sorted_deduplicated_values(self):
        thresholds = compute_quantile_thresholds([1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(thresholds, sorted(set(thresholds)))


class DiscretizePoolTests(SimpleTestCase):
    def test_ignores_rate_and_date_offset_features(self):
        cases = [_case(reference_match_rate=0.9, common_date_offset_days=0, amount_diff=100 + i) for i in range(10)]
        thresholds = discretize_pool(cases)
        self.assertNotIn("reference_match_rate", thresholds)
        self.assertNotIn("common_date_offset_days", thresholds)
        self.assertIn("amount_diff", thresholds)

    def test_ignores_non_numeric_values(self):
        cases = [_case(some_text="not a number") for _ in range(5)]
        thresholds = discretize_pool(cases)
        self.assertEqual(thresholds, {})


class DiscretizeCaseTests(SimpleTestCase):
    def test_date_offset_zero_gives_equality_item(self):
        case = _case(common_date_offset_days=0)
        items = discretize_case(case, {})
        self.assertIn(ConditionItem("common_date_offset_days", "==", 0).label, items)

    def test_date_offset_nonzero_gives_at_least_one_item(self):
        case = _case(common_date_offset_days=3)
        items = discretize_case(case, {})
        self.assertIn(ConditionItem("common_date_offset_days", ">=", 1).label, items)

    def test_rate_feature_high_value_gets_multiple_ge_items(self):
        case = _case(reference_match_rate=0.97)
        items = discretize_case(case, {})
        self.assertIn(ConditionItem("reference_match_rate", ">=", 0.95).label, items)
        self.assertIn(ConditionItem("reference_match_rate", ">=", 0.9).label, items)
        self.assertIn(ConditionItem("reference_match_rate", ">=", 0.8).label, items)
        self.assertIn(ConditionItem("reference_match_rate", ">=", 0.5).label, items)

    def test_rate_feature_low_value_gets_lt_items_not_ge(self):
        case = _case(reference_match_rate=0.3)
        items = discretize_case(case, {})
        self.assertIn(ConditionItem("reference_match_rate", "<", 0.5).label, items)
        self.assertNotIn(ConditionItem("reference_match_rate", ">=", 0.5).label, items)

    def test_every_threshold_produces_exactly_one_directional_item(self):
        # For every rate threshold, exactly one of >= / < should appear — never both, never neither.
        case = _case(reference_match_rate=0.85)
        items = discretize_case(case, {})
        from ..discretizer import RATE_THRESHOLDS
        for threshold in RATE_THRESHOLDS:
            ge_present = ConditionItem("reference_match_rate", ">=", threshold).label in items
            lt_present = ConditionItem("reference_match_rate", "<", threshold).label in items
            self.assertNotEqual(ge_present, lt_present)  # exactly one is true

    def test_quantile_feature_uses_given_thresholds_both_directions(self):
        thresholds = {"amount_diff": [100.0, 200.0]}
        high_case = _case(amount_diff=250.0)
        low_case = _case(amount_diff=50.0)

        high_items = discretize_case(high_case, thresholds)
        low_items = discretize_case(low_case, thresholds)

        self.assertIn(ConditionItem("amount_diff", ">=", 100.0).label, high_items)
        self.assertIn(ConditionItem("amount_diff", ">=", 200.0).label, high_items)
        self.assertIn(ConditionItem("amount_diff", "<", 100.0).label, low_items)
        self.assertIn(ConditionItem("amount_diff", "<", 200.0).label, low_items)

    def test_missing_feature_contributes_nothing(self):
        case = _case(reference_match_rate=0.9)  # no amount_diff at all
        items = discretize_case(case, {"amount_diff": [100.0]})
        self.assertFalse(any("amount_diff" in item for item in items))
