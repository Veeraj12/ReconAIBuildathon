"""
Data shapes for point (d) — association-rule mining over resolved
cases, to discover candidate NEW cause conditions instead of requiring
a human to hand-write every CauseKnowledge entry.

Mirrors learning/schema.py's split (a plain input dataclass + a plain
output dataclass, both JSON-round-trippable, neither ever touching
Django or scikit-learn directly) — see discovery/README.md §1 for why
this needed a NEW input shape (ResolvedCase, raw continuous stats)
rather than reusing learning/schema.py's FeedbackRecord (boolean
condition evaluations only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ResolvedCase:
    """One accountant decision, with the RAW aggregate_stats captured
    at the time — the discovery-side counterpart to
    learning/schema.py's FeedbackRecord. See CauseFeedback.raw_stats
    (parser/models.py) for where this comes from in production."""

    cause_id: str
    pattern_type: str
    exception_type: str
    raw_stats: dict
    confirmed: bool
    recorded_at: str

    def to_dict(self) -> dict:
        return {
            "causeId": self.cause_id,
            "patternType": self.pattern_type,
            "exceptionType": self.exception_type,
            "rawStats": self.raw_stats,
            "confirmed": self.confirmed,
            "recordedAt": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResolvedCase":
        return cls(
            cause_id=d["causeId"],
            pattern_type=d["patternType"],
            exception_type=d["exceptionType"],
            raw_stats=dict(d.get("rawStats") or {}),
            confirmed=bool(d["confirmed"]),
            recorded_at=d["recordedAt"],
        )


@dataclass
class ConditionItem:
    """One discretized, binary-testable fact about a ResolvedCase's
    raw_stats — e.g. "reference_match_rate >= 0.9" or
    "common_date_offset_days == 0". This is deliberately shaped so it
    converts 1:1 into a `knowledge.schema.FeatureCondition` — the
    whole point of discretizing this way (see discretizer.py's
    docstring) rather than generic quantile-bin labels is that a
    discovered rule should be directly usable as a catalogue.py draft,
    not need a second translation step a human could get wrong."""

    feature: str
    operator: str
    value: float

    @property
    def label(self) -> str:
        """The string used as the actual mining "item" — human
        readable, and reversible back into (feature, operator, value)
        via `from_label`."""
        return f"{self.feature}{self.operator}{self.value}"

    @classmethod
    def from_label(cls, label: str) -> "ConditionItem":
        for operator in (">=", "<=", "==", "!=", "<", ">"):
            if operator in label:
                feature, _, value = label.partition(operator)
                return cls(feature=feature, operator=operator, value=float(value))
        raise ValueError(f"Not a recognized ConditionItem label: {label!r}")


OUTCOME_CONFIRMED = "outcome:confirmed"
OUTCOME_REJECTED = "outcome:rejected"


@dataclass
class DiscoveredRule:
    """One mined association rule: `antecedent` (a set of
    ConditionItem labels) predicts `OUTCOME_CONFIRMED` for some
    cause_id, with the given support/confidence/lift. This is the RAW
    mining output — see discoverer.py's `to_candidate_cause()` for how
    one of these becomes something that looks like a `CauseKnowledge`
    draft ready to paste into catalogue.py."""

    cause_id: str
    pattern_type: str
    antecedent: frozenset  # of ConditionItem labels (strings)
    support: float  # fraction of this cause_id's resolved cases matching the antecedent
    confidence: float  # P(confirmed | antecedent)
    lift: float  # confidence / P(confirmed) overall for this cause_id — >1 means genuinely predictive, not just common
    sample_count: int  # how many resolved cases the antecedent matched, in absolute terms

    def to_dict(self) -> dict:
        return {
            "causeId": self.cause_id,
            "patternType": self.pattern_type,
            "antecedent": sorted(self.antecedent),
            "support": round(self.support, 4),
            "confidence": round(self.confidence, 4),
            "lift": round(self.lift, 4),
            "sampleCount": self.sample_count,
        }


@dataclass
class CandidateCauseDraft:
    """A DiscoveredRule reshaped into the exact same fields a
    `knowledge.schema.CauseKnowledge` entry needs — meant to be read by
    a human and, if it looks right, copied (by hand — see
    discovery/README.md §4 on why this is never auto-inserted) into
    catalogue.py, possibly after editing the name/templates.

    Two situations produce a CandidateCauseDraft, distinguished by
    `existing_cause_conditions` being empty or not:
      - The antecedent conditions AREN'T already declared as
        supporting_conditions for this cause_id in the current
        catalogue -> this is a candidate NEW supporting_condition for
        an EXISTING cause (`is_new_condition_for_existing_cause=True`).
      - No catalogue entry exists for this cause_id/pattern_type at
        all (it's a cause_id an accountant free-typed, or one your
        team plans to formalize) -> this reads as a candidate BRAND
        NEW cause (`is_new_condition_for_existing_cause=False`).
    Either way the output shape is identical; a human makes the actual
    catalogue.py edit either way, and the discovery tool doesn't need
    to know which case applies to be useful — it's noted here purely
    as a hint for whoever's reviewing.
    """

    suggested_cause_id: str
    suggested_name: str
    pattern_type: str
    suggested_supporting_conditions: list  # list of {"feature", "operator", "value"} dicts
    support: float
    confidence: float
    lift: float
    sample_count: int
    is_new_condition_for_existing_cause: bool
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "suggestedCauseId": self.suggested_cause_id,
            "suggestedName": self.suggested_name,
            "patternType": self.pattern_type,
            "suggestedSupportingConditions": self.suggested_supporting_conditions,
            "support": round(self.support, 4),
            "confidence": round(self.confidence, 4),
            "lift": round(self.lift, 4),
            "sampleCount": self.sample_count,
            "isNewConditionForExistingCause": self.is_new_condition_for_existing_cause,
            "notes": self.notes,
        }
