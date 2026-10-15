# `analytics/explanation/learning/` — README

This package answers two specific questions raised about the Phase 3
explanation engine:

> **(a)** Can we learn `scoring.py`'s weights from real outcomes instead
> of hand-picking them?
> **(b)** Can we turn the resulting score into a genuinely calibrated
> probability instead of trusting an arbitrary threshold?

This document walks through exactly what was built, why each piece
exists, and how it fits into the existing Phase 1/2/3 pipeline you
already have documented. Read this before touching any file in this
folder.

---

## 1. The problem this solves

`scoring.py`'s formula is:

```
score = 0.10                                    (BASE_RETRIEVED_SCORE)
      + 0.30 * pattern_confidence                (PATTERN_CONFIDENCE_WEIGHT)
      + min(0.20 * support_count, 0.50)           (SUPPORT_CONDITION_POINTS, capped)
      - 0.35 * contradiction_count                (CONTRADICTION_PENALTY)
```

Every one of those four numbers — `0.10`, `0.30`, `0.20`, `0.35`, and
the thresholds `0.70`/`0.40`/`0.25` — is a **documented guess**. The
module docstring is explicit that the formula is transparent and
deterministic, never that the constants were derived from real
outcomes. That's a reasonable place to start with zero data. It stops
being reasonable once accountants start actually confirming or
rejecting the causes the system suggests — at that point you have
labeled training data sitting unused.

`(a)` and `(b)` turn that accumulating feedback into a real, if simple,
supervised learning problem, while preserving every property this
codebase already insists on elsewhere:

- **Deterministic at inference** — same input always produces the same
  output, given a fixed `trained_weights.json`. No sampling, no
  randomness in the request path.
- **Fully inspectable** — every learned number is a plain float in a
  JSON file, not a black box. You can open `trained_weights.json` and
  read exactly what the model learned, the same way you read
  `genericBank.json` to see what header aliases are configured.
- **Never worse than what already existed** — a cause with no
  feedback (which is every cause, on day one) behaves *exactly* like
  the untouched rule-based formula. This is proven, not just claimed
  — see §5.

---

## 2. The pieces, in the order data flows through them

```
CauseFeedback (Django model, parser/models.py)
        │  .to_feedback_record()
        ▼
FeedbackRecord            (learning/schema.py)
        │  grouped by cause_id
        ▼
CauseScorerTrainer.fit_all()   (learning/trainer.py)
        │  per cause_id, if enough data:
        │    1. LogisticRegression, cross-validated, for calibration input
        │    2. fit_calibration_curve() — isotonic regression   (learning/calibration.py)
        │    3. LogisticRegression refit on ALL that cause's data — production weights
        ▼
TrainedCauseModel          (learning/schema.py)
        │
        ▼
save_trained_models()      (learning/store.py)  →  trained_weights.json
        │
        │  (separately, at request time)
        ▼
LearnedCauseScoringEngine.score_all()   (learning/learned_scorer.py)
        │  per candidate cause:
        │    trained model exists?  →  learned score (this file)
        │    no trained model?      →  CauseScoringEngine._score_one() (unmodified formula)
        ▼
ScoredCause   (same shape scoring.py always produced — nothing downstream changes)
```

### 2.1 `feature_vector.py` — the shared vocabulary

Both training and inference need to describe "how well does this
pattern's evidence match this cause's conditions" as numbers. This
file is the single place that does that conversion, so training data
and live inference data are guaranteed to be built the same way.

For a given `CauseKnowledge` entry (e.g. `BANK_SERVICE_CHARGE`, which
declares two supporting conditions and one contradicting condition),
`build_feature_vector(context, entry)` returns something like:

```python
{
    "pattern_confidence": 0.91,
    "support::reference_match_rate::>=::0.9": 1.0,
    "support::common_date_offset_days::==::0": 1.0,
    "contradict::reference_match_rate::<::0.5": 0.0,
}
```

Two rules matter here, both carried over unchanged from `scoring.py`:

1. **Each cause has its own feature space.** `TAX`'s conditions aren't
   `BANK_SERVICE_CHARGE`'s conditions, so there's no shared "global"
   feature vector across different causes — this is why training
   happens **per cause_id** (§2.3), not as one big multi-class model.
2. **A missing feature is encoded as `0.0`, never omitted, never
   `1.0`.** `FeatureCondition.evaluate()` returns `None` when a
   feature genuinely isn't present in `aggregate_stats` — this file
   converts that `None` to `0.0`, matching the exact "missing evidence
   is never positive evidence" rule `scoring.py`'s own docstring
   insists on. This isn't a new decision; it's the same rule applied
   in a new place.

Feature *names* are derived directly from `(feature, operator, value)`
— if you edit a threshold in `catalogue.py` (say, change `>= 0.90` to
`>= 0.85`), the resulting feature name changes too. That's
intentional: old feedback trained against the old threshold shouldn't
silently be treated as if it were evidence for the new one. See
`trainer.py`'s docstring for exactly what happens to old feedback when
this occurs (short version: it's still used, with the since-changed
condition read as `0.0`, i.e. "we don't have this signal for this old
record" — conservative, not wrong, but worth knowing about).

### 2.2 `schema.py` — the two data shapes

- **`FeedbackRecord`** — one accountant decision. Captures the feature
  vector *at the time the feedback was given* (not recomputed later),
  so a subsequent catalogue edit can't retroactively rewrite what
  historical training data "actually" showed.
- **`TrainedCauseModel`** — one cause's learned model: `feature_names`
  + `coefficients` (same order, one weight per feature) + `intercept`,
  plus `calibration_x`/`calibration_y` (the isotonic calibration
  curve, as plain sorted breakpoint lists). Both are plain dataclasses
  with `to_dict()`/`from_dict()` — **nothing here is ever a pickled
  scikit-learn object**, by design (see §2.4).

### 2.3 `trainer.py` — the actual learning

`CauseScorerTrainer.fit_all(records)`:

1. Groups feedback by `cause_id`.
2. For each cause_id with **≥ 20 total records AND ≥ 5 of each outcome**
   (`MIN_SAMPLES_PER_CAUSE`, `MIN_PER_CLASS` — tune these if you want),
   trains a model. Anything below that bar is silently skipped — not
   an error, just "not enough data yet for this specific cause."
3. Training itself is two passes, which is the part worth understanding:
   - **Pass 1 (calibration only):** `sklearn.model_selection.cross_val_predict`
     with `StratifiedKFold` produces **out-of-fold** predicted
     probabilities — i.e., each record's prediction comes from a model
     that never saw that record during its own fit. These go into
     `fit_calibration_curve()` (isotonic regression) to learn the
     mapping "the model said 70% confident → how often was it actually
     right?" If you skipped this and calibrated against the same data
     the model was fit on, the curve would just rubber-stamp the
     model's own overconfidence.
   - **Pass 2 (production weights):** a fresh `LogisticRegression` is
     fit on **all** of that cause's feedback (no held-out split) — this
     is what ships as `coefficients`/`intercept`. Using every example
     here is fine and correct; only the *calibration* curve needed the
     out-of-fold discipline, not the weights themselves.
4. Why logistic regression specifically: it's the direct learned
   analogue of `scoring.py`'s own linear formula —
   `score = 0.10 + 0.30*confidence + ...` is a weighted sum squashed
   into `[0, 1]`; `sigmoid(intercept + Σ w_i·x_i)` is the same shape,
   just with `w_i` and the intercept coming from data instead of a
   guess. Every weight is one interpretable number, matching the
   "no black box" bar the rest of Phase 3 holds itself to.

`predict_raw_probability(model, vector)` is the inference-side half:
pure Python (`math.exp`, no sklearn import) — applying an already-fit
model doesn't need the ML library, only fitting one does.

### 2.4 `calibration.py` — why this is a separate step

A logistic regression's own `predict_proba` is only "calibrated" in
the limit of lots of data; on the handful of feedback examples you'll
have early on, it commonly is not (a model can be very confident and
wrong just as easily as it can be timid and right). Isotonic
regression fixes this cheaply by learning a **non-decreasing step
function** mapping "raw predicted probability" → "empirical fraction
actually correct," fit on the out-of-fold predictions from §2.3.

The important engineering choice here: **scikit-learn is only imported
at training time** (`fit_calibration_curve`, lazily inside the
function). At inference time, `apply_calibration()` is nothing but
`numpy.interp` over two plain lists — no live dependency on a fitted
sklearn object, no version-compatibility risk between when a model was
trained and when it's loaded in a running server, and the whole
calibration curve is just two columns of numbers you could paste into
a spreadsheet if you wanted to eyeball it.

### 2.5 `store.py` — the JSON file, not a pickle

`trained_weights.json` (one file, all causes, keyed by `cause_id`) is
the only artifact this whole package produces. It's deliberately:

- **Plain JSON**, not a pickle — open it in a text editor, diff it in
  a PR, understand exactly what changed after a retrain.
- **Loaded once at process start** (or whenever you construct a new
  `LearnedCauseScoringEngine()`), not re-read per request.
- **Merge-friendly** — `train_cause_scorer` (the management command,
  §4) merges newly-trained causes into whatever was already on disk,
  so a cause that temporarily has too little fresh data doesn't lose
  its last-known-good model.

### 2.6 `learned_scorer.py` — the hybrid, drop-in engine

`LearnedCauseScoringEngine` **extends** `CauseScoringEngine` and
overrides only `_score_one`:

```python
def _score_one(self, context, candidate):
    model = self.trained_models.get(candidate.knowledge.cause_id)
    if model is None:
        return super()._score_one(context, candidate)   # unmodified rule-based formula
    ...                                                   # learned + calibrated score
```

Everything else — `score_all`'s sort/tie-break, the `ScoredCause`
shape, how evidence text is phrased (still `CauseScoringEngine._phrase`,
inherited) — is untouched. This is why `analytics/explanation/service.py`
only needed a **one-line change**: swap the default `scorer=` from
`CauseScoringEngine()` to `LearnedCauseScoringEngine()`. Nothing in
`explanation_generator.py` or `grounding.py` had to change at all —
they only ever see `ScoredCause` objects and don't care which engine
produced them.

---

## 3. What's persisted, and why this is the one exception to "nothing is stored"

Every other part of Phase 1/2/3 is stateless — reconciliation results
and pattern analyses are recomputed fresh on every request, nothing
touches a database. `CauseFeedback` (in `parser/models.py`) is the
deliberate, sole exception, because training data is the one thing
that genuinely has to accumulate across many separate reconciliations
over time — a learned model is only as good as how much feedback
exists, and feedback from last month's reconciliation is exactly as
valuable as feedback from five minutes ago.

`CauseFeedback` stores:

- `cause_id`, `pattern_type`, `exception_type` — enough to know what
  was being judged.
- `features` (JSON) — the exact output of `build_feature_vector` at
  the moment of feedback. **Never raw transaction data.** This is the
  same "never a raw dump" discipline `PatternContext` already applies
  everywhere else in Phase 3.
- `confirmed` — the accountant's yes/no.
- `submitted_by`, `created_at` — audit trail.

It's captured via a new endpoint:

```
POST /api/v1/exceptions/patterns/<pattern_id>/causes/feedback/
{
  ...same exception payload as /exceptions/analyze/...,
  "causeId": "BANK_SERVICE_CHARGE",
  "confirmed": true
}
```

which follows the exact same "re-derive the pattern fresh from the
resent exception payload" pattern `/exceptions/analyze/` and
`/exceptions/patterns/<id>/explain/` already use — nothing new is
invented in how the request works, it's the same stateless-request
shape you already had, with one new write action added: accountant/
admin only, matching `/reconcile/`'s own permission level (this is a
judgment call that trains a model other users will later rely on, so
it's treated as a write, not a read).

---

## 4. Retraining

```
python manage.py train_cause_scorer            # trains everything with enough data
python manage.py train_cause_scorer --dry-run  # report what would happen, write nothing
python manage.py train_cause_scorer --min-cause-id BANK_SERVICE_CHARGE   # just one cause
```

This is **not** triggered automatically by anything in the request
path — `LearnedCauseScoringEngine` only ever *reads*
`trained_weights.json`; nothing in `views.py` or `service.py` writes
to it. Run this by hand, on a cron, or from whatever job runner your
deployment already uses, on whatever cadence makes sense once feedback
starts accumulating (weekly is a reasonable starting point — there's
no benefit to retraining more often than meaningful new feedback
volume arrives).

---

## 5. The guarantee this all rests on

The one thing that had to be true before shipping this as the new
default: **a cause with zero feedback must score identically to the
original, unmodified rule-based formula.** This was verified directly,
not just argued for — `learning/tests/test_learned_scorer.py`'s
`ColdStartFallbackTests` asserts exact score/confidence-level/evidence
equality between `CauseScoringEngine` and `LearnedCauseScoringEngine`
when `trained_models={}`, and the same test also confirms an
*individual* untrained cause_id falls back correctly even when *other*
causes in the same batch do have trained models (i.e., the hybrid
behavior is per-cause, not all-or-nothing).

This is what makes it safe that `analytics/explanation/service.py` now
defaults to `LearnedCauseScoringEngine()` on day one, before a single
piece of feedback has been collected — behavior doesn't change at all
until real data starts arriving.

---

## 6. Known limitations / deliberately out of scope here

- **Thresholds (`0.70`/`0.40`/`0.25`) are still shared, hand-picked
  constants**, imported from `scoring.py` rather than learned — a
  learned score is calibrated in the sense that "0.7 means ~70% right,"
  but nothing here has yet learned *where the best cut points are* for
  turning that into "high/medium/low." Worth revisiting once enough
  calibrated data exists across many causes to check whether those cut
  points are still the right ones.
- **No online/incremental learning.** Retraining is a full batch job
  (§4), not something that updates live as feedback arrives. Fine at
  this data volume; would need revisiting at much larger scale.

---

## 7. Point (c) — case-based reasoning for the sparse-data middle tier

**Status: implemented.** `similar_cases.py` + `db_bridge.py` fill the
gap called out in §6 of the original version of this document: a
cause with, say, 8 feedback records (above zero, below the 20/5-per-
class bar `trainer.py` requires) used to get no benefit at all — it
fell all the way back to the rule-based formula, identical to a cause
with *zero* feedback. That's now a proper third tier.

### 7.1 The technique: k-Nearest Neighbors + Bayesian shrinkage

This is deliberately a different ML technique from (a)/(b)'s logistic
regression — **k-NN is instance-based learning**: instead of fitting
parameters that summarize a whole dataset, you find the K most similar
past examples and let them vote directly. It needs no minimum sample
count to produce *some* reasonable estimate, which is exactly the
property that makes it fit this specific gap between "zero data" and
"enough data to train a stable parametric model."

`CaseIndex.nearest(query_vector, k=5)` measures Euclidean distance
between the live pattern's feature vector — the exact same vector
`feature_vector.build_feature_vector` produces for tiers 0 and 2, no
new features introduced — and every past feedback record for that
cause_id, returning the K closest.

A raw "3 of 3 nearby cases were confirmed" would overstate what three
examples can tell you, so the neighbor vote is blended with the
rule-based formula's own score as a **prior**, via additive
(Bayesian/Laplace-style) smoothing:

```
score = (confirmed_among_neighbors + PRIOR_STRENGTH * prior)
        / (neighbors_used + PRIOR_STRENGTH)
```

With zero neighbors this collapses *exactly* to the rule-based prior —
no discontinuity at the boundary between "no feedback" and "a little
feedback." `PRIOR_STRENGTH = 3.0` means the rule-based prior counts for
roughly 3 pseudo-observations; tune this if the blended score feels
too sticky (raise it) or too jumpy (lower it) once real feedback
starts flowing.

### 7.2 The resulting three-tier hybrid

```
Tier 0  (0 feedback records for this cause_id)
          -> pure rule-based formula (scoring.py, byte-for-byte unchanged)
Tier 1  (some feedback, below trainer.py's training minimum, NEW)
          -> k-NN + shrinkage toward the rule-based prior (similar_cases.py)
Tier 2  (>= MIN_SAMPLES_PER_CAUSE, >= MIN_PER_CLASS of each outcome)
          -> trained-and-calibrated logistic regression (trainer.py)
```

`learned_scorer.py`'s `_score_one` now checks tiers in that order —
per *candidate*, not globally, so one pattern's explanation can
legitimately mix a Tier 2 cause with a Tier 0 cause if only one of the
candidates has accumulated feedback. Verified directly (not just
argued for): a cause with a trained model behaves exactly as before,
a cause with 4 feedback records and no trained model correctly moves
its score away from the pure rule-based baseline in the direction the
neighbor evidence supports, and a cause with zero feedback in either
tier is still byte-for-byte identical to the untouched rule-based
formula.

### 7.3 Why this needed a new Django-touching file

Every file in this package except one is deliberately Django-free —
they operate on the plain `FeedbackRecord` dataclass and can be
imported/tested with zero Django configuration (confirmed directly:
importing the entire package, including `db_bridge.py` itself,
succeeds with no Django installed at all). `db_bridge.py` is the one
exception, clearly labeled as such in its own docstring: it's the
single place that queries the live `CauseFeedback` table and converts
rows into `FeedbackRecord`s, because *something* has to bridge
"database rows" to "plain dataclasses," and isolating that bridge to
one small file keeps everything else in the package testable without
a database.

Case indexes are deliberately **not** cached the way `trained_weights.json`
is — Tier 1 exists specifically to reflect feedback that hasn't
accumulated enough to retrain yet, so `db_bridge.load_case_indexes()`
queries fresh on every request that reaches `views.py`'s
`explain_pattern`. At the data volumes this table is expected to hold
(feedback rows, not raw transactions), that's cheap; if it ever
becomes a real cost, the fix is a short-TTL cache in `db_bridge.py`
alone (the same cache framework `cache.py` already uses for
explanations) — no other file in this package would need to change.

### 7.4 Wiring

`views.py`'s `explain_pattern` now builds the scorer explicitly instead
of relying on `PatternExplanationService`'s zero-argument default:

```python
scorer = LearnedCauseScoringEngine(case_indexes=db_bridge.load_case_indexes())
explanation_service = PatternExplanationService(scorer=scorer)
```

This still sits inside the same `try/except` that already existed —
if querying feedback or building the index ever fails, Phase 1/2
results are still returned, just without an explanation, exactly like
any other Phase 3 failure mode this codebase already handles. Tier 2
(trained models) is unaffected — `LearnedCauseScoringEngine` still
loads `trained_weights.json` via its own default when `trained_models`
isn't passed explicitly.

**Also wired:** `views.py`'s `reconbot_ask` (the chat bot endpoint) now
builds the same `LearnedCauseScoringEngine(case_indexes=...)` and
passes it into `ReconBotService(explanation_service=PatternExplanationService(scorer=scorer))`,
so the bot's "why" answers get Tier 1 too — not just the `/explain/`
REST endpoint. `reconbot/service.py` itself was deliberately left
unchanged: it still constructs `PatternExplanationService()` with no
arguments as its own internal default, so it remains fully
Django-independent and testable on its own (consistent with the rest
of the reconbot package's design). The DB-touching wiring lives only
in `views.py`, the one place in this call chain that was already
Django-aware — same division of responsibility as `explain_pattern`.

---

## 8. Closing the Phase 2 / Phase 3 "loose connection"

**The risk:** `knowledge/loader.py`'s `KNOWN_PATTERN_TYPES` is a
hand-maintained copy of every `pattern_type` string
`patterns.py`'s `DeterministicPatternDiscoveryEngine._classify()` can
produce. If a future change to Phase 2 — most plausibly point (e), a
clustering-based discovery engine, which `patterns.py`'s own docstring
explicitly anticipates being swapped in later — ever introduces a new
`pattern_type` without a matching update here, that new pattern_type
would silently retrieve zero catalogue candidates and produce an
explanation indistinguishable from "we checked thoroughly and found no
strong cause," when the real story is "we never recognized this
category at all." Three changes close this gap:

1. **`patterns.py` now declares `PatternType`** — a namespace class
   (matching the existing `ExceptionType` convention in `features.py`)
   listing every string `_classify()` can return, plus a `PatternType.ALL`
   tuple. `_classify()` itself was refactored to return these named
   constants instead of raw string literals — same exact values, zero
   behavior change (verified: a full `PatternAnalysisService.analyze()`
   run before and after produces identical `pattern_type` strings).
   This gives Phase 2 a single, authoritative, importable vocabulary
   instead of scattered string literals — the thing a future
   clustering engine should extend if it needs a new category, and the
   thing tests can check against.

2. **A new, mechanical test** — `explanation/tests/test_pattern_type_consistency.py` —
   asserts `set(PatternType.ALL).issubset(KNOWN_PATTERN_TYPES)`. This
   replaces what `ARCHITECTURE_PHASE3_LOCAL.md` describes as a fragile,
   regex-based scan of `_classify()`'s source code with a structural
   comparison of two real, importable sets — robust even if `_classify()`
   is rewritten entirely or replaced by a different discovery engine,
   as long as that engine declares its output vocabulary in
   `PatternType` (or extends it) rather than hand-typing strings.
   Verified directly: with the codebase as it stands today the test
   passes; simulating a hypothetical new pattern_type added to
   `PatternType.ALL` without a matching `KNOWN_PATTERN_TYPES` update
   makes it fail with exactly the missing type named in the message.

3. **A runtime guard, not just a build-time one** —
   `explanation/service.py`'s `explain()` now checks
   `context.pattern_type in KNOWN_PATTERN_TYPES` on every request (not
   just at catalogue-load time, which only ever validated the
   catalogue's own entries, never a live pattern coming from Phase 2).
   When unrecognized, it logs a distinct `unrecognized_pattern_type`
   event (the thing to alert/monitor on) and passes
   `pattern_type_recognized=False` into
   `DeterministicExplanationGenerator.generate()`
   (`explanation_generator.py`), which produces a specific, actionable
   message —

   > "Pattern type 'X' is not recognized by the current knowledge
   > catalogue, so no candidate causes could be evaluated for it at
   > all — this is different from having evaluated candidates and
   > found none strong enough..."

   — instead of the generic "no known cause matched" text a genuinely
   evidence-weak-but-recognized pattern gets. Verified directly: a
   known pattern_type (`MISSING_SOURCE`) is completely unaffected; a
   fabricated unrecognized pattern_type produces exactly this distinct
   message and nothing else.

This means whoever eventually builds (e) doesn't need to remember any
of this — if they extend `PatternType` and forget to update
`KNOWN_PATTERN_TYPES`, the test in step 2 fails their build; if
somehow that's missed anyway, the runtime guard in step 3 still
prevents silent misrepresentation in production and gives an explicit
log line to catch it.

