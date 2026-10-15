"""
Phase 1 — Exception Feature Extraction.

────────────────────────────────────────────────────────────────────
Single responsibility: convert the exception records already produced
by ReconciliationService.reconcile() into a standardized, analytical
ExceptionFeature representation.

This module NEVER:
  - decides whether two transactions match (that's MatchingEngine's
    job, already done by the time anything here runs)
  - re-parses amounts/dates from raw strings (the reconciliation
    service's `_clean_amount` / `_clean_date` already normalized every
    Transaction dict this consumes — amounts are floats rounded to 2
    decimals, dates are "YYYY-MM-DD" strings)
  - discovers patterns (that's Phase 2, patterns.py)

INPUT CONTRACT
──────────────
This consumes exactly the shape returned by
`ReconciliationService.reconcile()` — specifically the sub-objects
under "exceptions", "missingInBank", "missingInTally", "duplicates",
and "signReversalHints". See parser/services/reconciliationService.py
and frontend/src/features/reconciliation/types.ts (Transaction,
ExceptionPair, GroupedMatch) for the authoritative field shapes this
was built against.

A NOTE ON IDENTITY
──────────────────
The current data model has no persisted, cross-run transaction ID —
reconciliation is stateless (no DB row is written per transaction).
The only stable identifier available is `excelRow`, which is only
unique *within one side of one uploaded file*. So:

  - Every ExceptionFeature gets a synthetic `exception_id`, unique
    within the analysis run, built from the exception type plus the
    excelRow(s) of the transaction(s) involved (e.g.
    "AMOUNT_MISMATCH:B214:T087").
  - bank_excel_row / tally_excel_row are carried through separately
    so the frontend can still say "open bank row 214".

If/when reconciliation runs are persisted (mentioned as a future
possibility in parser/permissions.py's IsAccountantOrAdmin docstring),
a real transaction_id can replace this without changing anything else
in Phase 1 or Phase 2 — extraction only touches `_identity()` below.

A NOTE ON LEDGER FIELDS
───────────────────────
The prompt/spec that motivated this module anticipated bank_ledger /
tally_ledger fields. The current parsers (parser/parsers/*.py,
genericBank.json / genericTally.json) do not capture a ledger/account
column — only date, reference, description, amount, transactionType.
Rather than inventing ledger data that doesn't exist, `ledger_match`
and the ledger fields are always `None` for now. The extractor is
written so that wiring in a real ledger column later (should the
parsers start capturing one) only requires updating `_ledger_of()`.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional, Any


# ────────────────────────────────────────────────────────────────
# Exception type vocabulary
# ────────────────────────────────────────────────────────────────

class ExceptionType:
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    GROUPED_MATCH = "GROUPED_MATCH"
    MISSING_IN_TALLY = "MISSING_IN_TALLY"
    MISSING_IN_BANK = "MISSING_IN_BANK"
    DUPLICATE_BANK = "DUPLICATE_BANK"
    DUPLICATE_TALLY = "DUPLICATE_TALLY"
    SIGN_REVERSAL_HINT = "SIGN_REVERSAL_HINT"

    ALL = (
        AMOUNT_MISMATCH, TYPE_MISMATCH, GROUPED_MATCH,
        MISSING_IN_TALLY, MISSING_IN_BANK,
        DUPLICATE_BANK, DUPLICATE_TALLY, SIGN_REVERSAL_HINT,
    )


# ────────────────────────────────────────────────────────────────
# Output model
# ────────────────────────────────────────────────────────────────

@dataclass
class SideSnapshot:
    """One side (bank or tally) of an exception, or all-None if that
    side genuinely has no row (e.g. missing-in-tally has no tally side).
    """
    exists: bool = False
    excel_row: Optional[int] = None
    amount: Optional[float] = None
    date: Optional[str] = None
    reference: Optional[str] = None
    ledger: Optional[str] = None
    transaction_type: Optional[str] = None
    description: Optional[str] = None


@dataclass
class ExceptionFeature:
    exception_id: str
    exception_type: str

    bank: SideSnapshot
    tally: SideSnapshot

    features: dict = field(default_factory=dict)

    # Present only for GROUPED_MATCH (many-to-one) records — the
    # transactions on the "many" side, kept separately since they
    # don't fit the bank/tally 1:1 shape every other exception type
    # uses. Empty list for every other exception type.
    group_member_excel_rows: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ────────────────────────────────────────────────────────────────
# Extractor
# ────────────────────────────────────────────────────────────────

class ExceptionFeatureExtractor:
    """
    extract(exception_type, record) -> ExceptionFeature
    extract_all(reconciliation_exceptions) -> list[ExceptionFeature]

    `reconciliation_exceptions` is the dict of exception-shaped
    sub-objects a client already received from POST /api/v1/reconcile/
    (see module docstring). This class does not fetch or recompute
    reconciliation state — it only reshapes what's handed to it.
    """

    # Two amounts are treated as "the same" only after currency
    # rounding (already applied by ReconciliationService), so an exact
    # float comparison here is safe and intentional — no new tolerance
    # is being introduced.
    AMOUNT_EQUALITY_EPSILON = 0.005

    def extract_all(self, reconciliation_exceptions: dict) -> list[ExceptionFeature]:
        out: list[ExceptionFeature] = []

        exceptions = reconciliation_exceptions.get("exceptions", {}) or {}
        for pair in exceptions.get("amountMismatches", []) or []:
            out.append(self._from_pair(ExceptionType.AMOUNT_MISMATCH, pair))
        for pair in exceptions.get("typeMismatches", []) or []:
            out.append(self._from_pair(ExceptionType.TYPE_MISMATCH, pair))
        for group in exceptions.get("groupedMatches", []) or []:
            out.append(self._from_group(group))

        for txn in reconciliation_exceptions.get("missingInTally", []) or []:
            out.append(self._from_single(ExceptionType.MISSING_IN_TALLY, txn))
        for txn in reconciliation_exceptions.get("missingInBank", []) or []:
            out.append(self._from_single(ExceptionType.MISSING_IN_BANK, txn))

        duplicates = reconciliation_exceptions.get("duplicates", {}) or {}
        for txn in duplicates.get("bank", []) or []:
            out.append(self._from_single(ExceptionType.DUPLICATE_BANK, txn))
        for txn in duplicates.get("tally", []) or []:
            out.append(self._from_single(ExceptionType.DUPLICATE_TALLY, txn))

        for pair in reconciliation_exceptions.get("signReversalHints", []) or []:
            out.append(self._from_pair(ExceptionType.SIGN_REVERSAL_HINT, pair))

        return out

    def extract(self, exception_type: str, record: Any) -> ExceptionFeature:
        """Single-record convenience API, dispatching on type. Useful
        for unit tests and for future callers that already know the
        type of one record they want featurized."""
        if exception_type == ExceptionType.GROUPED_MATCH:
            return self._from_group(record)
        if exception_type in (
            ExceptionType.MISSING_IN_TALLY, ExceptionType.MISSING_IN_BANK,
            ExceptionType.DUPLICATE_BANK, ExceptionType.DUPLICATE_TALLY,
        ):
            return self._from_single(exception_type, record)
        return self._from_pair(exception_type, record)

    # ────────────────────────────────────────────────────────────
    # Per-shape builders
    # ────────────────────────────────────────────────────────────

    def _from_pair(self, exception_type: str, pair: dict) -> ExceptionFeature:
        bank_txn = pair.get("bankTransaction") or {}
        tally_txn = pair.get("tallyTransaction") or {}

        bank = self._side(bank_txn, exists=True)
        tally = self._side(tally_txn, exists=True)

        exception_id = (
            f"{exception_type}:B{bank.excel_row}:T{tally.excel_row}"
        )

        features = self._numeric_and_boolean_features(bank, tally)
        features["duplicate_flag"] = False
        features["sign_reversal_flag"] = exception_type == ExceptionType.SIGN_REVERSAL_HINT

        return ExceptionFeature(
            exception_id=exception_id,
            exception_type=exception_type,
            bank=bank,
            tally=tally,
            features=features,
        )

    def _from_single(self, exception_type: str, txn: dict) -> ExceptionFeature:
        is_bank_side = exception_type in (
            ExceptionType.MISSING_IN_TALLY, ExceptionType.DUPLICATE_BANK,
        )

        present = self._side(txn, exists=True)
        absent = SideSnapshot(exists=False)

        bank = present if is_bank_side else absent
        tally = absent if is_bank_side else present

        exception_id = (
            f"{exception_type}:{'B' if is_bank_side else 'T'}{present.excel_row}"
        )

        features = self._numeric_and_boolean_features(bank, tally)
        features["duplicate_flag"] = exception_type in (
            ExceptionType.DUPLICATE_BANK, ExceptionType.DUPLICATE_TALLY,
        )
        features["sign_reversal_flag"] = False

        return ExceptionFeature(
            exception_id=exception_id,
            exception_type=exception_type,
            bank=bank,
            tally=tally,
            features=features,
        )

    def _from_group(self, group: dict) -> ExceptionFeature:
        direction = group.get("direction")
        one_txn = group.get("oneTransaction") or {}
        many_txns = group.get("manyTransactions") or []

        one_is_bank = direction == "TALLY_MANY_TO_BANK_ONE"

        one_side = self._side(one_txn, exists=True)
        # The "many" side has no single representative row — record
        # its excel rows separately rather than forcing a fake single
        # SideSnapshot.
        many_amount_sum = round(
            sum((t.get("amount") or 0.0) for t in many_txns), 2
        )
        many_side = SideSnapshot(
            exists=True,
            excel_row=None,
            amount=many_amount_sum,
            date=one_txn.get("date"),  # grouped matches are same-date by construction
            reference=None,
            ledger=None,
            transaction_type=None,
            description=f"{len(many_txns)} transactions summing to {many_amount_sum}",
        )

        bank = one_side if one_is_bank else many_side
        tally = many_side if one_is_bank else one_side

        member_rows = [t.get("excelRow") for t in many_txns]
        exception_id = (
            f"{ExceptionType.GROUPED_MATCH}:{direction}:"
            f"{'B' if one_is_bank else 'T'}{one_side.excel_row}"
        )

        features = self._numeric_and_boolean_features(bank, tally)
        features["duplicate_flag"] = False
        features["sign_reversal_flag"] = False
        features["group_size"] = len(many_txns)
        features["group_direction"] = direction

        return ExceptionFeature(
            exception_id=exception_id,
            exception_type=ExceptionType.GROUPED_MATCH,
            bank=bank,
            tally=tally,
            features=features,
            group_member_excel_rows=member_rows,
        )

    # ────────────────────────────────────────────────────────────
    # Shared helpers
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _side(txn: dict, exists: bool) -> SideSnapshot:
        if not txn:
            return SideSnapshot(exists=False)
        return SideSnapshot(
            exists=exists,
            excel_row=txn.get("excelRow"),
            amount=txn.get("amount"),
            date=txn.get("date"),
            reference=txn.get("reference"),
            ledger=None,  # see module docstring: not captured upstream yet
            transaction_type=txn.get("transactionType"),
            description=txn.get("description"),
        )

    def _numeric_and_boolean_features(self, bank: SideSnapshot, tally: SideSnapshot) -> dict:
        """
        Every feature here is purpose-built for what Phase 2 needs to
        find recurring patterns — see the rationale table in
        ARCHITECTURE.md for why each one is here (nothing is added
        "just in case").
        """
        f: dict[str, Any] = {
            "bank_exists": bank.exists,
            "tally_exists": tally.exists,
            "amount_difference": None,
            "absolute_amount_difference": None,
            "percentage_difference": None,
            "date_difference_days": None,
            "signed_date_offset_days": None,
            "reference_match": None,
            "amount_match": None,
            "date_match": None,
            "ledger_match": None,  # always None until a ledger column exists upstream
            "transaction_type_bank": bank.transaction_type,
            "transaction_type_tally": tally.transaction_type,
        }

        if bank.exists and tally.exists:
            if bank.amount is not None and tally.amount is not None:
                diff = round(bank.amount - tally.amount, 2)
                f["amount_difference"] = diff
                f["absolute_amount_difference"] = abs(diff)
                f["amount_match"] = abs(diff) <= self.AMOUNT_EQUALITY_EPSILON
                # Percentage relative to the tally side (the "expected"
                # ledger value) — guarded against a zero denominator,
                # which a naive diff/base calculation would otherwise
                # turn into inf/NaN and silently corrupt every
                # downstream statistic.
                if tally.amount not in (0, None):
                    f["percentage_difference"] = round((diff / tally.amount) * 100, 4)
                else:
                    f["percentage_difference"] = None

            if bank.date and tally.date:
                signed_days = _days_between(bank.date, tally.date)
                if signed_days is not None:
                    f["signed_date_offset_days"] = signed_days
                    f["date_difference_days"] = abs(signed_days)
                    f["date_match"] = signed_days == 0

            if bank.reference is not None and tally.reference is not None:
                f["reference_match"] = (
                    bank.reference.strip().upper() == tally.reference.strip().upper()
                    if bank.reference and tally.reference else False
                )

        return f


def _days_between(date_a: str, date_b: str) -> Optional[int]:
    """date_a - date_b in days, both 'YYYY-MM-DD' strings (the format
    ReconciliationService already normalizes every date to)."""
    from datetime import date as _date

    try:
        a = _date.fromisoformat(date_a)
        b = _date.fromisoformat(date_b)
        return (a - b).days
    except (ValueError, TypeError):
        return None
