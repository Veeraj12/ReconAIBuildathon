"""
Multi-source reconciliation.

    Primary source (e.g. Bank)
            |
            +---- reconcile against Secondary #1 (e.g. Tally)   ReconciliationService, unchanged
            |
            +---- reconcile against Secondary #2 (e.g. Payment Gateway)   ReconciliationService, unchanged
            |
            +---- ... (N secondaries)
            |
            v
    Cross-source aggregation: for each primary transaction, which
    secondaries actually have a matching record?

WHY HUB-AND-SPOKE, NOT FULL N-WAY (every source vs every other source)
────────────────────────────────────────────────────────────────────────
Full N-way matching (bank<->tally, bank<->gateway, tally<->gateway, ...)
grows combinatorially (N choose 2 pairs to reconcile) and, more
importantly, doesn't match how reconciliation works in practice: a
bank statement is the ground truth for money that actually moved, and
every other system (accounting ledger, payment gateway, invoicing
system) gets reconciled AGAINST it, not against each other. Hub-and-
spoke is both the simpler implementation and the financially correct
model.

WHY ReconciliationService NEEDED ZERO MODIFICATIONS
──────────────────────────────────────────────────────
`ReconciliationService.__init__` reads its secondary-side config from
the class attribute `TALLY_CONFIG_FILE`. Because that's a *class*
attribute (not hardcoded inline), a dynamically-created subclass that
only overrides `TALLY_CONFIG_FILE` gets every other line of the
already-tested matching engine — waterfall rules, tolerances,
duplicate detection, exception categorization — completely unchanged.
This file never touches reconciliationService.py.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .reconciliationService import ReconciliationService

DEFAULT_SECONDARY_CONFIG_FILE = "genericSecondary.json"

# Max number of sample transactions kept per cross-source status bucket
# — keeps the response bounded regardless of dataset size, same
# principle as Phase 2/3's capped representative samples.
MAX_SAMPLES_PER_BUCKET = 25


def _build_secondary_reconciliation_service(config_file: str) -> ReconciliationService:
    """Dynamically subclasses ReconciliationService with a different
    TALLY_CONFIG_FILE — see module docstring for why this requires zero
    changes to reconciliationService.py itself. The base class's
    "tally" slot becomes, functionally, "whichever secondary source
    we're reconciling the primary against right now"; the JSON field
    names in that pairwise result (tallyTransaction, missingInTally,
    duplicates.tally, ...) are relabeled by this module before they
    reach the API response — see _relabel_pairwise_result."""
    subclass = type(
        "SecondarySourceReconciliationService",
        (ReconciliationService,),
        {"TALLY_CONFIG_FILE": config_file},
    )
    return subclass()


@dataclass
class SecondarySourceSpec:
    name: str  # human label, e.g. "Payment Gateway" — becomes the key
               # in every multi-source output structure
    file: object  # a Django UploadedFile (or any file-like FileLoader accepts)
    config_file: str = DEFAULT_SECONDARY_CONFIG_FILE


class MultiSourceReconciliationService:
    """
    Reconciles one primary source against N secondary sources and adds
    a cross-source view on top of N independent, already-tested
    pairwise reconciliations.

    Each secondary source is parsed and matched completely
    independently — a bug or data-quality issue in one secondary source
    (e.g. a malformed Payment Gateway export) cannot affect the
    Bank<->Tally reconciliation at all. This isolation is a deliberate
    property, not an accident of the implementation: multi-source
    reconciliation should degrade gracefully per-source, not fail as a
    single unit.
    """

    def __init__(self, primary_label: str = "Bank"):
        self.primary_label = primary_label

    def reconcile(self, primary_file, secondary_specs: list) -> dict:
        if not secondary_specs:
            raise ValueError("At least one secondary source is required for multi-source reconciliation.")

        names = [s.name for s in secondary_specs]
        if len(names) != len(set(names)):
            raise ValueError("Secondary source names must be unique.")

        pairwise_results = {}
        pairwise_errors = {}

        for spec in secondary_specs:
            try:
                service = _build_secondary_reconciliation_service(spec.config_file)
                # ReconciliationService.reconcile() re-seeks and re-reads
                # both files from scratch on every call (see
                # FileLoader._seek_start), so the same primary_file object
                # can safely be reused across every secondary in this loop.
                raw_result = service.reconcile(primary_file, spec.file)
                pairwise_results[spec.name] = self._relabel_pairwise_result(raw_result)
            except Exception as error:
                # One bad secondary source must not take down the whole
                # multi-source run — the others still get reconciled and
                # reported; this one shows up as a per-source error
                # instead of a fatal 500 for the entire request.
                pairwise_errors[spec.name] = str(error)

        if not pairwise_results:
            raise ValueError(
                "None of the secondary sources could be reconciled: "
                + "; ".join(f"{name}: {msg}" for name, msg in pairwise_errors.items())
            )

        primary_records = self._collect_primary_records(pairwise_results)
        status_by_source = {
            name: self._status_map(result) for name, result in pairwise_results.items()
        }
        breakdown, samples = self._build_cross_source_breakdown(
            primary_records, status_by_source, list(pairwise_results.keys())
        )
        summary = self._build_summary(primary_records, pairwise_results, breakdown)

        return {
            "success": True,
            "primarySource": self.primary_label,
            "secondarySources": names,
            "secondarySourceErrors": pairwise_errors,
            "summary": summary,
            "crossSourceBreakdown": breakdown,
            "sampleTransactions": samples,
            "pairwiseResults": pairwise_results,
        }

    # ────────────────────────────────────────────────────────────
    # Relabeling — a pairwise result still internally uses
    # ReconciliationService's bank/tally vocabulary regardless of what
    # the secondary source actually is; this renames the secondary-side
    # keys to the source's real name before it's ever returned to a
    # caller, so a Payment Gateway result never says "tally" anywhere.
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _relabel_pairwise_result(raw: dict) -> dict:
        s = raw["summary"]
        relabeled_summary = {
            "totalPrimaryTransactions": s["totalBankTransactions"],
            "totalSecondaryTransactions": s["totalTallyTransactions"],
            "matchedTransactions": s["matchedTransactions"],
            "missingInSecondary": s["missingInBank"],
            "missingInPrimary": s["missingInTally"],
            "matchPercentage": s["matchPercentage"],
            "referenceMatches": s["referenceMatches"],
            "dateToleranceMatches": s["dateToleranceMatches"],
            "amountDateMatches": s["amountDateMatches"],
            "amountMismatches": s["amountMismatches"],
            "typeMismatches": s["typeMismatches"],
            "groupedMatches": s["groupedMatches"],
            "signReversalHints": s["signReversalHints"],
            "duplicatePrimary": s["duplicateBank"],
            "duplicateSecondary": s["duplicateTally"],
            "duplicateEntries": s["duplicateEntries"],
            "invalidPrimaryRows": s["invalidBankRows"],
            "invalidSecondaryRows": s["invalidTallyRows"],
            "invalidPrimaryRowData": s["invalidBankRowData"],
            "invalidSecondaryRowData": s["invalidTallyRowData"],
        }
        return {
            "summary": relabeled_summary,
            "warnings": raw["warnings"],
            "matchedTransactions": raw["matchedTransactions"],
            "missingInPrimary": raw["missingInTally"],
            "missingInSecondary": raw["missingInBank"],
            "exceptions": raw["exceptions"],
            "duplicates": {
                "primary": raw["duplicates"]["bank"],
                "secondary": raw["duplicates"]["tally"],
            },
            "signReversalHints": raw["signReversalHints"],
        }

    # ────────────────────────────────────────────────────────────
    # Cross-source status
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _status_map(result: dict) -> dict:
        """excelRow -> "MATCHED" | "EXCEPTION" | "MISSING" for every
        primary-side row this pairwise result touched. EXCEPTION means
        the secondary source DOES have a corresponding record, just not
        a clean match (amount/type mismatch, grouped/split match, or a
        sign-reversal hint) — worth distinguishing from MISSING (no
        corresponding record found at all), since they call for very
        different investigation."""
        status = {}

        for m in result["matchedTransactions"]:
            status[m["bankTransaction"]["excelRow"]] = "MATCHED"

        for txn in result["missingInPrimary"]:
            status[txn["excelRow"]] = "MISSING"

        for pair in result["exceptions"]["amountMismatches"]:
            status[pair["bankTransaction"]["excelRow"]] = "EXCEPTION"
        for pair in result["exceptions"]["typeMismatches"]:
            status[pair["bankTransaction"]["excelRow"]] = "EXCEPTION"
        for pair in result["signReversalHints"]:
            status[pair["bankTransaction"]["excelRow"]] = "EXCEPTION"

        for group in result["exceptions"]["groupedMatches"]:
            if group["direction"] == "TALLY_MANY_TO_BANK_ONE":
                status[group["oneTransaction"]["excelRow"]] = "MATCHED"
            else:
                for txn in group["manyTransactions"]:
                    status[txn["excelRow"]] = "MATCHED"

        return status

    @staticmethod
    def _collect_primary_records(pairwise_results: dict) -> dict:
        """A canonical excelRow -> primary-transaction-record map,
        built by unioning the primary side across every pairwise
        result — a single pairwise reconciliation already partitions
        100% of the primary file across matched/missing/exception
        categories exactly once, so this just needs one consistent
        source of record detail (date/reference/amount) regardless of
        which secondary a given row's status came from."""
        records = {}

        def add(row):
            records.setdefault(row["excelRow"], row)

        for result in pairwise_results.values():
            for m in result["matchedTransactions"]:
                add(m["bankTransaction"])
            for txn in result["missingInPrimary"]:
                add(txn)
            for pair in result["exceptions"]["amountMismatches"]:
                add(pair["bankTransaction"])
            for pair in result["exceptions"]["typeMismatches"]:
                add(pair["bankTransaction"])
            for pair in result["signReversalHints"]:
                add(pair["bankTransaction"])
            for group in result["exceptions"]["groupedMatches"]:
                if group["direction"] == "TALLY_MANY_TO_BANK_ONE":
                    add(group["oneTransaction"])
                else:
                    for txn in group["manyTransactions"]:
                        add(txn)

        return records

    def _build_cross_source_breakdown(self, primary_records: dict, status_by_source: dict, source_names: list):
        # Group every primary row by the exact set of secondary sources
        # where it's a "problem" (MISSING or EXCEPTION, i.e. not a clean
        # MATCHED) — this generalizes to any N and directly answers the
        # most useful multi-source question: "is this row fine
        # everywhere except one specific system?"
        buckets = defaultdict(list)

        for excel_row in primary_records:
            problem_sources = tuple(
                name for name in source_names
                if status_by_source[name].get(excel_row, "MISSING") != "MATCHED"
            )
            buckets[problem_sources].append(excel_row)

        breakdown = []
        samples = {}
        for problem_sources, rows in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
            label = self._label_for_bucket(problem_sources, source_names)
            breakdown.append({
                "problemSources": list(problem_sources),
                "label": label,
                "count": len(rows),
            })
            sample_rows = rows[:MAX_SAMPLES_PER_BUCKET]
            samples[label] = [self._to_sample(primary_records[r]) for r in sample_rows]

        return breakdown, samples

    @staticmethod
    def _label_for_bucket(problem_sources: tuple, all_sources: list) -> str:
        if not problem_sources:
            return "Fully reconciled across all sources"
        if len(problem_sources) == len(all_sources):
            return "Missing or mismatched in every secondary source"
        if len(problem_sources) == 1:
            return f"Missing or mismatched only in {problem_sources[0]}"
        return f"Missing or mismatched in {', '.join(problem_sources)}"

    @staticmethod
    def _to_sample(record: dict) -> dict:
        return {
            "excelRow": record.get("excelRow"),
            "date": record.get("date"),
            "reference": record.get("reference"),
            "amount": record.get("amount"),
            "transactionType": record.get("transactionType"),
        }

    @staticmethod
    def _build_summary(primary_records: dict, pairwise_results: dict, breakdown: list) -> dict:
        # NOTE on "partiallyReconciled" with a single secondary source:
        # with N=1, "missing from the only secondary" IS "missing from
        # every secondary" — there is no partial state possible, so such
        # a row is correctly counted as unreconciled, not partial.
        # partiallyReconciled only becomes reachable once there are 2+
        # secondaries and a row's problem-set is a strict, non-empty
        # subset of all of them.
        total = len(primary_records)
        fully_reconciled = next((b["count"] for b in breakdown if not b["problemSources"]), 0)
        unreconciled = next(
            (b["count"] for b in breakdown if len(b["problemSources"]) == len(pairwise_results)), 0
        )
        partially_reconciled = total - fully_reconciled - unreconciled

        per_source = {}
        for name, result in pairwise_results.items():
            s = result["summary"]
            per_source[name] = {
                "matched": s["matchedTransactions"],
                "missing": s["missingInPrimary"],
                "matchPercentage": s["matchPercentage"],
                "amountMismatches": s["amountMismatches"],
                "typeMismatches": s["typeMismatches"],
                "duplicates": s["duplicateSecondary"],
            }

        return {
            "totalPrimaryTransactions": total,
            "fullyReconciled": fully_reconciled,
            "partiallyReconciled": partially_reconciled,
            "unreconciled": unreconciled,
            "perSource": per_source,
        }
