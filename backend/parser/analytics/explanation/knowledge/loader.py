"""
Loads and validates a list of CauseKnowledge entries into a queryable
KnowledgeCatalogue. Validation is deliberately strict and fails fast —
the catalogue is static, curated code (not user input), so a typo
(duplicate ID, unrecognized pattern type) should be caught immediately
at import/startup time rather than silently producing a catalogue
entry that can never be retrieved.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Optional

from .schema import CauseKnowledge

# Mirrors the exact pattern_type vocabulary
# DeterministicPatternDiscoveryEngine._classify() produces in
# parser/analytics/patterns.py. Kept as a literal set here (rather than
# importing patterns.py) to avoid coupling the knowledge package to
# Phase 2 internals — cross-checked by
# tests/test_knowledge_loader.py::test_known_pattern_types_matches_phase2.
KNOWN_PATTERN_TYPES = {
    "RECURRING_AMOUNT_DIFFERENCE",
    "RECURRING_PERCENTAGE_DIFFERENCE",
    "DATE_OFFSET",
    "MISSING_SOURCE",
    "DUPLICATE",
    "SIGN_REVERSAL",
    "REFERENCE_MATCH_ONLY",
    "GROUPED_MATCH",
    "MIXED_ATTRIBUTE",
    "UNCLASSIFIED_RECURRING",
}


class KnowledgeValidationError(Exception):
    """Raised when the catalogue itself is malformed — a coding error
    in the knowledge base, not a runtime/data problem."""


class KnowledgeCatalogue:
    def __init__(self, entries: list[CauseKnowledge]):
        self._entries = list(entries)
        self._by_id = {e.cause_id: e for e in self._entries}
        self._by_pattern_type: dict[str, list[CauseKnowledge]] = defaultdict(list)
        for entry in self._entries:
            for pattern_type in entry.applicable_pattern_types:
                self._by_pattern_type[pattern_type].append(entry)

    def for_pattern_type(self, pattern_type: str) -> list[CauseKnowledge]:
        """Returns entries in the catalogue's own declared order —
        deterministic, and stable across calls (no sorting/shuffling)."""
        return list(self._by_pattern_type.get(pattern_type, []))

    def get(self, cause_id: str) -> Optional[CauseKnowledge]:
        return self._by_id.get(cause_id)

    def all(self) -> list[CauseKnowledge]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def load_catalogue(entries: Iterable[CauseKnowledge]) -> KnowledgeCatalogue:
    entries = list(entries)
    seen_ids: set[str] = set()

    for entry in entries:
        if not entry.cause_id:
            raise KnowledgeValidationError("A knowledge entry has an empty cause_id.")
        if entry.cause_id in seen_ids:
            raise KnowledgeValidationError(f"Duplicate cause_id in knowledge catalogue: {entry.cause_id!r}")
        seen_ids.add(entry.cause_id)

        if not entry.name:
            raise KnowledgeValidationError(f"{entry.cause_id}: name is required.")
        if not entry.applicable_pattern_types:
            raise KnowledgeValidationError(f"{entry.cause_id}: applicable_pattern_types must be non-empty.")
        for pattern_type in entry.applicable_pattern_types:
            if pattern_type not in KNOWN_PATTERN_TYPES:
                raise KnowledgeValidationError(
                    f"{entry.cause_id}: unsupported pattern_type {pattern_type!r}. "
                    f"Must be one of {sorted(KNOWN_PATTERN_TYPES)}."
                )
        if not entry.explanation_template:
            raise KnowledgeValidationError(f"{entry.cause_id}: explanation_template is required.")
        if not entry.investigation_template:
            raise KnowledgeValidationError(f"{entry.cause_id}: investigation_template is required.")

    return KnowledgeCatalogue(entries)


def default_catalogue() -> KnowledgeCatalogue:
    """The catalogue used in production — see knowledge/catalogue.py
    for the actual entries."""
    from .catalogue import CAUSE_KNOWLEDGE_ENTRIES
    return load_catalogue(CAUSE_KNOWLEDGE_ENTRIES)
