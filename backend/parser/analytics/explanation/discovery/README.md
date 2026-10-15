# `analytics/explanation/discovery/` — README (point (d))

This package answers the question raised in the handoff prompt for
point (d):

> Can we automatically discover new candidate cause CONDITIONS from
> historical data, using association rule mining, instead of requiring
> a human to hand-write every entry in `catalogue.py`?

**This never touches `catalogue.py` automatically.** Every output is a
draft for a human to review and, if it looks right, copy by hand into
`catalogue.py` — see §4 for why.

**Not interconnected with (a)/(b)/(c):** this package doesn't import
`learning/`, and nothing in `learning/` imports this. It reads a
different column of the same `CauseFeedback` table (see §1) and writes
nothing back to it.

---

## 1. The schema decision (resolving the "wrinkle" from the handoff prompt)

The handoff prompt flagged this explicitly up front: `CauseFeedback.features`
(what (a)/(b)/(c) already store) only holds BOOLEAN evaluations of an
EXISTING cause's already-declared conditions — "was `reference_match_rate
>= 0.9` true." That tells you nothing about a condition nobody thought
to write down yet, which is exactly what discovery needs to find.

**Decision made: extend `CauseFeedback`, don't create a new table.**
`parser/models.py`'s `CauseFeedback` gained one new field,
`raw_stats` (a `JSONField`, migration `0003_causefeedback_raw_stats.py`)
— the RAW, continuous `PatternContext.aggregate_stats` at the moment
feedback was given (e.g. `{"average_absolute_amount_difference": 251.0,
"reference_match_rate": 0.94, ...}`). `views.py`'s `submit_cause_feedback`
already builds `context` at exactly the point it creates the
`CauseFeedback` row — capturing `context.aggregate_stats` alongside
the existing `feature_vector` cost nothing extra to wire in.

Why extend rather than add a new table:
- Every feedback submission is already one event with one natural
  timestamp/user/cause_id — splitting "boolean features" and "raw
  stats" about the SAME event into two tables would need a join to
  reconstruct anything, for no benefit.
- Old rows (before this field existed) simply have `raw_stats == {}` —
  `default=dict` means no data migration was needed, and `db_bridge.py`
  excludes empty `raw_stats` automatically. Not an error, just "this
  row has nothing for discovery to mine."

`CauseFeedback.to_resolved_case()` is the bridging method (parallel to
the existing `to_feedback_record()`), producing a `discovery.schema.ResolvedCase`
— the plain dataclass everything else in this package operates on.

---

## 2. The pieces, in the order data flows through them

```
CauseFeedback.raw_stats (Django model, parser/models.py)
        │  .to_resolved_case()
        ▼
ResolvedCase              (discovery/schema.py)
        │  grouped by cause_id (discoverer.py)
        ▼
discretizer.discretize_pool / discretize_case
        │  turns continuous stats into binary ConditionItem labels
        ▼
transactions: List[frozenset(labels + outcome item)]
        │
        ▼
itemset_mining.frequent_itemsets           (Apriori)
        │
        ▼
itemset_mining.generate_rules(consequent_filter="outcome:confirmed")
        │
        ▼
itemset_mining.remove_redundant_rules      (drop dominated supersets)
itemset_mining.deduplicate_equivalent_rules (drop statistical twins)
        │
        ▼
CandidateCauseDraft[]      (discoverer.py — catalogue-shaped, human-reviewable)
```

### 2.1 `discretizer.py` — why NOT generic quantile bins

The obvious default for discretizing a continuous feature is
`pandas.qcut` into "bin_0".."bin_3". That's wrong here specifically:
the whole point is that a discovered rule should be immediately
pasteable into `catalogue.py` as a `FeatureCondition(feature, operator,
value)` — "bin_2" doesn't tell a reviewer what threshold it means, and
stops meaning anything once the training pool that produced the bin
edges is gone. Every item this file produces is a complete `(feature,
operator, value)` triple from the start.

Two different strategies, chosen by feature shape:
- **Rate-like features** (any key containing `"rate"`) use FIXED,
  catalogue-familiar thresholds — 0.5, 0.8, 0.9, 0.95 — the same style
  every hand-written entry already uses.
- **`common_date_offset_days`** gets exactly two items — `== 0` and
  `>= 1` — matching how every hand-written entry already treats this
  feature (offset vs. no offset, not a magnitude).
- **Everything else** uses data-driven quantile thresholds (25th/50th/
  75th percentile of the current mining pool) — deterministic, no
  universal meaningful cutoff exists for a raw amount difference.

**A real bug found and fixed during development:** the first version
only ever emitted `feature>=threshold` items, never the "low value"
direction. This meant a genuine pattern like "a SMALL, fee-like amount
difference predicts confirmation" was structurally impossible to
discover — not filtered out, literally unrepresentable. Verified with
a synthetic scenario built specifically to need the missing direction:
it produced **zero** candidates before the fix, and the correct
candidate after. The fix: for every threshold, emit exactly one
directional item — `>=` if satisfied, `<` if not — so both directions
are always discoverable.

### 2.2 `itemset_mining.py` — hand-written Apriori, on purpose

Not a library (e.g. `mlxtend`) — this project's dependency list is
deliberately short, and at this data scale (hundreds/low thousands of
resolved cases, dozens of discretized items — not millions of
transactions) a ~150-line, fully-readable implementation is more
trustworthy than an opaque call into someone else's code, consistent
with this codebase's existing preference for transparent algorithms
over black boxes (logistic regression over an uninterpretable
classifier in `learning/trainer.py`; a documented arithmetic formula in
`scoring.py`).

**Verified against ground truth, not just internal consistency**: the
classic bread/milk/diaper/beer/cola market-basket example (a standard
textbook illustration with an independently known correct answer) —
`frequent_itemsets` and `generate_rules` reproduce every documented
support/confidence/lift value exactly, including correctly PRUNING
`{bread, milk, diaper}` (true support 0.4, below a 0.6 threshold) via
the apriori downward-closure property.

**A second real bug found and fixed**: a realistic mining run
initially produced **208 candidate drafts** for what was genuinely one
underlying finding — Apriori correctly explores every frequent
superset of a strong rule, so a single real pattern produces dozens of
"the same finding plus one more redundant condition" variants. This
directly defeats the goal of a small, human-reviewable list. Two
targeted fixes:
- `remove_redundant_rules` — drops any rule whose antecedent is a
  strict superset of another rule (same consequent) achieving equal or
  better confidence. A standard, well-established technique in
  association-rule-mining literature ("non-redundant"/"generalized"
  rules), not a novel invention here.
- `deduplicate_equivalent_rules` — catches a residual gap the subset
  filter can't: several DIFFERENT threshold variants of the same
  feature (e.g. `reference_match_rate >= 0.8` vs `>= 0.9` vs `>= 0.95`)
  that happen to be statistically IDENTICAL in the data at hand.
  Neither is a subset of the other, so the first filter can't catch
  it; this one collapses exact statistical ties to one representative.

Together these took the same scenario from **208 drafts down to 5**,
with the single most informative finding (`average_absolute_amount_difference
< 300 AND reference_match_rate >= 0.8`, confidence 1.00, lift 3.92,
n=42) surfacing cleanly at the top.

**Known, documented limitation (not silently pretended away):** the
dedup step only catches EXACT statistical ties. It doesn't understand
that `reference_match_rate >= 0.8` is inherently "looser" than `>= 0.9`
for the same feature and therefore preferable even when their stats
differ slightly — that would need the miner to understand ordering
relationships between different threshold-items of the same feature
("quantitative association rule" territory), which is a legitimate,
larger follow-up if 5-20 near-miss variants still feels like too many
once real feedback data starts flowing.

### 2.3 `discoverer.py` — orchestration, and cross-referencing the real catalogue

Mining runs **per cause_id**, not pooled across all feedback — same
reasoning as `learning/trainer.py` training one model per cause_id:
different cause_ids have different raw_stats vocabularies in practice
(a `MISSING_SOURCE` case has no amount-difference stats at all), so
pooling would dilute real associations with irrelevant absent-feature
noise.

Before surfacing a rule, `CauseCandidateDiscoverer` checks it against
the REAL, currently-shipped catalogue (`default_catalogue()` —
zero Django dependency, safe to load here, same as
`learned_scorer.py` loading `trained_weights.json`):
- If the rule's conditions are a subset of what's ALREADY declared for
  that `cause_id` → suppressed entirely. Re-confirming something
  already written down isn't a new finding.
- If the `cause_id` already has a catalogue entry but these specific
  conditions aren't declared → surfaced, flagged
  `is_new_condition_for_existing_cause=True` ("refine this existing
  cause").
- If no catalogue entry exists for this `cause_id` at all (it only
  exists because it was free-typed in feedback) → surfaced, flagged
  `False` ("consider a whole new `CauseKnowledge` entry").

This scoping is deliberate and worth being honest about: this package
finds NEW CONDITIONS for cause_ids that already have SOME feedback —
it does not invent a plausible cause_id out of nothing for a pattern
nobody has ever tagged with anything. See §5 for why that's a
meaningfully different, harder problem.

---

## 3. Running it

```
python manage.py discover_cause_candidates
python manage.py discover_cause_candidates --cause-id BANK_SERVICE_CHARGE --min-sample-count 5
python manage.py discover_cause_candidates --output candidates.json
```

Tunable via flags: `--min-support`, `--min-confidence`, `--min-lift`,
`--min-sample-count` (defaults: 0.10 / 0.75 / 1.2 / 10 — see
`discoverer.py`'s `DEFAULT_*` constants). Like `train_cause_scorer`,
this is not triggered automatically by anything in the request path —
run it by hand or on whatever cadence makes sense once feedback
accumulates.

---

## 4. Why this never writes to `catalogue.py` automatically

Same principle `knowledge/loader.py` already enforces for hand-written
entries (fail loudly at load time on a malformed one) applied one step
earlier: a mined rule is a STATISTICAL association in whatever
feedback happens to exist so far — it has no way to know if 42 cases
sharing a coincidental feature combination reflects a genuine
accounting reason or a quirk of which reconciliations happened to get
reviewed this month. `catalogue.py` entries carry human-written
`explanation_template`/`investigation_template` text that a human
needs to compose thoughtfully regardless — auto-inserting a rule with
a placeholder name (`"Refined conditions for BANK_SERVICE_CHARGE"`)
would either ship confusing/generic text to end users, or require the
same human review the current design already asks for, just later and
with less context (having to reverse-engineer what the auto-generated
entry meant, instead of reading a rule with its support/confidence/lift
laid out clearly).

---

## 5. What this does NOT do (and a reasonable next step)

**Finding a genuinely never-before-suggested cause** — a pattern type
where NO cause_id has ever been proposed or confirmed by any
accountant — is a different, harder problem this package doesn't
attempt to solve. It would need either:
- accountants providing free-text "what I think actually happened"
  notes when rejecting every suggested cause (not currently captured
  anywhere in `submit_cause_feedback`), mined separately with text
  clustering/topic modeling, or
- unsupervised clustering directly on `PatternContext.aggregate_stats`
  across ALL patterns regardless of what cause was or wasn't
  suggested, looking for a tight cluster of patterns that never scored
  well under ANY current cause — closer in spirit to point (e) than to
  this package.

Both are legitimate future extensions, deliberately left out of this
delivery to keep scope honest and match what the underlying data
(`CauseFeedback`, keyed by cause_id) can actually support today.
