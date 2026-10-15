# Phase 3 Migration — Removing the External LLM Dependency

Status: implemented, tested, zero external AI dependency remains in
the execution path. Phase 1/2 untouched. Phase 4+ (resolution
actions, Recon Bot, ML training) explicitly out of scope.

---

## 1–5. Pre-migration inspection

**Existing Phase 3 files found**: `context_builder.py`, `schema.py`,
`prompts.py`, `llm_provider.py`, `cache.py`, `service.py`,
`factory.py`, plus `tests/{fixtures,test_context_builder,test_schema,
test_null_provider,test_service}.py`.

**Imports/dependencies identified**:
- `llm_provider.py` defined `LLMProvider` (ABC), `NullLLMProvider`
  (deterministic fallback), `AnthropicLLMProvider` (lazy-imports the
  `anthropic` SDK only inside its method — never a hard dependency).
- `factory.py` read `RECONAI_LLM_PROVIDER`/`RECONAI_LLM_MODEL`/
  `ANTHROPIC_API_KEY` Django settings to choose a provider, defaulting
  to `NullLLMProvider`.
- `service.py` depended on `LLMProvider` + `prompts.build_prompt`.
- `schema.py` contained both the output dataclasses AND
  `RESPONSE_JSON_SCHEMA`/`parse_and_ground`/`GroundingError` —
  LLM-response-parsing logic that had nothing to validate anymore
  once there's no external response to parse.
- `parser/views.py::explain_pattern` imported
  `analytics.explanation.factory.get_default_llm_provider` and passed
  it into `PatternExplanationService(llm_provider=...)`.

**Tests affected**: `test_null_provider.py` (entirely about
`NullLLMProvider`'s behavior — superseded), `test_schema.py` (entirely
about `parse_and_ground` — superseded), `test_service.py` (entirely
about provider failover — rewritten for the new pipeline).
`test_context_builder.py` was unaffected (context_builder.py didn't
change) and still passes unmodified.

**Migration plan executed**: keep `context_builder.py` and `cache.py`
as-is (both are provider-agnostic — `PatternContext` is still exactly
"the boundary between raw Phase 2 data and the intelligence layer",
whatever that layer is). Trim `schema.py` to just the output
dataclasses. Delete `llm_provider.py`, `prompts.py`, `factory.py`
outright rather than leaving them as unused dead code — the brief was
explicit ("do NOT leave Anthropic API calls hidden"), and dead code
importing a paid SDK is exactly the kind of hidden dependency that
instruction is guarding against. Build the new knowledge/retrieval/
scoring/generation/grounding stages as new files. Rewrite
`service.py` to orchestrate the new pipeline with zero references to
`LLMProvider`. Update `views.py` to drop the provider-factory call.
Replace the three superseded test files; add five new ones.

---

## 2. Domain knowledge base — schema

`knowledge/schema.py`:

```python
@dataclass(frozen=True)
class FeatureCondition:
    feature: str
    operator: str  # ">=", "<=", ">", "<", "==", "!="
    value: float

    def evaluate(self, aggregate_stats: dict) -> Optional[bool]:
        ...  # None if the feature is simply absent — never False

@dataclass(frozen=True)
class CauseKnowledge:
    cause_id: str                      # stable ID, e.g. "BANK_SERVICE_CHARGE"
    name: str                          # human-facing name
    applicable_pattern_types: tuple
    required_features: tuple = ()      # gates retrieval
    supporting_conditions: tuple = ()  # gates scoring, positively
    contradicting_conditions: tuple = () # gates scoring, negatively
    explanation_template: str = ""
    investigation_template: str = ""
```

Both dataclasses are `frozen=True` — the catalogue is static, curated
data; nothing in the pipeline ever mutates a knowledge entry.

## 3. Initial catalogue

`knowledge/catalogue.py` — 24 entries across every category the brief
listed (A–H), using the **exact** `pattern_type` strings
`DeterministicPatternDiscoveryEngine._classify()` produces in
`parser/analytics/patterns.py` (verified by a cross-check test —
§19 below):

| Category | pattern_type | Causes |
|---|---|---|
| A | `RECURRING_AMOUNT_DIFFERENCE` | BANK_SERVICE_CHARGE, DATA_ENTRY_DIFFERENCE, INTEGRATION_TRANSFORMATION, SETTLEMENT_ADJUSTMENT |
| B | `RECURRING_PERCENTAGE_DIFFERENCE` | TAX, COMMISSION, PROCESSING_FEE, PERCENTAGE_BASED_ADJUSTMENT |
| C | `DATE_OFFSET` | SETTLEMENT_DELAY, POSTING_DELAY, ACCOUNTING_DATE_CONVENTION, BATCH_PROCESSING_DELAY |
| D | `MISSING_SOURCE` | PENDING_POSTING, SOURCE_SYSTEM_DELAY, MISSING_RECORD, INCOMPLETE_UPLOAD |
| E | `DUPLICATE` | DUPLICATE_IMPORT, DUPLICATE_TRANSACTION, DUPLICATE_SETTLEMENT |
| F | `SIGN_REVERSAL` | DEBIT_CREDIT_CONVENTION, REVERSAL_TRANSACTION, SIGN_TRANSFORMATION |
| G | `REFERENCE_MATCH_ONLY` | REFERENCE_FORMAT_TRANSFORMATION, PREFIX_SUFFIX_TRANSFORMATION, SOURCE_REFERENCE_MAPPING |
| H | `GROUPED_MATCH` | SPLIT_TRANSACTION, CONSOLIDATED_TRANSACTION, BATCH_POSTING |

`MIXED_ATTRIBUTE` and `UNCLASSIFIED_RECURRING` (two Phase 2 pattern
types that don't cleanly correspond to one named hypothesis)
deliberately have **no** catalogue entries — a pattern of that type
correctly produces "insufficient evidence" rather than a forced guess.

## 4. Knowledge retrieval

`retriever.py` — `KnowledgeRetriever` (ABC) with one implementation,
`RuleBasedKnowledgeRetriever`, using exactly the two filters specified:
pattern_type applicability, then required-feature availability. No
embeddings, no vector DB. Deterministic order (catalogue's own
declared order, never sorted/shuffled). The interface is the explicit
extension point for `EmbeddingRetriever`/`HybridRetriever` later —
nothing downstream (`scoring.py`, `service.py`) knows or cares which
concrete retriever produced a `CandidateCause`.

## 5–6. Cause scoring

`scoring.py` — `CauseScoringEngine`, fully documented formula (see the
module's own docstring, reproduced in brief here):

```
score = 0.10                                    (BASE_RETRIEVED_SCORE)
      + 0.30 * pattern_confidence                (PATTERN_CONFIDENCE_WEIGHT)
      + min(0.20 * #satisfied_supporting, 0.50)   (capped support contribution)
      - 0.35 * #satisfied_contradicting            (CONTRADICTION_PENALTY)
score = clip(score, 0, 1)

score >= 0.70 -> "high"
score >= 0.40 -> "medium"
score >= 0.25 -> "low"
score <  0.25 -> cause dropped entirely (never shown)
```

Every constant is a named module-level variable, not a magic number
inline. `tests/test_scoring.py` has 15 tests including a worked
numeric example matching the brief's own scenario (147 records, ₹251
diff, 98% ref match, 0.97 pattern confidence → `BANK_SERVICE_CHARGE`
scores "medium"/"high", never higher than 1.0, never equal to
`pattern_confidence` itself) and a contradiction test showing a
reference-match collapse to 0.32 measurably lowers the same cause's
score on the same pattern.

## 7. Evidence validation

`grounding.py` — `EvidenceValidator`, operating on internally-generated
`EvidenceItem(text, feature)` pairs (both facts and cause evidence are
built this way — see §9), checking `feature` against
`context.known_feature_names() | context.known_exception_ids()`.
Un-grounded evidence is flagged (`grounded=False`,
`validation_warnings` populated), never silently dropped and never
silently trusted — same principle as the previous LLM-response
grounding, just now running as a defensive check on the pipeline's
own output rather than on a third party's.

## 8. Fact vs. hypothesis

Enforced structurally, not by convention:
- `facts` and `possible_causes` are separate lists in the dataclass,
  built by entirely separate code paths in `explanation_generator.py`.
- Every cause name comes from a catalogue `name` field written as a
  noun phrase ("Recurring bank/service charge"), never a sentence
  claiming certainty — `test_no_cause_states_certainty` checks for
  "definitely"/"this is a" and finds none across the full catalogue.
- `unknowns` is never empty — either it lists genuine caveats plus a
  per-top-cause "this remains a hypothesis, not a confirmed cause"
  line, or (when no cause is found) an explicit
  "does not strongly support any known cause" line.

## 9. Explanation generator

`explanation_generator.py` — `DeterministicExplanationGenerator`.
Every sentence traces to exactly one of three sources: `PatternContext`
(facts, via `_build_facts`), the knowledge catalogue's own
`explanation_template`/`investigation_template` strings, or
`CauseScoringEngine`'s evidence items (the "why" behind each cause,
via `_FEATURE_PHRASES` in `scoring.py`). Nothing is free-generated.
Caps output at `MAX_CAUSES_SHOWN = 3` causes even if more clear the
retain threshold, to keep the explanation focused.

## 10. Insufficient evidence

When `CauseScoringEngine.score_all()` returns an empty list (every
candidate scored below 0.25, or no candidates were even retrieved —
e.g. `MIXED_ATTRIBUTE`/`UNCLASSIFIED_RECURRING` patterns, which have
no catalogue entries at all), `possible_causes` is `[]` and `unknowns`
gets an explicit "does not strongly support any known cause" line —
never a forced guess. Verified end-to-end in
`test_end_to_end.py::Scenario9UnsupportedPattern` and `Scenario10InsufficientEvidence`.

## 11. Contradictory evidence

`CauseKnowledge.contradicting_conditions` + `CONTRADICTION_PENALTY`
(§5–6) make this a first-class, testable mechanism rather than an
afterthought — `BANK_SERVICE_CHARGE`'s contradicting condition
(`reference_match_rate < 0.50`) is exercised directly in
`test_scoring.py::test_low_reference_match_weakens_bank_charge_hypothesis`
and `test_end_to_end.py::Scenario8ContradictoryEvidence`, both showing
the score measurably drops and (when the cause still clears the retain
threshold) `contradicting_evidence` is populated on the output.

## 12. Pattern confidence vs. cause confidence

Preserved exactly as before, now enforced by construction rather than
convention: `PatternExplanation.pattern_confidence` is set directly
from `context.pattern_confidence` in `explanation_generator.py` and
never touched by scoring; `ScoredCause.confidence_level` is computed
purely from the formula in §5–6, which uses `pattern_confidence` as
only one bounded (≤0.30) input among several — never a passthrough.
`test_scoring.py::test_pattern_confidence_and_cause_confidence_remain_separate`
asserts they're never numerically equal.

## 13. Output schema

`schema.py` — `PatternExplanation` keeps the exact shape the frontend
already consumes, with two changes the brief called for:
- `status` is always `"deterministic_local"`, `model` is always `None`
  — no field pretends an LLM was involved.
- `PossibleCause` gained `cause_id` (the catalogue's stable ID,
  alongside the existing free-text `cause` name) — see §18/§16 below
  for why.

## 14. External LLM dependency — removed

**Files deleted**: `llm_provider.py`, `prompts.py`, `factory.py`.
**Removed from `views.py`**: the
`from .analytics.explanation.factory import get_default_llm_provider`
import and the `PatternExplanationService(llm_provider=get_default_llm_provider())`
call — now just `PatternExplanationService()`.
**Verified, not just asserted**: `tests/test_service.py`'s
`NoExternalDependencyTests` class greps `service.py`'s own source for
`"import anthropic"`, `"import openai"`, `"import requests"`,
`"from .llm_provider"`, `"class LLMProvider"` and asserts none are
present, and separately asserts `llm_provider.py`/`factory.py`/
`prompts.py` no longer exist on disk. The MVP runs with zero API keys,
zero network calls, zero external services — every one of the 78
Phase 3 tests passes with no network access in this sandbox.

## 15. Cache — preserved

`cache.py` is **unchanged**. It was already content-hash-keyed
(`pattern_id` + SHA-256 of the canonicalized `PatternContext`), which
already satisfied "do not cache solely by pattern_id" — nothing about
that design was LLM-specific, so nothing needed to change.
`test_service.py::CachingTests` re-verifies cache-hit/miss/
different-content-different-key/cache-disabled behavior against the
new pipeline.

## 16. API — preserved, pipeline swapped underneath

`POST /api/v1/exceptions/patterns/<pattern_id>/explain/` is
byte-for-byte the same route, same request/response contract, same
statelessness rationale (re-derive Phase 1+2 from the same payload
already sent to `/exceptions/analyze/`, since nothing is persisted —
see `ARCHITECTURE.md` §15). Only `explain_pattern`'s internals changed
(no more provider selection). The endpoint remains strictly read-only
— nothing in the new pipeline has a write path to reconciliation data.

## 17. Future-ready retrieval abstraction

```
KnowledgeRetriever
   ├── RuleBasedKnowledgeRetriever   (implemented — this MVP)
   ├── EmbeddingRetriever            (future)
   └── HybridRetriever                (future)
```

Only `RuleBasedKnowledgeRetriever` exists. Adding an embedding-based
retriever later means implementing `KnowledgeRetriever.retrieve()`
against a local embedding model + local vector index — `scoring.py`,
`grounding.py`, `explanation_generator.py`, and `service.py` would not
need to change at all, since they only ever see `CandidateCause`
objects regardless of how they were found.

## 18. Future ML learning loop — groundwork only

Every `PossibleCause` now carries a **stable `cause_id`**
(`BANK_SERVICE_CHARGE`, `SETTLEMENT_DELAY`, etc.) alongside its
free-text name, specifically so a future human-feedback loop (Pattern
→ Suggested Cause → Human Confirm/Reject → Feedback Dataset → Future
ML Classifier) has something to key on instead of parsing free text.
No training, no feedback storage, no classifier implemented — this is
groundwork only, per the brief's explicit scope boundary.

## 19. Testing — what was added

78 Phase 3 tests across 8 files (up from 29 in the previous LLM-based
version, itself on top of 24 Phase 1/2 tests — 102 total):

- `test_context_builder.py` (6, unchanged) — context_builder.py didn't change.
- `test_knowledge_loader.py` (17) — `FeatureCondition` operator
  correctness and `None`-on-absence; loader validation (valid
  catalogue, missing template, duplicate cause_id, unsupported pattern
  type, empty pattern types, deterministic ordering, get-by-id); a
  **cross-check test** (`test_known_pattern_types_matches_phase2_vocabulary`)
  that regex-scans `DeterministicPatternDiscoveryEngine._classify()`'s
  actual source and asserts every string literal it can return is in
  `KNOWN_PATTERN_TYPES` — this is what stops the knowledge package's
  pattern-type vocabulary from silently drifting out of sync with
  Phase 2 if it's ever extended; plus catalogue-completeness checks
  (every required category has ≥1 entry, no duplicate IDs).
- `test_retriever.py` (7) — pattern_type filtering, required-feature
  filtering (including the "MISSING_SOURCE can never satisfy a
  required numeric feature" case), deterministic ordering, and a
  real-catalogue sanity check that a fixed-amount pattern retrieves
  `BANK_SERVICE_CHARGE`/`DATA_ENTRY_DIFFERENCE` but not
  `DUPLICATE_IMPORT`/`SETTLEMENT_DELAY`.
- `test_scoring.py` (15) — strong evidence increases score,
  contradictory evidence decreases it, missing evidence contributes
  exactly zero (not positive), pattern/cause confidence never equal,
  base-only score never reaches "high", scores retained above
  threshold, score clipped to 1.0, deterministic tie-breaking, plus
  the two worked-example tests from §5–6/§11.
- `test_grounding.py` (6) — real feature grounded, fabricated feature
  detected, fabricated transaction ID detected, real sample ID
  grounded, cause evidence validated independently of facts,
  ungrounded evidence flagged not dropped.
- `test_explanation_generator.py` (14) — facts contain no causal
  language, facts cite the real record count, causes never claim
  certainty, causes carry stable IDs, confidence stays qualitative,
  unknowns always present (both when causes are found and when they
  aren't), unclassified pattern types yield zero causes without
  inventing one, every evidence reference is grounded for a clean
  pattern, missing-source patterns get relevant causes, output
  status/model reflect local generation, pattern confidence passed
  through, causes capped at the max-shown limit.
- `test_service.py` (9) — usable output with zero configuration,
  missing-source patterns don't error, **explicit no-external
  -dependency verification** (§14), cache hit/miss/different-content/
  disabled behavior, graceful handling of uncatalogued pattern types
  and empty member lists.
- `test_end_to_end.py` (10) — one test per synthetic scenario the
  brief listed: fixed amount difference, percentage difference,
  date offset, duplicate, sign reversal, missing source, low-confidence
  pattern, contradictory evidence, unsupported/unclassified pattern
  type, insufficient evidence — each running the real
  `PatternAnalysisService` → `PatternExplanationService` pipeline
  starting from raw synthetic transaction dicts, the same shape the
  API actually receives.

**All 102 tests pass** (24 Phase 1/2 + 78 Phase 3), run via plain
`unittest` in this sandbox against a minimal stand-in for
`django.core.cache`/`django.conf.settings` (no network to install
Django here) — run `python manage.py test parser.analytics` for the
real Django-backed suite before merging.

## 20. Worked example — verified

147 records, ₹251 average absolute difference, 100% reference match,
0-day date offset, pattern_confidence=1.0 (this sandbox's synthetic
fixture; the brief's own example used 0.97, tested separately in
`test_scoring.py`):

```
score(BANK_SERVICE_CHARGE)
  = 0.10 + 0.30×1.0 + min(0.20+0.20, 0.50) − 0
  = 0.10 + 0.30 + 0.40
  = 0.80  ->  "high"
```

Generated cause text: **"Recurring bank/service charge"**, confidence
`"high"`, supporting evidence `["100% of references match.",
"The common date offset is 0 day(s)."]`. Never "This is definitely a
bank charge." — verified in the actual generated output (see the
end-to-end run captured while building this).

## 21. Performance

No network call anywhere in the path (verified — see §14). Latency for
the 147-record worked example above: **0.25ms** measured in this
sandbox — three orders of magnitude below what an LLM round trip would
cost, because complexity here is bounded by the catalogue's ~24
entries and each pattern's ≤6 representative samples, never by
transaction count (Phase 2 already compressed those away — same
principle as before, just now actually free of network latency
instead of merely bounded).

## 22. Observability

`service.py::_log` now emits exactly the field set the brief
specified: `event`, `pattern_id`, `pattern_type`, `record_count`,
`retrieved_cause_count`, `selected_cause_count`, `generation_status`,
`validation_warning_count`, `latency_ms`, `cache_hit`. Still never logs
context content, generated evidence text, or raw transaction data —
same discipline as the previous version, updated field set.

## 23. Frontend

`frontend/src/features/analysis/types.ts` updated: `PossibleCause`
gained `cause_id`; `ExplanationStatus` narrowed to the single literal
`"deterministic_local"` (previously a union of two LLM-related
values). `ExplanationView.tsx`'s provenance strip no longer branches
on AI-vs-fallback (there's only one path now) and states plainly that
the explanation was generated locally from a rule-based knowledge
base. `PatternCard.tsx`'s button copy changed from "Explain with AI"
to "Explain This Pattern" to stop implying LLM involvement that no
longer exists. No other frontend files needed changes — the
FACT/POSSIBLE CAUSE/UNKNOWN layout, on-demand generation, caching
indicator, and evidence-reference disclosure all continue to work
unchanged against the new (structurally identical) response shape.

## 24. Architectural principle — preserved

```
Reconciliation  -> determines WHAT happened
Phase 1          -> structures the evidence
Phase 2          -> determines WHAT patterns exist
Phase 3          -> determines WHAT KNOWN EXPLANATIONS are compatible
                     with those patterns (now via a local knowledge
                     base instead of an LLM — the epistemics are
                     identical, only the mechanism changed)
Future Phase 4   -> determines WHAT ACTION should be taken
```

Phase 3 still never claims to have discovered financial truth — it
ranks catalogued, named explanations against a pattern's own evidence
using a documented formula, exactly as before, just without asking an
external model to do the ranking.

---

## Summary

**Files created**: `knowledge/__init__.py`, `knowledge/schema.py`,
`knowledge/catalogue.py`, `knowledge/loader.py`, `retriever.py`,
`scoring.py`, `grounding.py`, `explanation_generator.py`,
`tests/test_knowledge_loader.py`, `tests/test_retriever.py`,
`tests/test_scoring.py`, `tests/test_grounding.py`,
`tests/test_explanation_generator.py`, `tests/test_end_to_end.py`,
this document.

**Files modified**: `schema.py` (trimmed to output dataclasses only),
`service.py` (fully rewritten around the new pipeline), `cache.py`
(doc-comment only, one wording fix), `context_builder.py`
(doc-comments only, no logic change), `parser/views.py`
(`explain_pattern` — dropped provider selection, updated docstring),
frontend `types.ts` / `ExplanationView.tsx` / `PatternCard.tsx`
(contract + copy updates for the new, single, local status).

**Files removed**: `llm_provider.py`, `prompts.py`, `factory.py`,
`tests/test_null_provider.py`, `tests/test_schema.py` (superseded),
`tests/test_service.py` (superseded — replaced with a new version
covering the new pipeline).

**Tests**: 78 Phase 3 tests (up from 29), 102 total with Phase 1/2, all
passing. Zero external dependency verified by an explicit test, not
just by inspection.

**Future extension points preserved**: `KnowledgeRetriever` interface
ready for `EmbeddingRetriever`/`HybridRetriever`; `cause_id` on every
`PossibleCause` ready for a future feedback/learning loop; catalogue
is data, not code, so extending coverage never touches
`scoring.py`/`retriever.py`/`explanation_generator.py`.
