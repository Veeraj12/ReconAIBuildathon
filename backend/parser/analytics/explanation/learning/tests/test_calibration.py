from django.test import SimpleTestCase

from ..calibration import apply_calibration, fit_calibration_curve


class FitCalibrationCurveTests(SimpleTestCase):
    def test_curve_is_non_decreasing(self):
        raw = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        outcomes = [0, 0, 0, 1, 0, 1, 1, 1, 1]
        x, y = fit_calibration_curve(raw, outcomes)
        self.assertEqual(y, sorted(y))

    def test_returns_plain_json_safe_lists(self):
        raw = [0.1, 0.5, 0.9]
        outcomes = [0, 1, 1]
        x, y = fit_calibration_curve(raw, outcomes)
        self.assertIsInstance(x, list)
        self.assertIsInstance(y, list)
        self.assertTrue(all(isinstance(v, float) for v in x))
        self.assertTrue(all(isinstance(v, float) for v in y))


class ApplyCalibrationTests(SimpleTestCase):
    def test_interpolates_between_known_points(self):
        x = [0.0, 0.5, 1.0]
        y = [0.0, 0.6, 1.0]
        # halfway between the first two breakpoints
        result = apply_calibration(0.25, x, y)
        self.assertAlmostEqual(result, 0.3, places=6)

    def test_clips_outside_known_range(self):
        x = [0.2, 0.8]
        y = [0.1, 0.9]
        self.assertAlmostEqual(apply_calibration(0.0, x, y), 0.1)
        self.assertAlmostEqual(apply_calibration(1.0, x, y), 0.9)

    def test_empty_curve_is_a_no_op(self):
        # No calibration data yet (e.g. a model trained on the
        # smallest allowed sample with fit_calibration_curve producing
        # degenerate output shouldn't happen, but a defensively empty
        # curve must never crash scoring — it should just pass the
        # raw probability through unchanged).
        self.assertEqual(apply_calibration(0.42, [], []), 0.42)
