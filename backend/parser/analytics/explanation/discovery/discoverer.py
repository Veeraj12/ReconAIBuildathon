"""
CauseCandidateDiscoverer — orchestrates discovery/'s whole pipeline:

    ResolvedCase[]  (one cause_id's worth, from db_bridge or tests)
            |  discretizer.discretize_pool / discretize_case
    transactions: List[frozenset(ConditionItem labels + outcome item)]
            |  itemset_mining.frequent_itemsets
    frequent itemsets
            |  itemset_mining.generate_rules(consequent_filter=OUTCOME_CONFIRMED)
    AssociationRule[]  (antecedent -> "confirmed")
            |  filter out rules already covered by the existing catalogue
            |  to_candidate_cause()
    CandidateCauseDraft[]

WHY MINING RUNS PER CAUSE_ID, NOT ACROSS ALL FEEDBACK AT ONCE
──────────────────────────────────────────────────────────────────
Same reasoning as trainer.py training one model per cause_id (see
learning/trainer.py's docstring): different cause_ids have different
raw_stats vocabularies in practice (a MISSING_SOURCE case has no
amount-difference stats at all), so pooling everything together would
mean most itemsets involve features that are simply absent for most
transactions — diluting real associations rather than surfacing them.
Mining within one cause_id's own resolved-case history asks a
sharper, more useful question: "for cases already tagged as possibly
being this cause, which additional conditions best separate the ones
that were actually confirmed from the ones that were rejected?"

This deliberately only discovers CONDITIONS FOR CAUSE_IDS THAT ALREADY
HAVE SOME FEEDBACK — it complements the hand-written catalogue by
finding conditions worth ADDING, it does not invent an entirely
unprecedented cause_id out of nothing (see discovery/README.md §5 for
why finding truly novel, never-suggested causes is a different,
harder problem this file doesn't attempt to solve, and what a
reasonable next step for that would look like).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Optional

from ..knowledge.loader import KnowledgeCatalogue, default_catalogue
from .discretizer import discretize_case, discretize_pool
from .itemset_mining import deduplicate_equivalent_rules, frequent_itemsets, generate_rules, remove_redundant_rules
from .schema import CandidateCauseDraft, ConditionItem, OUTCOME_CONFIRMED, OUTCOME_REJECTED, ResolvedCase

DEFAULT_MIN_SUPPORT = 0.10
DEFAULT_MIN_CONFIDENCE = 0.75
DEFAULT_MIN_LIFT = 1.2
DEFAULT_MIN_SAMPLE_COUNT = 10


class CauseCandidateDiscoverer:
    def __init__(
        self,
        min_support: float = DEFAULT_MIN_SUPPORT,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        min_lift: float = DEFAULT_MIN_LIFT,
        min_sample_count: int = DEFAULT_MIN_SAMPLE_COUNT,
        catalogue: Optional[KnowledgeCatalogue] = None,
    ):
        self.min_support = min_support
        self.min_confidence = min_confidence
        self.min_lift = min_lift
        self.min_sample_count = min_sample_count
        # default_catalogue() has zero Django dependency (see
        # knowledge/loader.py / knowledge/catalogue.py) — safe to load
        # here without breaking this package's own Django-free rule,
        # same as learned_scorer.py loading trained_weights.json.
        self.catalogue = catalogue if catalogue is not None else default_catalogue()

    def discover(self, cases: List[ResolvedCase]) -> List[CandidateCauseDraft]:
        """Groups `cases` by cause_id and mines each group
        independently. Returns drafts sorted by (lift desc, confidence
        desc) across all cause_ids — the single most interesting
        finding first, regardless of which cause_id it came from."""
        by_cause: Dict[str, List[ResolvedCase]] = defaultdict(list)
        for case in cases:
            by_cause[case.cause_id].append(case)

        drafts: List[CandidateCauseDraft] = []
        for cause_id, cause_cases in by_cause.items():
            drafts.extend(self._discover_for_cause(cause_id, cause_cases))

        drafts.sort(key=lambda d: (-d.lift, -d.confidence, d.suggested_cause_id))
        return drafts

    def _discover_for_cause(self, cause_id: str, cases: List[ResolvedCase]) -> List[CandidateCauseDraft]:
        if len(cases) < self.min_sample_count:
            return []

        pattern_type = cases[0].pattern_type  # a cause_id's feedback should all share one pattern_type by construction
        quantile_thresholds = discretize_pool(cases)

        transactions = []
        for case in cases:
            items = set(discretize_case(case, quantile_thresholds))
            items.add(OUTCOME_CONFIRMED if case.confirmed else OUTCOME_REJECTED)
            transactions.append(frozenset(items))

        itemsets = frequent_itemsets(transactions, min_support=self.min_support)
        rules = generate_rules(
            itemsets,
            min_confidence=self.min_confidence,
            min_lift=self.min_lift,
            consequent_filter=frozenset([OUTCOME_CONFIRMED]),
        )
        rules = remove_redundant_rules(rules)
        rules = deduplicate_equivalent_rules(rules)

        existing_entry = self.catalogue.get(cause_id)

        drafts = []
        for rule in rules:
            sample_count = round(rule.support * len(cases))
            if sample_count < self.min_sample_count:
                continue

            conditions = [
                {"feature": item.feature, "operator": item.operator, "value": item.value}
                for item in (ConditionItem.from_label(label) for label in sorted(rule.antecedent))
            ]

            if existing_entry is not None and self._already_declared(existing_entry, conditions):
                # This rule only re-states conditions catalogue.py
                # already has for this cause — not a new finding, skip
                # it rather than making a human re-review something
                # they already wrote.
                continue

            drafts.append(self._to_candidate_draft(
                cause_id, pattern_type, rule, sample_count, conditions,
                is_new_condition_for_existing_cause=existing_entry is not None,
            ))
        return drafts

    @staticmethod
    def _already_declared(entry, conditions: List[dict]) -> bool:
        declared = {(c.feature, c.operator, c.value) for c in entry.supporting_conditions}
        proposed = {(c["feature"], c["operator"], c["value"]) for c in conditions}
        return proposed.issubset(declared)

    @staticmethod
    def _to_candidate_draft(cause_id, pattern_type, rule, sample_count, conditions, is_new_condition_for_existing_cause: bool) -> CandidateCauseDraft:
        if is_new_condition_for_existing_cause:
            name = f"Refined conditions for {cause_id}"
            review_note = (
                f"'{cause_id}' already exists in catalogue.py — these conditions aren't currently "
                f"declared for it. Consider adding them to its supporting_conditions."
            )
        else:
            name = f"Candidate new cause: {cause_id}"
            review_note = (
                f"No catalogue.py entry exists for cause_id '{cause_id}' yet — this cause_id only "
                f"exists because an accountant free-typed/was given it via feedback. Consider "
                f"writing a full CauseKnowledge entry if this looks like a genuine new cause."
            )

        return CandidateCauseDraft(
            suggested_cause_id=cause_id,
            suggested_name=name,
            pattern_type=pattern_type,
            suggested_supporting_conditions=conditions,
            support=rule.support,
            confidence=rule.confidence,
            lift=rule.lift,
            sample_count=sample_count,
            is_new_condition_for_existing_cause=is_new_condition_for_existing_cause,
            notes=(
                f"Mined from {sample_count} historical resolved cases tagged {cause_id!r}. "
                f"{round(rule.confidence * 100)}% of cases matching this condition set were "
                f"confirmed — {round(rule.lift, 2)}x more predictive than this cause_id's overall "
                f"confirm-rate. {review_note}"
            ),
        )
