"""
Feature vector construction for the learned cause scorer.

DESIGN PRINCIPLE — reuse, don't reinvent
──────────────────────────────────────────
This file does NOT introduce any new signal that scoring.py doesn't
already compute. It extracts the exact same boolean evaluations —
`FeatureCondition.evaluate(context.aggregate_stats)` for every
supporting/contradicting condition a CauseKnowledge entry declares,
plus `context.pattern_confidence` — and exposes them as a flat numeric
vector instead of folding them straight into the fixed arithmetic
formula.

This matters for two reasons:
  1. It means "the learned model" and "the rule-based model" are
     scored from IDENTICAL evidence — a learned weight of 0.42 for
     `support::reference_match_rate>=0.9` is directly comparable to
     the rule-based formula's flat 0.20, because they're reading the
     same True/False fact.
  2. `None` (a feature that's simply absent) is encoded as 0.0 — the
     same "missing evidence is never positive evidence" rule
     scoring.py's own docstring insists on. There is no code path here
     where "we don't know" becomes a positive signal for training,
     either.

FEATURE NAMESPACE
───────────────────
Each CauseKnowledge entry has its own fixed set of conditions, so each
cause_id naturally has its own feature vocabulary — there's no shared
global feature space across different causes. Feature names are built
directly from the condition definition so they're stable across runs
and human-readable in the exported weights file:

    "support::reference_match_rate::>=::0.9"
    "contradict::reference_match_rate::<::0.5"
    "pattern_confidence"                        (always present)

Because a feature name is derived from (feature, operator, value), if
you edit a condition's threshold in catalogue.py (e.g. change 0.90 to
0.85), it becomes a *different* feature name. That's intentional: a
previously-trained weight for the old threshold should not silently
carry over to a redefined condition — see learning/trainer.py's
docstring for how this is handled (old feature names are simply
absent from new data and drop out of the model on the next retrain).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import-time only, avoids a hard dependency here
    from ..context_builder import PatternContext
    from ..knowledge.schema import CauseKnowledge

PATTERN_CONFIDENCE_KEY = "pattern_confidence"


def _condition_feature_name(prefix: str, feature: str, operator: str, value: float) -> str:
    return f"{prefix}::{feature}::{operator}::{value}"


def build_feature_vector(context: "PatternContext", entry: "CauseKnowledge") -> dict:
    """Build the feature vector for one (pattern context, candidate
    cause) pair — the same pair scoring.py's `_score_one` receives.

    Returns a dict of {feature_name: 0.0 or 1.0} plus the
    always-present `pattern_confidence` (a continuous 0..1 value, not
    boolean). Safe to call at both training time (building a
    FeedbackRecord) and inference time (scoring a live candidate) —
    it's a pure function of (context, entry), nothing else.
    """
    stats = context.aggregate_stats
    vector: dict = {PATTERN_CONFIDENCE_KEY: float(context.pattern_confidence)}

    for condition in entry.supporting_conditions:
        name = _condition_feature_name("support", condition.feature, condition.operator, condition.value)
        vector[name] = 1.0 if condition.evaluate(stats) is True else 0.0

    for condition in entry.contradicting_conditions:
        name = _condition_feature_name("contradict", condition.feature, condition.operator, condition.value)
        vector[name] = 1.0 if condition.evaluate(stats) is True else 0.0

    return vector


def feature_names_for_cause(entry: "CauseKnowledge") -> list:
    """The full, ordered feature vocabulary for one cause — used to
    keep training and inference vectors aligned to the same column
    order regardless of dict insertion order. Sorted for determinism
    (matches the codebase-wide rule: no output should ever depend on
    incidental dict/set ordering)."""
    names = {PATTERN_CONFIDENCE_KEY}
    for condition in entry.supporting_conditions:
        names.add(_condition_feature_name("support", condition.feature, condition.operator, condition.value))
    for condition in entry.contradicting_conditions:
        names.add(_condition_feature_name("contradict", condition.feature, condition.operator, condition.value))
    return sorted(names)


def vector_to_row(vector: dict, feature_names: list) -> list:
    """Turn a {name: value} vector into a plain ordered list matching
    `feature_names`, filling 0.0 for any name the vector doesn't have
    (e.g. old feedback recorded against a since-edited condition set).
    """
    return [float(vector.get(name, 0.0)) for name in feature_names]
