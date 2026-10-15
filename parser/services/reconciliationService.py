import numpy as np
import pandas as pd
from collections import defaultdict
import re   
from parser.parsers.csvParser import FileLoader
from .configLoader import ConfigLoader
from .semanticColumnMapper import map_unknown_columns


class ReconciliationService:
    # Did the changes at 110 to 119 line for date consideration if not found in config 
    """
    Reconciles a bank statement against a tally export.

    ── Design notes ──────────────────────────────────────────────────
    A senior accountant doesn't reconcile in a single "matched vs.
    missing" pass. They work through a priority-ordered waterfall:

      1. REFERENCE               reference + amount + type + date all agree
      2. REFERENCE_DATE_TOLERANCE
                                  same reference, amount, type — date is
                                  off by a few days (value-date vs.
                                  posting-date lag, weekend clearing, etc.)
      3. TYPE_MISMATCH            same reference + amount, but one side
                                  says DEBIT and the other CREDIT — usually
                                  a misclassification, needs a human look
      4. AMOUNT_MISMATCH          same reference, different amount — a
                                  partial payment, bank charge deduction,
                                  or data-entry typo
      5. AMOUNT_DATE              no usable reference on one side — fall
                                  back to matching by amount + type, with
                                  date within tolerance
      6. (informational only) SIGN_REVERSAL_HINT — same amount and date,
                                  opposite type, on two rows that would
                                  otherwise stay unmatched.
      7. Whatever is left over after all of the above is genuinely
         missing from the other side — UNLESS it's the leftover half
         of a same-file duplicate pair, in which case it's flagged as
         a duplicate artifact rather than a real gap (see
         _annotate_duplicate_artifacts).

    Matching uses an index + "claimed" tracking loop rather than
    pd.merge on the match columns, specifically because merging on
    non-unique keys cartesian-explodes whenever either file has
    duplicate-looking rows. The loop guarantees strict one-to-one
    pairing regardless of duplicates, and does it fast: columns are
    pulled to plain Python lists once up front, since per-row
    .iat[]/.iloc[] access inside a loop carries real pandas overhead
    at scale (this was the difference between ~30s and ~3s at 100K
    rows in testing).

    Header mapping is config-driven (genericBank.json / genericTally.json,
    loaded via ConfigLoader) rather than hardcoded, and supports THREE
    real-world amount formats, resolved per row in this priority order:
      a) a unified Amount column + a separate TransactionType/DR-CR column
      b) split Debit/Credit columns (type inferred from which has a value)
      c) a unified Amount column alone (type left unknown — treated as a
         wildcard during matching, never a false mismatch)
    ────────────────────────────────────────────────────────────────
    """

    REQUIRED_CANONICAL_FIELDS = {
            "date",
        }

    AMOUNT_CANONICAL_FIELDS = {
        "amount",
        "debit",
        "credit",
    }

    BANK_CONFIG_FILE = "genericBank.json"
    TALLY_CONFIG_FILE = "genericTally.json"

    DATE_TOLERANCE_DAYS = 2

    # Canonicalizes whatever shorthand a file uses for debit/credit.
    TYPE_ALIASES = {
        "DR": "DEBIT", "D": "DEBIT", "DEBIT": "DEBIT",
        "WITHDRAWAL": "DEBIT", "WITHDRAW": "DEBIT",
        "CR": "CREDIT", "C": "CREDIT", "CREDIT": "CREDIT", "DEPOSIT": "CREDIT",
    }

    OUTPUT_COLUMNS = ["date", "reference", "description", "amount", "transactionType", "excelRow"]

    def __init__(self):
        self.bank = None
        self.tally = None

        self.bank_config = ConfigLoader.load(self.BANK_CONFIG_FILE)
        self.tally_config = ConfigLoader.load(self.TALLY_CONFIG_FILE)

        self.warnings = []

        self.bank_dropped = 0
        self.tally_dropped = 0

    # ────────────────────────────────────────────────────────────
    # Loading & header mapping
    # ────────────────────────────────────────────────────────────

    def _load_files(self, bank_file, tally_file):
        self.bank = FileLoader.load(bank_file)
        self.tally = FileLoader.load(tally_file)

    @staticmethod
    def _normalize_key(text):
        text = str(text).strip().lower()
        text = re.sub(r"[_\-]+", " ", text)
        text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
        text = re.sub(r"\s+", " ", text)
        return text

    @classmethod
    def _normalize_headers(cls, df, config):
        rename_map = {}
        header_mapping = config["headerMapping"]
        for column in df.columns:
            key = cls._normalize_key(column)
            for standard, aliases in header_mapping.items():
                normalized_aliases = [cls._normalize_key(a) for a in aliases]
                if key == standard.lower() or key in normalized_aliases:
                    rename_map[column] = standard
                    break
                if standard == "date" and re.search(r"\bdate\b", key):
                    rename_map[column] = standard
                    break
        return df.rename(columns=rename_map)
    
    def _apply_semantic_column_mapping(self, df, config=None):
        """
        Apply semantic/NLP mapping only to columns that were not
        recognized by deterministic header aliases.

        config (optional) supplies this file's deterministic
        alias lists, which are forwarded to the mapper so the
        fuzzy name pre-pass has the same vocabulary the alias
        layer uses.

        Returns:
            normalized_df, recognition_report
        """

        canonical_fields = {
            "date",
            "reference",
            "description",
            "voucher",
            "ledger",
            "debit",
            "credit",
            "amount",
            "transactionType",
            "balance",
        }

        known_columns = {
            column
            for column in df.columns
            if column in canonical_fields
        }

        alias_map = None
        if config:
            header_mapping = config.get("headerMapping")
            if header_mapping:
                alias_map = {
                    standard: list(aliases)
                    for standard, aliases in header_mapping.items()
                }

        result = map_unknown_columns(
            df,
            known_columns,
            alias_map,
        )

        semantic_mapping = result["mapping"]
        recognition_report = result["report"]

        if semantic_mapping:
            df = df.rename(columns=semantic_mapping)

        return df, recognition_report
    # def _validate_columns(self, df, config, label):
    #     columns = set(df.columns)

    #     required = config.get("requiredHeaders", {})

    #     critical_missing = [c for c in required.get("critical", []) if c not in columns]
    #     if critical_missing:
    #         raise ValueError(
    #             f"{label} file is missing required column(s): "
    #             f"{', '.join(critical_missing)}"
    #         )

    #     # Matching is meaningless without some amount signal, regardless
    #     # of which of the three supported shapes it comes in.
    #     if not ({"debit", "credit", "amount"} & columns):
    #         config_file = self.BANK_CONFIG_FILE if label == "Bank" else self.TALLY_CONFIG_FILE
    #         raise ValueError(
    #             f"{label} file has no Amount, Debit, or Credit column. "
    #             f"If your file uses a different header name, add it to "
    #             f"the alias list in {config_file} and re-upload — no "
    #             f"code change needed."
    #         )

    #     recommended_missing = [
    #         c for c in required.get("recommended", []) if c not in columns
    #     ]
    #     if recommended_missing:
    #         self.warnings.append(
    #             f"{label} file has no {', '.join(recommended_missing)} "
    #             f"column(s) — matching will proceed with reduced "
    #             f"confidence checks where that data would have helped."
    #         )

    def _validate_columns(
        self,
        df,
        config,
        label,
        semantic_report=None,
    ):
        columns = set(df.columns)
        required = config.get("requiredHeaders", {})

        # ========================================================
        # 1. Critical / required columns
        # ========================================================

        critical_missing = [
            c
            for c in required.get("critical", [])
            if c not in columns
        ]

        if critical_missing:

            details = []

            if semantic_report:

                for column_name, result in semantic_report.items():

                    if result.get("status") not in {
                        "ambiguous",
                        "unrecognized",
                        "collision",
                    }:
                        continue

                    candidates = result.get("candidates", [])

                    if not candidates:
                        continue

                    best_candidate = candidates[0]

                    if best_candidate["field"] in critical_missing:

                        details.append(
                            f"'{column_name}' was the closest candidate "
                            f"for '{best_candidate['field']}' "
                            f"(confidence "
                            f"{best_candidate['score']:.2f})"
                        )

            if details:
                raise ValueError(
                    f"{label} file is missing required column(s): "
                    f"{', '.join(critical_missing)}. "
                    f"Semantic recognition was not confident enough: "
                    f"{'; '.join(details)}."
                )

            raise ValueError(
                f"{label} file is missing required column(s): "
                f"{', '.join(critical_missing)}"
            )

        # ========================================================
        # 2. Monetary signal
        # ========================================================

        amount_fields = {
            "amount",
            "debit",
            "credit",
        }

        if not (amount_fields & columns):

            details = []

            if semantic_report:

                for column_name, result in semantic_report.items():

                    candidates = result.get("candidates", [])

                    if not candidates:
                        continue

                    best_candidate = candidates[0]

                    if best_candidate["field"] in amount_fields:

                        details.append(
                            f"'{column_name}' was the closest candidate "
                            f"for '{best_candidate['field']}' "
                            f"(confidence "
                            f"{best_candidate['score']:.2f})"
                        )

            if details:
                raise ValueError(
                    f"{label} file has no recognized monetary "
                    f"column (Amount, Debit, or Credit). "
                    f"Semantic recognition was not confident enough: "
                    f"{'; '.join(details)}."
                )

            raise ValueError(
                f"{label} file has no recognized monetary "
                f"column (Amount, Debit, or Credit). "
                f"The semantic column mapper could not identify "
                f"a suitable monetary field."
            )

        # ========================================================
        # 3. Recommended columns
        # ========================================================

        recommended_missing = [
            c
            for c in required.get("recommended", [])
            if c not in columns
        ]

        if recommended_missing:

            self.warnings.append(
                f"{label} file has no "
                f"{', '.join(recommended_missing)} "
                f"column(s) — matching will proceed with reduced "
                f"confidence checks where that data would have helped."
            )
    # ────────────────────────────────────────────────────────────
    # Field cleaning
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_amount(series):
        cleaned = (
            series.fillna("").astype(str)
            .str.strip()
            .str.replace("₹", "", regex=False)
            .str.replace("Rs.", "", regex=False)
            .str.replace("Rs", "", regex=False)
            .str.replace(",", "", regex=False)
        )

        cleaned = cleaned.replace({
            "": None,
            "nan": None,
            "NaN": None,
            "None": None,
            "<NA>": None,
        })

        is_negative_paren = (
            cleaned.fillna("")
            .str.startswith("(")
            .fillna(False)
            & cleaned.fillna("")
            .str.endswith(")")
            .fillna(False)
        )
        cleaned = (
            cleaned.fillna("")
            .str.replace("(", "", regex=False)
            .fillna("")
            .str.replace(")", "", regex=False)
            .fillna("")
        )
        cleaned = cleaned.where(is_negative_paren, cleaned.str.replace("-", "", regex=False))

        numeric = pd.to_numeric(cleaned, errors="coerce")
        numeric = numeric.where(~is_negative_paren, -numeric)

        # Round to currency precision so floating-point drift (99.999999999
        # vs 100.0) never causes a false mismatch downstream.
        return numeric.round(2)

    @staticmethod
    def _clean_date(series):
        """
        Robust date parser.

        Supports:
        - Excel datetime
        - Excel serial numbers
        - DD-MM-YYYY
        - DD/MM/YYYY
        - MM/DD/YYYY
        - YYYY-MM-DD
        - DD-Mon-YYYY

        Returns a normalized datetime Series.
        """

        # -----------------------------------------
        # Already datetime
        # -----------------------------------------

        if pd.api.types.is_datetime64_any_dtype(series):
            return series.dt.normalize()

        # -----------------------------------------
        # Clean values
        # -----------------------------------------

        cleaned = (
            series.fillna("")
            .astype(str)
            .str.strip()
            .replace({
                "": pd.NA,
                "nan": pd.NA,
                "NaN": pd.NA,
                "None": pd.NA,
                "<NA>": pd.NA,
            })
        )

        # -----------------------------------------
        # Excel serial numbers
        # (only if ALL non-null values are numeric)
        # -----------------------------------------

        non_null = cleaned.dropna()

        if (
            len(non_null) > 0
            and non_null.astype(str)
            .str.fullmatch(r"\d+(\.\d+)?")
            .all()
        ):
            return (
                pd.to_datetime(
                    non_null.astype(float),
                    unit="D",
                    origin="1899-12-30",
                    errors="coerce",
                )
                .reindex(series.index)
                .dt.normalize()
            )

        # -----------------------------------------
        # Detect slash dates
        # -----------------------------------------

        slash = non_null[non_null.str.contains("/", regex=False)]

        if not slash.empty:

            # Same check the dash branch below already does: a leading
            # 4-digit component can only be a year, never a day or
            # month, so YYYY/MM/DD must be checked for FIRST — before
            # the day>12 heuristic below, which would otherwise
            # misread a year like "2026" as "day > 12, so this must be
            # DD/MM/YYYY" and silently produce NaT for every row (that
            # exact failure was confirmed against real ISO-with-slashes
            # data, e.g. "2026/01/15", before this check was added).
            sample = slash.iloc[0]
            if len(sample.split("/")[0]) == 4:

                return pd.to_datetime(
                    cleaned,
                    format="%Y/%m/%d",
                    errors="coerce",
                ).dt.normalize()

            first = []
            second = []

            for value in slash.head(30):

                try:
                    a, b, _ = value.split("/")

                    first.append(int(a))
                    second.append(int(b))

                except Exception:
                    continue

            if any(x > 12 for x in first):

                return pd.to_datetime(
                    cleaned,
                    format="%d/%m/%Y",
                    errors="coerce",
                ).dt.normalize()

            if any(x > 12 for x in second):

                return pd.to_datetime(
                    cleaned,
                    format="%m/%d/%Y",
                    errors="coerce",
                ).dt.normalize()

            # Ambiguous
            return pd.to_datetime(
                cleaned,
                dayfirst=True,
                errors="coerce",
            ).dt.normalize()

        # -----------------------------------------
        # Dash separated
        # -----------------------------------------

        dash = non_null[non_null.str.contains("-", regex=False)]

        if not dash.empty:

            sample = dash.iloc[0]

            if len(sample.split("-")[0]) == 4:

                return pd.to_datetime(
                    cleaned,
                    format="%Y-%m-%d",
                    errors="coerce",
                ).dt.normalize()

            if any(month.isalpha() for month in sample.split("-")):

                return pd.to_datetime(
                    cleaned,
                    format="%d-%b-%Y",
                    errors="coerce",
                ).dt.normalize()

            # Same verification the slash branch does above — don't
            # assume DD-MM-YYYY just because it's the more common
            # convention. Only trust it when the data actually proves
            # it (a component > 12 that can only be a day).
            first, second = [], []
            for value in dash.head(30):
                try:
                    a, b, _ = value.split("-")
                    first.append(int(a))
                    second.append(int(b))
                except Exception:
                    continue

            if any(x > 12 for x in second):
                return pd.to_datetime(
                    cleaned,
                    format="%m-%d-%Y",
                    errors="coerce",
                ).dt.normalize()

            # Either DD-MM-YYYY is confirmed (some first > 12) or it's
            # genuinely ambiguous — DD-MM-YYYY is the more common
            # convention outside the US, so it's the reasonable default
            # for an unverified case, but this is a guess, not proof.
            return pd.to_datetime(
                cleaned,
                format="%d-%m-%Y",
                errors="coerce",
            ).dt.normalize()

        # -----------------------------------------
        # Final fallback
        # -----------------------------------------

        return pd.to_datetime(
            cleaned,
            errors="coerce",
            dayfirst=True,
        ).dt.normalize()

    # @staticmethod
    # def _clean_date(series):
    #     return pd.to_datetime(series,format="%d-%m-%Y", errors="coerce").dt.normalize()

    # @staticmethod
    # def _clean_date(series):
        """
        Smart date parser for ReconAI.

        Supports:
        ✓ DD-MM-YYYY
        ✓ DD/MM/YYYY
        ✓ MM/DD/YYYY
        ✓ YYYY-MM-DD
        ✓ DD-Mon-YYYY
        ✓ Excel datetime
        ✓ Excel serial dates

        Uses only ONE parse for the entire column.
        """

        # ---------------------------------------
        # Already datetime
        # ---------------------------------------

        if pd.api.types.is_datetime64_any_dtype(series):
            return series.dt.normalize()

        # ---------------------------------------
        # Clean values
        # ---------------------------------------

        cleaned = (
            series.fillna("")
            .astype(str)
            .str.strip()
            .replace({
                "": None,
                "nan": None,
                "NaN": None,
                "None": None,
                "<NA>": None
            })
        )

        # ---------------------------------------
        # Excel Serial Dates
        # ---------------------------------------

        numeric = pd.to_numeric(cleaned, errors="coerce")

        if numeric.notna().all():

            return pd.to_datetime(
                numeric,
                origin="1899-12-30",
                unit="D"
            ).dt.normalize()

        # ---------------------------------------
        # Take first few samples
        # ---------------------------------------

        sample = cleaned.dropna().head(30)

        if sample.empty:
            return pd.Series(pd.NaT, index=series.index)

        first = sample.iloc[0]

        # ---------------------------------------
        # YYYY-MM-DD
        # ---------------------------------------

        if re.match(r"^\d{4}-\d{2}-\d{2}$", first):

            return pd.to_datetime(
                cleaned,
                format="%Y-%m-%d",
                errors="coerce"
            ).dt.normalize()

        # ---------------------------------------
        # DD-Mon-YYYY
        # ---------------------------------------

        if re.match(r"^\d{2}-[A-Za-z]{3}-\d{4}$", first):

            return pd.to_datetime(
                cleaned,
                format="%d-%b-%Y",
                errors="coerce"
            ).dt.normalize()

        # ---------------------------------------
        # Dash separated
        # Example:
        # 14-01-2026
        # ---------------------------------------

        if "-" in first:

            return pd.to_datetime(
                cleaned,
                format="%d-%m-%Y",
                errors="coerce"
            ).dt.normalize()

        # ---------------------------------------
        # Slash separated
        # Need to determine whether
        # DD/MM or MM/DD
        # ---------------------------------------

        if "/" in first:

            first_numbers = []
            second_numbers = []

            for value in sample:

                parts = value.split("/")

                if len(parts) != 3:
                    continue

                try:
                    first_numbers.append(int(parts[0]))
                    second_numbers.append(int(parts[1]))
                except:
                    continue

            # Clearly DD/MM/YYYY

            if any(x > 12 for x in first_numbers):

                return pd.to_datetime(
                    cleaned,
                    format="%d/%m/%Y",
                    errors="coerce"
                ).dt.normalize()

            # Clearly MM/DD/YYYY

            if any(x > 12 for x in second_numbers):

                return pd.to_datetime(
                    cleaned,
                    format="%m/%d/%Y",
                    errors="coerce"
                ).dt.normalize()

            # Ambiguous:
            # Default to Indian banking.

            return pd.to_datetime(
                cleaned,
                dayfirst=True,
                errors="coerce"
            ).dt.normalize()

        # ---------------------------------------
        # Final fallback
        # ---------------------------------------

        return pd.to_datetime(
            cleaned,
            dayfirst=True,
            errors="coerce"
        ).dt.normalize()

    @staticmethod
    def _clean_ref_raw(series):
        cleaned = series.fillna("").astype(str)
        cleaned = cleaned.str.strip().str.upper()
        cleaned = cleaned.replace({"NAN": "", "NONE": "", "<NA>": ""})
        cleaned = cleaned.str.replace(r"[\s\-/]", "", regex=True)
        return cleaned

    @staticmethod
    def _clean_reference(series):
        cleaned = series.fillna("").astype(str)
        cleaned = cleaned.str.strip().str.upper()
        cleaned = cleaned.replace({"NAN": "", "NONE": "", "<NA>": ""})
        cleaned = cleaned.str.replace(r"[\s\-/]", "", regex=True)
        return cleaned

    @classmethod
    def _clean_type(cls, series):
        cleaned = series.fillna("").astype(str)
        cleaned = cleaned.str.strip().str.upper()
        cleaned = cleaned.map(
            lambda v: cls.TYPE_ALIASES.get(v, v if v not in ("NAN", "NONE") else None)
        )
        return cleaned

    def _resolve_amount_type(self, df):
        """
        Three supported shapes, checked in this order:

          a) Amount column + an explicit TransactionType/DR-CR column
             (this is what Bank_Transactions_10000.csv / Tally_Entries_
             10000.csv actually use)
          b) Split Debit/Credit columns, resolved per row (a real
             statement has ONE of the two populated per row, not a
             file-wide constant — earlier code got this wrong)
          c) Amount column alone — type stays unknown/wildcard rather
             than guessed
        """

        has_amount = "amount" in df.columns
        has_explicit_type = "transactionType" in df.columns

        if has_amount and has_explicit_type:
            amount = self._clean_amount(df["amount"])
            ttype = self._clean_type(df["transactionType"])
            return amount, ttype

        debit = self._clean_amount(df["debit"]) if "debit" in df.columns else None
        credit = self._clean_amount(df["credit"]) if "credit" in df.columns else None
        plain_amount = self._clean_amount(df["amount"]) if has_amount else None

        amount = pd.Series(np.nan, index=df.index)
        ttype = pd.Series([None] * len(df), index=df.index, dtype=object)

        if debit is not None:
            mask = debit.notna() & (debit != 0)
            amount = amount.where(~mask, debit)
            ttype = ttype.mask(mask, "DEBIT")

        if credit is not None:
            mask = credit.notna() & (credit != 0) & amount.isna()
            amount = amount.where(~mask, credit)
            ttype = ttype.mask(mask, "CREDIT")

        if plain_amount is not None:
            mask = plain_amount.notna() & amount.isna()
            amount = amount.where(~mask, plain_amount)
            # type genuinely unknown for these rows — left as None,
            # treated as a wildcard by _types_compatible, never a
            # manufactured mismatch.

        return amount, ttype

    def _clean(self, df, config, label):

        df = self._normalize_headers(df, config)

        df, semantic_report = self._apply_semantic_column_mapping(df, config)

        self._validate_columns(
            df,
            config,
            label,
            semantic_report,
        )

        df = df.copy()

        # Preserve the original spreadsheet row number (header = row 1)
        # before any rows get dropped, so results can still say "check
        # row 214" even after invalid rows are filtered out.

        df["excelRow"] = (df.index + 2).astype(int)

        df["date"] = self._clean_date(df["date"]) if "date" in df.columns else pd.NaT

        df["reference"] = (
            self._clean_reference(df["reference"]) if "reference" in df.columns else ""
        )

        amount, ttype = self._resolve_amount_type(df)
        df["amount"] = amount
        df["transactionType"] = ttype

        if "description" in df.columns:
            text_series = df["description"]
            filled = text_series.fillna("").astype(str)
            filled = filled.str.strip()
            df["description"] = filled
        else:
            df["description"] = ""

        # A row with no usable date or amount can't be matched by any
        # rule — drop it and report the count instead of letting it
        # silently poison results (e.g. matching by date coincidence).
        valid_mask = df["date"].notna() & df["amount"].notna()
        invalid_df = df[~valid_mask].copy()

        dropped = int((~valid_mask).sum())
        df = df[valid_mask].reset_index(drop=True)

        if not invalid_df.empty:
            invalid_df["date"] = (
                invalid_df["date"]
                .dt.strftime("%Y-%m-%d")
            )

            invalid_df = (
                invalid_df
                .astype(object)
                .where(pd.notna(invalid_df), None)
            )


        return df[self.OUTPUT_COLUMNS], dropped, invalid_df.to_dict("records")

    # ────────────────────────────────────────────────────────────
    # Duplicate detection (informational — same-file repeats)
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _to_display_records(df):
        # Same conversion the matching pass applies: Timestamp -> ISO
        # string, numpy.int64/float64 -> native Python types. Needed
        # here too since duplicate rows are sliced from the typed
        # DataFrame, not built from the already-converted match records.
        display = df.copy()
        display["date"] = display["date"].dt.strftime("%Y-%m-%d")
        display["excelRow"] = display["excelRow"].astype(int)
        display["amount"] = display["amount"].astype(float)
        return display.to_dict("records")

    @staticmethod
    def _find_duplicates(df):
        """
        Returns (all_involved_rows, extra_copy_count).

        all_involved_rows: every row that belongs to a group sharing
        identical reference/amount/date/type — used for the Duplicates
        tab so a reviewer can see each pair/group side by side.

        extra_copy_count: group size minus one, summed across groups —
        i.e. "how many redundant rows exist beyond the first legitimate
        one". This is the number surfaced in the summary card, because
        "all involved rows" reads as roughly double the actual number
        of duplicate incidents (a pair of 2 identical rows is one
        duplicate incident, not two).
        """

        match_columns = ["reference", "amount", "date", "transactionType"]

        working = df.copy()
        # An all-None transactionType column can drift dtype across a
        # groupby/duplicated round trip; a stable sentinel avoids that,
        # local to this comparison only.
        working["transactionType"] = working["transactionType"].fillna("UNSPECIFIED")

        involved_mask = working.duplicated(subset=match_columns, keep=False)
        extra_mask = working.duplicated(subset=match_columns, keep="first")

        return df[involved_mask], int(extra_mask.sum())

    # ────────────────────────────────────────────────────────────
    # Matching
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _types_compatible(type_a, type_b):
        if type_a is None or type_b is None or pd.isna(type_a) or pd.isna(type_b):
            return True
        return type_a == type_b

    def _find_grouped_matches(
        self,
        bank_claimed, tally_claimed,
        bank_amount, bank_date, bank_type, bank_records,
        tally_amount, tally_date, tally_type, tally_records,
    ):
        """
        Bounded brute-force subset-sum search for many-to-one matches
        on the same date, in both directions (many bank -> one tally,
        and many tally -> one bank).

        Full subset-sum is NP-hard in general, but real-world same-date
        clusters are small (a handful of transactions sharing a date is
        normal; hundreds is not), so this is bounded per-date rather
        than attempted globally:
          - MAX_CANDIDATES: dates with more unclaimed transactions than
            this on the "many" side are skipped entirely (too expensive,
            and too ambiguous to be a useful automatic suggestion anyway)
          - MAX_GROUP_SIZE: only subset sizes 2..6 are tried — beyond
            that a grouping is more likely coincidental than a genuine
            split transaction
        """
        import itertools

        MAX_CANDIDATES = 12
        MAX_GROUP_SIZE = 6

        bank_by_date = defaultdict(list)
        for pos in range(len(bank_amount)):
            if not bank_claimed[pos]:
                bank_by_date[bank_date[pos]].append(pos)

        tally_by_date = defaultdict(list)
        for pos in range(len(tally_amount)):
            if not tally_claimed[pos]:
                tally_by_date[tally_date[pos]].append(pos)

        shared_dates = set(bank_by_date) & set(tally_by_date)
        grouped_matches = []

        def find_group_summing_to(candidate_positions, amounts, target, target_type, types):
            # Smallest groups first — a 2-way split is a more useful
            # suggestion than a coincidental 6-way one, and this lets
            # us stop at the first (smallest) match found.
            for size in range(2, min(MAX_GROUP_SIZE, len(candidate_positions)) + 1):
                for combo in itertools.combinations(candidate_positions, size):
                    combo_type = None
                    types_ok = True
                    for pos in combo:
                        t = types[pos]
                        if t is not None:
                            if combo_type is None:
                                combo_type = t
                            elif combo_type != t:
                                types_ok = False
                                break
                    if not types_ok:
                        continue
                    if not self._types_compatible(combo_type, target_type):
                        continue

                    if round(sum(amounts[pos] for pos in combo), 2) == target:
                        return combo
            return None

        for date in shared_dates:
            bank_candidates = bank_by_date[date]
            tally_candidates = tally_by_date[date]

            if len(bank_candidates) > MAX_CANDIDATES or len(tally_candidates) > MAX_CANDIDATES:
                continue

            # Direction 1: several tally entries -> one bank entry
            # (the case in the example this feature was built for:
            # 3 x tally ₹5,000 == 1 x bank ₹15,000, same date)
            remaining_tally = list(tally_candidates)
            for bank_pos in list(bank_candidates):
                if bank_claimed[bank_pos] or not remaining_tally:
                    continue
                combo = find_group_summing_to(
                    remaining_tally, tally_amount, bank_amount[bank_pos],
                    bank_type[bank_pos], tally_type,
                )
                if combo:
                    bank_claimed[bank_pos] = True
                    for pos in combo:
                        tally_claimed[pos] = True
                        remaining_tally.remove(pos)
                    grouped_matches.append({
                        "direction": "TALLY_MANY_TO_BANK_ONE",
                        "date": bank_records[bank_pos]["date"],
                        "oneTransaction": bank_records[bank_pos],
                        "manyTransactions": [tally_records[pos] for pos in combo],
                        "amount": float(bank_amount[bank_pos]),
                        "reason": f"{len(combo)} tally entries on this date sum to "
                                  f"exactly one bank entry — likely a split/consolidated "
                                  f"transaction, not a genuine discrepancy.",
                    })

            # Direction 2: several bank entries -> one tally entry
            remaining_bank = [p for p in bank_candidates if not bank_claimed[p]]
            for tally_pos in list(tally_candidates):
                if tally_claimed[tally_pos] or not remaining_bank:
                    continue
                combo = find_group_summing_to(
                    remaining_bank, bank_amount, tally_amount[tally_pos],
                    tally_type[tally_pos], bank_type,
                )
                if combo:
                    tally_claimed[tally_pos] = True
                    for pos in combo:
                        bank_claimed[pos] = True
                        remaining_bank.remove(pos)
                    grouped_matches.append({
                        "direction": "BANK_MANY_TO_TALLY_ONE",
                        "date": tally_records[tally_pos]["date"],
                        "oneTransaction": tally_records[tally_pos],
                        "manyTransactions": [bank_records[pos] for pos in combo],
                        "amount": float(tally_amount[tally_pos]),
                        "reason": f"{len(combo)} bank entries on this date sum to "
                                  f"exactly one tally entry — likely a split/consolidated "
                                  f"transaction, not a genuine discrepancy.",
                    })

        return grouped_matches

    def _build_reference_index(self, references):
        index = defaultdict(list)
        for pos, ref in enumerate(references):
            if ref:
                index[ref].append(pos)
        return index

    def _build_amount_type_index(self, amounts, types):
        index = defaultdict(list)
        for pos in range(len(amounts)):
            index[(amounts[pos], types[pos])].append(pos)
        return index

    def _match(self):

        bank = self.bank
        tally = self.tally

        bank_len = len(bank)
        tally_len = len(tally)

        bank_reference = bank["reference"].tolist()
        bank_amount = bank["amount"].tolist()
        bank_type = bank["transactionType"].tolist()
        bank_date = bank["date"].tolist()

        tally_reference = tally["reference"].tolist()
        tally_amount = tally["amount"].tolist()
        tally_type = tally["transactionType"].tolist()
        tally_date = tally["date"].tolist()

        # JSON output needs native Python types: Timestamp isn't
        # serializable by plain json.dumps, and numpy.int64 (what
        # df.index + 2 produces) isn't serializable at all, even under
        # Django's JSONEncoder. The typed lists above stay untouched
        # and are what the date math below actually uses — these
        # display copies exist purely for the response payload.
        bank_display = bank.copy()
        bank_display["date"] = bank_display["date"].dt.strftime("%Y-%m-%d")
        bank_display["excelRow"] = bank_display["excelRow"].astype(int)
        bank_display["amount"] = bank_display["amount"].astype(float)
        bank_records = bank_display.to_dict("records")

        tally_display = tally.copy()
        tally_display["date"] = tally_display["date"].dt.strftime("%Y-%m-%d")
        tally_display["excelRow"] = tally_display["excelRow"].astype(int)
        tally_display["amount"] = tally_display["amount"].astype(float)
        tally_records = tally_display.to_dict("records")

        tally_ref_index = self._build_reference_index(tally_reference)
        tally_amount_type_index = self._build_amount_type_index(tally_amount, tally_type)

        bank_claimed = [False] * bank_len
        tally_claimed = [False] * tally_len

        matched = []
        amount_mismatches = []
        type_mismatches = []
        rule_counts = defaultdict(int)

        # ---- Pass 1: reference-driven matching -------------------
        for bank_pos in range(bank_len):

            reference = bank_reference[bank_pos]
            if not reference:
                continue

            candidates = [
                pos for pos in tally_ref_index.get(reference, [])
                if not tally_claimed[pos]
            ]
            if not candidates:
                continue

            b_amount = bank_amount[bank_pos]
            b_type = bank_type[bank_pos]
            b_date = bank_date[bank_pos]

            best = None  # (priority, date_diff, pos, rule)

            for pos in candidates:
                t_amount = tally_amount[pos]
                t_type = tally_type[pos]
                t_date = tally_date[pos]

                same_amount = b_amount == t_amount
                same_type = self._types_compatible(b_type, t_type)
                date_diff = abs((b_date - t_date).days)

                if same_amount and same_type:
                    if date_diff == 0:
                        priority, rule = 0, "REFERENCE"
                    else:
                        priority, rule = 1, "REFERENCE_DATE_TOLERANCE"
                elif same_amount and not same_type:
                    priority, rule = 2, "TYPE_MISMATCH"
                else:
                    priority, rule = 3, "AMOUNT_MISMATCH"

                candidate_tuple = (priority, date_diff, pos, rule)
                if best is None or candidate_tuple < best:
                    best = candidate_tuple

            _, date_diff, pos, rule = best

            bank_claimed[bank_pos] = True
            tally_claimed[pos] = True

            bank_record = bank_records[bank_pos]
            tally_record = tally_records[pos]

            if rule in ("REFERENCE", "REFERENCE_DATE_TOLERANCE"):
                matched.append({
                    "bankTransaction": bank_record,
                    "tallyTransaction": tally_record,
                    "matchingRule": rule,
                    "dateDifferenceDays": date_diff,
                    "withinTolerance": date_diff <= self.DATE_TOLERANCE_DAYS,
                })
                rule_counts[rule] += 1

            elif rule == "TYPE_MISMATCH":
                type_mismatches.append({
                    "bankTransaction": bank_record,
                    "tallyTransaction": tally_record,
                    "reason": "Same reference and amount, but transaction "
                              "type differs (DEBIT vs CREDIT).",
                })

            else:  # AMOUNT_MISMATCH
                amount_mismatches.append({
                    "bankTransaction": bank_record,
                    "tallyTransaction": tally_record,
                    "reason": "Same reference, but amount differs.",
                    "amountDifference": round(
                        float(b_amount) - float(tally_amount[pos]), 2
                    ),
                })

        # ---- Pass 2: fallback amount + type + date matching ------
        for bank_pos in range(bank_len):

            if bank_claimed[bank_pos]:
                continue

            b_amount = bank_amount[bank_pos]
            b_type = bank_type[bank_pos]
            b_date = bank_date[bank_pos]

            candidates = [
                pos for pos in tally_amount_type_index.get((b_amount, b_type), [])
                if not tally_claimed[pos]
            ]

            best_pos, best_diff = None, None
            for pos in candidates:
                diff = abs((b_date - tally_date[pos]).days)
                if diff <= self.DATE_TOLERANCE_DAYS and (best_diff is None or diff < best_diff):
                    best_pos, best_diff = pos, diff

            if best_pos is None:
                continue

            bank_claimed[bank_pos] = True
            tally_claimed[best_pos] = True

            matched.append({
                "bankTransaction": bank_records[bank_pos],
                "tallyTransaction": tally_records[best_pos],
                "matchingRule": "AMOUNT_DATE",
                "dateDifferenceDays": best_diff,
                "withinTolerance": True,
            })
            rule_counts["AMOUNT_DATE"] += 1

        # ---- Pass 3: grouped (many-to-one) date-based matching -----
        # Handles the case neither Pass 1 nor Pass 2 can: several
        # transactions on one side that together equal a single
        # transaction on the other side, same date — e.g. three ₹5,000
        # tally entries that together match one ₹15,000 bank entry.
        # Reference matching can't find this (references differ), and
        # amount+date matching needs an exact per-row amount, not a
        # summed one. This is a strong signal, not a certainty, so
        # matched groups go to exceptions for human review rather than
        # matchedTransactions.
        grouped_matches = self._find_grouped_matches(
            bank_claimed, tally_claimed,
            bank_amount, bank_date, bank_type, bank_records,
            tally_amount, tally_date, tally_type, tally_records,
        )

        # ---- Pass 4: sign-reversal hint -----
        # NOTE: unlike the earlier passes, a sign-reversal hint is a
        # WEAKER signal (same amount+date, opposite type, no reference
        # match at all) — the reconciler still needs to look at it, so
        # it's surfaced as its own category rather than treated as a
        # confident match. But the pair WAS found on both sides, just
        # with a polarity difference, so it must not ALSO be reported
        # as "missing" (no counterpart found at all) — those are
        # contradictory descriptions of the same transaction, and
        # leaving both bank_claimed/tally_claimed False here silently
        # double-counted every such row into missingInTally AND
        # missingInBank (verified: for N sign-reversal hints, both
        # missing counts were inflated by exactly N). Marking both
        # sides claimed once a hint is recorded keeps "missing" meaning
        # "no counterpart found at all", full stop.
        sign_reversal_hints = []
        unclaimed_tally_by_amount = defaultdict(list)
        for pos in range(tally_len):
            if not tally_claimed[pos]:
                unclaimed_tally_by_amount[tally_amount[pos]].append(pos)

        hinted_tally = set()
        for bank_pos in range(bank_len):
            if bank_claimed[bank_pos]:
                continue

            b_amount = bank_amount[bank_pos]
            b_type = bank_type[bank_pos]
            b_date = bank_date[bank_pos]

            for pos in unclaimed_tally_by_amount.get(b_amount, []):
                if pos in hinted_tally:
                    continue

                t_type = tally_type[pos]
                if b_type is None or t_type is None or b_type == t_type:
                    continue

                diff = abs((b_date - tally_date[pos]).days)
                if diff <= self.DATE_TOLERANCE_DAYS:
                    sign_reversal_hints.append({
                        "bankTransaction": bank_records[bank_pos],
                        "tallyTransaction": tally_records[pos],
                        "reason": "Same amount and date, opposite transaction "
                                  "type — possible polarity-convention "
                                  "difference between systems.",
                    })
                    hinted_tally.add(pos)
                    bank_claimed[bank_pos] = True
                    tally_claimed[pos] = True
                    break

        missing_in_tally = [
            bank_records[i] for i in range(bank_len) if not bank_claimed[i]
        ]
        missing_in_bank = [
            tally_records[i] for i in range(tally_len) if not tally_claimed[i]
        ]

        return {
            "matched": matched,
            "rule_counts": rule_counts,
            "amount_mismatches": amount_mismatches,
            "type_mismatches": type_mismatches,
            "grouped_matches": grouped_matches,
            "sign_reversal_hints": sign_reversal_hints,
            "missing_in_tally": missing_in_tally,
            "missing_in_bank": missing_in_bank,
        }

    # ────────────────────────────────────────────────────────────
    # Duplicate-artifact annotation
    # ────────────────────────────────────────────────────────────

    @staticmethod
    def _annotate_duplicate_artifacts(missing_rows, duplicate_rows_df):
        """
        A "missing" row whose excelRow also shows up in the duplicate
        report isn't a genuine gap — it's the leftover copy of a
        reference that WAS reconciled, just with one extra redundant
        entry on this side. Flag it so it doesn't read as an
        unexplained discrepancy.
        """
        duplicate_excel_rows = set(duplicate_rows_df["excelRow"].tolist())

        for row in missing_rows:
            is_duplicate_artifact = row["excelRow"] in duplicate_excel_rows
            row["duplicateArtifact"] = is_duplicate_artifact
            if is_duplicate_artifact:
                row["note"] = (
                    "This reference was matched elsewhere — this row is "
                    "a redundant duplicate copy, not a genuine gap."
                )

        return missing_rows

    # ────────────────────────────────────────────────────────────
    # Orchestration
    # ────────────────────────────────────────────────────────────

    def reconcile(self, bank_file, tally_file):

        self._load_files(bank_file, tally_file)

        self.bank, self.bank_dropped, invalidBankRows = self._clean(self.bank, self.bank_config, "Bank")
        self.tally, self.tally_dropped, invalidTallyRows = self._clean(self.tally, self.tally_config, "Tally")

        bank_duplicate_rows, bank_duplicate_extra = self._find_duplicates(self.bank)
        tally_duplicate_rows, tally_duplicate_extra = self._find_duplicates(self.tally)

        result = self._match()

        missing_in_tally = self._annotate_duplicate_artifacts(
            result["missing_in_tally"], bank_duplicate_rows
        )
        missing_in_bank = self._annotate_duplicate_artifacts(
            result["missing_in_bank"], tally_duplicate_rows
        )

        matched = result["matched"]
        total_bank = len(self.bank)
        total_tally = len(self.tally)

        summary = {
            "totalBankTransactions": total_bank,
            "totalTallyTransactions": total_tally,
            "matchedTransactions": len(matched),
            "missingInBank": len(missing_in_bank),
            "missingInTally": len(missing_in_tally),
            "matchPercentage": (
                round(len(matched) / total_bank * 100, 2) if total_bank else 0
            ),
            "referenceMatches": result["rule_counts"].get("REFERENCE", 0),
            "dateToleranceMatches": result["rule_counts"].get("REFERENCE_DATE_TOLERANCE", 0),
            "amountDateMatches": result["rule_counts"].get("AMOUNT_DATE", 0),
            "amountMismatches": len(result["amount_mismatches"]),
            "typeMismatches": len(result["type_mismatches"]),
            "groupedMatches": len(result["grouped_matches"]),
            "signReversalHints": len(result["sign_reversal_hints"]),
            # "Extra copy" counts (group size - 1, summed) — the number
            # an accountant actually means by "how many duplicates do I
            # have", not the raw count of every row sitting in a
            # duplicate group (which reads as roughly double).
            "duplicateBank": bank_duplicate_extra,
            "duplicateTally": tally_duplicate_extra,
            "duplicateEntries": bank_duplicate_extra + tally_duplicate_extra,
            "invalidBankRows": self.bank_dropped,
            "invalidTallyRows": self.tally_dropped,
            "invalidBankRowData":invalidBankRows,
            "invalidTallyRowData":invalidTallyRows,
        }

        return {
            "success": True,
            "summary": summary,
            "warnings": self.warnings,
            "matchedTransactions": matched,
            "missingInBank": missing_in_bank,
            "missingInTally": missing_in_tally,
            "exceptions": {
                "amountMismatches": result["amount_mismatches"],
                "typeMismatches": result["type_mismatches"],
                "groupedMatches": result["grouped_matches"],
            },
            "duplicates": {
                # Full detail (all rows in each duplicate group) for the
                # Duplicates tab — intentionally larger than the summary
                # count above; see _find_duplicates docstring.
                "bank": self._to_display_records(bank_duplicate_rows),
                "tally": self._to_display_records(tally_duplicate_rows),
            },
            "signReversalHints": result["sign_reversal_hints"],
        }