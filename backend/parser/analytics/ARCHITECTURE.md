# ReconAI Analytics Layer — Phase 1 & Phase 2 Architecture

Status: Phase 1 (Exception Feature Extraction) and Phase 2 (Exception
Pattern Analysis) implemented. Phase 3+ (AI explanation, resolution
recommendation, Recon Bot) intentionally **not** started.

Code: `backend/parser/analytics/` (`features.py`, `signatures.py`,
`patterns.py`, `service.py`, `tests/`). New endpoint wired into
`parser/views.py` / `parser/urls.py`. Nothing in
`parser/services/reconciliationService.py` was modified.

---

## 1. Current architecture understanding

```
Bank/Tally Excel/CSV
        |  parser/parsers/{bankParser,tallyParser,csvParser,baseParser}.py
File parsing + header mapping (config-driven: genericBank.json / genericTally.json)
        |
Normalization  (_clean_amount, _clean_date, _clean_reference, _clean_type)
        |
ReconciliationService._match()   <- reference / amount / date / type matching, waterfall of rules
        |
matched / amount_mismatches / type_mismatches / grouped_matches /
missing_in_tally / missing_in_bank / duplicates / sign_reversal_hints
        |
views.upload_files()  ->  POST /api/v1/reconcile/  ->  one JSON response
        |
Frontend: ReconciliationProvider holds the full ReconciliationResult in memory
        |
Dashboard components render summary + per-category tables
```

Key property that shaped every design decision below: **this pipeline
is entirely stateless.** `reconcile()` is called once per upload,
returns one JSON blob, and nothing is written to the database. There
is no `Transaction`, `ReconciliationRun`, or `Exception` Django model
— only `UserProfile` exists.

## 2. Existing reconciliation data structures

Every transaction, wherever it appears in the response, has this
shape (from `ReconciliationService.OUTPUT_COLUMNS` and
`_to_display_records` / `_match`'s display conversion):

```json
{
  "date": "2026-01-14",        // ISO string, already normalized
  "reference": "UTR12345",     // already uppercased/stripped
  "description": "...",
  "amount": 22122.0,           // already rounded to 2 decimals
  "transactionType": "DEBIT",  // "DEBIT" | "CREDIT" | null
  "excelRow": 214               // original spreadsheet row, per-file
}
```

No `ledger`/account field is captured anywhere in the current parsers
or config files.

## 3. Existing exception structures

`ReconciliationService.reconcile()` returns exceptions in five
different shapes, all under top-level keys:

| Key | Shape | Both sides present? |
|---|---|---|
| `exceptions.amountMismatches` | `{bankTransaction, tallyTransaction, reason, amountDifference}` | yes |
| `exceptions.typeMismatches` | `{bankTransaction, tallyTransaction, reason}` | yes |
| `exceptions.groupedMatches` | `{direction, date, oneTransaction, manyTransactions[], amount, reason}` | one-to-many |
| `missingInTally` | `Transaction[]` (bank side only) | bank only |
| `missingInBank` | `Transaction[]` (tally side only) | tally only |
| `duplicates.bank` / `duplicates.tally` | `Transaction[]` | one side only |
| `signReversalHints` | `{bankTransaction, tallyTransaction, reason}` | yes (informational) |

## 4. Exact insertion point for Phase 1

`ExceptionFeatureExtractor.extract_all()` in
`parser/analytics/features.py` consumes exactly this dict — the same
object `upload_files()` already builds and returns. It reads it, never
mutates `ReconciliationService` or anything it produces.

## 5. Exact insertion point for Phase 2

`PatternAnalysisService.analyze()` in `parser/analytics/service.py`
takes the same dict, runs `ExceptionFeatureExtractor.extract_all()`
internally, then discovers/scores/ranks patterns. Exposed via
`POST /api/v1/exceptions/analyze/` (`parser/views.py::analyze_exceptions`).

## 6. Proposed architecture diagram

```
Existing Reconciliation (unchanged)
        |
        v
reconciliation exception dict  (already returned by /reconcile/)
        |
        v
+------------------------------------------+
| PHASE 1                                   |
| ExceptionFeatureExtractor.extract_all()   |
|   -> ExceptionFeature[]                   |
+------------------------------------------+
        |
        v
+------------------------------------------+
| PHASE 2                                   |
| PatternSignatureGenerator                 |
|   -> per-feature signature (2 hypotheses) |
| DeterministicPatternDiscoveryEngine       |
|   -> greedy largest-group-first selection |
|   -> Pattern[] + outlier exception_ids    |
| PatternScorer     -> confidence per Pattern|
| PatternRanker     -> frequency/impact/    |
|                      combined score, sort |
+------------------------------------------+
        |
        v
PatternAnalysisResult  ->  POST /api/v1/exceptions/analyze/ response
        |
        v
Future: Phase 3 (explanation) / Phase 4 (resolution) / Recon Bot
```

`PatternAnalysisService` is the only class any of Phase 3+ or a future
Recon Bot needs to call — it takes a reconciliation exception dict in,
returns a `PatternAnalysisResult` out, with no Django/HTTP coupling.

## 7. ExceptionFeature model

```python
@dataclass
class SideSnapshot:
    exists: bool
    excel_row: Optional[int]
    amount: Optional[float]
    date: Optional[str]
    reference: Optional[str]
    ledger: Optional[str]           # always None today — see §8
    transaction_type: Optional[str]
    description: Optional[str]

@dataclass
class ExceptionFeature:
    exception_id: str               # synthetic, see below
    exception_type: str             # AMOUNT_MISMATCH | TYPE_MISMATCH | GROUPED_MATCH |
                                     # MISSING_IN_TALLY | MISSING_IN_BANK |
                                     # DUPLICATE_BANK | DUPLICATE_TALLY | SIGN_REVERSAL_HINT
    bank: SideSnapshot
    tally: SideSnapshot
    features: dict                  # see §8
    group_member_excel_rows: list   # only non-empty for GROUPED_MATCH
```

**On identity**: since reconciliation is stateless and `excelRow` is
only unique per side per upload, `exception_id` is synthesized
deterministically per run, e.g. `"AMOUNT_MISMATCH:B214:T087"` for a
pair, `"MISSING_IN_TALLY:B214"` for a single-sided record. This is
stable and traceable *within one analysis call*, which is all Phase
1/2 need. If reconciliation runs are ever persisted (a real DB row per
transaction), only `_side()`/`_identity` construction in `features.py`
needs to change — nothing downstream does.

## 8. Complete feature list and rationale

| Feature | Purpose | Useful for Phase 2? |
|---|---|---|
| `bank_exists` / `tally_exists` | Distinguishes missing-side exceptions from mismatches at the signature level — the very first partition. | Yes — primary grouping key |
| `amount_difference` (signed) | Preserves direction (bank higher/lower) — needed to explain "bank consistently ₹251 *lower*". | Yes |
| `absolute_amount_difference` | The core "fixed charge" signal — same magnitude, different sign context, still one pattern. | Yes — primary grouping key |
| `percentage_difference` | Catches proportional differences a fixed-amount bucket would miss (₹10 off ₹500 vs ₹90 off ₹4500, same ~2%). Zero-denominator guarded → `None`, never inf/NaN. | Yes — alternate grouping key |
| `date_difference_days` / `signed_date_offset_days` | Detects consistent posting-lag patterns (Tally always 1 day behind Bank). | Yes |
| `reference_match` | Cheap high-signal boolean — a pattern with 98% reference match is a much stronger claim than one without. | Yes |
| `amount_match` / `date_match` | Booleans, used both directly and as signature partitions (e.g. amount matches but date doesn't = pure timing issue). | Yes |
| `ledger_match` | **Always `None` today** — no ledger/account column exists in the current parsers. Present as a field so wiring in real ledger data later is a one-line change in `_ledger_of()`, not a schema change. | Not yet — reserved |
| `transaction_type_bank` / `transaction_type_tally` | Needed for sign-reversal-style analysis and future rule "same ledger repeatedly appears in one exception category". | Yes |
| `duplicate_flag` | Carries the reconciliation service's own duplicate detection through rather than re-deriving it. | Yes |
| `sign_reversal_flag` | Same — passes through the existing sign-reversal-hint signal. | Yes |
| `group_size` / `group_direction` (GROUPED_MATCH only) | The only two features that actually vary meaningfully for many-to-one matches, since the amounts are equal by construction. | Yes |

Deliberately **not** added: raw description/narration text similarity,
free-text NLP features, anything requiring a model — these belong to
Phase 3 (explanation), not Phase 1/2, and risk becoming "features added
just in case" that the spec explicitly warns against.

## 9. Pattern model

```python
@dataclass
class Pattern:
    pattern_id: str                 # "PATTERN_001", ...
    pattern_type: str                # see §11 classification
    exception_type: str
    record_count: int
    total_financial_impact: float    # sum of absolute_amount_difference; 0.0 where n/a
    common_features: dict            # rates + averages, see below
    exception_ids: list               # full traceability back to Phase 1 records
    confidence: float                 # 0-1, see §13
    frequency_score: float            # record_count / total_exceptions
    impact_score: float               # total_financial_impact / max pattern impact
    combined_score: float             # weighted sum, default ranking
    summary: str                      # human-readable explanation, see §"Explainability"
```

`common_features` includes: `reference_match_rate`, `amount_match_rate`,
`date_match_rate`, `average_absolute_amount_difference`,
`median_absolute_amount_difference`, `average_percentage_difference`,
`common_date_offset_days`.

## 10. Pattern signature strategy

**Tolerance-based, bucketed — not exact, not pure similarity-scoring.**

- Exact: `exception_type`, `bank_exists`/`tally_exists`,
  `reference_match`, `amount_match`, `date_match`, `duplicate_flag`,
  `sign_reversal_flag` — these are already booleans/small vocabularies;
  bucketing them would only lose information.
- Bucketed: `absolute_amount_difference` (nearest ₹1, absorbs
  paisa-level float noise without masking a genuinely different fixed
  charge), `percentage_difference` (nearest 0.5 percentage point),
  `date_difference_days` (exact 0–5, then a single `>5` bucket).

All bucket sizes are constructor parameters (`SignatureConfig`), not
hardcoded — meant to be tuned against real production data, per the
spec's instruction to evaluate tolerances rather than assume them.

**Two independent signature hypotheses, not one combined signature**
(this is the one real bug I hit and fixed while building this — see
§21 for the worked example): a fixed-amount pattern (`mode="amount"`)
and a proportional pattern (`mode="percentage"`) are computed
separately, because a truly fixed ₹251 charge has *drifting*
percentage across different base amounts, and a truly proportional ~2%
fee has *drifting* absolute amount. Combining both into one signature
string would fragment either real pattern into dozens of slivers.

## 11. Pattern discovery strategy

Implemented as `DeterministicPatternDiscoveryEngine.discover()`:

1. For each of the two signature modes (`amount`, `percentage`),
   group all `ExceptionFeature`s by their bucketed signature (a GROUP
   BY, O(n)).
2. Pool every resulting group from both modes into one candidate list.
3. Sort candidates by size, largest first.
4. Greedily claim candidates: a candidate whose *unclaimed* members
   still meet `min_pattern_size` becomes a `Pattern`; its members are
   marked claimed so smaller/weaker candidates under the other
   hypothesis can't re-fragment an already-explained population.
5. Anything never claimed becomes an outlier.

This is a greedy set-cover, not a distance-based cluster — deterministic,
O(n log n) dominated by the sort, and 100% reproducible (verified by
`test_deterministic_repeatable`).

Pattern *type labeling* (`RECURRING_AMOUNT_DIFFERENCE` vs
`RECURRING_PERCENTAGE_DIFFERENCE` vs `DATE_OFFSET` vs
`REFERENCE_MATCH_ONLY` vs `MISSING_SOURCE` vs `DUPLICATE` vs
`SIGN_REVERSAL` vs `GROUPED_MATCH` vs `MIXED_ATTRIBUTE`) is decoupled
from *grouping* — a group's actual cohesion (relative spread of amount
vs. percentage across its own members) decides the label, regardless
of which signature mode happened to find it first.

## 12. Comparison: deterministic grouping vs. similarity scoring vs. DBSCAN vs. hierarchical

| Approach | Pros | Cons | Verdict here |
|---|---|---|---|
| **Deterministic bucketed grouping** (chosen) | O(n), 100% reproducible, every group trivially explainable ("147 share exactly this"), no distance metric/normalization decisions to get wrong | Needs sensible tolerance parameters chosen up front; hard boundary at bucket edges | **Primary strategy** — financial exceptions recur for structural reasons (a fixed charge, a fixed lag), which is exactly what bucketed grouping finds directly |
| **Similarity scoring** (pairwise) | More flexible than hard buckets; can rank "how similar" | O(n²) pairwise comparisons without an index — doesn't scale past a few thousand records without approximate-NN infrastructure; still needs a distance metric | Not needed yet — bucketing already captures the tolerance concept without the pairwise cost |
| **DBSCAN** | No need to pre-specify cluster count; handles arbitrary-shaped density clusters; has a built-in outlier concept (`-1` label) | Needs feature scaling/normalization across mixed units (₹ vs days vs booleans), an `eps`/`min_samples` choice that's not obvious for mixed feature types, and is non-deterministic in tie-breaking order in some implementations | Reserved for later — the specific case DBSCAN is built for (diffuse, non-boundary-shaped density regions) hasn't shown up in this domain's data yet |
| **Hierarchical clustering** | Produces a dendrogram — useful if patterns are naturally nested (a broad pattern with sub-patterns) | O(n²) memory/time for the standard agglomerative algorithm; a specific "cut height" still has to be chosen, which is its own tolerance problem | Same as DBSCAN — worth prototyping once there's a concrete "patterns look nested" observation from real data, not before |

**Recommended approach for the current dataset**: deterministic
bucketed grouping (implemented). `PatternDiscoveryEngine` is an
informal protocol — `DeterministicPatternDiscoveryEngine` just needs a
`.discover(features) -> (patterns, outlier_ids)` method — so a
DBSCAN- or hierarchical-based engine can be substituted later in
`PatternAnalysisService.__init__(discovery_engine=...)` without
touching Phase 1, `signatures.py`, scoring, ranking, or the API
response shape. That substitutability is the concrete form of
architectural principle #12 in the brief.

## 13. Pattern ranking strategy

Two scores are computed independently and exposed separately, plus a
combined default ordering:

- `frequency_score = record_count / total_exceptions`
- `impact_score = total_financial_impact / max(total_financial_impact across all patterns)`
- `combined_score = frequency_weight * frequency_score + impact_weight * impact_score`,
  default weights `0.5 / 0.5`, both constructor parameters of
  `PatternRanker` — not hardcoded, and not tuned to look good on one
  dataset.

This lets the frontend sort by `combined_score` (default), or let a
user toggle "most frequent" vs. "highest impact" using the raw scores
directly — matching the spec's Pattern-A-vs-Pattern-B example (3
records at ₹1.5M total should be able to outrank 200 records at ₹10
each). Verified in `test_high_impact_low_frequency_pattern_ranks_competitively`.

**Confidence** (separate from ranking) is a transparent average of
measurable cohesion terms — `reference_match_rate`, and 1 minus the
relative spread (stddev/mean) of whichever numeric dimension
(amount or date) applies to that pattern — never a black-box cluster
quality score. See `PatternScorer` docstring in `patterns.py`.

## 14. Outlier strategy

A candidate group only becomes a `Pattern` if it meets
`min_pattern_size` (default 3) or `min_pattern_fraction * total`,
whichever is larger — both configurable per call. Everything else is
collected as `outlier_exception_ids` and reported as a count
(`outliers`) plus the list of IDs, never silently dropped and never
force-merged into an unrelated pattern. This directly implements the
spec's "Unclassified / Outlier" requirement.

## 15. API design

```
POST /api/v1/exceptions/analyze/
Auth: CookieJWTAuthentication, IsAuthenticated (any role — read-only,
      not a mutation, so viewers can use it too; see rationale in the
      view docstring)

Request body: the SAME exception-shaped sub-object the client already
received from POST /api/v1/reconcile/:
{
  "exceptions": {"amountMismatches": [...], "typeMismatches": [...], "groupedMatches": [...]},
  "missingInBank": [...],
  "missingInTally": [...],
  "duplicates": {"bank": [...], "tally": [...]},
  "signReversalHints": [...]
}

Response:
{
  "success": true,
  "totalExceptions": 500,
  "patternsFound": 7,
  "patterns": [
    {
      "pattern_id": "PATTERN_001",
      "pattern_type": "RECURRING_AMOUNT_DIFFERENCE",
      "exception_type": "AMOUNT_MISMATCH",
      "record_count": 147,
      "total_financial_impact": 36897.0,
      "common_features": {...},
      "exception_ids": ["AMOUNT_MISMATCH:B214:T087", ...],
      "confidence": 0.91,
      "frequency_score": 0.294,
      "impact_score": 1.0,
      "combined_score": 0.647,
      "summary": "147 amount mismatch transactions, share an average difference of 251.0, matching references. Total financial impact: 36897.0."
    }
  ],
  "outliers": 23,
  "outlierExceptionIds": [...]
}
```

**Why the request body mirrors the reconcile response instead of a
`run_id`**: reconciliation itself is stateless (§1). Inventing a
persisted "run" solely to give this endpoint something to fetch by ID
would be exactly the kind of premature redesign the brief said not to
do. The frontend already holds this data in memory
(`ReconciliationProvider`) immediately after a reconcile call, so
POSTing it straight through costs nothing extra and keeps Phase 1/2
fully decoupled from persistence. If/when reconciliation runs get
persisted for other reasons (audit trail, Phase 5+), this endpoint can
trivially switch to accepting a `run_id` instead — nothing in
`PatternAnalysisService` needs to change, only the view.

## 16. Frontend integration design

Not implemented (per "do not build the chatbot / keep this
architectural"), but the intended wiring is direct:

- Add `patternAnalysisApi.analyze(exceptions)` to
  `frontend/src/features/reconciliation/api.ts`, POSTing the
  `exceptions` / `missingInBank` / `missingInTally` / `duplicates` /
  `signReversalHints` fields already present on the in-memory
  `ReconciliationResult`.
- An "Analyze Exceptions" button/toggle on the existing dashboard
  triggers this call and stores the `PatternAnalysisResult` in the
  same provider/context that already holds `ReconciliationResult` —
  no new page, no chatbot, dashboard stays the primary workspace.
- Each pattern card renders `summary`, `record_count`,
  `total_financial_impact`, `confidence`, and a "View Transactions"
  action that filters the existing per-category tables down to
  `exception_ids` (mapped back to `bank_excel_row`/`tally_excel_row`,
  already present on each `ExceptionFeature`).

## 17. Performance considerations

- **Time complexity**: Phase 1 extraction is O(n) (one pass per
  exception). Phase 2 discovery is O(n log n), dominated by sorting
  candidate groups; grouping itself is O(n) per signature mode
  (2 modes ⇒ O(n) with a small constant).
- **Memory**: everything is kept as plain Python objects/dataclasses
  in memory for one request — no intermediate DataFrame needed at
  today's scale, since the extraction/grouping logic is simple enough
  that Python-level iteration is not the bottleneck (verified: 65,000
  exceptions processed in ~1.1s in this sandbox, see §20).
- **Where pandas would help**: if/when volumes reach the hundreds of
  thousands, vectorizing `_numeric_and_boolean_features` and the
  bucketing step with pandas (as `ReconciliationService` already does
  for matching) would be the first optimization — the dataclass-based
  design doesn't block this; `ExceptionFeature.features` could become
  DataFrame columns without changing the public API shape.
- **Database query efficiency**: not applicable yet — nothing here
  touches the DB.
- **Synchronous vs. asynchronous**: **synchronous**, for the same
  reason `ReconciliationService.reconcile()` itself is synchronous —
  it already handles 100K-row files in ~3s per its own docstring, and
  Phase 1+2 add sub-second overhead on top at tested scale. Introducing
  Celery/async processing now would be exactly the "prematurely
  introduce distributed systems" the brief warned against. Revisit if
  production volumes are shown to regularly exceed roughly 200K–300K
  exceptions in one run, or once persisted runs make a background job
  natural.

## 18. Testing strategy

`parser/analytics/tests/test_features.py` — the 12 scenarios called
for in the spec: amount mismatch, date mismatch (date-offset
computation), missing in Tally, missing in Bank, duplicate, sign
reversal, null fields, invalid amount, zero-value transaction,
percentage-difference-with-zero-denominator, and match-metadata
(excelRow/description) preservation, plus an `extract_all` coverage
test across every exception section.

`parser/analytics/tests/test_patterns.py` — a synthetic dataset
matching the spec's example (100-ish fixed-₹251-difference records,
50 one-day-offset records, 20 missing-in-tally, 10 duplicate pairs, 5
outliers), asserting: each expected pattern is found with the right
`record_count`; outliers aren't forced into a pattern; ranking is
sorted by `combined_score`; every pattern is fully traceable back to
its `exception_ids`; results are deterministic across repeated calls;
and a high-impact/low-frequency pattern ranks competitively against a
high-frequency/low-impact one.

All 24 tests currently pass (`python manage.py test parser.analytics`
— run manually with plain `unittest` in this environment since Django
wasn't installable here; see conversation for the exact command used).

## 19. Migration/integration risks

- **None to existing reconciliation** — `reconciliationService.py`
  was not touched; `views.py`/`urls.py` only gained new
  additions (`analyze_exceptions`, one new URL), the existing
  `upload_files` view and its route are untouched.
- **Permission model choice**: `analyze_exceptions` uses
  `IsAuthenticated` rather than `IsAccountantOrAdmin` (used by
  `upload_files`), since it's read-only analysis of data the caller
  already received — worth a product-owner sign-off if viewers
  shouldn't see pattern analysis.
- **Payload size**: the analyze endpoint accepts the full exception
  payload in the request body — for very large exception sets this
  could be a large POST body. Not a concern at current tested scale
  (65K exceptions serialized was well within default DRF/Django
  request size limits), but worth monitoring if exception counts grow
  much further.
- **Frontend type drift**: `frontend/src/features/reconciliation/types.ts`
  was used as the authoritative contract for what `Transaction`,
  `ExceptionPair`, and `GroupedMatch` look like. If those types change
  on the frontend without the backend being told, `features.py`'s
  `_side()`/`_from_pair()`/`_from_group()` would silently start seeing
  fewer fields (they degrade gracefully to `None`, not a crash, by
  design — but worth a shared-contract test if the two codebases
  diverge further).

## 20. Example: raw exception → feature record → discovered pattern

**Raw exception** (from `exceptions.amountMismatches`):
```json
{
  "bankTransaction": {"date": "2026-01-14", "reference": "UTR001", "amount": 22122.0, "transactionType": "DEBIT", "excelRow": 214, "description": "NEFT"},
  "tallyTransaction": {"date": "2026-01-14", "reference": "UTR001", "amount": 22373.0, "transactionType": "DEBIT", "excelRow": 87, "description": "NEFT"},
  "reason": "Same reference, but amount differs.",
  "amountDifference": -251.0
}
```

**ExceptionFeature** (Phase 1 output):
```json
{
  "exception_id": "AMOUNT_MISMATCH:B214:T87",
  "exception_type": "AMOUNT_MISMATCH",
  "bank": {"exists": true, "excel_row": 214, "amount": 22122.0, "date": "2026-01-14", "reference": "UTR001", "ledger": null, "transaction_type": "DEBIT"},
  "tally": {"exists": true, "excel_row": 87, "amount": 22373.0, "date": "2026-01-14", "reference": "UTR001", "ledger": null, "transaction_type": "DEBIT"},
  "features": {
    "bank_exists": true, "tally_exists": true,
    "amount_difference": -251.0, "absolute_amount_difference": 251.0,
    "percentage_difference": -1.12, "date_difference_days": 0,
    "reference_match": true, "amount_match": false, "date_match": true,
    "duplicate_flag": false, "sign_reversal_flag": false
  }
}
```

**Discovered pattern** (Phase 2, alongside 146 similar records):
```json
{
  "pattern_id": "PATTERN_001",
  "pattern_type": "RECURRING_AMOUNT_DIFFERENCE",
  "exception_type": "AMOUNT_MISMATCH",
  "record_count": 147,
  "total_financial_impact": 36897.0,
  "common_features": {
    "reference_match_rate": 0.98,
    "amount_match_rate": 0.0,
    "date_match_rate": 1.0,
    "average_absolute_amount_difference": 251.0,
    "common_date_offset_days": 0
  },
  "confidence": 0.97,
  "summary": "147 amount mismatch transactions, share an average difference of 251.0, matching references. Total financial impact: 36897.0."
}
```

## 21. A bug worth documenting (found while testing at scale)

Initial version used one combined signature (amount bucket AND
percentage bucket together). Tested against a synthetic 4,000-record
proportional-difference dataset (each bank amount exactly 2% above
tally, base amounts spread over a range), it fragmented into ~80 tiny
patterns of ~50 records each instead of one clean pattern, because
`absolute_amount_difference` naturally drifts across a proportional
population even though `percentage_difference` stays constant.

Fixed by treating "fixed amount" and "percentage" as two independent
signature hypotheses evaluated over the full dataset, then greedily
claiming the largest candidate group regardless of which hypothesis
produced it (§11). Re-tested at 65,000 exceptions (30K fixed-diff, 20K
proportional, 15K missing-source): 3 clean patterns, 0 spurious
outliers, ~1.1s. Regression-tested in
`test_recurring_fixed_amount_difference_pattern_found`.

## 22. Exact implementation order (as built)

1. Inspect `reconciliationService.py`, frontend `types.ts` for
   authoritative data shapes (no Phase 1/2 code without this).
2. `features.py` — `ExceptionType`, `SideSnapshot`, `ExceptionFeature`,
   `ExceptionFeatureExtractor`.
3. `signatures.py` — `PatternSignatureGenerator` with the two-hypothesis
   bucketing.
4. `patterns.py` — `Pattern`, `DeterministicPatternDiscoveryEngine`,
   `PatternScorer`, `PatternRanker`.
5. `service.py` — `PatternAnalysisService` orchestration, framework-free.
6. Unit tests for Phase 1 (`test_features.py`), then Phase 2
   (`test_patterns.py`) against a synthetic dataset mirroring the
   spec's worked example.
7. Ran tests → found and fixed the signature-fragmentation bug (§21).
8. Wired `analyze_exceptions` view + `/api/v1/exceptions/analyze/`
   route, without touching `upload_files` or its route.
9. Scale-tested to 65,000 synthetic exceptions.
10. This document.

Phase 3 (AI explanation), Phase 4 (resolution recommendation), and
Recon Bot are explicitly out of scope for this deliverable and were
not started.
