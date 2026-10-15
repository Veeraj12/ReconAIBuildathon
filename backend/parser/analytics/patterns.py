"""
Phase 2 — Exception Pattern Analysis.

Pipeline (each stage is a separate, independently swappable object —
see PatternAnalysisService in service.py for how they're wired):

    ExceptionFeature[]  (Phase 1 output)
            |
    PatternSignatureGenerator   (signatures.py)
            |
    PatternDiscoveryEngine      (this file — deterministic strategy)
            |
    PatternScorer                (confidence, per-pattern stats)
            |
    PatternRanker                (frequency vs financial-impact ranking)
            |
    PatternAnalysisResult        (service.py)

DISCOVERY STRATEGY
───────────────────
`DeterministicPatternDiscoveryEngine` groups ExceptionFeatures by their
bucketed signature string (a GROUP BY, not a distance-based cluster).
This is deliberately the *first* strategy evaluated, not a stopgap:
see signatures.py's module docstring and ARCHITECTURE.md for why
financially meaningful patterns in this domain are signature-shaped
(a handful of features repeating near-exactly) rather than density-
shaped, which is what would justify DBSCAN/hierarchical clustering.

`PatternDiscoveryEngine` is an informal protocol (duck-typed: anything
with a `.discover(features) -> list[Pattern]` method) precisely so a
clustering-based engine can be dropped in later — per architectural
principle #12 — without Phase 1 or the API response shape changing.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .features import ExceptionFeature, ExceptionType
from .signatures import PatternSignatureGenerator


# ────────────────────────────────────────────────────────────────
# Pattern-type vocabulary
# ────────────────────────────────────────────────────────────────

class PatternType:
    """Every string `DeterministicPatternDiscoveryEngine._classify()`
    can return, in one place — the single source of truth for Phase
    2's pattern-type vocabulary.

    WHY THIS EXISTS (closing a loose connection between Phase 2 and
    Phase 3): `knowledge/loader.py`'s `KNOWN_PATTERN_TYPES` is a
    hand-maintained copy of this exact set, kept as a literal rather
    than importing this module directly, specifically to avoid
    coupling the knowledge package to Phase 2 internals. A hand-
    maintained copy can silently drift if this file ever changes —
    which matters most if `_classify` (or a future clustering-based
    discovery engine swapped in per this module's own docstring,
    "architectural principle #12") is ever extended with a new
    category. `PatternType.ALL` is what
    `analytics/explanation/tests/test_pattern_type_consistency.py`
    checks `KNOWN_PATTERN_TYPES` against, so that drift is caught
    mechanically instead of relying on someone remembering to update
    a comment.

    A future clustering-based discovery engine is not required to
    reuse these exact 10 categories — but if it introduces a new one,
    add it HERE first; the consistency test will then fail loudly
    until `knowledge/loader.py`'s `KNOWN_PATTERN_TYPES` (and ideally a
    corresponding catalogue.py entry) is updated to match, rather than
    the new category silently falling through Phase 3 as
    indistinguishable from "no strong cause found."
    """

    RECURRING_AMOUNT_DIFFERENCE = "RECURRING_AMOUNT_DIFFERENCE"
    RECURRING_PERCENTAGE_DIFFERENCE = "RECURRING_PERCENTAGE_DIFFERENCE"
    DATE_OFFSET = "DATE_OFFSET"
    MISSING_SOURCE = "MISSING_SOURCE"
    DUPLICATE = "DUPLICATE"
    SIGN_REVERSAL = "SIGN_REVERSAL"
    REFERENCE_MATCH_ONLY = "REFERENCE_MATCH_ONLY"
    GROUPED_MATCH = "GROUPED_MATCH"
    MIXED_ATTRIBUTE = "MIXED_ATTRIBUTE"
    UNCLASSIFIED_RECURRING = "UNCLASSIFIED_RECURRING"

    ALL = (
        RECURRING_AMOUNT_DIFFERENCE, RECURRING_PERCENTAGE_DIFFERENCE, DATE_OFFSET,
        MISSING_SOURCE, DUPLICATE, SIGN_REVERSAL, REFERENCE_MATCH_ONLY,
        GROUPED_MATCH, MIXED_ATTRIBUTE, UNCLASSIFIED_RECURRING,
    )


# ────────────────────────────────────────────────────────────────
# Output model
# ────────────────────────────────────────────────────────────────

@dataclass
class Pattern:
    pattern_id: str
    pattern_type: str
    exception_type: str

    record_count: int
    total_financial_impact: float

    common_features: dict = field(default_factory=dict)
    exception_ids: list = field(default_factory=list)

    confidence: float = 0.0
    frequency_score: float = 0.0
    impact_score: float = 0.0
    combined_score: float = 0.0

    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "exception_type": self.exception_type,
            "record_count": self.record_count,
            "total_financial_impact": self.total_financial_impact,
            "common_features": self.common_features,
            "exception_ids": self.exception_ids,
            "confidence": self.confidence,
            "frequency_score": self.frequency_score,
            "impact_score": self.impact_score,
            "combined_score": self.combined_score,
            "summary": self.summary,
        }


# ────────────────────────────────────────────────────────────────
# Discovery (deterministic signature grouping)
# ────────────────────────────────────────────────────────────────

class DeterministicPatternDiscoveryEngine:
    """
    Groups ExceptionFeatures by bucketed signature, then builds a
    Pattern per group meeting `min_pattern_size`. Groups below that
    threshold are NOT force-fit into a pattern — their members are
    returned separately as outliers (see `discover`'s return shape).
    """

    def __init__(
        self,
        signature_generator: Optional[PatternSignatureGenerator] = None,
        min_pattern_size: int = 3,
        min_pattern_fraction: float = 0.0,
    ):
        self.signature_generator = signature_generator or PatternSignatureGenerator()
        self.min_pattern_size = min_pattern_size
        self.min_pattern_fraction = min_pattern_fraction

    # Every numeric-difference hypothesis this engine currently tests.
    # Adding a third hypothesis later (e.g. a reference-formatting
    # similarity signature) means adding it to this tuple — the greedy
    # selection below handles any number of candidate modes.
    SIGNATURE_MODES = ("amount", "percentage")

    def discover(self, exception_features: list[ExceptionFeature]) -> tuple[list[Pattern], list[str]]:
        """
        Returns (patterns, outlier_exception_ids).

        Layered / greedy-largest-group-first strategy: a fixed
        absolute difference (e.g. always exactly ₹251, a flat bank
        charge) and a proportional difference (e.g. always ~2%, a
        percentage-based fee) are two different, mutually exclusive
        hypotheses about *why* a set of exceptions recurs (see
        PatternSignatureGenerator.signature_for). Both hypotheses are
        evaluated over the FULL input independently, producing a pool
        of candidate groups. Candidates are then claimed largest-first
        (a classic greedy set-cover): the biggest, most explanatory
        grouping wins its members before smaller, weaker groupings
        under a different hypothesis get a chance to fragment the same
        population. This specifically avoids the failure mode where a
        genuinely proportional pattern (one big percentage-mode group)
        would otherwise get sliced into dozens of tiny amount-mode
        slivers, one per differing base amount.

        Whatever no candidate group can claim above `min_pattern_size`
        is a true outlier — not forced into either hypothesis.
        """
        if not exception_features:
            return [], []

        total = len(exception_features)
        threshold = max(self.min_pattern_size, math.ceil(self.min_pattern_fraction * total))

        candidates: list[tuple[str, list[ExceptionFeature]]] = []
        for mode in self.SIGNATURE_MODES:
            groups: dict[str, list[ExceptionFeature]] = defaultdict(list)
            for ef in exception_features:
                sig = self.signature_generator.signature_for(ef, mode=mode)
                groups[sig].append(ef)
            candidates.extend(groups.items())

        # Largest candidate group first, regardless of which
        # hypothesis produced it.
        candidates.sort(key=lambda kv: len(kv[1]), reverse=True)

        claimed: set[str] = set()
        patterns: list[Pattern] = []

        for signature, members in candidates:
            unclaimed = [m for m in members if m.exception_id not in claimed]
            if len(unclaimed) < threshold:
                continue
            patterns.append(self._build_pattern(len(patterns), signature, unclaimed))
            claimed.update(m.exception_id for m in unclaimed)

        outliers = [ef.exception_id for ef in exception_features if ef.exception_id not in claimed]
        return patterns, outliers

    # ────────────────────────────────────────────────────────────

    def _build_pattern(self, index: int, signature: str, members: list[ExceptionFeature]) -> Pattern:
        exception_type = members[0].exception_type

        abs_diffs = [
            m.features.get("absolute_amount_difference")
            for m in members
            if m.features.get("absolute_amount_difference") is not None
        ]
        pct_diffs = [
            m.features.get("percentage_difference")
            for m in members
            if m.features.get("percentage_difference") is not None
        ]
        date_diffs = [
            m.features.get("date_difference_days")
            for m in members
            if m.features.get("date_difference_days") is not None
        ]

        n = len(members)
        reference_match_rate = _rate(members, "reference_match")
        amount_match_rate = _rate(members, "amount_match")
        date_match_rate = _rate(members, "date_match")

        total_financial_impact = round(sum(abs_diffs), 2) if abs_diffs else 0.0

        amount_spread = _relative_spread(abs_diffs)
        percentage_spread = _relative_spread(pct_diffs)

        common_features = {
            "reference_match_rate": reference_match_rate,
            "amount_match_rate": amount_match_rate,
            "date_match_rate": date_match_rate,
            "average_absolute_amount_difference": _mean(abs_diffs),
            "median_absolute_amount_difference": _median(abs_diffs),
            "average_percentage_difference": _mean(pct_diffs),
            "common_date_offset_days": _mode(date_diffs),
        }

        pattern_type = self._classify(
            exception_type, common_features, amount_spread, percentage_spread,
        )

        pattern = Pattern(
            pattern_id=f"PATTERN_{index + 1:03d}",
            pattern_type=pattern_type,
            exception_type=exception_type,
            record_count=n,
            total_financial_impact=total_financial_impact,
            common_features=common_features,
            exception_ids=[m.exception_id for m in members],
        )
        pattern.summary = self._summarize(pattern)
        return pattern

    @staticmethod
    def _classify(
        exception_type: str, cf: dict,
        amount_spread: Optional[float] = None, percentage_spread: Optional[float] = None,
    ) -> str:
        if exception_type in (ExceptionType.MISSING_IN_TALLY, ExceptionType.MISSING_IN_BANK):
            return PatternType.MISSING_SOURCE
        if exception_type in (ExceptionType.DUPLICATE_BANK, ExceptionType.DUPLICATE_TALLY):
            return PatternType.DUPLICATE
        if exception_type == ExceptionType.SIGN_REVERSAL_HINT:
            return PatternType.SIGN_REVERSAL
        if exception_type == ExceptionType.GROUPED_MATCH:
            return PatternType.GROUPED_MATCH

        has_fixed_amount_diff = (cf.get("average_absolute_amount_difference") or 0) > 0
        has_date_offset = (cf.get("common_date_offset_days") or 0) > 0
        ref_high = (cf.get("reference_match_rate") or 0) >= 0.8

        if has_fixed_amount_diff and (cf.get("date_match_rate") or 0) >= 0.8 and ref_high:
            # Distinguish "always off by the same rupee amount" from
            # "always off by roughly the same percentage" using
            # whichever dimension is tighter around its own members —
            # a flat fee has near-zero amount_spread; a proportional
            # fee has near-zero percentage_spread instead, even though
            # both were grouped via the "amount" or "percentage"
            # signature mode upstream (grouping and classification are
            # deliberately decoupled: classification re-examines the
            # resulting group's actual cohesion rather than trusting
            # which mode produced it).
            if (
                percentage_spread is not None
                and (amount_spread is None or percentage_spread < amount_spread)
            ):
                return PatternType.RECURRING_PERCENTAGE_DIFFERENCE
            return PatternType.RECURRING_AMOUNT_DIFFERENCE
        if has_date_offset and (cf.get("amount_match_rate") or 0) >= 0.8:
            return PatternType.DATE_OFFSET
        if ref_high and not has_fixed_amount_diff and not has_date_offset:
            return PatternType.REFERENCE_MATCH_ONLY
        if has_fixed_amount_diff and has_date_offset:
            return PatternType.MIXED_ATTRIBUTE
        return PatternType.UNCLASSIFIED_RECURRING

    @staticmethod
    def _summarize(p: Pattern) -> str:
        cf = p.common_features
        bits = [f"{p.record_count} {p.exception_type.replace('_', ' ').lower()} transactions"]

        avg_diff = cf.get("average_absolute_amount_difference")
        if avg_diff:
            bits.append(f"share an average difference of {avg_diff}")

        offset = cf.get("common_date_offset_days")
        if offset:
            bits.append(f"a {offset}-day date offset")

        if (cf.get("reference_match_rate") or 0) >= 0.8:
            bits.append("matching references")

        if p.pattern_type == "MISSING_SOURCE":
            bits = [f"{p.record_count} transactions have no reliable counterpart on the other side"]
        elif p.pattern_type == "DUPLICATE":
            bits = [f"{p.record_count} transactions appear to be duplicate entries"]
        elif p.pattern_type == "SIGN_REVERSAL":
            bits = [f"{p.record_count} transactions match on amount/date but have opposite transaction type"]
        elif p.pattern_type == "GROUPED_MATCH":
            bits = [f"{p.record_count} many-to-one grouped matches (split/consolidated transactions)"]
        elif p.pattern_type == "RECURRING_PERCENTAGE_DIFFERENCE":
            avg_pct = cf.get("average_percentage_difference")
            bits = [f"{p.record_count} transactions consistently differ by roughly {avg_pct}%"]
            if (cf.get("reference_match_rate") or 0) >= 0.8:
                bits.append("with matching references")

        return ", ".join(bits) + f". Total financial impact: {p.total_financial_impact}."


# ────────────────────────────────────────────────────────────────
# Scoring — confidence
# ────────────────────────────────────────────────────────────────

class PatternScorer:
    """
    Confidence is a transparent, deterministic function of how
    internally cohesive a pattern's members are — NOT a black-box
    cluster-quality metric. It is the (weighted) average of:

      - reference_match_rate       (how uniformly references agree)
      - amount cohesion            (1 - relative spread of the
                                     absolute amount difference around
                                     its own mean — a tight repeated
                                     ₹251 scores near 1.0, a scattered
                                     set of "amount differs" scores
                                     low)
      - date cohesion              (same idea, over date_difference_days)

    Each term is optional — a pattern with no numeric spread to
    measure (e.g. MISSING_SOURCE, which has no amount/date diff at
    all) is scored purely on the terms that apply to it, not
    penalized for missing ones.
    """

    def score(self, pattern: Pattern, members: list[ExceptionFeature]) -> float:
        terms = []

        if pattern.common_features.get("reference_match_rate") is not None:
            terms.append(pattern.common_features["reference_match_rate"])

        amount_cohesion = self._cohesion(members, "absolute_amount_difference")
        if amount_cohesion is not None:
            terms.append(amount_cohesion)

        date_cohesion = self._cohesion(members, "date_difference_days")
        if date_cohesion is not None:
            terms.append(date_cohesion)

        if not terms:
            # Nothing measurable (e.g. a purely categorical group like
            # MISSING_SOURCE) — cohesion is definitionally perfect
            # since the group-by signature itself was exact.
            return 1.0

        return round(sum(terms) / len(terms), 4)

    @staticmethod
    def _cohesion(members: list[ExceptionFeature], key: str) -> Optional[float]:
        values = [m.features.get(key) for m in members if m.features.get(key) is not None]
        if len(values) < 2:
            return None
        mean = statistics.mean(values)
        if mean == 0:
            # All zero is perfect cohesion (e.g. every date_match=True).
            return 1.0 if statistics.pstdev(values) == 0 else 0.5
        spread = statistics.pstdev(values) / abs(mean)
        return round(max(0.0, 1.0 - min(spread, 1.0)), 4)


# ────────────────────────────────────────────────────────────────
# Ranking — frequency vs financial impact
# ────────────────────────────────────────────────────────────────

class PatternRanker:
    """
    Exposes frequency_score and impact_score independently (so the
    frontend can sort by either), plus a combined_score used as the
    default ordering. Weights are explicit constructor parameters, not
    a hidden formula — see the DELIVERABLE doc for why 0.5/0.5 is the
    starting default rather than something tuned to look good on one
    dataset.
    """

    def __init__(self, frequency_weight: float = 0.5, impact_weight: float = 0.5):
        self.frequency_weight = frequency_weight
        self.impact_weight = impact_weight

    def rank(self, patterns: list[Pattern], total_exceptions: int) -> list[Pattern]:
        if not patterns:
            return patterns

        max_impact = max((p.total_financial_impact for p in patterns), default=0.0) or 1.0

        for p in patterns:
            p.frequency_score = round(p.record_count / total_exceptions, 4) if total_exceptions else 0.0
            p.impact_score = round(p.total_financial_impact / max_impact, 4)
            p.combined_score = round(
                self.frequency_weight * p.frequency_score + self.impact_weight * p.impact_score,
                4,
            )

        return sorted(patterns, key=lambda p: p.combined_score, reverse=True)


# ────────────────────────────────────────────────────────────────
# Small stats helpers (kept dependency-free / no numpy requirement
# for what are single-pass computations over already-small lists)
# ────────────────────────────────────────────────────────────────

def _rate(members: list[ExceptionFeature], key: str) -> Optional[float]:
    values = [m.features.get(key) for m in members if m.features.get(key) is not None]
    if not values:
        return None
    return round(sum(1 for v in values if v) / len(values), 4)


def _mean(values: list[float]) -> Optional[float]:
    return round(statistics.mean(values), 4) if values else None


def _median(values: list[float]) -> Optional[float]:
    return round(statistics.median(values), 4) if values else None


def _relative_spread(values: list[float]) -> Optional[float]:
    """Coefficient-of-variation-style spread: stddev / |mean|. Lower
    means the values are more tightly clustered around their own
    average — used only to decide, after the fact, which numeric
    dimension actually explains a group (see `_classify`)."""
    if len(values) < 2:
        return None
    mean = statistics.mean(values)
    if mean == 0:
        return 0.0 if statistics.pstdev(values) == 0 else None
    return abs(statistics.pstdev(values) / mean)


def _mode(values: list[int]) -> Optional[int]:
    if not values:
        return None
    try:
        return statistics.mode(values)
    except statistics.StatisticsError:
        # Multiple equally-common values — fall back to the median,
        # which is still a reasonable single representative value.
        return int(statistics.median(values))
