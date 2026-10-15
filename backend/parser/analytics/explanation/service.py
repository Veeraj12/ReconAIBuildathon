"""
Phase 3 (local) — PatternExplanationService.

    Pattern + ExceptionFeature[] members  (Phase 2 / Phase 1)
            |  PatternContextBuilder.build
    PatternContext
            |  cache lookup (context content hash)
            |  [miss]
            v
    KnowledgeRetriever.retrieve  ->  CandidateCause[]
            |
    CauseScoringEngine.score_all  ->  ScoredCause[]
            |
    DeterministicExplanationGenerator.generate
    (internally: EvidenceValidator.validate)
            v
    PatternExplanation
            |  cache store
    returned to caller (view)

No LLM, no network call, anywhere in this path — see
ARCHITECTURE_PHASE3_LOCAL.md for the migration from the previous
external-provider design. This module retains no reference to an
LLMProvider abstraction at all; there is nothing to fail over to
because there is nothing external to fail.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from ..features import ExceptionFeature
from . import cache as explanation_cache
from .context_builder import PatternContextBuilder
from .knowledge.loader import KNOWN_PATTERN_TYPES, KnowledgeCatalogue, default_catalogue
from .retriever import KnowledgeRetriever, RuleBasedKnowledgeRetriever
from .scoring import CauseScoringEngine
from .learning.learned_scorer import LearnedCauseScoringEngine
from .explanation_generator import DeterministicExplanationGenerator
from .schema import EvidenceReference, PatternExplanation, PossibleCause

logger = logging.getLogger("parser.analytics.explanation")


class PatternExplanationService:
    def __init__(
        self,
        context_builder: Optional[PatternContextBuilder] = None,
        catalogue: Optional[KnowledgeCatalogue] = None,
        retriever: Optional[KnowledgeRetriever] = None,
        scorer: Optional[CauseScoringEngine] = None,
        generator: Optional[DeterministicExplanationGenerator] = None,
        use_cache: bool = True,
        cache_ttl: int = explanation_cache.DEFAULT_TTL_SECONDS,
    ):
        self.context_builder = context_builder or PatternContextBuilder()
        self.catalogue = catalogue or default_catalogue()
        self.retriever = retriever or RuleBasedKnowledgeRetriever()
        # LearnedCauseScoringEngine is a strict superset of CauseScoringEngine:
        # any cause_id with no trained model (the case for every cause_id on
        # day one, before any feedback is collected) falls back to the exact
        # same rule-based formula CauseScoringEngine implements — see
        # learning/learned_scorer.py's module docstring and
        # learning/tests/test_learned_scorer.py's ColdStartFallbackTests for
        # the guarantee this relies on. Pass scorer=CauseScoringEngine()
        # explicitly if you need to force pure rule-based scoring (e.g. to
        # A/B compare against the learned version).
        self.scorer = scorer or LearnedCauseScoringEngine()
        self.generator = generator or DeterministicExplanationGenerator()
        self.use_cache = use_cache
        self.cache_ttl = cache_ttl

    def explain(self, pattern, members: list[ExceptionFeature]) -> PatternExplanation:
        context = self.context_builder.build(pattern, members)
        context_dict = context.to_dict()

        if self.use_cache:
            cached = explanation_cache.get_cached_explanation(context_dict)
            if cached is not None:
                self._log(
                    "cache_hit", pattern.pattern_id, pattern_type=context.pattern_type,
                    record_count=context.record_count, latency_ms=0, cache_hit=True,
                )
                cached = dict(cached)
                cached["from_cache"] = True
                return _explanation_from_dict(cached)

        start = time.monotonic()

        pattern_type_recognized = context.pattern_type in KNOWN_PATTERN_TYPES
        if not pattern_type_recognized:
            # Loud and distinct on purpose — see explanation_generator.py's
            # _build_unknowns for why this must never be silently
            # indistinguishable from "no cause matched." This log line is
            # the thing to alert/monitor on if Phase 2's pattern-discovery
            # logic (patterns.py's PatternType) is ever extended without a
            # matching update to knowledge/loader.py's KNOWN_PATTERN_TYPES.
            self._log(
                "unrecognized_pattern_type", pattern.pattern_id,
                pattern_type=context.pattern_type, record_count=context.record_count,
            )

        candidates = self.retriever.retrieve(context, self.catalogue)
        scored_causes = self.scorer.score_all(context, candidates)
        explanation = self.generator.generate(context, scored_causes, pattern_type_recognized=pattern_type_recognized)

        latency_ms = round((time.monotonic() - start) * 1000, 2)
        self._log(
            "generated", pattern.pattern_id,
            pattern_type=context.pattern_type,
            pattern_type_recognized=pattern_type_recognized,
            record_count=context.record_count,
            retrieved_cause_count=len(candidates),
            selected_cause_count=len(explanation.possible_causes),
            generation_status=explanation.status,
            validation_warning_count=len(explanation.validation_warnings),
            latency_ms=latency_ms,
            cache_hit=False,
        )

        if self.use_cache:
            explanation_cache.set_cached_explanation(context_dict, explanation.to_dict(), ttl=self.cache_ttl)

        return explanation

    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _log(event: str, pattern_id: str, **fields):
        """Structured, metadata-only logging — never the context content
        (amounts/references/samples) or generated evidence text, per the
        brief's 'do not log sensitive financial data' requirement."""
        logger.info(
            "phase3_explanation event=%s pattern_id=%s %s",
            event, pattern_id,
            " ".join(f"{k}={v}" for k, v in fields.items() if v is not None),
        )


def _explanation_from_dict(d: dict) -> PatternExplanation:
    return PatternExplanation(
        pattern_id=d["pattern_id"],
        summary=d["summary"],
        facts=d["facts"],
        possible_causes=[PossibleCause(**c) for c in d["possible_causes"]],
        unknowns=d["unknowns"],
        recommended_investigation=d["recommended_investigation"],
        evidence_references=[EvidenceReference(**r) for r in d["evidence_references"]],
        pattern_confidence=d["pattern_confidence"],
        confidence_methodology=d["confidence_methodology"],
        status=d["status"],
        model=d["model"],
        generated_at=d["generated_at"],
        validation_warnings=d["validation_warnings"],
        from_cache=d.get("from_cache", True),
    )
