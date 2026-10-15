"""
db_bridge.py — the ONE file in analytics/explanation/discovery/ that
is allowed to import Django, mirroring the exact same convention
established in analytics/explanation/learning/db_bridge.py. Every
other module in this package (schema.py, discretizer.py,
itemset_mining.py, discoverer.py) is Django-free and independently
testable.
"""

from __future__ import annotations

from typing import List, Optional

from .schema import ResolvedCase


def load_resolved_cases(only_cause_ids: Optional[list] = None) -> List[ResolvedCase]:
    """Loads every CauseFeedback row with non-empty `raw_stats` and
    converts it to a ResolvedCase. Rows created before `raw_stats`
    existed (or via any code path that never set it) have
    `raw_stats == {}` and are excluded here — they simply have nothing
    for discovery to mine, which is a normal, expected state, not an
    error (see parser/models.py's CauseFeedback.raw_stats docstring)."""
    from parser.models import CauseFeedback  # local import: Django must be configured to call this function

    queryset = CauseFeedback.objects.exclude(raw_stats={})
    if only_cause_ids:
        queryset = queryset.filter(cause_id__in=only_cause_ids)

    return [row.to_resolved_case() for row in queryset.iterator()]
