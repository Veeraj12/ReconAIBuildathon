"""
Trains one interpretable, calibrated scorer per cause_id from
accumulated accountant feedback — this is item (a) and (b) from the
design discussion: learn scoring.py's weights from outcomes instead of
hand-picking them, and calibrate the result into a real probability
instead of trusting an arbitrary threshold.

WHY PER-CAUSE, NOT ONE GLOBAL MODEL
──────────────────────────────────────
Each CauseKnowledge entry has its own feature vocabulary (see
feature_vector.py) — "reference_match_rate >= 0.90" for
BANK_SERVICE_CHARGE means something different from whatever features
TAX or DUPLICATE_IMPORT check. Training a separate small model per
cause_id keeps every learned weight interpretable in exactly the same
way scoring.py's hand-written constants were: "how much does this
specific piece of evidence move the needle for THIS specific
hypothesis" — not a black-box combination across unrelated causes.

WHY LOGISTIC REGRESSION SPECIFICALLY
────────────────────────────────────────
It's the natural drop-in replacement for scoring.py's linear formula:
  scoring.py:  score = 0.10 + 0.30*confidence + 0.20*support_count - 0.35*contradiction_count
  learned:     score = sigmoid(intercept + w_1*x_1 + w_2*x_2 + ... )
Same shape (a weighted sum of the same evidence, squashed into 0..1),
except the weights come from `LogisticRegression.fit()` instead of a
guess. Every learned weight is a single readable number in
trained_weights.json — no hidden layers, no black box, so a reviewer
can still ask "why does this cause score high?" and get a real answer
("reference_match_rate>=0.9 carries weight 0.61 for this cause"),
exactly the property the rest of this codebase insists on everywhere
else (see grounding.py, scoring.py's own docstrings).

COLD START AND SPARSE DATA
─────────────────────────────
A cause_id is only trained if it has at least `MIN_SAMPLES_PER_CAUSE`
feedback records AND at least `MIN_PER_CLASS` of each outcome
(confirmed / rejected) — logistic regression on 6 examples, 5 of them
the same label, produces a model that has memorized noise, not
learned anything. Causes below this bar are simply left OUT of the
returned model dict; `learned_scorer.py` falls back to the original
rule-based `CauseScoringEngine._score_one` for any cause with no
trained model. This is a deliberate two-tier design, not a
missing feature — see learned_scorer.py's own docstring.

WHAT HAPPENS WHEN A CATALOGUE CONDITION CHANGES
───────────────────────────────────────────────────
Historical FeedbackRecords carry whatever feature vector existed at
the time they were recorded (see schema.py). If catalogue.py's
conditions change, `feature_vector.feature_names_for_cause` will
return a different vocabulary than some old records have —
`_align_features` below fills any feature the record predates with
0.0 (matching feature_vector.vector_to_row's own behavior), so old
feedback still contributes to training on the new feature set rather
than being discarded, at the cost of that specific historical example
looking like it "didn't have" a since-added condition. This is a
conservative default (missing = 0, same "missing evidence is never
positive" rule used everywhere else); if a catalogue change is large
enough that this approximation feels wrong, the more correct fix is to
stop counting feedback recorded before the change (filter by
`recorded_at` before calling `fit`).
"""

from __future__ import annotations

import datetime as _dt
import math
from collections import defaultdict
from typing import Dict, List

from .calibration import fit_calibration_curve
from .feature_vector import vector_to_row
from .schema import FeedbackRecord, TrainedCauseModel

MIN_SAMPLES_PER_CAUSE = 20
MIN_PER_CLASS = 5
CALIBRATION_FOLDS = 5


class InsufficientDataError(Exception):
    """Raised (and caught internally — never surfaces to callers of
    `fit_all`) when a cause_id doesn't have enough feedback yet."""


class CauseScorerTrainer:
    def fit_all(self, records: List[FeedbackRecord]) -> Dict[str, TrainedCauseModel]:
        """Group feedback by cause_id and train whichever causes have
        enough data. Returns only the causes that were actually
        trained — callers merge this into (or replace) the existing
        trained_weights.json via learning/store.py, so a cause that
        drops below the threshold on a later retrain simply keeps its
        previous model rather than being deleted (see
        management/commands/train_cause_scorer.py)."""
        by_cause: Dict[str, List[FeedbackRecord]] = defaultdict(list)
        for record in records:
            by_cause[record.cause_id].append(record)

        trained: Dict[str, TrainedCauseModel] = {}
        for cause_id, cause_records in by_cause.items():
            try:
                trained[cause_id] = self._fit_one(cause_id, cause_records)
            except InsufficientDataError:
                continue
        return trained

    def _fit_one(self, cause_id: str, records: List[FeedbackRecord]) -> TrainedCauseModel:
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import StratifiedKFold, cross_val_predict

        labels = [1 if r.confirmed else 0 for r in records]
        positive_count = sum(labels)
        negative_count = len(labels) - positive_count

        if len(records) < MIN_SAMPLES_PER_CAUSE or positive_count < MIN_PER_CLASS or negative_count < MIN_PER_CLASS:
            raise InsufficientDataError(
                f"{cause_id}: {len(records)} records ({positive_count} confirmed, "
                f"{negative_count} rejected) — need >= {MIN_SAMPLES_PER_CAUSE} total and "
                f">= {MIN_PER_CLASS} of each outcome."
            )

        feature_names = sorted({name for r in records for name in r.features.keys()})
        X = [vector_to_row(r.features, feature_names) for r in records]
        y = labels

        n_splits = min(CALIBRATION_FOLDS, positive_count, negative_count)
        cv = StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=42)

        # Out-of-fold probabilities for calibration ONLY — never used
        # as the production coefficients themselves (see module
        # docstring's "raw probabilities... never the same rows the
        # model itself was fit on").
        oof_model = LogisticRegression(max_iter=1000)
        oof_probabilities = cross_val_predict(oof_model, X, y, cv=cv, method="predict_proba")[:, 1]
        calibration_x, calibration_y = fit_calibration_curve(list(oof_probabilities), y)

        # Production model: refit on ALL available feedback for this
        # cause, so the shipped coefficients use every example, not
        # just one fold's worth.
        final_model = LogisticRegression(max_iter=1000)
        final_model.fit(X, y)

        return TrainedCauseModel(
            cause_id=cause_id,
            feature_names=feature_names,
            coefficients=[float(c) for c in final_model.coef_[0]],
            intercept=float(final_model.intercept_[0]),
            calibration_x=calibration_x,
            calibration_y=calibration_y,
            trained_at=_dt.datetime.utcnow().isoformat() + "Z",
            sample_count=len(records),
            positive_count=positive_count,
        )


def predict_raw_probability(model: TrainedCauseModel, feature_vector: dict) -> float:
    """Apply a trained model's linear weights + sigmoid to a live
    feature vector — used by learned_scorer.py at inference time. Pure
    Python/math, no sklearn import needed to just apply an already-fit
    model (same "no live ML-library dependency at inference" principle
    as calibration.apply_calibration)."""
    row = vector_to_row(feature_vector, model.feature_names)
    z = model.intercept + sum(w * x for w, x in zip(model.coefficients, row))
    return 1.0 / (1.0 + math.exp(-z))
