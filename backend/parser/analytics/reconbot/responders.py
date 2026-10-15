"""
Recon Bot — response builders.

Every function here is a pure transformation: (already-computed
Phase 1/2/3 data) -> BotResponse. None of them fetch, filter, or
reason over raw transactions themselves — that boundary is what keeps
the bot an interface rather than a second analysis engine (see
schema.py's module docstring). service.py is the only place that
calls Phase 1/2/3 services; this module just phrases their output.

All numbers quoted in an answer come from one of:
  - the reconciliation `summary` dict (already computed by
    ReconciliationService.reconcile())
  - a `PatternAnalysisResult` / `Pattern` (already computed by
    Phase 2's PatternAnalysisService)
  - a `PatternExplanation` (already computed by Phase 3's
    PatternExplanationService)
Nothing is invented here.
"""

from __future__ import annotations

from typing import Optional

from .intent_classifier import BotIntent
from .schema import BotContext, BotResponse

MAX_REFERENCED_IDS = 200

# Maps an ExceptionType constant to (summary dict key, human label).
# The summary dict key for duplicates is deliberately the smaller
# "extra copy" count (group size - 1, summed), not the larger "all
# rows involved in a duplicate group" count — see
# ReconciliationService._find_duplicates' docstring. This is what a
# user actually means by "how many duplicates do I have".
_EXCEPTION_TYPE_SUMMARY_MAP = {
    "AMOUNT_MISMATCH": ("amountMismatches", "amount mismatches"),
    "TYPE_MISMATCH": ("typeMismatches", "type mismatches"),
    "MISSING_IN_TALLY": ("missingInTally", "transactions missing in Tally"),
    "MISSING_IN_BANK": ("missingInBank", "transactions missing in Bank"),
    "DUPLICATE_BANK": ("duplicateBank", "duplicate entries in the Bank file"),
    "DUPLICATE_TALLY": ("duplicateTally", "duplicate entries in the Tally file"),
    "SIGN_REVERSAL_HINT": ("signReversalHints", "sign reversal hints"),
    "GROUPED_MATCH": ("groupedMatches", "grouped (split/consolidated) matches"),
}

HELP_TEXT = (
    "I can answer questions about this reconciliation's exceptions and patterns, using only what "
    "the reconciliation, pattern-analysis, and explanation engines have already computed. Try:\n"
    "• \"Give me a summary\"\n"
    "• \"How many amount mismatches are there?\"\n"
    "• \"What patterns did you find?\"\n"
    "• \"Why do we have so many amount mismatches?\"\n"
    "• \"Which pattern has the highest financial impact?\"\n"
    "• \"Show me those transactions\" (after I mention a pattern or exception type)\n"
    "• \"What about the outliers?\""
)


def respond_greeting(context: BotContext) -> BotResponse:
    return BotResponse(
        answer="Hi — I can help you understand this reconciliation's exceptions and patterns. "
               "Ask me things like \"give me a summary\" or \"why do we have so many amount mismatches?\"",
        intent=BotIntent.GREETING,
        suggested_follow_ups=["Give me a summary", "What patterns did you find?"],
        context=context,
    )


def respond_help(context: BotContext) -> BotResponse:
    return BotResponse(answer=HELP_TEXT, intent=BotIntent.HELP, context=context)


def respond_unknown(message: str, context: BotContext) -> BotResponse:
    return BotResponse(
        answer="I'm not sure how to answer that from the reconciliation data. " + HELP_TEXT,
        intent=BotIntent.UNKNOWN,
        context=context,
    )


def respond_summary_overview(summary: dict, pattern_result, context: BotContext) -> BotResponse:
    match_pct = summary.get("matchPercentage", 0)
    total_bank = summary.get("totalBankTransactions", 0)
    matched = summary.get("matchedTransactions", 0)
    total_exceptions_amount = (
        summary.get("amountMismatches", 0) + summary.get("typeMismatches", 0)
        + summary.get("missingInBank", 0) + summary.get("missingInTally", 0)
        + summary.get("duplicateEntries", 0) + summary.get("signReversalHints", 0)
        + summary.get("groupedMatches", 0)
    )

    lines = [
        f"{matched} of {total_bank} bank transactions matched ({match_pct}%).",
        f"That leaves roughly {total_exceptions_amount} exceptions across all categories: "
        f"{summary.get('amountMismatches', 0)} amount mismatches, {summary.get('typeMismatches', 0)} type mismatches, "
        f"{summary.get('missingInBank', 0)} missing in Bank, {summary.get('missingInTally', 0)} missing in Tally, "
        f"{summary.get('duplicateEntries', 0)} duplicates, {summary.get('signReversalHints', 0)} sign-reversal hints, "
        f"and {summary.get('groupedMatches', 0)} grouped matches.",
    ]
    if pattern_result is not None:
        lines.append(
            f"Pattern analysis found {pattern_result.patterns_found} recurring pattern"
            f"{'s' if pattern_result.patterns_found != 1 else ''} "
            f"({pattern_result.outliers} exceptions didn't fit a pattern closely enough to be grouped)."
        )

    return BotResponse(
        answer=" ".join(lines),
        intent=BotIntent.SUMMARY_OVERVIEW,
        suggested_follow_ups=["What patterns did you find?", "Which exception type is most common?"],
        context=context,
    )


def respond_exception_type_count(exception_type: str, summary: dict, context: BotContext) -> BotResponse:
    if exception_type == "DUPLICATE":
        bank = summary.get("duplicateBank", 0)
        tally = summary.get("duplicateTally", 0)
        total = summary.get("duplicateEntries", bank + tally)
        answer = (
            f"There are {total} duplicate entries in total — {bank} in the Bank file and {tally} in the "
            f"Tally file. (This counts each duplicate incident once; a pair of identical rows counts as 1.)"
        )
        new_context = BotContext(last_pattern_id=context.last_pattern_id, last_exception_type="DUPLICATE")
        return BotResponse(
            answer=answer, intent=BotIntent.EXCEPTION_TYPE_COUNT,
            suggested_follow_ups=["Show me those duplicates", "Why do these duplicates happen?"],
            context=new_context,
        )

    mapping = _EXCEPTION_TYPE_SUMMARY_MAP.get(exception_type)
    if mapping is None:
        return respond_unknown(exception_type, context)

    key, label = mapping
    count = summary.get(key, 0)
    answer = f"There are {count} {label}."
    new_context = BotContext(last_pattern_id=context.last_pattern_id, last_exception_type=exception_type)
    return BotResponse(
        answer=answer,
        intent=BotIntent.EXCEPTION_TYPE_COUNT,
        suggested_follow_ups=[f"Why do we have so many {label}?", "Show me those transactions"],
        context=new_context,
    )


def respond_patterns_overview(pattern_result, context: BotContext) -> BotResponse:
    if pattern_result is None or not pattern_result.patterns:
        return BotResponse(
            answer="I didn't find any recurring patterns in the current exceptions — either there aren't "
                   "enough exceptions to form a pattern, or they're too dissimilar from each other.",
            intent=BotIntent.PATTERNS_OVERVIEW,
            context=context,
        )

    top = pattern_result.patterns[:5]
    lines = [f"I found {pattern_result.patterns_found} recurring pattern(s) across {pattern_result.total_exceptions} exceptions:"]
    for i, p in enumerate(top, start=1):
        impact = f", ₹{p.total_financial_impact:,.2f} impact" if p.total_financial_impact else ""
        lines.append(f"{i}. {p.pattern_type.replace('_', ' ').title()} — {p.record_count} records{impact}")
    if pattern_result.outliers:
        lines.append(f"{pattern_result.outliers} exceptions didn't fit a pattern and are shown as outliers.")

    return BotResponse(
        answer="\n".join(lines),
        intent=BotIntent.PATTERNS_OVERVIEW,
        referenced_pattern_ids=[p.pattern_id for p in top],
        suggested_follow_ups=["Why does the top pattern happen?", "Which pattern has the highest financial impact?"],
        context=context,
    )


def respond_pattern_explain(pattern, explanation, context: BotContext) -> BotResponse:
    bits = [explanation.summary]

    if explanation.possible_causes:
        top_cause = explanation.possible_causes[0]
        evidence = "; ".join(top_cause.supporting_evidence) if top_cause.supporting_evidence else ""
        bits.append(
            f"The most likely explanation is \"{top_cause.cause}\" ({top_cause.confidence_level} confidence)"
            + (f" — {evidence}." if evidence else ".")
        )
        if len(explanation.possible_causes) > 1:
            others = ", ".join(f"{c.cause} ({c.confidence_level})" for c in explanation.possible_causes[1:])
            bits.append(f"Other possibilities considered: {others}.")
    else:
        bits.append("No catalogued explanation strongly matches this pattern's evidence yet.")

    if explanation.unknowns:
        bits.append(f"Worth noting: {explanation.unknowns[0]}")
    if explanation.recommended_investigation:
        bits.append(f"Suggested next step: {explanation.recommended_investigation}")

    new_context = BotContext(last_pattern_id=pattern.pattern_id, last_exception_type=pattern.exception_type)
    return BotResponse(
        answer=" ".join(bits),
        intent=BotIntent.PATTERN_EXPLAIN,
        referenced_pattern_ids=[pattern.pattern_id],
        referenced_exception_ids=pattern.exception_ids[:MAX_REFERENCED_IDS],
        suggested_follow_ups=["Show me those transactions", "Which pattern has the highest financial impact?"],
        context=new_context,
    )


def respond_pattern_not_found(context: BotContext) -> BotResponse:
    return BotResponse(
        answer="I couldn't tell which pattern you mean. Try \"why does pattern 1 happen?\", or ask about an "
               "exception type directly, like \"why do we have so many amount mismatches?\"",
        intent=BotIntent.PATTERN_EXPLAIN,
        context=context,
    )


def respond_show_transactions(exception_ids: list, description: str, context: BotContext) -> BotResponse:
    if not exception_ids:
        return BotResponse(
            answer="I don't have anything specific to show yet — ask me about a pattern or an exception "
                   "type first, like \"why do we have so many amount mismatches?\", then say \"show me those\".",
            intent=BotIntent.SHOW_TRANSACTIONS,
            context=context,
        )

    capped = exception_ids[:MAX_REFERENCED_IDS]
    truncated_note = (
        f" (showing the first {len(capped)} of {len(exception_ids)})" if len(exception_ids) > len(capped) else ""
    )
    return BotResponse(
        answer=f"Here are the transactions behind {description} — {len(exception_ids)} total{truncated_note}.",
        intent=BotIntent.SHOW_TRANSACTIONS,
        referenced_exception_ids=capped,
        context=context,
    )


def respond_highest_impact(pattern_result, context: BotContext) -> BotResponse:
    if pattern_result is None or not pattern_result.patterns:
        return BotResponse(
            answer="There are no recurring patterns to compare yet.",
            intent=BotIntent.HIGHEST_IMPACT, context=context,
        )
    top = max(pattern_result.patterns, key=lambda p: p.total_financial_impact)
    new_context = BotContext(last_pattern_id=top.pattern_id, last_exception_type=top.exception_type)
    return BotResponse(
        answer=(
            f"The pattern with the highest financial impact is a {top.pattern_type.replace('_', ' ').lower()} "
            f"pattern — {top.record_count} records totaling ₹{top.total_financial_impact:,.2f}."
        ),
        intent=BotIntent.HIGHEST_IMPACT,
        referenced_pattern_ids=[top.pattern_id],
        suggested_follow_ups=["Why does this pattern happen?", "Show me those transactions"],
        context=new_context,
    )


def respond_most_frequent(pattern_result, context: BotContext) -> BotResponse:
    if pattern_result is None or not pattern_result.patterns:
        return BotResponse(
            answer="There are no recurring patterns to compare yet.",
            intent=BotIntent.MOST_FREQUENT, context=context,
        )
    top = max(pattern_result.patterns, key=lambda p: p.record_count)
    new_context = BotContext(last_pattern_id=top.pattern_id, last_exception_type=top.exception_type)
    return BotResponse(
        answer=(
            f"The most frequent pattern is a {top.pattern_type.replace('_', ' ').lower()} pattern, "
            f"affecting {top.record_count} of {pattern_result.total_exceptions} exceptions."
        ),
        intent=BotIntent.MOST_FREQUENT,
        referenced_pattern_ids=[top.pattern_id],
        suggested_follow_ups=["Why does this pattern happen?", "Show me those transactions"],
        context=new_context,
    )


def respond_outliers(pattern_result, context: BotContext) -> BotResponse:
    if pattern_result is None:
        return BotResponse(answer="No pattern analysis has been run yet.", intent=BotIntent.OUTLIERS, context=context)
    if not pattern_result.outliers:
        return BotResponse(
            answer="There are no outliers — every exception fit into a recurring pattern.",
            intent=BotIntent.OUTLIERS, context=context,
        )
    ids = pattern_result.outlier_exception_ids[:MAX_REFERENCED_IDS]
    return BotResponse(
        answer=(
            f"{pattern_result.outliers} of {pattern_result.total_exceptions} exceptions didn't fit a "
            f"recurring pattern closely enough to be grouped, so they're shown as unclassified rather than "
            f"forced into a misleading pattern."
        ),
        intent=BotIntent.OUTLIERS,
        referenced_exception_ids=ids,
        context=context,
    )
