"""
Phase 3 — AI context construction.

Single responsibility: turn a Phase 2 `Pattern` plus its underlying
Phase 1 `ExceptionFeature` members into a small, structured
`PatternContext` — the ONLY thing the local intelligence layer
(retriever.py / scoring.py / explanation_generator.py) ever sees. This
is the component that enforces the core design principle: nothing
downstream ever receives a raw dump of the dataset, only aggregate
statistics, pattern metadata, and a small number of
deterministically-selected representative transactions.

Nothing in this file does knowledge retrieval, scoring, or explanation
generation — that's retriever.py / scoring.py /
explanation_generator.py. This file is pure, decodable, testable
evidence assembly, unchanged since before Phase 3 dropped its external
LLM dependency (see ARCHITECTURE_PHASE3_LOCAL.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from ..features import ExceptionFeature


# Hard ceiling on how many individual transactions are ever included
# in a context, regardless of pattern size — this is what keeps token
# usage bounded even for a 10,000-record pattern.
DEFAULT_MAX_SAMPLES = 6


@dataclass
class TransactionSample:
    """A compact, downstream-facing view of one ExceptionFeature — only the
    fields relevant to explaining a pattern, never the full record."""
    exception_id: str
    role: str  # "typical" | "highest_impact" | "boundary"
    bank_amount: Optional[float]
    tally_amount: Optional[float]
    amount_difference: Optional[float]
    percentage_difference: Optional[float]
    date_difference_days: Optional[int]
    reference_match: Optional[bool]
    bank_reference: Optional[str]
    tally_reference: Optional[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PatternContext:
    pattern_id: str
    pattern_type: str
    exception_type: str
    record_count: int
    total_financial_impact: float
    pattern_confidence: float  # pass-through from Phase 2 — Phase 3 never overwrites this

    aggregate_stats: dict = field(default_factory=dict)
    representative_samples: list = field(default_factory=list)  # list[TransactionSample]
    outlier_note: Optional[str] = None
    caveats: list = field(default_factory=list)  # e.g. "ledger data unavailable"

    def to_dict(self) -> dict:
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type,
            "exception_type": self.exception_type,
            "record_count": self.record_count,
            "total_financial_impact": self.total_financial_impact,
            "pattern_confidence": self.pattern_confidence,
            "aggregate_stats": self.aggregate_stats,
            "representative_samples": [s.to_dict() for s in self.representative_samples],
            "outlier_note": self.outlier_note,
            "caveats": self.caveats,
        }

    def known_feature_names(self) -> set:
        """Every feature name Phase 3 is allowed to cite as evidence —
        used by the grounding validator (grounding.py) to reject any
        claim referencing a feature that was never actually supplied."""
        return set(self.aggregate_stats.keys())

    def known_exception_ids(self) -> set:
        return {s.exception_id for s in self.representative_samples}


class PatternContextBuilder:
    """
    build(pattern, members) -> PatternContext

    `members` must be exactly the ExceptionFeature objects whose
    exception_id appears in `pattern.exception_ids` (the caller —
    PatternExplanationService — is responsible for that lookup, same
    pattern PatternAnalysisService already uses internally).
    """

    def __init__(self, max_samples: int = DEFAULT_MAX_SAMPLES):
        self.max_samples = max_samples

    def build(self, pattern, members: list[ExceptionFeature]) -> PatternContext:
        aggregate_stats = self._aggregate_stats(pattern)
        samples = self._select_representative_samples(members)
        caveats = self._caveats(pattern, members)

        return PatternContext(
            pattern_id=pattern.pattern_id,
            pattern_type=pattern.pattern_type,
            exception_type=pattern.exception_type,
            record_count=pattern.record_count,
            total_financial_impact=pattern.total_financial_impact,
            pattern_confidence=pattern.confidence,
            aggregate_stats=aggregate_stats,
            representative_samples=samples,
            outlier_note=self._outlier_note(pattern),
            caveats=caveats,
        )

    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _aggregate_stats(pattern) -> dict:
        """Only the Phase 2 common_features that are actually
        meaningful for this pattern type are surfaced — e.g. a
        MISSING_SOURCE pattern has no amount-difference stats, so
        those keys are simply absent rather than sent as null noise."""
        stats = {
            "record_count": pattern.record_count,
            "total_financial_impact": pattern.total_financial_impact,
        }
        for key, value in (pattern.common_features or {}).items():
            if value is not None:
                stats[key] = value
        return stats

    def _select_representative_samples(self, members: list[ExceptionFeature]) -> list[TransactionSample]:
        """
        Deterministic selection (same input -> same samples, every
        time — required for caching and for reproducible tests):

          1. "Typical" — members whose absolute_amount_difference is
             closest to the pattern's own median (or, for patterns
             with no amount signal, simply the first N by exception_id
             order, which is itself deterministic).
          2. "Highest impact" — the single member with the largest
             absolute_amount_difference, if any — the spec explicitly
             calls out that high-impact examples matter even in a
             frequency-dominated pattern.
          3. "Boundary" — the member furthest from the pattern's own
             centroid but still inside it, to show the AI (and the
             user) the tolerance edge rather than only the cleanest
             cases.
        """
        if not members:
            return []

        diffs = [
            (m, m.features.get("absolute_amount_difference"))
            for m in members
            if m.features.get("absolute_amount_difference") is not None
        ]

        chosen: list[tuple[ExceptionFeature, str]] = []
        chosen_ids: set = set()

        if diffs:
            diffs_sorted_by_value = sorted(diffs, key=lambda t: t[1])
            median_value = diffs_sorted_by_value[len(diffs_sorted_by_value) // 2][1]

            by_closeness_to_median = sorted(diffs, key=lambda t: abs(t[1] - median_value))
            typical_budget = max(1, self.max_samples - 2)
            for m, _ in by_closeness_to_median[:typical_budget]:
                if m.exception_id not in chosen_ids:
                    chosen.append((m, "typical"))
                    chosen_ids.add(m.exception_id)

            highest_impact = max(diffs, key=lambda t: t[1])[0]
            if highest_impact.exception_id not in chosen_ids and len(chosen) < self.max_samples:
                chosen.append((highest_impact, "highest_impact"))
                chosen_ids.add(highest_impact.exception_id)

            boundary = max(diffs, key=lambda t: abs(t[1] - median_value))[0]
            if boundary.exception_id not in chosen_ids and len(chosen) < self.max_samples:
                chosen.append((boundary, "boundary"))
                chosen_ids.add(boundary.exception_id)
        else:
            # No amount signal at all (e.g. MISSING_SOURCE, DUPLICATE)
            # — fall back to a stable, deterministic ordering by
            # exception_id so the same pattern always yields the same
            # sample set.
            for m in sorted(members, key=lambda m: m.exception_id)[: self.max_samples]:
                chosen.append((m, "typical"))
                chosen_ids.add(m.exception_id)

        return [self._to_sample(m, role) for m, role in chosen[: self.max_samples]]

    @staticmethod
    def _to_sample(ef: ExceptionFeature, role: str) -> TransactionSample:
        return TransactionSample(
            exception_id=ef.exception_id,
            role=role,
            bank_amount=ef.bank.amount,
            tally_amount=ef.tally.amount,
            amount_difference=ef.features.get("amount_difference"),
            percentage_difference=ef.features.get("percentage_difference"),
            date_difference_days=ef.features.get("date_difference_days"),
            reference_match=ef.features.get("reference_match"),
            bank_reference=ef.bank.reference,
            tally_reference=ef.tally.reference,
        )

    @staticmethod
    def _outlier_note(pattern) -> Optional[str]:
        if pattern.record_count and pattern.confidence < 0.6:
            return (
                f"This pattern has below-average internal consistency "
                f"(confidence {pattern.confidence}) — members may be more "
                f"loosely related than a typical pattern."
            )
        return None

    @staticmethod
    def _caveats(pattern, members: list[ExceptionFeature]) -> list[str]:
        caveats = []
        if all(m.bank.ledger is None and m.tally.ledger is None for m in members):
            caveats.append(
                "No ledger/account field is available in the source data for these transactions."
            )
        if pattern.exception_type in ("MISSING_IN_TALLY", "MISSING_IN_BANK"):
            caveats.append(
                "These transactions have no counterpart record at all on the other side — "
                "there is no difference data to analyze, only presence/absence."
            )
        return caveats
