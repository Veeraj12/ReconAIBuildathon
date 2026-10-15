"""
Point (c) — case-based reasoning / retrieval-augmented cause scoring.

THE GAP THIS FILLS
─────────────────────
`trainer.py` only trains a cause once it has ≥ MIN_SAMPLES_PER_CAUSE
(20) feedback records with ≥ MIN_PER_CLASS (5) of each outcome. Below
that bar, `learned_scorer.py` falls all the way back to the original
rule-based formula — reasonable, but it means the FIRST 19 pieces of
feedback for any given cause contribute nothing to what the user sees.
That's a real gap: 3 confirmations and 0 rejections is not enough to
train a stable logistic regression, but it is not nothing either — a
human reviewing that evidence would let it nudge their judgment.

THE APPROACH: k-NEAREST-NEIGHBORS + BAYESIAN SHRINKAGE
─────────────────────────────────────────────────────────
This is deliberately a different (and much older, better-understood)
ML technique than trainer.py's logistic regression — k-Nearest
Neighbors is "instance-based learning": instead of fitting parameters
that summarize the whole dataset, you find the K most similar past
examples and let them vote directly. It needs no minimum sample count
to produce a reasonable estimate the way a parametric model does,
which is exactly the property that makes it fit this specific gap.

Two steps:

1. **Find neighbors.** `CaseIndex.nearest(query_vector, k)` measures
   Euclidean distance between the live pattern's feature vector (the
   exact same vector `feature_vector.build_feature_vector` produces —
   this file introduces no new features, just a new way of using the
   existing ones) and every past feedback record's feature vector for
   that cause_id, and returns the K closest.

2. **Blend, don't just average.** A raw "3 of 3 neighbors confirmed
   this cause" would imply 100% confidence off three examples, which
   overstates what three examples can actually tell you. `shrinkage_score`
   uses additive (Bayesian/Laplace-style) smoothing to blend the
   empirical neighbor outcome with the rule-based formula's own score
   as a PRIOR, weighted by `PRIOR_STRENGTH` pseudo-observations:

       score = (confirmed_neighbors + PRIOR_STRENGTH * prior)
               / (neighbors_used + PRIOR_STRENGTH)

   With 0 neighbors this collapses EXACTLY to the rule-based prior
   (Tier 0 behavior — no discontinuity at the boundary between "no
   feedback" and "a little feedback"). As real neighbor evidence
   accumulates, the score shifts smoothly toward the empirical
   confirm-rate among those neighbors, at a rate controlled by how
   much you trust the prior relative to a single real observation
   (`PRIOR_STRENGTH = 3.0` means "the rule-based prior counts for
   about as much as 3 real neighbor votes" — tune this if the blended
   score feels too sticky or too jumpy in practice).

WHERE THIS SITS IN THE THREE-TIER HYBRID
────────────────────────────────────────────
    Tier 0  (0 feedback records for this cause_id)
              -> pure rule-based formula (scoring.py, unchanged)
    Tier 1  (some feedback, but below trainer.py's training minimum)
              -> THIS FILE: k-NN + shrinkage toward the rule-based prior
    Tier 2  (>= MIN_SAMPLES_PER_CAUSE, >= MIN_PER_CLASS of each outcome)
              -> trainer.py's trained-and-calibrated logistic regression

See learned_scorer.py for how the three tiers are actually selected
per candidate cause at scoring time.

WHY THIS FILE STAYS DJANGO-FREE
────────────────────────────────
Like every other file in this package except db_bridge.py, this module
only knows about `FeedbackRecord` (a plain dataclass) — it never
queries `CauseFeedback` (the Django model) directly. Building a
`CaseIndex` from live database rows is db_bridge.py's job specifically
so this file (and its tests) never need Django installed/configured to
run.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

from .feature_vector import vector_to_row
from .schema import FeedbackRecord

MIN_CASES_FOR_KNN = 3
DEFAULT_K = 5
PRIOR_STRENGTH = 3.0


@dataclass
class Neighbor:
    record: FeedbackRecord
    distance: float


class CaseIndex:
    """A small, in-memory nearest-neighbor index over one cause_id's
    accumulated feedback. Cheap to rebuild from scratch on every
    request at this data scale (feedback volume per cause is expected
    to stay in the hundreds/low thousands, not millions) — no need for
    a real vector database here."""

    def __init__(self, records: List[FeedbackRecord]):
        self.records = records
        # Union of every feature name any record in this index used —
        # same "align on the union, fill gaps with 0.0" rule as
        # trainer.py, for the same reason (a catalogue edit shouldn't
        # make older feedback unusable, just less informative).
        self.feature_names = sorted({name for r in records for name in r.features.keys()})

    def __len__(self) -> int:
        return len(self.records)

    def nearest(self, query_vector: dict, k: int = DEFAULT_K) -> List[Neighbor]:
        if not self.records:
            return []
        query_row = vector_to_row(query_vector, self.feature_names)
        neighbors = [
            Neighbor(record=record, distance=_euclidean(query_row, vector_to_row(record.features, self.feature_names)))
            for record in self.records
        ]
        # Ties broken by recorded_at for full determinism regardless of
        # the records' original (e.g. database query) ordering.
        neighbors.sort(key=lambda n: (n.distance, n.record.recorded_at))
        return neighbors[:k]


def _euclidean(a: List[float], b: List[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def build_case_indexes(records: List[FeedbackRecord]) -> Dict[str, CaseIndex]:
    """Group feedback by cause_id into one CaseIndex per cause — the
    plain-Python equivalent of trainer.py's `fit_all` grouping step,
    just building a lookup index instead of fitting a model."""
    by_cause: Dict[str, List[FeedbackRecord]] = defaultdict(list)
    for record in records:
        by_cause[record.cause_id].append(record)
    return {cause_id: CaseIndex(cause_records) for cause_id, cause_records in by_cause.items()}


def shrinkage_score(neighbors: List[Neighbor], prior: float, prior_strength: float = PRIOR_STRENGTH) -> float:
    """Blend the empirical confirm-rate among `neighbors` with `prior`
    (the rule-based formula's own raw score for this candidate) via
    additive/Bayesian smoothing. See module docstring for the formula
    and the reasoning behind it. Bounded in [0, 1] whenever `prior` is,
    since it's a weighted average of `prior` (in [0,1]) and a 0/1
    confirm-rate."""
    if not neighbors:
        return prior
    confirmed = sum(1 for n in neighbors if n.record.confirmed)
    return (confirmed + prior_strength * prior) / (len(neighbors) + prior_strength)


def rule_based_raw_score(context, candidate) -> float:
    """Recomputes scoring.py's raw arithmetic score for one candidate —
    BEFORE the `< MIN_RETAIN_THRESHOLD -> None` retention filter that
    `CauseScoringEngine._score_one` applies. We need the un-filtered
    number here because it's used as the shrinkage PRIOR (see
    `shrinkage_score`): a candidate whose rule-based score is a weak
    0.22 is still a meaningful prior to shrink away from as real
    neighbor evidence accumulates, and simply treating every
    below-threshold candidate as a 0.0 prior would understate how much
    the rule-based evidence actually supports it.

    NOTE — deliberate small duplication: this mirrors
    `CauseScoringEngine._score_one`'s arithmetic exactly (same four
    constants, imported from scoring.py so they can never drift
    independently), but recreates the four-line formula rather than
    calling a private method, to avoid changing `scoring.py` itself —
    that file is exercised by an existing, dedicated test suite
    (`explanation/tests/test_scoring.py`) that this feature has no
    need to put at risk. If `scoring.py`'s formula ever changes
    structurally (not just its constants), update this function to
    match.
    """
    from ..scoring import BASE_RETRIEVED_SCORE, CONTRADICTION_PENALTY, MAX_SUPPORT_CONTRIBUTION, PATTERN_CONFIDENCE_WEIGHT, SUPPORT_CONDITION_POINTS

    entry = candidate.knowledge
    stats = context.aggregate_stats

    support_points = sum(
        SUPPORT_CONDITION_POINTS for condition in entry.supporting_conditions if condition.evaluate(stats) is True
    )
    contradiction_points = sum(
        CONTRADICTION_PENALTY for condition in entry.contradicting_conditions if condition.evaluate(stats) is True
    )

    raw_score = (
        BASE_RETRIEVED_SCORE
        + PATTERN_CONFIDENCE_WEIGHT * context.pattern_confidence
        + min(support_points, MAX_SUPPORT_CONTRIBUTION)
        - contradiction_points
    )
    return max(0.0, min(1.0, raw_score))
