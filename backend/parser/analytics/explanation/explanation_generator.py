"""
DeterministicExplanationGenerator — the component that replaces the
LLM for Phase 3's MVP. Every sentence it produces comes from one of
three places, never from free generation: `PatternContext` (facts),
the knowledge catalogue's templates (cause explanations/investigation
text), or `CauseScoringEngine`'s evidence items (why a cause was
scored the way it was). This is enforced structurally by which list a
sentence goes into, not by asking a model nicely.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .context_builder import PatternContext
from .grounding import EvidenceItem, EvidenceValidator
from .schema import EvidenceReference, PatternExplanation, PossibleCause
from .scoring import ScoredCause

# How many candidate causes to surface, at most, even if more scored
# above the retain threshold — keeps the explanation focused rather
# than listing every marginally-plausible hypothesis.
MAX_CAUSES_SHOWN = 3


class DeterministicExplanationGenerator:
    def generate(
        self,
        context: PatternContext,
        scored_causes: list[ScoredCause],
        pattern_type_recognized: bool = True,
    ) -> PatternExplanation:
        facts = self._build_facts(context)
        shown_causes = scored_causes[:MAX_CAUSES_SHOWN]

        validation = EvidenceValidator().validate(context, facts, shown_causes)

        possible_causes = [
            PossibleCause(
                cause=sc.name,
                cause_id=sc.cause_id,
                confidence_level=sc.confidence_level,
                supporting_evidence=[g.text for g in validation.grounded_causes[sc.cause_id]["supporting"]],
                contradicting_evidence=[g.text for g in validation.grounded_causes[sc.cause_id]["contradicting"]],
            )
            for sc in shown_causes
        ]

        unknowns = self._build_unknowns(context, shown_causes, pattern_type_recognized)
        summary = self._build_summary(context, shown_causes)
        recommended_investigation = (
            shown_causes[0].investigation_template
            if shown_causes
            else "No catalogued cause matched this pattern strongly enough for a specific recommendation — "
                 "a manual review of a sample of the affected transactions is suggested."
        )

        evidence_references = [
            EvidenceReference(claim=g.text, feature=g.feature, grounded=g.grounded)
            for g in validation.grounded_facts
        ]
        for sc in shown_causes:
            for g in validation.grounded_causes[sc.cause_id]["supporting"]:
                evidence_references.append(EvidenceReference(claim=g.text, feature=g.feature, grounded=g.grounded))
            for g in validation.grounded_causes[sc.cause_id]["contradicting"]:
                evidence_references.append(EvidenceReference(claim=g.text, feature=g.feature, grounded=g.grounded))

        return PatternExplanation(
            pattern_id=context.pattern_id,
            summary=summary,
            facts=[g.text for g in validation.grounded_facts],
            possible_causes=possible_causes,
            unknowns=unknowns,
            recommended_investigation=recommended_investigation,
            evidence_references=evidence_references,
            pattern_confidence=context.pattern_confidence,
            validation_warnings=validation.warnings,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # ────────────────────────────────────────────────────────────
    # FACTS — directly transcribed from context, never causal.
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_facts(context: PatternContext) -> list[EvidenceItem]:
        stats = context.aggregate_stats
        facts = [EvidenceItem(f"{context.record_count} transactions belong to this pattern.", "record_count")]

        if stats.get("average_absolute_amount_difference") is not None:
            facts.append(EvidenceItem(
                f"The average absolute difference is {stats['average_absolute_amount_difference']}.",
                "average_absolute_amount_difference",
            ))
        if stats.get("average_percentage_difference") is not None:
            facts.append(EvidenceItem(
                f"The average percentage difference is {stats['average_percentage_difference']}%.",
                "average_percentage_difference",
            ))
        if stats.get("common_date_offset_days") is not None:
            facts.append(EvidenceItem(
                f"The common date offset is {stats['common_date_offset_days']} day(s).",
                "common_date_offset_days",
            ))
        if stats.get("reference_match_rate") is not None:
            facts.append(EvidenceItem(
                f"{round(stats['reference_match_rate'] * 100)}% of records have matching references.",
                "reference_match_rate",
            ))
        if stats.get("amount_match_rate") is not None:
            facts.append(EvidenceItem(
                f"{round(stats['amount_match_rate'] * 100)}% of records have exactly matching amounts.",
                "amount_match_rate",
            ))

        facts.append(EvidenceItem(
            f"Total financial impact of this pattern is {context.total_financial_impact}.",
            "total_financial_impact",
        ))
        return facts

    # ────────────────────────────────────────────────────────────
    # SUMMARY — one sentence, pattern-type-aware, still fact-only.
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_summary(context: PatternContext, shown_causes: list[ScoredCause]) -> str:
        stats = context.aggregate_stats
        n = context.record_count
        pt = context.pattern_type

        if pt == "MISSING_SOURCE":
            core = f"{n} transactions have no counterpart on the other side"
        elif pt == "DUPLICATE":
            core = f"{n} transactions appear to be duplicate entries"
        elif pt == "SIGN_REVERSAL":
            core = f"{n} transactions match on amount and date but differ in transaction type"
        elif pt == "GROUPED_MATCH":
            core = f"{n} transactions form many-to-one grouped matches"
        elif stats.get("average_absolute_amount_difference") is not None:
            core = f"{n} transactions show a recurring {stats['average_absolute_amount_difference']} amount difference"
        elif stats.get("average_percentage_difference") is not None:
            core = f"{n} transactions show a recurring {stats['average_percentage_difference']}% difference"
        elif stats.get("common_date_offset_days") is not None:
            core = f"{n} transactions show a consistent {stats['common_date_offset_days']}-day date offset"
        else:
            core = f"{n} transactions share a common recurring signature"

        qualifiers = []
        if (stats.get("reference_match_rate") or 0) >= 0.8:
            qualifiers.append("references generally match")
        if (stats.get("date_match_rate") or 0) >= 0.8 and pt not in ("DATE_OFFSET",):
            qualifiers.append("dates generally match")

        sentence = core
        if qualifiers:
            sentence += " while " + " and ".join(qualifiers)
        sentence += "."

        if not shown_causes:
            sentence += " No catalogued explanation strongly matches this evidence yet."

        return sentence

    # ────────────────────────────────────────────────────────────
    # UNKNOWNS — explicit, never skipped.
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _build_unknowns(
        context: PatternContext,
        shown_causes: list[ScoredCause],
        pattern_type_recognized: bool = True,
    ) -> list[str]:
        unknowns = list(context.caveats)

        if not pattern_type_recognized:
            # Distinct from "no cause matched" below — this means no
            # cause could even be CONSIDERED, because this pattern_type
            # isn't in the knowledge package's known vocabulary at all.
            # This is the signal that closes the loose connection
            # between Phase 2 (pattern discovery) and Phase 3 (this
            # knowledge catalogue): if pattern-discovery logic is ever
            # changed to introduce a new pattern_type (e.g. a future
            # clustering-based engine — see patterns.py's own
            # docstring on this), this is what surfaces it, instead of
            # it silently looking identical to "we checked thoroughly
            # and found no strong cause."
            unknowns.append(
                f"Pattern type '{context.pattern_type}' is not recognized by the current knowledge "
                f"catalogue, so no candidate causes could be evaluated for it at all — this is "
                f"different from having evaluated candidates and found none strong enough. This "
                f"usually means the pattern-discovery logic has introduced a new category that the "
                f"knowledge catalogue hasn't been updated to cover yet, and should be flagged for review."
            )
        elif not shown_causes:
            unknowns.append(
                "The available pattern evidence does not strongly support any known cause in the "
                "local knowledge base."
            )
        else:
            top = shown_causes[0]
            unknowns.append(
                f"The source data does not explicitly identify this pattern as a "
                f"{top.name.lower()} — this remains a hypothesis, not a confirmed cause."
            )

        if context.outlier_note:
            unknowns.append(context.outlier_note)

        return unknowns
