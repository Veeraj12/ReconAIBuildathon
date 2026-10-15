"""
Probability calibration — turns "the model's raw sigmoid output" into
"a number that actually means what it says" (a score of 0.7 should
mean roughly 70% of causes scored ~0.7 were confirmed correct).

WHY THIS IS A SEPARATE STEP FROM THE SCORING FORMULA
───────────────────────────────────────────────────────
`scoring.py`'s hand-written formula produces a 0..1 number, but
0.10 + 0.30*confidence + ... was never validated against real
outcomes — it's a reasonable-looking guess (its own docstring is
explicit about this: "no arbitrary numbers" refers to the arithmetic
being *documented*, not to the constants being *derived from data*).
A learned logistic-regression model's raw `predict_proba` output has
the same issue on small/imbalanced training sets: logistic regression
is "calibrated" in the textbook sense only asymptotically, and with a
few dozen feedback records per cause it commonly is not.

Isotonic regression fixes this cheaply: fit a non-decreasing step
function mapping "raw predicted probability" -> "empirical fraction
confirmed correct", using OUT-OF-FOLD predictions (never the same rows
the linear model itself was fit on) so the calibration curve isn't
just memorizing the training set.

WHY BREAKPOINTS, NOT A PICKLED MODEL
───────────────────────────────────────
`sklearn.isotonic.IsotonicRegression` is fit once, offline, during
training (see trainer.py). At INFERENCE time (inside a live request),
this module never imports scikit-learn at all — it just linearly
interpolates between the exported (x, y) breakpoints with
`numpy.interp`. Two benefits:
  1. No live dependency on a specific sklearn version / no pickle
     deserialization of a model object in the request path — the
     calibration curve is just two lists of numbers, auditable the
     same way every other config file in this project is.
  2. `numpy.interp`'s default out-of-bounds behavior (clip to the
     nearest known y-value) matches `IsotonicRegression(out_of_bounds=
     "clip")` used at fit time, so behavior is identical, not just
     "close enough".
"""

from __future__ import annotations

from typing import Tuple

import numpy as np


def fit_calibration_curve(raw_probabilities: list, outcomes: list) -> Tuple[list, list]:
    """Fit an isotonic (non-decreasing) calibration curve.

    `raw_probabilities` should be OUT-OF-FOLD predictions from the
    linear model (see trainer.py's use of cross_val_predict) — never
    the same rows the model itself memorized, or the curve would just
    rubber-stamp the model's own overconfidence.

    Returns (calibration_x, calibration_y) as plain Python lists,
    ready to store in a TrainedCauseModel and to JSON-serialize.
    """
    from sklearn.isotonic import IsotonicRegression  # imported lazily: only needed at training time

    ir = IsotonicRegression(out_of_bounds="clip")
    ir.fit(raw_probabilities, outcomes)
    return list(map(float, ir.X_thresholds_)), list(map(float, ir.y_thresholds_))


def apply_calibration(raw_probability: float, calibration_x: list, calibration_y: list) -> float:
    """Map one raw probability through an already-fit calibration
    curve. Pure numpy — no sklearn import in the live request path."""
    if not calibration_x or not calibration_y:
        return raw_probability
    return float(np.interp(raw_probability, calibration_x, calibration_y))
