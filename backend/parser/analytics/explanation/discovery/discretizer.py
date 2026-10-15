"""
Turns a ResolvedCase's raw, continuous `aggregate_stats` into a set of
binary ConditionItem labels ("reference_match_rate>=0.9",
"common_date_offset_days==0", ...) that association-rule mining can
work with.

WHY NOT GENERIC QUANTILE BINS
─────────────────────────────────
The obvious default for discretizing a continuous feature is
`pandas.qcut` — split into N equal-frequency bins, label them
"bin_0".."bin_3". That's the wrong choice here specifically because
the whole point of discovery (d) is to produce something a human can
paste into `catalogue.py` as a `FeatureCondition(feature, operator,
value)` — and "reference_match_rate is in bin_2" doesn't tell you
what threshold bin_2 actually means, or survive catalogue.py being
read six months later once the training pool that produced the bin
edges is long gone. Every item this file produces is a real, complete
`(feature, operator, value)` triple from the start, so a discovered
rule is immediately catalogue-shaped — no second translation step a
human reviewer could get subtly wrong.

TWO DIFFERENT DISCRETIZATION STRATEGIES, BY FEATURE SHAPE
──────────────────────────────────────────────────────────────
1. **Rate-like features** (any key containing "rate" — e.g.
   `reference_match_rate`, `amount_match_rate`, `date_match_rate`):
   already live on a meaningful, universal 0..1 scale, so this file
   uses FIXED, human-familiar thresholds — 0.5, 0.8, 0.9, 0.95 — the
   same style of round, meaningful cutoff every hand-written entry in
   `catalogue.py` already uses (e.g. `>= 0.90`). A discovered rule
   citing "reference_match_rate >= 0.9" reads exactly like something a
   human would have written by hand.

2. **`common_date_offset_days`**: gets exactly two candidate items —
   `== 0` and `>= 1` — because for this specific feature the
   meaningful distinction in every hand-written catalogue entry is
   "no offset" vs. "some offset", not a magnitude threshold.

3. **Everything else** (amount/percentage differences, financial
   impact, etc.): no universal meaningful cutoff exists (₹251 means
   something different to a ₹500 pattern than a ₹50,000 one), so
   thresholds are DATA-DRIVEN — the 25th/50th/75th percentile of that
   feature across the current mining pool, computed once per
   `discretize_pool` call. This is still fully deterministic (same
   input pool -> same thresholds -> same items, every time) — nothing
   here is random or dependent on execution order.

A missing feature (not present in a given case's raw_stats) simply
contributes no items for it — same "absence is not evidence" principle
used throughout learning/feature_vector.py.
"""

from __future__ import annotations

import statistics
from typing import Dict, FrozenSet, List

from .schema import ConditionItem, ResolvedCase

RATE_THRESHOLDS = (0.5, 0.8, 0.9, 0.95)
DATE_OFFSET_FEATURE = "common_date_offset_days"
QUANTILE_FRACTIONS = (0.25, 0.5, 0.75)


def _is_rate_feature(feature: str) -> bool:
    return "rate" in feature


def compute_quantile_thresholds(values: List[float]) -> List[float]:
    """25th/50th/75th percentile of `values`, deduplicated and sorted
    — deterministic given the same input list (statistics.quantiles
    uses a fixed, documented interpolation method, no randomness)."""
    if len(values) < 4:
        # Too few points for meaningful quantiles — fall back to just
        # the median, which is still well-defined for 1+ values.
        return [statistics.median(values)] if values else []
    quantiles = statistics.quantiles(values, n=4, method="inclusive")  # [Q1, Q2, Q3]
    return sorted(set(round(q, 6) for q in quantiles))


def discretize_pool(cases: List[ResolvedCase]) -> Dict[str, List[float]]:
    """Computes the data-driven quantile thresholds for every
    non-rate, non-date-offset numeric feature present anywhere in
    `cases`. Returns {feature_name: [threshold, ...]}. Called ONCE per
    cause_id's pool (see discoverer.py) so every case in that pool is
    discretized against the exact same thresholds — mixing thresholds
    computed from different pools within one mining run would make
    support/confidence numbers meaningless."""
    values_by_feature: Dict[str, List[float]] = {}
    for case in cases:
        for feature, value in case.raw_stats.items():
            if feature == DATE_OFFSET_FEATURE or _is_rate_feature(feature):
                continue
            if not isinstance(value, (int, float)):
                continue
            values_by_feature.setdefault(feature, []).append(float(value))

    return {feature: compute_quantile_thresholds(values) for feature, values in values_by_feature.items()}


def discretize_case(case: ResolvedCase, quantile_thresholds: Dict[str, List[float]]) -> FrozenSet[str]:
    """Converts one ResolvedCase's raw_stats into the set of
    ConditionItem labels it satisfies, using thresholds already
    computed for the whole pool via `discretize_pool`.

    For every threshold, EXACTLY ONE directional item is emitted —
    either "feature>=threshold" (if satisfied) or "feature<threshold"
    (if not) — so both "a high value predicts X" and "a low value
    predicts X" associations are discoverable. An earlier version of
    this function only ever emitted the ">=" side, which meant mining
    could never surface a real "low value predicts confirmation"
    pattern (e.g. a genuinely small, fee-like financial impact) at
    all — silently missing half of what's discoverable. Verified this
    fix directly: a synthetic scenario built specifically to need the
    "<" direction produced zero candidates before this fix and the
    correct candidate after it."""
    items = set()

    for feature, value in case.raw_stats.items():
        if not isinstance(value, (int, float)):
            continue

        if feature == DATE_OFFSET_FEATURE:
            if value == 0:
                items.add(ConditionItem(feature, "==", 0).label)
            else:
                items.add(ConditionItem(feature, ">=", 1).label)
            continue

        thresholds = RATE_THRESHOLDS if _is_rate_feature(feature) else quantile_thresholds.get(feature, [])
        for threshold in thresholds:
            if value >= threshold:
                items.add(ConditionItem(feature, ">=", threshold).label)
            else:
                items.add(ConditionItem(feature, "<", threshold).label)

    return frozenset(items)
