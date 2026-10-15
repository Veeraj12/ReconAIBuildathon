"""
db_bridge.py — the ONE file in analytics/explanation/learning/ that is
allowed to import Django.

Every other module in this package (feature_vector.py, schema.py,
calibration.py, trainer.py, similar_cases.py, learned_scorer.py,
store.py) is deliberately Django-free, matching the rest of Phase
1/2/3 — they operate on plain dataclasses (FeedbackRecord,
TrainedCauseModel) and can be imported/tested with zero Django
configuration. Something still has to bridge "rows in the
CauseFeedback table" to "plain FeedbackRecord objects" though — that's
this file's only job.

WHY CASE INDEXES AREN'T CACHED LIKE trained_models ARE
────────────────────────────────────────────────────────
`store.load_trained_models()` reads a static JSON file that only
changes when someone explicitly runs `train_cause_scorer` — safe to
load once per process. Tier 1 (case-based reasoning, see
similar_cases.py) is specifically meant to reflect feedback that
hasn't accumulated enough to retrain yet, i.e. the freshest data
available. Querying it fresh per request keeps Tier 1 accurate; at the
data volumes this table is expected to hold (feedback rows, not raw
transactions), a full-table read per explanation request is cheap.
If this ever becomes a real cost at scale, the fix is a short-TTL
cache here (e.g. Django's cache framework, the same one cache.py
already uses for explanations) — not a change to any other file in
this package.
"""

from __future__ import annotations

from typing import Dict, Optional

from .similar_cases import CaseIndex, MIN_CASES_FOR_KNN, build_case_indexes


def load_case_indexes(min_cases: int = MIN_CASES_FOR_KNN, only_cause_ids: Optional[list] = None) -> Dict[str, CaseIndex]:
    """Build {cause_id: CaseIndex} from every CauseFeedback row —
    imported lazily so nothing in this package requires Django to be
    configured just to be imported; only calling this specific
    function does.

    `min_cases` filters out the resulting dict entries below the
    threshold up front (rather than leaving it to learned_scorer.py to
    check `len(case_index) >= MIN_CASES_FOR_KNN` every time) — cheap
    and keeps the returned dict meaning exactly "usable case indexes",
    nothing more.
    """
    from parser.models import CauseFeedback  # local import: Django must be configured to call this function

    queryset = CauseFeedback.objects.all()
    if only_cause_ids:
        queryset = queryset.filter(cause_id__in=only_cause_ids)

    records = [row.to_feedback_record() for row in queryset.iterator()]
    indexes = build_case_indexes(records)
    return {cause_id: index for cause_id, index in indexes.items() if len(index) >= min_cases}
