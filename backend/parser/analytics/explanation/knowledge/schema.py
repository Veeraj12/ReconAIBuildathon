"""
Domain knowledge schema — NOT an LLM, NOT a model. A `CauseKnowledge`
entry is a static, human-curated fact: "if a pattern looks like X,
here is one plausible named explanation, and here is exactly what
pattern evidence would make that explanation more or less believable."

Everything downstream (retriever.py, scoring.py) operates purely on
these declarative structures — no cause-specific logic is hardcoded
into the scoring engine (see scoring.py's module docstring for why
that separation matters: it's what makes the catalogue extendable
without touching the engine).
"""

from __future__ import annotations

import operator as _operator
from dataclasses import dataclass, field
from typing import Optional

_OPERATORS = {
    ">=": _operator.ge,
    "<=": _operator.le,
    ">": _operator.gt,
    "<": _operator.lt,
    "==": _operator.eq,
    "!=": _operator.ne,
}


@dataclass(frozen=True)
class FeatureCondition:
    """One evaluable statement about a PatternContext's aggregate_stats,
    e.g. FeatureCondition("reference_match_rate", ">=", 0.90)."""

    feature: str
    operator: str
    value: float

    def __post_init__(self):
        if self.operator not in _OPERATORS:
            raise ValueError(
                f"Unsupported operator {self.operator!r} for feature {self.feature!r}; "
                f"must be one of {sorted(_OPERATORS)}"
            )

    def evaluate(self, aggregate_stats: dict) -> Optional[bool]:
        """True/False if the feature is present and comparable. `None`
        if the feature is simply absent from this pattern's context —
        this is a load-bearing distinction: an absent feature must
        never be silently treated as a satisfied (or failed)
        condition. See scoring.py for how `None` is handled (it earns
        zero points either way, never counted as supporting evidence)."""
        if self.feature not in aggregate_stats:
            return None
        actual = aggregate_stats[self.feature]
        if actual is None:
            return None
        return _OPERATORS[self.operator](actual, self.value)


@dataclass(frozen=True)
class CauseKnowledge:
    cause_id: str  # stable ID, e.g. "BANK_SERVICE_CHARGE" — see schema.py's
                    # PossibleCause.cause_id docstring for why this matters
    name: str  # human-facing name, e.g. "Recurring bank/service charge"

    applicable_pattern_types: tuple = field(default_factory=tuple)

    # Features that MUST be present (non-null) in aggregate_stats for
    # this cause to even be retrieved as a candidate — see retriever.py.
    # An empty tuple means "no specific feature required beyond the
    # pattern_type match" (true for most MISSING_SOURCE/DUPLICATE/
    # SIGN_REVERSAL causes, which have little numeric signal to check).
    required_features: tuple = field(default_factory=tuple)

    # Conditions that, when satisfied, make this cause MORE credible.
    supporting_conditions: tuple = field(default_factory=tuple)  # tuple[FeatureCondition]

    # Conditions that, when satisfied, make this cause LESS credible —
    # this is what lets the engine represent contradictory evidence
    # (e.g. a fixed-difference pattern with a very low reference-match
    # rate weakens the "bank charge" hypothesis) rather than silently
    # ignoring it.
    contradicting_conditions: tuple = field(default_factory=tuple)  # tuple[FeatureCondition]

    explanation_template: str = ""
    investigation_template: str = ""
