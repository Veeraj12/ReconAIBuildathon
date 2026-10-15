"""
Knowledge retrieval — the "which causes could plausibly apply here at
all" step, BEFORE any evidence scoring happens.

`KnowledgeRetriever` is an abstract interface specifically so a future
implementation can retrieve candidates differently (embeddings, vector
search, a hybrid of both) without PatternExplanationService or
scoring.py needing to change — they only ever see `CandidateCause`
objects, never care how they were found.

THE MVP: RuleBasedKnowledgeRetriever
──────────────────────────────────────
Two deterministic filters, no embeddings, no vector DB:

  1. pattern_type applicability — a cause is only a candidate for the
     pattern types its catalogue entry declares.
  2. required-feature availability — a cause is only a candidate if
     every feature it needs is actually present (non-null) in this
     pattern's aggregate_stats. This is a retrieval-time filter, not a
     scoring penalty: a cause with a missing required feature isn't
     "weak evidence for this cause", it's not evaluable at all, so it
     never becomes a candidate in the first place (see scoring.py's
     module docstring for why this distinction matters for the "missing
     evidence must not become positive evidence" requirement).

Ordering is deterministic: candidates come back in the catalogue's own
declared order (see knowledge/loader.py — no sorting/shuffling), so
retrieval is 100% reproducible for identical input, same as Phase 2.

FUTURE EXTENSION POINT
───────────────────────
    KnowledgeRetriever
       ├── RuleBasedKnowledgeRetriever   (implemented)
       ├── EmbeddingRetriever            (future — local embedding model,
       │                                   semantic similarity between
       │                                   PatternContext and catalogue
       │                                   entry descriptions)
       └── HybridRetriever                (future — rule-based filter,
                                            then embedding-based ranking)
Only RuleBasedKnowledgeRetriever is implemented now, per the brief —
embeddings are explicitly out of scope for this MVP.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from .context_builder import PatternContext
from .knowledge.loader import KnowledgeCatalogue
from .knowledge.schema import CauseKnowledge


@dataclass
class CandidateCause:
    knowledge: CauseKnowledge


class KnowledgeRetriever(ABC):
    @abstractmethod
    def retrieve(self, context: PatternContext, catalogue: KnowledgeCatalogue) -> list[CandidateCause]:
        raise NotImplementedError


class RuleBasedKnowledgeRetriever(KnowledgeRetriever):
    def retrieve(self, context: PatternContext, catalogue: KnowledgeCatalogue) -> list[CandidateCause]:
        candidates: list[CandidateCause] = []
        for entry in catalogue.for_pattern_type(context.pattern_type):
            if self._required_features_available(entry, context):
                candidates.append(CandidateCause(knowledge=entry))
        return candidates

    @staticmethod
    def _required_features_available(entry: CauseKnowledge, context: PatternContext) -> bool:
        stats = context.aggregate_stats
        return all(feature in stats and stats[feature] is not None for feature in entry.required_features)
