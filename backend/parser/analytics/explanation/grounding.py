"""
Evidence grounding — the safety net that makes "every claim traceable
to evidence" an enforced property of the pipeline, not just a
convention the generator happens to follow.

Even though nothing downstream of Phase 1/2 is an external, untrusted
model anymore, this stage stays in the pipeline deliberately (per the
brief's explicit architecture): `ExplanationGenerator` and
`CauseScoringEngine` are still code that could have a bug — a typo'd
feature key, an off-by-one in a template — and `EvidenceValidator` is
what catches that class of mistake before it reaches the API response,
the same way a unit test would, but at runtime, on the specific data
being explained right now.

Every piece of evidence Phase 3 produces (a "fact", or a cause's
supporting/contradicting evidence) is generated internally as an
`EvidenceItem(text, feature)` — the text is never separated from the
feature key that justifies it. `EvidenceValidator` checks that key
against what `PatternContext` actually contains and produces a
`GroundedEvidence` with an explicit `grounded` flag; nothing is ever
silently trusted or silently dropped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .context_builder import PatternContext


@dataclass(frozen=True)
class EvidenceItem:
    """An internally-generated statement, paired with the exact
    context field it was derived from. `feature` must be a key from
    `PatternContext.aggregate_stats` (which already includes
    record_count/total_financial_impact — see context_builder.py) or
    an exception_id from `representative_samples`."""

    text: str
    feature: str


@dataclass(frozen=True)
class GroundedEvidence:
    text: str
    feature: str
    grounded: bool


@dataclass
class EvidenceValidationResult:
    grounded_facts: list = field(default_factory=list)  # list[GroundedEvidence]
    # cause_id -> {"supporting": [GroundedEvidence], "contradicting": [GroundedEvidence]}
    grounded_causes: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)


class EvidenceValidator:
    def validate(
        self,
        context: PatternContext,
        facts: list,  # list[EvidenceItem]
        scored_causes: list,  # list[scoring.ScoredCause]
    ) -> EvidenceValidationResult:
        known_features = context.known_feature_names() | context.known_exception_ids()
        warnings: list[str] = []

        def check(item: EvidenceItem) -> GroundedEvidence:
            grounded = item.feature in known_features
            if not grounded:
                warnings.append(
                    f"Evidence {item.text!r} cites {item.feature!r}, which was not found in the "
                    f"pattern context supplied for {context.pattern_id} — this evidence is unverified."
                )
            return GroundedEvidence(text=item.text, feature=item.feature, grounded=grounded)

        grounded_facts = [check(f) for f in facts]

        grounded_causes: dict = {}
        for scored in scored_causes:
            grounded_causes[scored.cause_id] = {
                "supporting": [check(e) for e in scored.supporting_evidence],
                "contradicting": [check(e) for e in scored.contradicting_evidence],
            }

        return EvidenceValidationResult(
            grounded_facts=grounded_facts, grounded_causes=grounded_causes, warnings=warnings
        )
