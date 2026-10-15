import pathlib
import tempfile

from django.test import SimpleTestCase

from ..schema import TrainedCauseModel
from ..store import load_trained_models, save_trained_models


def _model(cause_id="TEST_CAUSE"):
    return TrainedCauseModel(
        cause_id=cause_id,
        feature_names=["pattern_confidence", "support::reference_match_rate::>=::0.9"],
        coefficients=[0.1, 2.3],
        intercept=-1.5,
        calibration_x=[0.0, 0.5, 1.0],
        calibration_y=[0.0, 0.6, 1.0],
        trained_at="2026-01-01T00:00:00Z",
        sample_count=120,
        positive_count=52,
    )


class StoreRoundTripTests(SimpleTestCase):
    def test_missing_file_returns_empty_dict(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "does_not_exist.json"
        self.assertEqual(load_trained_models(path), {})

    def test_save_then_load_preserves_every_field(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "trained_weights.json"
        model = _model()
        save_trained_models({"TEST_CAUSE": model}, path)

        reloaded = load_trained_models(path)

        self.assertEqual(set(reloaded.keys()), {"TEST_CAUSE"})
        self.assertEqual(reloaded["TEST_CAUSE"], model)

    def test_output_is_plain_readable_json(self):
        # The whole point of storing weights this way instead of a
        # pickle is that a human/reviewer can open the file directly.
        path = pathlib.Path(tempfile.mkdtemp()) / "trained_weights.json"
        save_trained_models({"TEST_CAUSE": _model()}, path)

        raw_text = path.read_text(encoding="utf-8")
        self.assertIn("TEST_CAUSE", raw_text)
        self.assertIn("coefficients", raw_text)
        # No pickle/binary markers.
        self.assertNotIn("\x80", raw_text)

    def test_multiple_causes_stored_independently(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "trained_weights.json"
        save_trained_models({"A": _model("A"), "B": _model("B")}, path)
        reloaded = load_trained_models(path)
        self.assertEqual(set(reloaded.keys()), {"A", "B"})
