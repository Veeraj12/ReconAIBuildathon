"""
A small, dependency-free Apriori implementation — frequent itemset
mining plus association rule generation.

WHY HAND-WRITTEN INSTEAD OF A LIBRARY (e.g. mlxtend)
────────────────────────────────────────────────────────
1. This project's dependency list (requirements.txt) is deliberately
   short and every entry earns its place (see requirements.txt's own
   documentation) — adding a whole library for an algorithm this
   compact, at this data scale (mining pools of hundreds/low thousands
   of resolved cases, dozens of discretized items, not millions of
   transactions and thousands of items) isn't a good trade.
2. Apriori itself is exactly the kind of "transparent, no black box"
   algorithm this codebase already favors everywhere else (logistic
   regression over an unexplainable classifier in learning/trainer.py,
   a documented arithmetic formula in scoring.py) — a ~100-line,
   fully-readable implementation is arguably MORE trustworthy here
   than an opaque call into someone else's optimized C extension.

ALGORITHM (standard, textbook Apriori — no shortcuts)
──────────────────────────────────────────────────────────
1. Count every single item's support (fraction of transactions
   containing it); keep those >= min_support as the level-1 frequent
   itemsets.
2. Repeatedly: generate level-(k+1) CANDIDATES by joining pairs of
   level-k frequent itemsets that share k-1 items, then PRUNE any
   candidate that has a (k)-sized subset which wasn't itself frequent
   (the "apriori property": no superset of an infrequent itemset can
   be frequent, so checking every subset first saves counting
   candidates that are already known to fail). Count the survivors'
   real support in one more pass, keep those >= min_support.
3. Stop when a level produces zero frequent itemsets.

This means every explored itemset's every subset was already
confirmed frequent before it was even counted — the same discipline
every Apriori textbook describes, not a simplified/approximate
variant.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Dict, FrozenSet, List


def frequent_itemsets(transactions: List[FrozenSet[str]], min_support: float) -> Dict[FrozenSet[str], float]:
    """Returns {itemset: support} for every itemset (of any size) with
    support >= min_support. `support` is the fraction of transactions
    containing that itemset. Deterministic: iterates in a fixed,
    sorted order at every step, so tie-breaking never depends on
    incidental dict/set ordering."""
    n = len(transactions)
    if n == 0 or min_support <= 0:
        return {}

    all_frequent: Dict[FrozenSet[str], float] = {}

    # Level 1
    counts: Dict[FrozenSet[str], int] = {}
    for transaction in transactions:
        for item in transaction:
            key = frozenset([item])
            counts[key] = counts.get(key, 0) + 1

    level = {itemset: count / n for itemset, count in counts.items() if count / n >= min_support}
    all_frequent.update(level)

    k = 2
    while level:
        candidates = _apriori_gen(list(level.keys()), all_frequent, k)
        if not candidates:
            break

        counts = {candidate: 0 for candidate in candidates}
        for transaction in transactions:
            for candidate in candidates:
                if candidate.issubset(transaction):
                    counts[candidate] += 1

        level = {itemset: count / n for itemset, count in counts.items() if count / n >= min_support}
        all_frequent.update(level)
        k += 1

    return all_frequent


def _apriori_gen(level_k_minus_1: List[FrozenSet[str]], all_frequent: Dict[FrozenSet[str], float], k: int) -> List[FrozenSet[str]]:
    """Join step: combine every pair of (k-1)-itemsets that share
    exactly k-2 items into a k-itemset candidate. Prune step: discard
    any candidate with a (k-1)-subset that wasn't itself frequent."""
    sorted_itemsets = sorted(level_k_minus_1, key=lambda s: sorted(s))
    candidates = set()

    for i, a in enumerate(sorted_itemsets):
        for b in sorted_itemsets[i + 1:]:
            union = a | b
            if len(union) == k:
                candidates.add(union)

    def all_subsets_frequent(candidate: FrozenSet[str]) -> bool:
        return all(frozenset(subset) in all_frequent for subset in combinations(candidate, k - 1))

    return [c for c in candidates if all_subsets_frequent(c)]


@dataclass(frozen=True)
class AssociationRule:
    antecedent: FrozenSet[str]
    consequent: FrozenSet[str]
    support: float  # support of (antecedent ∪ consequent)
    confidence: float  # support(antecedent ∪ consequent) / support(antecedent)
    lift: float  # confidence / support(consequent)


def generate_rules(
    itemsets: Dict[FrozenSet[str], float],
    min_confidence: float = 0.5,
    min_lift: float = 1.0,
    consequent_filter: FrozenSet[str] = None,
) -> List[AssociationRule]:
    """Generates rules antecedent -> consequent from `itemsets` (the
    output of `frequent_itemsets`). Only single-item consequents are
    considered — deliberately, for this codebase's use case (see
    discoverer.py): a rule is only useful here if it reads as
    "these conditions -> this ONE outcome (confirmed)", not an
    arbitrary multi-item consequent.

    `consequent_filter`, if given, restricts output to rules whose
    consequent is EXACTLY that itemset (e.g. {"outcome:confirmed"}) —
    this is how discoverer.py asks only for "what predicts
    confirmation" rather than every possible pairwise association
    among discretized feature items, which would be a much larger and
    far less directly useful result set.

    Sorted by (lift desc, confidence desc, support desc) for a
    deterministic, most-interesting-first order."""
    rules: List[AssociationRule] = []

    for itemset, support in itemsets.items():
        if len(itemset) < 2:
            continue
        for consequent_item in itemset:
            consequent = frozenset([consequent_item])
            if consequent_filter is not None and consequent != consequent_filter:
                continue
            antecedent = itemset - consequent
            if not antecedent:
                continue

            antecedent_support = itemsets.get(antecedent)
            consequent_support = itemsets.get(consequent)
            if not antecedent_support or not consequent_support:
                # Shouldn't happen given the apriori property (every
                # subset of a frequent itemset is itself frequent and
                # therefore already in `itemsets`), but guard anyway
                # rather than dividing by zero.
                continue

            confidence = support / antecedent_support
            lift = confidence / consequent_support

            if confidence >= min_confidence and lift >= min_lift:
                rules.append(AssociationRule(antecedent=antecedent, consequent=consequent,
                                              support=support, confidence=confidence, lift=lift))

    rules.sort(key=lambda r: (-r.lift, -r.confidence, -r.support, sorted(r.antecedent)))
    return rules


def remove_redundant_rules(rules: List[AssociationRule]) -> List[AssociationRule]:
    """Drops any rule whose antecedent is a strict SUPERSET of another
    rule's antecedent (same consequent) that achieves equal or better
    confidence — i.e. keeps only "minimal"/"generalized" rules.

    WHY THIS STEP EXISTS
    ────────────────────────
    Apriori explores every frequent itemset, so if {A} -> C already
    has 100% confidence, {A, B} -> C, {A, B, D} -> C, and every other
    superset that happens to still meet the support threshold ALSO
    trivially have >= that confidence (adding a redundant extra
    condition to an already-perfect rule can't make it less
    predictive) — Apriori surfaces all of them as separate "rules."
    Without this filter, a single real pattern in the data can produce
    dozens of near-duplicate rules differing only by which
    incidental extra conditions got tacked on, which defeats the
    entire point of producing a small, human-reviewable list of
    candidates (see discoverer.py / discovery/README.md). This is a
    standard technique in association-rule-mining literature usually
    called "non-redundant" or "generalized" rule filtering — not a
    novel invention for this codebase, just correctly applying an
    established practice.

    Verified directly (see discovery/tests/test_itemset_mining.py):
    given a scenario engineered to produce many redundant supersets of
    one true minimal rule, this reduces the result to exactly that one
    rule.
    """
    kept: List[AssociationRule] = []
    for rule in rules:
        is_redundant = any(
            other is not rule
            and other.consequent == rule.consequent
            and other.antecedent < rule.antecedent  # strict subset
            and other.confidence >= rule.confidence
            for other in rules
        )
        if not is_redundant:
            kept.append(rule)
    return kept


def deduplicate_equivalent_rules(rules: List[AssociationRule]) -> List[AssociationRule]:
    """After `remove_redundant_rules`, some surviving rules can still
    be STATISTICALLY IDENTICAL twins of each other — most commonly
    when a feature has several candidate thresholds (e.g.
    `reference_match_rate >= 0.8` vs. `>= 0.9` vs. `>= 0.95`) and, in
    the data at hand, every case satisfying the strict threshold also
    happens to satisfy the loose one and vice versa, so all three
    achieve the exact same support/confidence/lift. `remove_redundant_rules`
    can't catch this — none of these three antecedents is a SUBSET of
    another (they're different items, not a strict superset), so
    they're not "redundant" in the subset sense, just informationally
    equivalent given this specific dataset.

    This keeps exactly one representative per distinct
    (support, confidence, lift) outcome, using the input order (which
    `generate_rules` already sorts deterministically) to pick which
    one survives — so this function's output is deterministic given a
    deterministically-ordered input, never arbitrary.

    This is a real, documented limitation, not a fully general
    solution: it only catches EXACT statistical ties, not "this
    threshold is looser and therefore preferable even though its
    stats differ slightly" — see discovery/README.md §6 for the
    honest scope note on this.
    """
    seen = set()
    kept = []
    for rule in rules:
        key = (round(rule.support, 6), round(rule.confidence, 6), round(rule.lift, 6), rule.consequent)
        if key in seen:
            continue
        seen.add(key)
        kept.append(rule)
    return kept
