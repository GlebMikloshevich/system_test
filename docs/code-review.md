# Code review — weak places & proposed fixes

A weakness audit of `ingoread-test`, ordered by impact on the system's job: a
trustworthy **pre-release gate** that produces document-extraction metrics and
blocks publishing to production when a build is worse or errored.

Each item has a **description** (what's wrong and why it matters) and a
**proposed fix**. Locations are `file:line` at time of writing.

Legend: 🔴 Critical · 🟠 High · 🟡 Medium · ⚪ Lower

> **Note on paths.** This audit's `file:line` references predate the move to a
> layered package. The modules it names now live at: `test_module.py` →
> `integration/runner.py`, `scorer_module.py` → `scoring/aggregate.py`,
> `logger_module.py` → `reporting/sinks.py`, `visualization_module.py` →
> `reporting/html.py`, `historical_scorer.py` → `gate/history.py` +
> `gate/release.py`, `run_module.py`/`suite_module.py`/`dataset_module.py` →
> `pipeline/`. `field_scorers.py` and `document_scorer.py` no longer exist —
> comparison is delegated to stickler under `scoring/`.

---

## 🔴 Critical — the gate can pass a build that actually regressed

### 1. Missing-label blind spot in the regression check
**Description.** `compare_to_previous` iterates only `current.document_results`
(`historical_scorer.py:25`). If a document type present in the baseline
**disappears** in the new run (the model stopped producing it, or all of its
docs errored out of `document_results`), it is never compared. Dropping hard
samples can even make overall `match_rate` *rise*, so the gate goes green on a
genuine regression.

**Proposed fix.** Also walk labels that exist in `previous` but are absent from
`current`. Treat a vanished label (or one whose sample count dropped sharply) as
a regression note and flip `degraded`. Iterate over
`set(prev_by_label) | set(cur_by_label)` rather than current only.

**Status.** ✅ Fixed: `compare_to_previous` now walks the union of labels and
flags a vanished label as `DEGRADED` (covered by
`test_vanished_label_counts_as_regression`). The sample-count-drop part is still
open (see item 2).

### 2. Sample-count collapse isn't checked
**Description.** The gate compares only rates, never volume. If this run scored
3 documents and the baseline scored 300 (mass timeouts, a truncated dataset),
`match_rate` over 3 docs can look fine and publish.

**Proposed fix.** Add a coverage check to the gate: block when
`current.total_samples` (and per-label `n`) falls below the baseline by more
than a configurable fraction (e.g. `min_sample_ratio: 0.9`). Surface it as a
distinct `gate_block=coverage_dropped` reason.

### 3. `total_samples` denominator is inconsistent
**Description.** `scorer_module.py:158` sets `total_samples` to the number of
*containers* (`test_stats.total_samples = len(dataset)`), while `match_rate` is
computed over *document pairs*. The headline "match_rate over total_samples"
mixes two denominators whenever a file has ≠1 document or half-pairs exist.

**Proposed fix.** Report both explicitly: keep `total_files` (containers) and add
`total_samples` = number of scored document pairs, with `match_rate` defined
over the latter. Update `MeasurementsResult` and the CLI summary line.

---

## 🟠 High — scoring produces wrong or misleading numbers

### 4. Boolean "garbage matches false"
**Description.** `field_scorers.py:65-67` maps both prediction and GT to
`value in {true,1,yes,y,да}`. An unparseable or empty prediction maps to
`False`, so against a `false` ground truth it scores **matched=True**. Bool
fields silently inflate accuracy; there is no "unrecognized → no credit" path.

**Proposed fix.** Parse to a tri-state (`True` / `False` / `unknown`). Treat an
unrecognized prediction as a non-match regardless of GT. Optionally accept a
configurable truthy/falsy vocabulary via `measurer_kwargs`.

**Status.** ✅ Fixed: `_bool_score` now parses tri-state via `_parse_bool`
(truthy/falsy vocabularies, `None` for anything else) and an unrecognized or
empty prediction never matches (covered by
`test_bool_scorer_unrecognized_prediction_gets_no_credit`). The configurable
vocabulary remains a possible follow-up.

### 5. All-or-nothing document match is brittle and low-resolution
**Description.** `document_scorer.py:48` sets `matched = all(field matches)`. On
an 11-field document one flaky field zeroes the whole document, so `match_rate`
saturates near 0 and loses resolution — a poor primary signal for a gate.
`fraction_fields_matched` is computed but used only for pairing cost, never
surfaced.

**Proposed fix.** Surface field-level match rate as a first-class metric
alongside the strict document match. Consider making the gate's primary signal
the mean `fraction_fields_matched`, keeping exact-document-match as a secondary.

### 6. Metric aggregation silently drops failures
**Description.** `scorer_module.py:49` excludes `inf` from `mae`/`mse` means. An
unparseable number counts as a *miss* in `match_rate` but is *removed* from the
error average — MAE can look great while matches are terrible. The match
denominator and the metric denominator disagree.

**Proposed fix.** Either count unparseable numbers with a defined penalty value
(not `inf`) so they stay in the average, or report the count of dropped/invalid
samples next to each aggregated metric so the average can't be read as complete.

### 7. Field-level vs document-level denominators differ
**Description.** Pred-only "half pairs" (hallucinated docs) carry
`field_metrics={}`, so they lower the document `match_rate` but are invisible to
per-field `match_rate` (`scorer_module.py:41-44`). Field rates can read higher
than the document rate over the same data.

**Proposed fix.** Define and document one convention. E.g. count a hallucinated
doc as a miss for every configured field, or report per-field rates with an
explicit "n scored" so the differing denominators are visible.

### 8. bbox / IoU contracts are unvalidated and silently wrong
**Description.** `_bbox_score` parses GT via `gt.strip("[]").split(",")` and
`_iou` assumes `[x1,y1,x2,y2]` with `x1<x2`. A `[x,y,w,h]` payload, a different
separator, or flipped coordinates produce a *plausible-but-wrong* IoU (or a
silent `matched=False`) — never an error.

**Proposed fix.** Validate and normalize bbox inputs (assert ordering, support a
declared coordinate convention via `measurer_kwargs`), and raise/flag on
malformed GT rather than scoring it as a quiet zero.

### 9. Numeric/text formatting mismatch
**Description.** `schemas.py` coerces scalars with `str()`, so `194.40 →
"194.4"`. For `literal`/`text` fields holding numbers, this mismatches a GT
string of `"194.40"`. Exact-match text scoring has no numeric awareness.

**Proposed fix.** For numeric-looking literals, compare canonicalized numeric
form, or expose a normalization option (`strip`/`casefold` already exist; add
numeric canonicalization) so trailing-zero/format differences don't read as
misses.

---

## 🟠 High — robustness / integration

### 10. No retry on transient HTTP failures
**Description.** In `http.py`, any 5xx or network blip on create or poll becomes
a `failed` document, which now blocks the release. Infra flakiness is conflated
with model regression, so the gate produces false blocks.

**Proposed fix.** Add bounded retry with backoff on idempotent operations
(status polls, and create if safe), distinguishing retryable transport errors
from terminal `FAILED` task status. Make attempts/backoff configurable.

### 11. Brittle status mapping
**Description.** `http.py:33` maps any unknown status to `FAILED`. If the API
ever returns `"success"`/`"done"`/`"finished"` instead of `"completed"`, *every*
document is marked failed and the gate hard-blocks. Only 5 strings are
recognized.

**Proposed fix.** Make the status vocabulary configurable, and log a loud
warning (or raise) on an unrecognized status instead of silently treating it as
failure, so a vocabulary drift is diagnosable rather than catastrophic.

### 12. Fragile response-shape guessing
**Description.** `http.py:84-92` does `payload.get("result", payload)`,
list-vs-dict sniffing, and in-place mutation. A single-doc dict or a
differently-keyed body silently yields an empty `result` — a parsing bug
disguised as "0 documents, COMPLETED."

**Proposed fix.** Pin the expected response contract with an explicit pydantic
model and fail loudly on shape mismatch. If multiple shapes must be supported,
make the mapping explicit and tested, not inferred.

### 13. Broad `except Exception` hides harness bugs
**Description.** `test_module.py:54` and `http.py` catch `Exception` and record a
per-doc "failed" with an opaque message. A genuine bug in scoring/parsing then
looks like a service failure rather than surfacing a stack trace.

**Proposed fix.** Narrow the catch to expected transport/timeout exception
types; let unexpected exceptions propagate (or log full traceback at ERROR) so
"service down" is distinguishable from "our code is broken."

### 14. Duplicate filenames overwrite each other
**Description.** `test_module.py:65` keys results by `filename`, and the scorer
looks up by `filename`. Two containers sharing a name (different `group_id`)
cause silent data loss.

**Proposed fix.** Detect duplicate filenames at dataset load and either error or
key results by a stable unique id (e.g. `(filename, group_id)` or an index).

**Status.** ✅ Fixed: the version 2 dataset format gives every sample a unique
`sample_id` (validated as unique at load), and `run_test`/`score` key
predictions by `DocumentContainer.key` — the sample id — instead of the
filename. Results carry `DocumentContainerPair.sample_id` alongside `filename`.
Covered by `tests/test_dataset_format.py`.

---

## 🟡 Medium — config safety & reproducibility

### 15. Typo'd config keys are silently ignored
**Description.** No pydantic model sets `extra="forbid"`, so `batch_sze: 100` or
a misplaced field is dropped and the default used — dangerous for a
config-driven gate.

**Proposed fix.** Add `model_config = ConfigDict(extra="forbid")` to the config
models (`TestConfig`, `ScorerConfig`, and nested configs) so unknown keys fail
fast with a clear error.

### 16. Unimplemented `selection` crashes the whole run after all I/O
**Description.** A config with `selection: all` (or the `take_first: false`
legacy alias) validates fine, then raises `NotImplementedError` deep in
`score()` (`field_scorers.py:41`), uncaught in the CLI — the entire run aborts
with a traceback and no results, *after* paying for every prediction.

**Proposed fix.** Validate selection support at config-load time (reject `top_n`
/`all` with a clear message), so the run never starts with a config it can't
score.

### 17. Second-granularity output filenames collide
**Description.** `logger_module.py:22` and the visualization module use
`%Y%m%dT%H%M%S`. Two runs in the same second overwrite each other's JSON/HTML.

**Proposed fix.** Add finer granularity (milliseconds) and/or a short unique
suffix to output filenames, or refuse to overwrite an existing file.

### 18. `time_per_sample` is wall-clock ÷ containers under concurrency
**Description.** With `batch_size` parallelism, `test_module.py`'s
`time_per_sample` does not reflect real per-document latency, weakening it as a
performance-regression signal even though per-doc `time` is recorded in
predictions.

**Proposed fix.** Aggregate true per-document latency from
`IngoreadFileResult.time` (mean / p50 / p95) instead of dividing wall-clock by
count, and report those percentiles.

---

## ⚪ Lower — observability & coverage

### 19. HTML report omits errors
**Description.** `visualization_module.py` renders only `document_results` — no
failures/timeouts list, no per-file error reasons. When the gate blocks a
release, the report doesn't help triage *why*.

**Proposed fix.** Add a section listing failed/timed-out files with their error
strings and any empty-but-COMPLETED files.

### 20. Result JSON embeds full `container_pairs`
**Description.** `results/models.py` stores every GT and prediction field in the
output; fine for smoke datasets, heavy and slow for real ones, while the history
step needs only aggregates.

**Proposed fix.** Split output into a compact summary (metrics + history inputs)
and an optional verbose artifact (full pairs) behind a flag.

### 21. Empty-but-COMPLETED predictions only warn, never gate
**Description.** `scorer_module.py:122` warns on COMPLETED responses with an
empty `result` list — the most likely signature of an integration-parsing break
— yet it passes the gate.

**Proposed fix.** Add an opt-in `fail_on_empty_predictions` gate switch that
blocks when COMPLETED responses returned no documents above a threshold.

### 22. Test coverage gaps
**Description.** No tests for: bool-false-matches-garbage (#4), numeric
`rel_tol`, unknown HTTP status (#11), retry behavior (#10), duplicate filenames
(#14), extra-config-key rejection (#15), or the missing-label regression case
(#1).

**Proposed fix.** Add targeted unit tests for each as the corresponding fix
lands, to lock the intended behavior.

---

## Suggested priority order

The highest leverage for a reliable release gate:

1. Gate blind spots — #1 (missing labels), #2 (coverage), #3 (denominator)
2. Scoring correctness — #4 (bool false positive), #6 (metric aggregation)
3. Config safety — #15 (`extra="forbid"`), #16 (validate selection early)
4. Integration resilience — #10 (retry), #11 (status), #12 (response shape)

Each lands with a test from #22.
