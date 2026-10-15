"""
Data shapes for the learning layer — a FeedbackRecord (one accountant's
confirm/reject decision on one suggested cause) and a TrainedCauseModel
(the learned weights + calibration curve for one cause_id, after
training). Both are plain dataclasses with to_dict/from_dict so they
round-trip cleanly through JSON (feedback via the Django model/DB,
trained models via a plain JSON file — see store.py) without ever
needing to pickle a scikit-learn object. Nothing in this package
persists a live sklearn estimator; only plain numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FeedbackRecord:
    """One data point: an accountant said 'yes, that cause was right'
    or 'no, it wasn't' for one specific candidate cause shown for one
    specific pattern. This is the training data for learning/trainer.py.

    `features` is the output of feature_vector.build_feature_vector at
    the moment feedback was given — captured then, not recomputed
    later, so a subsequent edit to catalogue.py's conditions can't
    silently rewrite historical training data.
    """

    cause_id: str
    pattern_type: str
    exception_type: str
    features: dict
    confirmed: bool
    recorded_at: str  # ISO-8601 timestamp string
    source: str = "accountant"  # who/what supplied this feedback
    reconciliation_reference: Optional[str] = None  # optional free-text pointer back to the run, for audit only

    def to_dict(self) -> dict:
        return {
            "causeId": self.cause_id,
            "patternType": self.pattern_type,
            "exceptionType": self.exception_type,
            "features": self.features,
            "confirmed": self.confirmed,
            "recordedAt": self.recorded_at,
            "source": self.source,
            "reconciliationReference": self.reconciliation_reference,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FeedbackRecord":
        return cls(
            cause_id=d["causeId"],
            pattern_type=d["patternType"],
            exception_type=d["exceptionType"],
            features=dict(d.get("features") or {}),
            confirmed=bool(d["confirmed"]),
            recorded_at=d["recordedAt"],
            source=d.get("source", "accountant"),
            reconciliation_reference=d.get("reconciliationReference"),
        )


@dataclass
class TrainedCauseModel:
    """The learned scorer for one cause_id: a logistic-regression-style
    linear model (feature_names/coefficients/intercept) plus an
    isotonic calibration curve (calibration_x/calibration_y) that maps
    the model's raw sigmoid probability to an empirically-calibrated
    probability. Every number here is plain and human-readable — you
    can open trained_weights.json and read exactly what the model
    learned, the same way you can open genericBank.json and read
    exactly what header aliases are configured.
    """

    cause_id: str
    feature_names: list
    coefficients: list  # same length/order as feature_names
    intercept: float
    calibration_x: list  # sorted raw-probability breakpoints
    calibration_y: list  # calibrated-probability at each breakpoint
    trained_at: str
    sample_count: int
    positive_count: int

    def to_dict(self) -> dict:
        return {
            "causeId": self.cause_id,
            "featureNames": self.feature_names,
            "coefficients": self.coefficients,
            "intercept": self.intercept,
            "calibrationX": self.calibration_x,
            "calibrationY": self.calibration_y,
            "trainedAt": self.trained_at,
            "sampleCount": self.sample_count,
            "positiveCount": self.positive_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TrainedCauseModel":
        return cls(
            cause_id=d["causeId"],
            feature_names=list(d["featureNames"]),
            coefficients=list(d["coefficients"]),
            intercept=float(d["intercept"]),
            calibration_x=list(d["calibrationX"]),
            calibration_y=list(d["calibrationY"]),
            trained_at=d["trainedAt"],
            sample_count=int(d["sampleCount"]),
            positive_count=int(d["positiveCount"]),
        )
