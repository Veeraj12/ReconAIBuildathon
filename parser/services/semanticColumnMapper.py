import difflib
import logging
import re
import warnings
from functools import lru_cache

import pandas as pd
from sentence_transformers import SentenceTransformer


# ============================================================
# Configuration
# ============================================================

logger = logging.getLogger(__name__)

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

# Starting values.
# We will calibrate these against the test dataset later.
# MIN_SIMILARITY = 0.55
# MIN_MARGIN = 0.08

MIN_SIMILARITY = 0.4
MIN_MARGIN = 0.04

# Fuzzy pre-pass thresholds. The fuzzy pass runs BEFORE the model
# (it is effectively free: pure string comparison, no torch) so it
# only claims a column when the name evidence is strong.
FUZZY_CUTOFF = 0.75
FUZZY_MARGIN = 0.05

# Value-based date detection: claim 'date' only when at least this
# fraction of sample values parse as calendar dates after discarding
# pure-digit strings (which could be reference numbers or epochs).
DATE_FALLBACK_RATIO = 0.8
DATE_FALLBACK_SAMPLES = 10


# ============================================================
# Canonical Financial Fields
# ============================================================

CANONICAL_FIELDS = {
    "date": {
        "description": (
            "The date associated with a financial transaction, "
            "such as transaction date, posting date, value date, "
            "settlement date, processing date or voucher date."
        ),
        "examples": [
            "transaction date",
            "posting date",
            "value date",
            "settlement date",
            "processing date",
        ],
    },

    "reference": {
        "description": (
            "A unique identifier used to identify a financial transaction, "
            "payment, transfer, cheque, settlement or order."
        ),
        "examples": [
            "transaction id",
            "payment reference",
            "UTR",
            "RRN",
            "voucher number",
            "cheque number",
            "order id",
        ],
    },

    "amount": {
        "description": (
            "The monetary value of a financial transaction, "
            "payment, settlement, transfer or accounting entry."
        ),
        "examples": [
            "transaction amount",
            "payment amount",
            "settlement value",
            "net amount",
            "financial value",
        ],
    },

    "debit": {
        "description": (
            "The monetary amount representing money leaving an account "
            "or a debit-side accounting transaction."
        ),
        "examples": [
            "debit amount",
            "withdrawal",
            "money out",
            "dr amount",
        ],
    },

    "credit": {
        "description": (
            "The monetary amount representing money entering an account "
            "or a credit-side accounting transaction."
        ),
        "examples": [
            "credit amount",
            "deposit",
            "money in",
            "cr amount",
        ],
    },

    "transactionType": {
        "description": (
            "The direction or type of a financial transaction, "
            "indicating whether money is debited, credited, incoming, "
            "outgoing, paid or received."
        ),
        "examples": [
            "debit credit",
            "DR CR",
            "flow direction",
            "transaction direction",
            "incoming outgoing",
            "payment type",
        ],
    },

    "description": {
        "description": (
            "Text describing or explaining a financial transaction, "
            "including narration, remarks, particulars or details."
        ),
        "examples": [
            "narration",
            "remarks",
            "particulars",
            "transaction details",
            "description text",
        ],
    },

    "voucher": {
        "description": (
            "An accounting voucher identifier or voucher number "
            "associated with a financial entry."
        ),
        "examples": [
            "voucher number",
            "voucher id",
            "accounting voucher",
        ],
    },

    "ledger": {
        "description": (
            "The accounting ledger, account or ledger name "
            "associated with a financial transaction."
        ),
        "examples": [
            "ledger name",
            "account",
            "ledger account",
        ],
    },

    "balance": {
        "description": (
            "The remaining or closing monetary balance of an account "
            "after financial transactions."
        ),
        "examples": [
            "closing balance",
            "available balance",
            "account balance",
        ],
    },
}


# ============================================================
# Header normalization + abbreviation lexicon
#
# These power the cheap, deterministic pre-passes that run BEFORE
# the embedding model. A fuzzy/lexicon hit costs microseconds and
# never pays torch's inference cost for columns a dictionary or the
# column's own values can already settle.
# ============================================================

# Common financial-column shorthands. Only unambiguous expansions
# belong here; "dr"/"cr" are deliberately absent (standalone they
# are too risky — they belong to debit/credit but both directions
# appear in a single transactionType column far too often).
_ABBREVIATIONS = {
    "amt": "amount",
    "amounts": "amount",
    "desc": "description",
    "descrip": "description",
    "descr": "description",
    "narr": "narration",
    "narrat": "narration",
    "vch": "voucher",
    "vchr": "voucher",
    "chq": "cheque",
    "ref": "reference",
    "refs": "reference",
    "txn": "transaction",
    "txns": "transaction",
    "tx": "transaction",
    "bal": "balance",
    "acct": "account",
    "acc": "account",
    "no": "number",
    "num": "number",
    "id": "id",
}

# Used by _normalize_header_key — mirrors the service's own
# _normalize_key so pre-pass evidence is computed against the exact
# same normalized form the deterministic alias pass uses.
_WHITESPACE_RUN = re.compile(r"\s+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])")
_SEPARATOR_RUN = re.compile(r"[_\-]+")
_PURE_DIGITS = re.compile(r"\d+")


def _normalize_header_key(text):
    """
    Normalize a header into a lowercase, space-separated key:
    lowercase, underscores/dashes -> spaces, camelCase seams -> spaces,
    whitespace collapsed.
    """
    text = str(text).strip().lower()
    text = _SEPARATOR_RUN.sub(" ", text)
    text = _CAMEL_BOUNDARY.sub(" ", text)
    text = _WHITESPACE_RUN.sub(" ", text)
    return text


def _expand_tokens(text):
    """
    Expand financial abbreviations token-wise, e.g. 'Vch No' ->
    ['voucher', 'number']. Unknown tokens pass through unchanged.
    """
    return [_ABBREVIATIONS.get(token, token) for token in text.split()]


# ============================================================
# Model Loading
# ============================================================

@lru_cache(maxsize=1)
def get_model():
    """
    Load the SentenceTransformer model once and reuse it.
    """
    return SentenceTransformer(MODEL_NAME)


# ============================================================
# Canonical Field Embeddings
# ============================================================

def build_field_text(field_name, metadata):
    """
    Convert a canonical field definition into semantic text.
    """

    examples = ", ".join(metadata["examples"])

    return (
        f"Financial spreadsheet field: {field_name}. "
        f"{metadata['description']} "
        f"Related terms include: {examples}."
    )


@lru_cache(maxsize=1)
def get_field_embeddings():
    """
    Generate embeddings for canonical fields once.
    """

    model = get_model()

    fields = list(CANONICAL_FIELDS.keys())

    field_texts = [
        build_field_text(
            field_name,
            CANONICAL_FIELDS[field_name],
        )
        for field_name in fields
    ]

    embeddings = model.encode(
        field_texts,
        normalize_embeddings=True,
    )

    return fields, embeddings


# ============================================================
# Fuzzy Pre-Pass (name evidence only — no model)
#
# Resolves columns whose names survive the deterministic alias
# pass but are recognizably close to a known alias: misspellings,
# applied abbreviations ("Amt", "Vch No", "Ref No"), plurals, and
# leftover decoration ("Bank Transaction Date (RTGS)").
# ============================================================

def _score_against_candidate(key_tokens, candidate_text):
    """
    Score one candidate alias string against normalized key tokens.

    Returns a score in [0, 1]:
      1.0   EVERY expanded candidate token appears among the key's
            expanded tokens. Short tokens matter (so meaningful
            abbreviations 'dr'/'cr' protect against partial matches),
            but pure-decoration tokens (parenthesized acronyms such
            as '(RTGS)', '(NEFT)') are stripped, since the enclosing
            word already carries the evidence.
      else  difflib sequence ratio, kept only when >= FUZZY_CUTOFF
            (catches misspellings and reordering that token
            coverage cannot)
      else  0.0
    """
    candidate_key = _normalize_header_key(candidate_text)
    candidate_tokens = _expand_tokens(candidate_key)

    # Token-coverage: an alias token that is only decoration (a
    # parenthesized acronym token like '(RTGS)' or '(NEFT)') should
    # not hard-block a match against a plain-name header. Drop such
    # tokens before the coverage test so the enclosing word carries
    # the coverage, and a decorated alias still matches the
    # undecorated key (e.g. 'Vch No' vs 'voucher number').
    stripped = [t for t in candidate_tokens if not (len(t) >= 2 and t.startswith('('))]
    if stripped and all(t in key_tokens for t in stripped):
        return 1.0

    joined_key = " ".join(key_tokens)
    joined_candidate = " ".join(candidate_tokens)
    ratio = difflib.SequenceMatcher(
        None,
        joined_key,
        joined_candidate,
    ).ratio()

    return ratio if ratio >= FUZZY_CUTOFF else 0.0


def fuzzy_match_column(column_name, alias_map=None):
    """
    Try to resolve a column header by name evidence alone.

    Candidate pool:
      - canonical field names and their curated examples
      - caller-supplied alias_map ({field: [alias, ...]}) so the
        same JSON alias lists the deterministic layer already uses
        reinforce the fuzzy layer

    Returns {"field", "confidence", "method"} when exactly one field
    stands clearly best, or None to defer to value evidence / the
    model. Ambiguity is deliberately left unresolved here — dict and
    difflib evidence cannot rank two competing fields, but the
    embedding model (name + values) still can, so ambiguity falls
    THROUGH rather than failing.
    """

    key = _normalize_header_key(column_name)
    key_tokens = _expand_tokens(key)

    # candidate -> {field: best_score} accumulated across every
    # alias/example string belonging to each field.
    best_by_field = {}

    pools = [
        (field, [field])
        for field in CANONICAL_FIELDS
    ]
    pools.extend(
        (field, list(metadata["examples"]))
        for field, metadata in CANONICAL_FIELDS.items()
    )
    if alias_map:
        pools.extend(
            (field, list(aliases))
            for field, aliases in alias_map.items()
        )

    for field, alias_text_pool in pools:
        for alias_text in alias_text_pool:
            score = _score_against_candidate(key_tokens, alias_text)
            if score <= 0.0:
                continue
            previous = best_by_field.get(field, 0.0)
            if score > previous:
                best_by_field[field] = score

    if not best_by_field:
        return None

    ranked = sorted(
        best_by_field.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    best_field, best_score = ranked[0]

    # Margin check applies even to perfect scores: a 1.0 shared by
    # two fields (e.g. 'voucher number' lives under both reference
    # and voucher) is an UNRESOLVED tie for pure name evidence, so
    # defer to value evidence / the model rather than letting
    # candidate-pool ordering win arbitrarily.
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if best_score - second_score < FUZZY_MARGIN:
        return None

    return {
        "field": best_field,
        "confidence": best_score,
        "method": "fuzzy_name",
    }


# ============================================================
# Value-Based Pre-Pass (value evidence only — no model)
#
# Column CONTENT proves a field's type independently of its name.
# Dates are unambiguous from value shape, so they can be claimed
# here. Monetary fields are NOT resolvable this way (amount, debit,
# credit and balance columns are all just numbers) — those stay
# with the embedding model, which can use name + value context.
# ============================================================

def detect_date_column(values, min_ratio=DATE_FALLBACK_RATIO):
    """
    Fraction of sample values that parse as calendar dates, after
    dropping pure-digit strings (epochs and reference numbers look
    numeric, and pd.to_datetime would happily "parse" integers as
    nanoseconds — that failure mode is exactly what the digit guard
    prevents).

    Returns a ratio in [0, 1]; caller maps 'date' only when the
    ratio clears DATE_FALLBACK_RATIO with enough surviving samples.
    """
    attempts = [
        str(value).strip()
        for value in values
        if value is not None and str(value).strip()
    ]

    # Pure digits: no separators, no letters. Cannot be trusted as
    # date evidence whether they parse or not.
    attempts = [
        text
        for text in attempts
        if not _PURE_DIGITS.fullmatch(text)
    ]

    if len(attempts) < 3:
        return 0.0

    series = pd.Series(attempts, dtype=object)

    with warnings.catch_warnings():
        # Mixed formats dayfirst/US coexisting is EXPECTED here; the
        # coerce + ratio is the decision, not a parse exhaustiveness.
        warnings.simplefilter("ignore")
        try:
            parsed = pd.to_datetime(
                series,
                errors="coerce",
                format="mixed",
                dayfirst=True,
            )
        except (TypeError, ValueError):
            # Older pandas without format="mixed".
            try:
                parsed = pd.to_datetime(
                    series,
                    errors="coerce",
                    dayfirst=True,
                )
            except (TypeError, ValueError):
                return 0.0

    parsed_ratio = float(parsed.notna().mean())
    return parsed_ratio


# ============================================================
# Unknown Column Representation
# ============================================================

def build_column_text(column_name, values):
    """
    Convert an unknown spreadsheet column into semantic text.

    Both the column name and representative values are supplied
    to the embedding model.
    """

    samples = [
        str(value).strip()
        for value in values
        if value is not None
    ][:5]

    sample_text = ", ".join(samples)

    return (
        f"Financial spreadsheet column named '{column_name}'. "
        f"Example values: {sample_text}."
    )


# ============================================================
# Candidate Ranking
#
# One batched encode call for EVERY unresolved column: the model is
# invoked at most once per sheet regardless of how many unknown
# columns remain, and field embeddings are computed once over the
# whole process (lru_cache). Columns are scored by a single
# matrix-vector product against the canonical-field embeddings.
# ============================================================

def rank_column_candidates(column_name, values):
    """
    Rank canonical fields according to semantic similarity.

    Kept for API compatibility (single column at a time); the batch
    path used by map_unknown_columns shares the same scoring math.
    """

    model = get_model()

    fields, field_embeddings = get_field_embeddings()

    column_text = build_column_text(
        column_name,
        values,
    )

    column_embedding = model.encode(
        [column_text],
        normalize_embeddings=True,
    )[0]

    # Embeddings are normalized, therefore dot product
    # is equivalent to cosine similarity.
    scores = field_embeddings @ column_embedding

    ranked_candidates = sorted(
        zip(fields, scores),
        key=lambda item: item[1],
        reverse=True,
    )

    return ranked_candidates


def _rank_from_score_row(fields, score_row):
    return sorted(
        zip(fields, score_row),
        key=lambda item: item[1],
        reverse=True,
    )


def _ranked_result_shapes(ranked_candidates):
    """
    Shared extraction of (best_field, best_score, second_field,
    second_score, candidates) from a ranked candidate list.
    """
    best_field, best_score = ranked_candidates[0]

    second_field = (
        ranked_candidates[1][0]
        if len(ranked_candidates) > 1
        else None
    )

    second_score = (
        float(ranked_candidates[1][1])
        if len(ranked_candidates) > 1
        else 0.0
    )

    best_score = float(best_score)

    candidates = [
        {
            "field": field,
            "score": float(score),
        }
        for field, score in ranked_candidates
    ]

    return best_field, best_score, second_field, second_score, candidates


def _log_recognition(column_name, candidates, extra=""):
    logger.debug(
        "semantic column '%s': top=%s%s",
        column_name,
        [
            (candidate["field"], round(candidate["score"], 4))
            for candidate in candidates[:5]
        ],
        extra or "",
    )


# ============================================================
# Confidence Decision
# ============================================================

def _decide_mapping(
    best_field,
    best_score,
    second_field,
    second_score,
    candidates,
):
    """
    Apply the similarity/margin thresholds. Shared by the
    single-column API (resolve_column) and the batched path, so
    both routes always rule identically.
    """

    margin = best_score - second_score

    # --------------------------------------------------------
    # Below absolute similarity threshold
    # --------------------------------------------------------

    if best_score < MIN_SIMILARITY:
        return {
            "status": "unrecognized",
            "field": None,
            "confidence": best_score,
            "second_best": second_field,
            "second_score": second_score,
            "margin": margin,
            "candidates": candidates,
        }

    # --------------------------------------------------------
    # Too close to another candidate
    # --------------------------------------------------------

    if margin < MIN_MARGIN:
        return {
            "status": "ambiguous",
            "field": None,
            "confidence": best_score,
            "second_best": second_field,
            "second_score": second_score,
            "margin": margin,
            "candidates": candidates,
        }

    # --------------------------------------------------------
    # Confident mapping
    # --------------------------------------------------------

    return {
        "status": "mapped",
        "field": best_field,
        "confidence": best_score,
        "second_best": second_field,
        "second_score": second_score,
        "margin": margin,
        "candidates": candidates,
    }


def resolve_column(column_name, values):
    """
    Decide whether an unknown column can be confidently mapped
    (single-column API; map_unknown_columns uses the batched path).

    Returns both the accepted mapping decision and enough
    information to explain why the decision was made.
    """

    ranked_candidates = rank_column_candidates(
        column_name,
        values,
    )

    if not ranked_candidates:
        return {
            "status": "ambiguous",
            "field": None,
            "confidence": 0.0,
            "second_best": None,
            "second_score": 0.0,
            "margin": 0.0,
            "candidates": [],
        }

    (
        best_field,
        best_score,
        second_field,
        second_score,
        candidates,
    ) = _ranked_result_shapes(ranked_candidates)

    result = _decide_mapping(
        best_field,
        best_score,
        second_field,
        second_score,
        candidates,
    )

    _log_recognition(column_name, candidates)

    return result


# ============================================================
# Multi-Column Semantic Mapping
#
# Pipeline per unknown column, cheapest evidence first:
#
#   1. fuzzy name pre-pass      (string ops, no model)
#   2. value-shape date detect  (pandas parse, no model)
#   3. embedding model          (ONE batched encode call total)
#
# Every decision passes the same accept/reject + collision rules,
# and the report keeps the full decision even when the mapper
# rejects the column.
# ============================================================

def map_unknown_columns(df, known_columns, alias_map=None):
    """
    Semantically map columns that were not already recognized
    by deterministic header aliases.

    alias_map is optional: {canonical field: [alias strings]} from
    the caller's deterministic configs, fed into the fuzzy pre-pass
    so a supported-but-unusual header only ever has to be added to
    the JSON config once.

    Returns:

        {
            "mapping": {
                "Net Settlement Value": "amount",
                "Flow Direction": "transactionType",
            },

            "report": {
                "Net Settlement Value": {
                    "status": "mapped",
                    "field": "amount",
                    "confidence": 0.82,
                    ...
                }
            }
        }
    """

    mapped_columns = {}
    recognition_report = {}

    # Canonical fields already occupied by deterministic mapping
    # cannot be claimed again by an unknown column.
    used_fields = {
        column
        for column in known_columns
        if column in CANONICAL_FIELDS
    }

    unknown_columns = [
        column_name
        for column_name in df.columns
        if column_name not in known_columns
    ]

    resolved = {}
    model_needed = []

    for column_name in unknown_columns:

        # ----------------------------------------------------
        # 1. Fuzzy name pre-pass (no model)
        # ----------------------------------------------------

        fuzzy = fuzzy_match_column(column_name, alias_map)
        if fuzzy is not None:
            resolved[column_name] = {
                "status": "mapped",
                "field": fuzzy["field"],
                "confidence": fuzzy["confidence"],
                "second_best": None,
                "second_score": 0.0,
                "margin": 0.0,
                "candidates": [
                    {
                        "field": fuzzy["field"],
                        "score": fuzzy["confidence"],
                    }
                ],
                "method": fuzzy["method"],
            }
            continue

        # ----------------------------------------------------
        # 2. Value-shape date detection (no model)
        # ----------------------------------------------------

        sample_values = (
            df[column_name]
            .dropna()
            .head(DATE_FALLBACK_SAMPLES)
            .tolist()
        )

        date_ratio = detect_date_column(sample_values)
        if date_ratio >= DATE_FALLBACK_RATIO:
            resolved[column_name] = {
                "status": "mapped",
                "field": "date",
                "confidence": date_ratio,
                "second_best": None,
                "second_score": 0.0,
                "margin": 0.0,
                "candidates": [
                    {
                        "field": "date",
                        "score": date_ratio,
                    }
                ],
                "method": "value_signal_date",
            }
            continue

        model_needed.append(column_name)

    # --------------------------------------------------------
    # 3. Embedding model — ONE batched call for every column
    #    still unresolved.
    # --------------------------------------------------------

    if model_needed:

        model = get_model()

        fields, field_embeddings = get_field_embeddings()

        column_texts = [
            build_column_text(
                column_name,
                df[column_name].dropna().head(5).tolist(),
            )
            for column_name in model_needed
        ]

        column_embeddings = model.encode(
            column_texts,
            normalize_embeddings=True,
        )

        # Embeddings are normalized, therefore dot product
        # is equivalent to cosine similarity.
        score_matrix = field_embeddings @ column_embeddings.T

        for column_index, column_name in enumerate(model_needed):

            ranked_candidates = _rank_from_score_row(
                fields,
                score_matrix[:, column_index],
            )

            (
                best_field,
                best_score,
                second_field,
                second_score,
                candidates,
            ) = _ranked_result_shapes(ranked_candidates)

            result = _decide_mapping(
                best_field,
                best_score,
                second_field,
                second_score,
                candidates,
            )

            _log_recognition(column_name, candidates)

            resolved[column_name] = result

    # --------------------------------------------------------
    # Accept/reject every unknown column under the same rules,
    # preserving the original report-first ordering.
    # --------------------------------------------------------

    for column_name in unknown_columns:

        result = resolved.get(column_name)
        if result is None:  # defensive; should not happen
            result = {
                "status": "unrecognized",
                "field": None,
                "confidence": 0.0,
                "second_best": None,
                "second_score": 0.0,
                "margin": 0.0,
                "candidates": [],
            }

        # Save the complete recognition decision even when
        # the mapper rejects the column.
        recognition_report[column_name] = result

        # Ignore uncertain/unrecognized columns.
        if result["status"] != "mapped":
            continue

        field = result["field"]

        # Prevent canonical-field collisions: a fallback pass must
        # not poach a field the deterministic layer already owns.
        if field in used_fields:

            result["status"] = "collision"
            result["reason"] = (
                f"Canonical field '{field}' is already "
                f"occupied by another column."
            )

            continue

        # Accept mapping.
        mapped_columns[column_name] = field
        used_fields.add(field)

    return {
        "mapping": mapped_columns,
        "report": recognition_report,
    }
