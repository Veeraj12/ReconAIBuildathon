"""
The initial local knowledge catalogue.

Each entry's `applicable_pattern_types` uses the EXACT pattern_type
strings `DeterministicPatternDiscoveryEngine._classify()` produces in
parser/analytics/patterns.py — RECURRING_AMOUNT_DIFFERENCE,
RECURRING_PERCENTAGE_DIFFERENCE, DATE_OFFSET, MISSING_SOURCE,
DUPLICATE, SIGN_REVERSAL, REFERENCE_MATCH_ONLY, GROUPED_MATCH. (Not
MIXED_ATTRIBUTE or UNCLASSIFIED_RECURRING — those pattern types
deliberately have no catalogue entries yet, since they don't cleanly
correspond to one named hypothesis; a pattern of that type will
correctly produce "insufficient evidence" rather than a forced guess.)

`required_features` gates retrieval (see retriever.py); conditions
gate scoring (see scoring.py). Feature names must match keys that
context_builder.py's `_aggregate_stats` can actually produce — see
parser/analytics/patterns.py's `Pattern.common_features` for the
authoritative list: reference_match_rate, amount_match_rate,
date_match_rate, average_absolute_amount_difference,
median_absolute_amount_difference, average_percentage_difference,
common_date_offset_days — plus record_count / total_financial_impact,
which context_builder always includes.
"""

from .schema import CauseKnowledge, FeatureCondition

CAUSE_KNOWLEDGE_ENTRIES: tuple[CauseKnowledge, ...] = (
    # ── A. RECURRING_AMOUNT_DIFFERENCE ──────────────────────────────
    CauseKnowledge(
        cause_id="BANK_SERVICE_CHARGE",
        name="Recurring bank/service charge",
        applicable_pattern_types=("RECURRING_AMOUNT_DIFFERENCE",),
        required_features=("average_absolute_amount_difference",),
        supporting_conditions=(
            FeatureCondition("reference_match_rate", ">=", 0.90),
            FeatureCondition("common_date_offset_days", "==", 0),
        ),
        contradicting_conditions=(
            FeatureCondition("reference_match_rate", "<", 0.50),
        ),
        explanation_template="A recurring fixed amount difference may indicate a bank or service charge.",
        investigation_template="Review whether the recurring difference corresponds to a configured bank or settlement charge.",
    ),
    CauseKnowledge(
        cause_id="DATA_ENTRY_DIFFERENCE",
        name="Systematic data-entry or rounding difference",
        applicable_pattern_types=("RECURRING_AMOUNT_DIFFERENCE",),
        required_features=("average_absolute_amount_difference",),
        supporting_conditions=(
            FeatureCondition("reference_match_rate", ">=", 0.80),
        ),
        contradicting_conditions=(
            FeatureCondition("reference_match_rate", "<", 0.50),
        ),
        explanation_template="A consistent difference across many transactions can also result from a systematic data-entry or rounding convention on one side.",
        investigation_template="Check for a systematic data-entry or rounding rule that could produce this fixed difference.",
    ),
    CauseKnowledge(
        cause_id="INTEGRATION_TRANSFORMATION",
        name="Integration/import amount transformation",
        applicable_pattern_types=("RECURRING_AMOUNT_DIFFERENCE",),
        required_features=("average_absolute_amount_difference",),
        supporting_conditions=(
            FeatureCondition("date_match_rate", ">=", 0.90),
        ),
        contradicting_conditions=(),
        explanation_template="An integration or import process that transforms amounts (e.g. adding a fixed fee or rounding) on one side could produce this pattern.",
        investigation_template="Check the integration/import configuration between the two systems for any amount transformation rule.",
    ),
    CauseKnowledge(
        cause_id="SETTLEMENT_ADJUSTMENT",
        name="Settlement or clearing adjustment",
        applicable_pattern_types=("RECURRING_AMOUNT_DIFFERENCE",),
        required_features=("average_absolute_amount_difference",),
        supporting_conditions=(),
        contradicting_conditions=(),
        explanation_template="The consistent difference could represent a settlement or clearing adjustment applied by one of the systems.",
        investigation_template="Confirm with the settlement/clearing process whether an adjustment of this size is expected.",
    ),

    # ── B. RECURRING_PERCENTAGE_DIFFERENCE ──────────────────────────
    CauseKnowledge(
        cause_id="TAX",
        name="Tax (e.g. GST/TDS) applied on one side",
        applicable_pattern_types=("RECURRING_PERCENTAGE_DIFFERENCE",),
        required_features=("average_percentage_difference",),
        supporting_conditions=(
            FeatureCondition("reference_match_rate", ">=", 0.90),
        ),
        contradicting_conditions=(
            FeatureCondition("reference_match_rate", "<", 0.50),
        ),
        explanation_template="A consistent percentage difference is often produced by a tax component applied on one side only.",
        investigation_template="Verify whether a tax component is being applied or recorded on only one side.",
    ),
    CauseKnowledge(
        cause_id="COMMISSION",
        name="Commission deducted from the transaction amount",
        applicable_pattern_types=("RECURRING_PERCENTAGE_DIFFERENCE",),
        required_features=("average_percentage_difference",),
        supporting_conditions=(
            FeatureCondition("reference_match_rate", ">=", 0.80),
        ),
        contradicting_conditions=(),
        explanation_template="A steady percentage deduction can indicate a commission taken out of the transaction amount.",
        investigation_template="Check whether a commission rate matching this percentage applies to these transactions.",
    ),
    CauseKnowledge(
        cause_id="PROCESSING_FEE",
        name="Payment processor fee",
        applicable_pattern_types=("RECURRING_PERCENTAGE_DIFFERENCE",),
        required_features=("average_percentage_difference",),
        supporting_conditions=(
            FeatureCondition("date_match_rate", ">=", 0.80),
        ),
        contradicting_conditions=(),
        explanation_template="Payment gateways and processors often deduct a percentage-based processing fee.",
        investigation_template="Confirm with the payment processor whether a fee of this percentage applies.",
    ),
    CauseKnowledge(
        cause_id="PERCENTAGE_BASED_ADJUSTMENT",
        name="General percentage-based adjustment",
        applicable_pattern_types=("RECURRING_PERCENTAGE_DIFFERENCE",),
        required_features=("average_percentage_difference",),
        supporting_conditions=(),
        contradicting_conditions=(),
        explanation_template="A general percentage-based adjustment configured in one system could also explain this.",
        investigation_template="Review any percentage-based adjustment rules configured in either system.",
    ),

    # ── C. DATE_OFFSET ───────────────────────────────────────────────
    CauseKnowledge(
        cause_id="SETTLEMENT_DELAY",
        name="Normal settlement delay",
        applicable_pattern_types=("DATE_OFFSET",),
        required_features=("common_date_offset_days",),
        supporting_conditions=(
            FeatureCondition("amount_match_rate", ">=", 0.90),
        ),
        contradicting_conditions=(),
        explanation_template="A consistent day offset commonly reflects the normal settlement delay between systems.",
        investigation_template="Confirm the expected settlement delay (in days) for this payment channel.",
    ),
    CauseKnowledge(
        cause_id="POSTING_DELAY",
        name="One-sided posting delay",
        applicable_pattern_types=("DATE_OFFSET",),
        required_features=("common_date_offset_days",),
        supporting_conditions=(
            FeatureCondition("reference_match_rate", ">=", 0.90),
        ),
        contradicting_conditions=(),
        explanation_template="One system may simply post entries a fixed number of days after the other.",
        investigation_template="Check whether one system's posting date lags the other by design.",
    ),
    CauseKnowledge(
        cause_id="ACCOUNTING_DATE_CONVENTION",
        name="Accounting-date vs. transaction-date convention",
        applicable_pattern_types=("DATE_OFFSET",),
        required_features=("common_date_offset_days",),
        supporting_conditions=(
            FeatureCondition("amount_match_rate", ">=", 0.95),
            FeatureCondition("reference_match_rate", ">=", 0.95),
        ),
        contradicting_conditions=(),
        explanation_template="Some systems record the accounting date rather than the transaction date, producing a fixed offset.",
        investigation_template="Check whether either system uses an accounting-date convention distinct from the transaction date.",
    ),
    CauseKnowledge(
        cause_id="BATCH_PROCESSING_DELAY",
        name="Batch/end-of-day processing delay",
        applicable_pattern_types=("DATE_OFFSET",),
        required_features=("common_date_offset_days",),
        supporting_conditions=(),
        contradicting_conditions=(),
        explanation_template="Batch-based processing (e.g. end-of-day settlement batches) can introduce a fixed day offset for many transactions at once.",
        investigation_template="Check whether these transactions are processed through an end-of-day or batch settlement cycle.",
    ),

    # ── D. MISSING_SOURCE ────────────────────────────────────────────
    CauseKnowledge(
        cause_id="PENDING_POSTING",
        name="Pending posting on the other side",
        applicable_pattern_types=("MISSING_SOURCE",),
        explanation_template="These transactions may simply not have been posted yet on the other side.",
        investigation_template="Check whether these transactions are pending posting in the other system.",
    ),
    CauseKnowledge(
        cause_id="SOURCE_SYSTEM_DELAY",
        name="Source-system delivery delay",
        applicable_pattern_types=("MISSING_SOURCE",),
        explanation_template="The source system that feeds the missing side may not have delivered these records yet.",
        investigation_template="Confirm with the source system whether there is a known delay in sending these records.",
    ),
    CauseKnowledge(
        cause_id="MISSING_RECORD",
        name="Genuinely unrecorded transaction",
        applicable_pattern_types=("MISSING_SOURCE",),
        explanation_template="These transactions may genuinely never have been recorded on the other side.",
        investigation_template="Manually verify a sample of these transactions against source documents to confirm whether they exist.",
    ),
    CauseKnowledge(
        cause_id="INCOMPLETE_UPLOAD",
        name="Incomplete file upload",
        applicable_pattern_types=("MISSING_SOURCE",),
        explanation_template="An incomplete or partial file upload could explain why these transactions are missing on one side.",
        investigation_template="Check whether the most recent upload for the missing side was complete.",
    ),

    # ── E. DUPLICATE ─────────────────────────────────────────────────
    CauseKnowledge(
        cause_id="DUPLICATE_IMPORT",
        name="Duplicate file import",
        applicable_pattern_types=("DUPLICATE",),
        explanation_template="The same source file, or a portion of it, may have been imported more than once.",
        investigation_template="Check for duplicate file imports covering the same date range.",
    ),
    CauseKnowledge(
        cause_id="DUPLICATE_TRANSACTION",
        name="Transaction recorded twice at the source",
        applicable_pattern_types=("DUPLICATE",),
        explanation_template="The same underlying transaction may have been recorded twice in the source system.",
        investigation_template="Verify with the source system whether these entries represent a single transaction recorded twice.",
    ),
    CauseKnowledge(
        cause_id="DUPLICATE_SETTLEMENT",
        name="Duplicate settlement record",
        applicable_pattern_types=("DUPLICATE",),
        explanation_template="A settlement or payment provider may have sent the same settlement record more than once.",
        investigation_template="Check with the settlement/payment provider for duplicate settlement records.",
    ),

    # ── F. SIGN_REVERSAL ─────────────────────────────────────────────
    CauseKnowledge(
        cause_id="DEBIT_CREDIT_CONVENTION",
        name="Opposite debit/credit sign convention",
        applicable_pattern_types=("SIGN_REVERSAL",),
        explanation_template="The two systems may simply use opposite debit/credit sign conventions.",
        investigation_template="Confirm which sign convention (debit-positive vs. credit-positive) each system uses.",
    ),
    CauseKnowledge(
        cause_id="REVERSAL_TRANSACTION",
        name="Genuine reversal/correction entry",
        applicable_pattern_types=("SIGN_REVERSAL",),
        explanation_template="This could represent a genuine reversal or correction transaction recorded on only one side.",
        investigation_template="Check whether a reversal or correction entry was intentionally posted.",
    ),
    CauseKnowledge(
        cause_id="SIGN_TRANSFORMATION",
        name="Integration sign-inversion step",
        applicable_pattern_types=("SIGN_REVERSAL",),
        explanation_template="An integration step may be inverting the transaction sign for one side only.",
        investigation_template="Review the integration/import logic for any sign-inversion step.",
    ),

    # ── G. REFERENCE_MATCH_ONLY ──────────────────────────────────────
    CauseKnowledge(
        cause_id="REFERENCE_FORMAT_TRANSFORMATION",
        name="Reference formatting difference",
        applicable_pattern_types=("REFERENCE_MATCH_ONLY",),
        supporting_conditions=(
            FeatureCondition("reference_match_rate", ">=", 0.80),
        ),
        explanation_template="References line up well, but a formatting difference elsewhere may be masking an otherwise-genuine match.",
        investigation_template="Review whether reference formatting (prefixes, suffixes, casing) differs consistently between the two systems.",
    ),
    CauseKnowledge(
        cause_id="PREFIX_SUFFIX_TRANSFORMATION",
        name="Consistent prefix/suffix transformation",
        applicable_pattern_types=("REFERENCE_MATCH_ONLY",),
        explanation_template="One system may consistently add a prefix or suffix to references that the other does not.",
        investigation_template="Compare a sample of references side-by-side for a consistent prefix/suffix pattern.",
    ),
    CauseKnowledge(
        cause_id="SOURCE_REFERENCE_MAPPING",
        name="Integration reference-mapping step",
        applicable_pattern_types=("REFERENCE_MATCH_ONLY",),
        explanation_template="An integration mapping step may be transforming references between the two systems.",
        investigation_template="Check the reference-mapping logic in the integration between these systems.",
    ),

    # ── H. GROUPED_MATCH ─────────────────────────────────────────────
    CauseKnowledge(
        cause_id="SPLIT_TRANSACTION",
        name="Transaction split across multiple entries",
        applicable_pattern_types=("GROUPED_MATCH",),
        supporting_conditions=(
            FeatureCondition("common_date_offset_days", "==", 0),
        ),
        explanation_template="One transaction on one side may have been split into several entries on the other, summing to the same amount and date.",
        investigation_template="Verify with the source system whether this transaction was intentionally split into multiple entries.",
    ),
    CauseKnowledge(
        cause_id="CONSOLIDATED_TRANSACTION",
        name="Multiple transactions consolidated into one",
        applicable_pattern_types=("GROUPED_MATCH",),
        supporting_conditions=(
            FeatureCondition("common_date_offset_days", "==", 0),
        ),
        explanation_template="Several transactions on one side may have been consolidated into a single entry on the other.",
        investigation_template="Confirm whether multiple transactions were intentionally consolidated into a single settlement entry.",
    ),
    CauseKnowledge(
        cause_id="BATCH_POSTING",
        name="Batch-posting/settlement-netting process",
        applicable_pattern_types=("GROUPED_MATCH",),
        explanation_template="This could reflect a batch-posting process that groups multiple entries into one settlement record.",
        investigation_template="Check whether a batch-posting or settlement-netting process produced this grouping.",
    ),
)
