"""
Recon Bot — orchestration.

    user message + reconciliation data + BotContext
            |
    IntentClassifier.classify        (intent_classifier.py — deterministic, no data access)
            |
    PatternAnalysisService.analyze   (Phase 2 — re-run fresh each request, same as
                                       /exceptions/analyze/ and /exceptions/patterns/<id>/explain/;
                                       see ARCHITECTURE.md §15/§19 for why re-deriving is
                                       cheap and avoids inventing persistence)
            |
    reference resolution              (which pattern/exception_type is "it"/"those"/"pattern 2"?)
            |
    [only for a "why" question] PatternExplanationService.explain   (Phase 3 — local, deterministic)
            |
    responders.respond_*              (phrasing only — see responders.py's module docstring)
            |
    BotResponse

This is the ONLY file in the reconbot package that touches
reconciliation data or calls another service — every other file here
is either pure string logic (intent_classifier.py) or pure phrasing
(responders.py). That separation is what keeps "the bot is an
interface, not a new analysis engine" true structurally.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..service import PatternAnalysisService
from ..explanation.service import PatternExplanationService
from . import responders
from .intent_classifier import BotIntent, ClassifiedIntent, IntentClassifier
from .schema import BotContext, BotResponse

logger = logging.getLogger("parser.analytics.reconbot")

_DUPLICATE_EXCEPTION_TYPES = ("DUPLICATE_BANK", "DUPLICATE_TALLY")


class ReconBotService:
    def __init__(
        self,
        analysis_service: Optional[PatternAnalysisService] = None,
        explanation_service: Optional[PatternExplanationService] = None,
        classifier: Optional[IntentClassifier] = None,
    ):
        self.analysis_service = analysis_service or PatternAnalysisService()
        self.explanation_service = explanation_service or PatternExplanationService()
        self.classifier = classifier or IntentClassifier()

    def ask(
        self,
        message: str,
        reconciliation_exceptions: dict,
        summary: dict,
        context: Optional[BotContext] = None,
    ) -> BotResponse:
        context = context or BotContext()
        classified = self.classifier.classify(message)

        features = self.analysis_service.feature_extractor.extract_all(reconciliation_exceptions)
        pattern_result = self.analysis_service.analyze_features(features)

        response = self._dispatch(classified, features, pattern_result, summary, context)

        logger.info(
            "reconbot_ask intent=%s pattern_refs=%d exception_refs=%d",
            classified.intent, len(response.referenced_pattern_ids), len(response.referenced_exception_ids),
        )
        return response

    # ────────────────────────────────────────────────────────────

    def _dispatch(
        self, classified: ClassifiedIntent, features: list, pattern_result, summary: dict, context: BotContext,
    ) -> BotResponse:
        if classified.intent == BotIntent.GREETING:
            return responders.respond_greeting(context)

        if classified.intent == BotIntent.HELP:
            return responders.respond_help(context)

        if classified.intent == BotIntent.SUMMARY_OVERVIEW:
            return responders.respond_summary_overview(summary, pattern_result, context)

        if classified.intent == BotIntent.EXCEPTION_TYPE_COUNT:
            return responders.respond_exception_type_count(classified.exception_type, summary, context)

        if classified.intent == BotIntent.PATTERNS_OVERVIEW:
            return responders.respond_patterns_overview(pattern_result, context)

        if classified.intent == BotIntent.PATTERN_EXPLAIN:
            return self._handle_pattern_explain(classified, features, pattern_result, context)

        if classified.intent == BotIntent.SHOW_TRANSACTIONS:
            return self._handle_show_transactions(classified, features, pattern_result, context)

        if classified.intent == BotIntent.HIGHEST_IMPACT:
            return responders.respond_highest_impact(pattern_result, context)

        if classified.intent == BotIntent.MOST_FREQUENT:
            return responders.respond_most_frequent(pattern_result, context)

        if classified.intent == BotIntent.OUTLIERS:
            return responders.respond_outliers(pattern_result, context)

        return responders.respond_unknown(classified.raw_message, context)

    # ────────────────────────────────────────────────────────────

    def _handle_pattern_explain(self, classified, features, pattern_result, context: BotContext) -> BotResponse:
        pattern = self._resolve_pattern(classified, pattern_result, context)
        if pattern is None:
            return responders.respond_pattern_not_found(context)

        by_id = {f.exception_id: f for f in features}
        members = [by_id[eid] for eid in pattern.exception_ids if eid in by_id]
        explanation = self.explanation_service.explain(pattern, members)
        return responders.respond_pattern_explain(pattern, explanation, context)

    def _handle_show_transactions(self, classified, features, pattern_result, context: BotContext) -> BotResponse:
        # Priority: an explicit reference in THIS message beats leftover
        # context from a previous turn — "show me the amount mismatches"
        # should show amount mismatches even if the last thing discussed
        # was a different pattern. Whichever reference wins must also be
        # written into the RETURNED context — otherwise a stale
        # last_exception_type from an earlier turn would survive even
        # after this turn explicitly resolved a different one.
        if classified.pattern_ordinal is not None and pattern_result is not None:
            idx = classified.pattern_ordinal - 1
            if 0 <= idx < len(pattern_result.patterns):
                pattern = pattern_result.patterns[idx]
                label = pattern.pattern_type.replace("_", " ").lower() + " pattern"
                updated_context = BotContext(last_pattern_id=pattern.pattern_id, last_exception_type=pattern.exception_type)
                return responders.respond_show_transactions(pattern.exception_ids, label, updated_context)

        exception_type = classified.exception_type or context.last_exception_type
        if exception_type:
            ids, label = self._ids_for_exception_type(exception_type, features, pattern_result)
            updated_context = BotContext(last_pattern_id=context.last_pattern_id, last_exception_type=exception_type)
            return responders.respond_show_transactions(ids, label, updated_context)

        if context.last_pattern_id and pattern_result is not None:
            pattern = next((p for p in pattern_result.patterns if p.pattern_id == context.last_pattern_id), None)
            if pattern is not None:
                label = pattern.pattern_type.replace("_", " ").lower() + " pattern"
                return responders.respond_show_transactions(pattern.exception_ids, label, context)

        return responders.respond_show_transactions([], "", context)

    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_pattern(classified: ClassifiedIntent, pattern_result, context: BotContext):
        if pattern_result is None or not pattern_result.patterns:
            return None
        patterns = pattern_result.patterns

        if classified.pattern_ordinal is not None:
            idx = classified.pattern_ordinal - 1
            return patterns[idx] if 0 <= idx < len(patterns) else None

        if classified.exception_type:
            match = ReconBotService._find_pattern_by_exception_type(patterns, classified.exception_type)
            if match is not None:
                return match

        if classified.pattern_type:
            match = next((p for p in patterns if p.pattern_type == classified.pattern_type), None)
            if match is not None:
                return match

        if context.last_pattern_id:
            match = next((p for p in patterns if p.pattern_id == context.last_pattern_id), None)
            if match is not None:
                return match

        if context.last_exception_type:
            match = ReconBotService._find_pattern_by_exception_type(patterns, context.last_exception_type)
            if match is not None:
                return match

        # Nothing specific was referenced at all — fall back to the
        # top-ranked pattern (Phase 2 already sorted by combined_score)
        # rather than refusing to answer. This only fires for a bare
        # "why is this happening?" with no prior context.
        return patterns[0]

    @staticmethod
    def _find_pattern_by_exception_type(patterns: list, exception_type: str):
        if exception_type == "DUPLICATE":
            return next((p for p in patterns if p.exception_type in _DUPLICATE_EXCEPTION_TYPES), None)
        return next((p for p in patterns if p.exception_type == exception_type), None)

    @staticmethod
    def _ids_for_exception_type(exception_type: str, features: list, pattern_result):
        # Prefer a pattern's own exception_ids (already Phase-2-grouped,
        # so more meaningful to show together) over a raw Phase 1 scan,
        # but fall back to the raw scan if no pattern of that type exists
        # (e.g. the type only produced outliers, or too few records to
        # form a pattern).
        if pattern_result is not None:
            match = ReconBotService._find_pattern_by_exception_type(pattern_result.patterns, exception_type)
            if match is not None:
                label = match.pattern_type.replace("_", " ").lower() + " pattern"
                return match.exception_ids, label

        types = _DUPLICATE_EXCEPTION_TYPES if exception_type == "DUPLICATE" else (exception_type,)
        ids = [f.exception_id for f in features if f.exception_type in types]

        if exception_type == "DUPLICATE":
            label = "duplicate entries"
        else:
            label = responders._EXCEPTION_TYPE_SUMMARY_MAP.get(
                exception_type, (None, exception_type.replace("_", " ").lower())
            )[1]
        return ids, label
