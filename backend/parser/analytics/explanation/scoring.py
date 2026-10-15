"""
Cause scoring — turns a list of retrieved CandidateCauses into
ScoredCauses, using an explicit, documented, deterministic formula.
No AI-generated confidence, no arbitrary numbers.

THE FORMULA
────────────
For each candidate cause:

    score = BASE_RETRIEVED_SCORE
          + PATTERN_CONFIDENCE_WEIGHT * pattern_confidence
          + min(support_points, MAX_SUPPORT_CONTRIBUTION)
          - CONTRADICTION_PENALTY * (number of triggered contradicting conditions)

    support_points = SUPPORT_CONDITION_POINTS * (number of triggered supporting conditions)

    score = clip(score, 0.0, 1.0)

Where:
    BASE_RETRIEVED_SCORE      = 0.10   — a cause that passed retrieval (pattern_type
                                          applies, required features exist) starts with
                                          a small non-zero credit: it is *structurally*
                                          relevant even before any specific evidence is
                                          checked.
    PATTERN_CONFIDENCE_WEIGHT = 0.30   — Phase 2's own confidence in the *pattern itself*
                                          (not the cause) contributes up to 0.30. A cause
                                          explaining a rock-solid pattern deserves more
                                          credit than the identical cause explaining a
                                          shaky one — but this is capped well under half
                                          the scale specifically so pattern confidence
                                          alone can never carry a cause to "high" on its
                                          own; see PATTERN VS CAUSE CONFIDENCE below.
    SUPPORT_CONDITION_POINTS  = 0.20   — each satisfied supporting_condition adds this much.
    MAX_SUPPORT_CONTRIBUTION  = 0.50   — total support contribution is capped so a cause
                                          with many loosely-related supporting conditions
                                          can't out-score one with fewer, stronger ones
                                          purely by having more conditions listed.
    CONTRADICTION_PENALTY     = 0.35   — each satisfied contradicting_condition subtracts
                                          this much — deliberately larger per-item than a
                                          single supporting condition's contribution, so
                                          contradictory evidence meaningfully outweighs
                                          one piece of supporting evidence, per the
                                          brief's explicit "do not hide contradictions"
                                          requirement.

THRESHOLDS
───────────
    score >= HIGH_THRESHOLD (0.70)   -> confidence_level = "high"
    score >= MEDIUM_THRESHOLD (0.40) -> confidence_level = "medium"
    score >= MIN_RETAIN_THRESHOLD (0.25) -> confidence_level = "low"
    score <  MIN_RETAIN_THRESHOLD    -> cause is DROPPED entirely (never appears in
                                          possible_causes) — this is what makes
                                          "insufficient evidence" a real, reachable
                                          outcome rather than a special-cased empty list:
                                          if every candidate scores below 0.25, the
                                          returned list is naturally empty and
                                          explanation_generator.py's insufficient-evidence
                                          path takes over. See tests/test_scoring.py for
                                          the worked numeric examples these thresholds
                                          were chosen against.

MISSING EVIDENCE IS NEVER POSITIVE EVIDENCE
─────────────────────────────────────────────
`FeatureCondition.evaluate()` returns `None` when a feature is simply
absent (see knowledge/schema.py). A `None` result earns zero points —
identical to a condition that evaluates to `False`. There is no code
path here where "we don't know" becomes "this supports the hypothesis".

PATTERN CONFIDENCE VS. CAUSE CONFIDENCE
──────────────────────────────────────────
These are two different numbers measuring two different things, and
this module is careful never to let them collapse into one:

  - `context.pattern_confidence` (from Phase 2) says how internally
    cohesive the *pattern's own membership* is — "how sure are we
    these 147 records actually belong together."
  - `ScoredCause.score` / `.confidence_level` says how well a
    *specific candidate explanation* fits that pattern's evidence —
    "how sure are we THIS is why they belong together."

A pattern can have pattern_confidence=0.97 while every candidate cause
still scores "medium" or lower, and that is a completely valid,
expected outcome (see the brief's own worked example) — it means
"we're confident these are the same phenomenon, but only moderately
confident about which known explanation accounts for it."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .context_builder import PatternContext
from .grounding import EvidenceItem
from .retriever import CandidateCause
from .schema import CONFIDENCE_LEVELS

BASE_RETRIEVED_SCORE = 0.10
PATTERN_CONFIDENCE_WEIGHT = 0.30
SUPPORT_CONDITION_POINTS = 0.20
MAX_SUPPORT_CONTRIBUTION = 0.50
CONTRADICTION_PENALTY = 0.35

HIGH_THRESHOLD = 0.70
MEDIUM_THRESHOLD = 0.40
MIN_RETAIN_THRESHOLD = 0.25


# Human phrasing per feature — a finance user reads "98% of references
# match", never "reference_match_rate >= 0.9". Kept as a small,
# explicit lookup rather than a generic f"{feature} {op} {value}" so
# every generated sentence reads naturally; falls back to a generic
# (still fully grounded) phrasing for any feature not in this table.
_FEATURE_PHRASES = {
    "reference_match_rate": lambda v: f"{round(v * 100)}% of references match",
    "amount_match_rate": lambda v: f"{round(v * 100)}% of amounts match exactly",
    "date_match_rate": lambda v: f"{round(v * 100)}% of dates match exactly",
    "common_date_offset_days": lambda v: f"the common date offset is {v} day(s)",
    "average_absolute_amount_difference": lambda v: f"the average absolute difference is {v}",
    "median_absolute_amount_difference": lambda v: f"the median absolute difference is {v}",
    "average_percentage_difference": lambda v: f"the average percentage difference is {v}%",
}


@dataclass
class ScoredCause:
    cause_id: str
    name: str
    score: float
    confidence_level: str  # one of CONFIDENCE_LEVELS
    supporting_evidence: list = field(default_factory=list)  # list[EvidenceItem]
    contradicting_evidence: list = field(default_factory=list)  # list[EvidenceItem]
    explanation_template: str = ""
    investigation_template: str = ""


class CauseScoringEngine:
    def score_all(self, context: PatternContext, candidates: list[CandidateCause]) -> list[ScoredCause]:
        scored = [self._score_one(context, candidate) for candidate in candidates]
        retained = [s for s in scored if s is not None]
        # Deterministic tie-break: score desc, then cause_id asc (stable
        # regardless of catalogue/dict ordering quirks).
        retained.sort(key=lambda s: (-s.score, s.cause_id))
        return retained

    def _score_one(self, context: PatternContext, candidate: CandidateCause) -> Optional[ScoredCause]:
        entry = candidate.knowledge
        stats = context.aggregate_stats

        supporting_evidence: list[EvidenceItem] = []
        support_points = 0.0
        for condition in entry.supporting_conditions:
            if condition.evaluate(stats) is True:
                support_points += SUPPORT_CONDITION_POINTS
                supporting_evidence.append(
                    EvidenceItem(text=self._phrase(condition.feature, stats), feature=condition.feature)
                )

        contradicting_evidence: list[EvidenceItem] = []
        contradiction_points = 0.0
        for condition in entry.contradicting_conditions:
            if condition.evaluate(stats) is True:
                contradiction_points += CONTRADICTION_PENALTY
                contradicting_evidence.append(
                    EvidenceItem(
                        text=f"Only {self._phrase(condition.feature, stats)}, which weakens this hypothesis.",
                        feature=condition.feature,
                    )
                )

        raw_score = (
            BASE_RETRIEVED_SCORE
            + PATTERN_CONFIDENCE_WEIGHT * context.pattern_confidence
            + min(support_points, MAX_SUPPORT_CONTRIBUTION)
            - contradiction_points
        )
        score = max(0.0, min(1.0, raw_score))

        if score < MIN_RETAIN_THRESHOLD:
            return None

        if score >= HIGH_THRESHOLD:
            level = CONFIDENCE_LEVELS[0]  # "high"
        elif score >= MEDIUM_THRESHOLD:
            level = CONFIDENCE_LEVELS[1]  # "medium"
        else:
            level = CONFIDENCE_LEVELS[2]  # "low"

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

    @staticmethod
    def _phrase(feature: str, stats: dict) -> str:
        value = stats.get(feature)
        builder = _FEATURE_PHRASES.get(feature)
        phrase = builder(value) if builder else f"{feature} is {value}"
        return phrase[0].upper() + phrase[1:] + "."
