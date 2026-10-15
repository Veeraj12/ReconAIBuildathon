"""
LearnedCauseScoringEngine — a three-tier drop-in replacement for
`scoring.CauseScoringEngine`:

    Tier 2 (best data):    trained-and-calibrated logistic regression
                           for this cause_id (trainer.py) — used when
                           enough feedback exists.
    Tier 1 (some data):    k-NN case-based reasoning with Bayesian
                           shrinkage toward the rule-based prior
                           (similar_cases.py) — used when there's
                           feedback, but not enough to train Tier 2.
    Tier 0 (no data):      the original, unmodified rule-based formula
                           (scoring.py) — used for any cause_id with
                           zero feedback at all, which is every
                           cause_id on day one.

Each tier is checked in that order per CANDIDATE (not globally) —
one pattern's explanation can legitimately mix a Tier 2 cause with a
Tier 0 cause if only one of the candidates has accumulated enough
feedback. This is a hybrid, not a replacement, and that's deliberate —
see each tier's own module (trainer.py, similar_cases.py, scoring.py)
for why each individually is the right tool for its data regime.

Because this class extends `CauseScoringEngine` and only overrides
`_score_one`, `PatternExplanationService(scorer=LearnedCauseScoringEngine(...))`
is the entire integration — nothing in service.py,
explanation_generator.py, or grounding.py needs to know or care which
tier produced a given ScoredCause. They all produce the exact same
shape.

CONFIDENCE-LEVEL THRESHOLDS ARE SHARED ACROSS ALL THREE TIERS
──────────────────────────────────────────────────────────────
`confidence_level` uses the SAME thresholds (0.70 / 0.40 / 0.25) in
every tier, imported from scoring.py rather than redefined here — so
"high confidence" means the same thing to a user regardless of which
tier produced it. Revisiting whether those cut points are still right
once real calibrated data exists is a deliberate future decision, not
something that should silently drift because different tiers picked
different numbers.

EVIDENCE TEXT IS BUILT THE SAME WAY IN EVERY TIER
────────────────────────────────────────────────────
`supporting_evidence` / `contradicting_evidence` always comes from the
SAME conditions, using the SAME phrasing table
(`CauseScoringEngine._phrase`) — a tier changes how much a piece of
evidence counts toward the final score, never what English sentence
describes it, and never introduces evidence the catalogue entry didn't
declare (grounding.py's validator would have nothing to check against
otherwise).
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from ..context_builder import PatternContext
from ..grounding import EvidenceItem
from ..retriever import CandidateCause
from ..schema import CONFIDENCE_LEVELS
from ..scoring import CauseScoringEngine, HIGH_THRESHOLD, MEDIUM_THRESHOLD, MIN_RETAIN_THRESHOLD, ScoredCause
from .calibration import apply_calibration
from .feature_vector import build_feature_vector
from .schema import TrainedCauseModel
from .similar_cases import CaseIndex, MIN_CASES_FOR_KNN, rule_based_raw_score, shrinkage_score
from .store import load_trained_models
from .trainer import predict_raw_probability


class LearnedCauseScoringEngine(CauseScoringEngine):
    def __init__(
        self,
        trained_models: Optional[Dict[str, TrainedCauseModel]] = None,
        case_indexes: Optional[Dict[str, CaseIndex]] = None,
        weights_path: Optional[Path] = None,
    ):
        # Loaded once at construction, not per-request — the same
        # pattern default_catalogue() uses for the (also static)
        # knowledge catalogue. Re-instantiate this class (or restart
        # the process) to pick up a freshly retrained
        # trained_weights.json.
        self.trained_models = trained_models if trained_models is not None else load_trained_models(weights_path)
        # Unlike trained_models, there's no on-disk default for
        # case_indexes — they're built fresh from live CauseFeedback
        # rows per request (see learning/db_bridge.py), because
        # feedback keeps arriving between retrains and Tier 1 is
        # meant to reflect the latest evidence, not a stale snapshot.
        # An empty dict here simply disables Tier 1 (every candidate
        # falls through to Tier 2 or Tier 0 only) — safe, not an error.
        self.case_indexes = case_indexes if case_indexes is not None else {}

    def _score_one(self, context: PatternContext, candidate: CandidateCause) -> Optional[ScoredCause]:
        entry = candidate.knowledge
        model = self.trained_models.get(entry.cause_id)
        if model is not None:
            return self._score_with_trained_model(context, candidate, model)

        case_index = self.case_indexes.get(entry.cause_id)
        if case_index is not None and len(case_index) >= MIN_CASES_FOR_KNN:
            return self._score_with_case_index(context, candidate, case_index)

        # No trained model, and not enough feedback for case-based
        # reasoning either — fall back to the exact, unmodified
        # rule-based formula.
        return super()._score_one(context, candidate)

    # ── Tier 2 ──────────────────────────────────────────────────

    def _score_with_trained_model(self, context: PatternContext, candidate: CandidateCause, model: TrainedCauseModel) -> Optional[ScoredCause]:
        entry = candidate.knowledge
        vector = build_feature_vector(context, entry)
        raw_probability = predict_raw_probability(model, vector)
        score = apply_calibration(raw_probability, model.calibration_x, model.calibration_y)
        score = max(0.0, min(1.0, score))
        return self._package(entry, context, score)

    # ── Tier 1 ──────────────────────────────────────────────────

    def _score_with_case_index(self, context: PatternContext, candidate: CandidateCause, case_index: CaseIndex) -> Optional[ScoredCause]:
        entry = candidate.knowledge
        vector = build_feature_vector(context, entry)
        neighbors = case_index.nearest(vector)
        prior = rule_based_raw_score(context, candidate)
        score = shrinkage_score(neighbors, prior)
        score = max(0.0, min(1.0, score))
        return self._package(entry, context, score)

    # ── shared packaging (both learned tiers use this; Tier 0 uses
    #    CauseScoringEngine's own, untouched _score_one instead) ──

    def _package(self, entry, context: PatternContext, score: float) -> Optional[ScoredCause]:
        if score < MIN_RETAIN_THRESHOLD:
            return None

        if score >= HIGH_THRESHOLD:
            level = CONFIDENCE_LEVELS[0]
        elif score >= MEDIUM_THRESHOLD:
            level = CONFIDENCE_LEVELS[1]
        else:
            level = CONFIDENCE_LEVELS[2]

        stats = context.aggregate_stats
        supporting_evidence = [
            EvidenceItem(text=self._phrase(condition.feature, stats), feature=condition.feature)
            for condition in entry.supporting_conditions
            if condition.evaluate(stats) is True
        ]
        contradicting_evidence = [
            EvidenceItem(
                text=f"Only {self._phrase(condition.feature, stats)}, which weakens this hypothesis.",
                feature=condition.feature,
            )
            for condition in entry.contradicting_conditions
            if condition.evaluate(stats) is True
        ]

        return ScoredCause(
            cause_id=entry.cause_id,
            name=entry.name,
            score=round(score, 4),
            confidence_level=level,
            supporting_evidence=supporting_evidence,
            contradicting_evidence=contradicting_evidence,
            explanation_template=entry.explanation_template,
            investigation_template=entry.investigation_template,
        )

