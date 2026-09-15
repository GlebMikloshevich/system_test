# ingoread-test

A **pre-release gate** for the *ingoread* document-extraction service.

Given a labelled dataset and one config file, it sends every document through an
extraction backend, scores the predictions against ground truth field by field,
publishes a JSON + HTML report, compares the run to a previous one, and **exits
non-zero to block publishing** when the build is worse or errored.

That last part is the point: the tool is not a benchmark you read, it is a check
CI acts on.

```bash
ingoread-test run config.yaml --previous last_good.json && publish
```

**Contents** — [How it works](#how-it-works) · [Install](#install) ·
[Quickstart](#quickstart) · [Read this before gating](#read-this-before-gating-on-it) ·
[Datasets](#datasets) · [Commands](#commands) · [Configuration](#configuration) ·
[Field types](#field-types) · [Scoring](#scoring) · [Release gate](#release-gate) ·
[Project layout](#project-layout) · [Development](#development) ·
[Troubleshooting](#troubleshooting)

**Further documentation**
- [docs/dataset-and-config-guide.md](docs/dataset-and-config-guide.md) — step-by-step: build a dataset, write a config
- [docs/commands.md](docs/commands.md) — exhaustive command, flag, config and field-type reference
- [docs/architecture.md](docs/architecture.md) — internals: layers, data models, scoring algorithm
- [docs/code-review.md](docs/code-review.md) — known weaknesses and proposed fixes
- [docs/PROJECT_REVIEW_AND_USER_GUIDE.md](docs/PROJECT_REVIEW_AND_USER_GUIDE.md) — full audit and roadmap

---

## How it works

```
config.yaml   ─▶ load_configs ─▶ TestConfig + ScorerConfig
s3://dataset/ ─▶ load_dataset ─▶ Dataset (samples ▸ documents ▸ fields)
                                   │        removed samples are dropped here
              integration.runner.run_test (batched async I/O)
                                   │  predictions: dict[sample_id → IngoreadFileResult]
              scoring.aggregate.score (Hungarian pairing + stickler comparison)
                                   │  MeasurementsResult
            ┌──────────────────────┼──────────────────┬──────────────────────┐
   JsonFileSink (.json)    reporting.html       S3ResultSink          gate.history
                              (.html)      (s3://…/<dataset>/        (vs --previous)
                                            <integration>/<date>/<time>/)
                                   │
                     gate.release.evaluate_release_gate ─▶ exit 0 | 1
```

One dataset's journey is `pipeline.run.execute_run`; `run` and `suite` both call
it, differing only in how they report progress and what they do with a failure.

## Install

Requires **Python ≥ 3.12**.

```bash
# with uv (recommended)
uv sync --extra test

# or with pip
pip install -e ".[test]"
```

The `test` extra adds pytest; the runtime itself needs no extra. Two optional
extras pull heavier comparator backends, and are only needed if your scorer
config asks for them:

| Extra | Install | Needed for |
| --- | --- | --- |
| `test` | `pip install -e ".[test]"` | running the test suite |
| `llm` | `pip install 'ingoread-test[llm]'` | `field_type: llm_text` (`LLMComparator`) |
| `semantic` | `pip install 'ingoread-test[semantic]'` | `comparator: SemanticComparator` |

After install the CLI is available two ways:

```bash
ingoread-test ...                 # console script
python -m ingoread_test.cli ...   # module form, identical
```

S3 access uses the ambient AWS configuration (environment variables, profile, or
instance role). Override the endpoint or region per config under `test.storage`,
or per `dataset` command with `--endpoint-url` / `--region`.

## Quickstart

The repository ships two runnable examples that need no bucket and no backend:

```bash
ingoread-test run examples/vehicle_registration/config.yaml
```

```
dataset=vehicle_registration source=examples/vehicle_registration/dataset samples=2 removed=0 excluded=0
scored_documents=1 overall_match_rate=1.000 total_samples=2 timeouts=0 failed=0
label=vehicle_registration n=2 match_rate=1.000
results_json=/…/results/20260915T123013__vehicle_registration.json
results_html=/…/results/20260915T123013__vehicle_registration.html
release_gate=OK
```

Every line is `key=value`, so CI can parse the run without the JSON. The last
line is always `release_gate=OK` or `release_gate=BLOCKED`, and the exit code
matches it (`0` / `1`).

Run several datasets behind one decision:

```bash
ingoread-test suite examples/suite.yaml
```

> That example scores `1.000` because it uses the **stub** integration, which
> echoes the ground truth back. It proves the wiring, not the service — see
> the warning below.

## Read this before gating on it

Two defaults can produce a green gate that means nothing. Both are known
([C1, C2](docs/PROJECT_REVIEW_AND_USER_GUIDE.md#critical-release-correctness-and-fail-open-behavior))
and neither is fixed yet, so guard against them in your config and CI.

**1. The default integration is the stub, and the stub scores 1.000.**
`test.integration.kind` defaults to `stub`, which echoes ground truth back when
it has no prediction file — every field matches. Config keys are not validated
for typos either, so a misspelled `integraton:` block silently leaves you on the
stub. A run that tests the harness against its own labels reports a perfect
score and allows the release.

*Guard:* set `kind` explicitly in every gating config, and assert the run
summary names the backend you meant before trusting `release_gate=OK`.

**2. Zero accuracy passes when there is no baseline.** The gate blocks on
errors, on nothing-scored, and on regression *against `--previous`*. There is no
absolute floor: a run that scores `0.000` with no backend errors and no
`--previous` exits `0`.

*Guard:* always pass `--previous` in CI, or treat a missing baseline as a
failure in the calling pipeline.

Also worth knowing: result artifacts embed the full ground truth and every
extracted value, so treat them as sensitive if your documents are.

## Datasets

A dataset is an S3 prefix (a local directory works identically) holding a
manifest, the documents, and one identifier file per sample:

```
s3://ingoread-datasets/invoices-ru
├── manifest.yaml       # version 2: named dataset, one unique sample_id per sample
├── invoice_001.pdf
├── invoice_001.id      # "inv-0001" — sent with the document on every request
└── …
```

```yaml
test:
  dataset:
    uri: s3://ingoread-datasets/invoices-ru
  results:
    uri: s3://ingoread-results/runs
```

Publish one with `ingoread-test dataset upload <local dir> <uri>` — it validates
the manifest and every id file before uploading anything, so a dataset that
would fail at run time is never published.

The prefix is mirrored into a local cache (`.cache/ingoread-datasets` by
default, `dataset.cache_dir` to change it) on first use and reused afterwards;
`--refresh` re-downloads it.

### The manifest (version 2)

```yaml
version: 2
name: vehicle_registration
samples:
  - sample_id: vrc_001              # also stored in vrc_001.id next to the file
    filename: vrc_001.pdf
    kwargs: {}                      # per-sample request kwargs (optional)
    documents:
      - doc_label: vehicle_registration
        page: 0
        bbox: [120, 80, 1480, 1020]   # used by the matcher when >1 doc per file
        fields:
          vin: {gt_value: "JTHBK1GG1F2123456"}
          engine_power: {gt_value: "184.0"}
          signature_box: {gt_value: [900, 820, 1180, 990]}

  - sample_id: vrc_002
    filename: vrc_002.pdf
    removed: true                   # skipped by every run, kept for the record
    removal: {reason: customer request, removed_by: gleb}
    documents: []
```

Ground truth carries **values only** — how a field is compared lives in the
scorer config, never in the dataset.

Version 1 manifests (a bare list, no ids) still load: each entry's filename stem
becomes its `sample_id`. The old `files_root` + `manifest` config pair still
works too.

### The sample identifier

Every sample has a unique `sample_id`, stored both in the manifest and in a
`<stem>.id` file beside the document. It is sent to the backend as its own form
field on every create-task request (`integration.sample_id_field`, default
`sample_id`), keys predictions and results so two samples may share a filename,
and names the sample during curation. A disagreement between the id file and the
manifest fails the run rather than sending an ambiguous identifier.

### Removing samples on request

Removal is a soft delete: the sample keeps its ground truth, gains a reason and
timestamp, and is skipped by every run — so it can be restored.

```bash
ingoread-test dataset list    s3://ingoread-datasets/invoices-ru
ingoread-test dataset remove  s3://ingoread-datasets/invoices-ru inv-0042 --reason "customer request"
ingoread-test dataset restore s3://ingoread-datasets/invoices-ru inv-0042
ingoread-test dataset remove  s3://ingoread-datasets/invoices-ru inv-0042 --purge   # drop the entry for good
```

Curation rewrites the manifest and nothing else — the sample's objects in S3 are
deleted by another system. For a one-off skip that leaves the dataset untouched,
use `run --exclude inv-0042`.

## Commands

### `run` — score one dataset and gate on it

```bash
ingoread-test run config.yaml [options]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--previous URI` | – | Prior result to diff against — a local path or an `s3://` URI (enables the regression check) |
| `--no-history` | off | Skip the comparison even if `--previous` is given |
| `--no-viz` | off | Skip HTML rendering |
| `--no-upload` | off | Skip the S3 results upload |
| `--results-dir DIR` | `results.local_dir` | Where the local JSON/HTML are written |
| `--exclude ID` | – | Skip this sample id or filename for this run only (repeatable) |
| `--refresh` | off | Re-download the dataset instead of using the local cache |

Output keys, in order: `dataset=`, `scored_documents=`/`overall_match_rate=`,
one `label=` per document type, `results_uploaded=` or `results_json=` /
`results_html=`, then `history_status=` / `history_delta=` / `history_note=`
when a baseline was read, then one `gate_block=` per failing condition and the
final `release_gate=`.

### `suite` — several datasets, one release decision

```bash
ingoread-test suite suite.yaml [--results-dir DIR] [--no-viz]
```

```yaml
name: nightly
# min_macro_match_rate: 0.95        # optional extra gate on the mean across datasets
datasets:
  - name: vehicle_registration
    config: vehicle_registration/config.yaml    # resolved relative to THIS file
    baseline: baselines/vehicle_registration.json   # optional, gates this member
    blocking: true
  - name: experimental_passports
    config: passports/config.yaml
    blocking: false                 # reported, but cannot block the release
```

Each member runs the same pipeline as `run` and keeps its own artifacts under
`<results-dir>/<member>/`; the suite adds a combined HTML report. A member that
cannot run at all is reported as one blocked dataset instead of taking the suite
down. The release is allowed only when every **blocking** member passed its own
gate and any `min_macro_match_rate` is met.

### `bootstrap` — draft ground truth from predictions

Seed a dataset from the recognizer's own output instead of labelling from
scratch:

```bash
ingoread-test run config.yaml --results-dir results          # 1. capture predictions
ingoread-test bootstrap results/<timestamp>__<name>.json \   # 2. draft GT from them
  --out dataset/manifest.yaml [--name NAME] [--overwrite]
```

The draft takes the first predicted value per field as `gt_value` (bbox-only
fields become an `"x1,y1,x2,y2"` string). Nothing is filtered or flagged, so
**review every `gt_value` before trusting it as ground truth**.

### `dataset` — inspect and curate

| Command | Purpose |
| --- | --- |
| `dataset list URI [--removed-only]` | Samples with their ids and removal state |
| `dataset upload DIR URI [--dry-run]` | Validate a dataset folder, then upload it |
| `dataset remove URI IDS… [--reason R] [--purge] [--dry-run] [--yes]` | Soft-remove (or purge) samples |
| `dataset restore URI IDS…  [--dry-run]` | Undo a soft removal |

All four accept `--manifest`, `--endpoint-url` and `--region`. Selectors are
sample ids or filenames. `--purge` prompts for confirmation unless `--yes`.

## Configuration

One YAML (or JSON) file with two top-level keys, `test` and `scorer`.

```yaml
test:
  name: vehicle_registration
  dataset:
    uri: s3://ingoread-datasets/vehicle_registration   # or a local directory
    manifest: manifest.yaml           # name inside the dataset (default)
    cache_dir: .cache/ingoread-datasets
    exclude_sample_ids: []            # skipped on every run of this config
  results:
    local_dir: results                # unset = stage in a temp dir, discard after upload
    uri: s3://ingoread-results/runs   # omit for local-only runs
    upload_html: true
  integration_name: ingoread          # names the endpoint and the results folder
  batch_size: 6                       # max concurrent in-flight documents
  timeout: 300                        # per-document hard timeout (seconds)
  integration:
    kind: http                        # stub | http | string — set it explicitly
    url: https://ingoread.internal
    auth_token: "…"                   # literal value — see the note below
    poll_interval: 1.0
    poll_timeout: 120                 # client-side cap so a stuck task fails
    sample_id_field: sample_id        # null to send no identifier
  history:
    match_rate_tolerance: 0.02
    fail_on_regression: true
    fail_on_error: true
    fail_on_empty: true
  storage:
    endpoint_url: null                # defaults to the ambient AWS config
    region_name: null

scorer:
  name: vehicle_registration
  measurement_configs:
    - doc_label: vehicle_registration
      multipage_matching: true        # pair across pages, not within a page
      match_threshold: 0.7            # similarity at which two documents pair
      fields:
        - field_name: vin
          field_type: text
        - field_name: engine_power
          field_type: number
          measurer_kwargs: {abs_tol: 0.5}
        - field_name: signature_box
          field_type: bbox
          measurer_kwargs: {iou_threshold: 0.5}
```

Defaults worth knowing: `batch_size: 6`, `timeout: 300.0`,
`integration.kind: stub`, `integration_name: ingoread`,
`match_rate_tolerance: 0.02`, all three `fail_on_*` **on**, and
`results.local_dir` unset (artifacts are staged in a temp directory and removed
once uploaded — a run that was *not* uploaded always keeps its copy).

> **Secrets are literal.** `auth_token` is read verbatim: the loader does **not**
> expand `${VAR}` or `$VAR`. Keep gating configs free of real tokens — generate
> the config in CI, or inject the value into a copy at run time — and never
> commit one with a live credential.

### Integrations (`test.integration.kind`)

- **`stub`** — deterministic, no network. Returns
  `stub_predictions_dir/<filename>.json` when present, otherwise echoes the
  ground truth back so every field matches. For smoke tests only.
- **`http`** — a live ingoread instance:
  - `POST {url}/api/integrations/{integration_name}` (multipart) → `{"task_id": …}`
  - `GET {url}/api/status/{task_id}`, polled until `completed` / `failed`
  - needs the documents on disk; they are fetched from `dataset.uri` into the cache.
- **`string`** — the same HTTP protocol with **no file part**: the input lives
  entirely in the request kwargs. For text-in / structure-out integrations.

Status strings are normalized (`completed` → COMPLETED, `queued` → QUEUED, …);
anything unrecognized is treated as `failed`.

### Per-document kwargs

`test.kwargs` (global) and a sample's `kwargs` (per file, from the manifest) are
merged and sent as form data. Serialization depends on `data_field_name`:

- **unset** (default) — *spread*: each kwarg becomes its own form field;
  non-string values are JSON-encoded.
- **set** — *single-blob*: all kwargs are JSON-encoded together under that one
  field name.

The sample id stays outside the kwargs payload in both modes — it identifies the
sample rather than configuring the request.

## Field types

Each `field_type` is shorthand for a [stickler](https://github.com/awslabs/stickler)
comparator and the value shape it compares:

| `field_type` | Comparator | Default threshold | Notes |
| --- | --- | --- | --- |
| `text` | `ExactComparator` | 1.0 | `measurer_kwargs: {strip, casefold}` |
| `literal` | `ExactComparator` | 1.0 | discrete codes, years, enums |
| `fuzzy_text` | `LevenshteinComparator` | 0.7 | graded similarity, case-insensitive |
| `number` | `NumericComparator` | 1.0 | `measurer_kwargs: {abs_tol, rel_tol}` |
| `bool` | `ExactComparator` | 1.0 | parses `true/1/yes/y/да` and `false/0/no/n/нет` |
| `date` | `DateComparator` | 1.0 | format-independent |
| `phone` | `PhoneComparator` | 1.0 | `comparator_kwargs: {region: RU}` |
| `bbox` | `BBoxIoUComparator` | 0.5 | one box; `measurer_kwargs: {iou_threshold}` |
| `bbox_set` | `BBoxIoUComparator` | 0.5 | many boxes, matched set-to-set |
| `llm_text` | `LLMComparator` | 0.7 | needs the `llm` extra |

Anything else stickler ships — `FuzzyComparator`, `SemanticComparator`, … — is
reachable with `comparator: <ClassName>` plus `comparator_kwargs`.

**Per-field knobs:** `weight` (importance in the document score), `threshold`,
`clip_under_threshold` (zero out sub-threshold similarity, on by default),
`comparator`, `comparator_kwargs`, `measurer_kwargs`, `field_group`, `ignore`
(document the field but never score it), `selection` and `top_n`.

`selection` decides what to do when the API returns several candidates for one
field: `first` (default) scores a single value; `all` and `top_n` (with
`top_n: N`) turn the field into a set that stickler matches element by element,
so order does not matter and extra or missing values surface as `fa` / `fn`.

**Per-document knobs:** `multipage_matching` (group by page or not),
`match_threshold` (the similarity at which a GT and a predicted document pair),
and `split_below_match_threshold` (report a weak assignment as a miss plus a
hallucination instead of a pair).

## Scoring

Comparison is delegated to stickler (`stickler-eval`). The scorer config *is*
the comparison schema: each `doc_label` becomes a `StructuredModel`, each field
a `ComparableField` carrying its comparator, threshold and weight. Scoring one
document is a single `compare_with()` call.

Documents of the same `doc_label` inside one file are paired with stickler's
Hungarian matcher — by page-bbox IoU when *every* document carries a valid box,
otherwise by whole-document similarity. Unmatched documents on either side are
kept as **half pairs**, so misses and hallucinations still appear in the report.

Two headline numbers come out:

| Metric | Meaning |
| --- | --- |
| `match_rate` | share of documents where **every** scored field was right — all or nothing |
| `mean_score` | stickler's weighted similarity — partial credit, so a near miss still counts |

Per field the report carries precision, recall, F1 and accuracy, plus the error
cells that actually fired: `fd` (wrong value), `fn` (missing value), `fa` (value
invented where the ground truth had none).

## Release gate

The rule: *publish only if metrics are not worse than the baseline **and** no
document errored.* The process exits non-zero when any enabled condition in
`test.history` trips.

| Condition | Default | Blocks when |
| --- | --- | --- |
| `fail_on_error` | true | any document failed or timed out |
| `fail_on_empty` | true | nothing was scored — no evidence to validate |
| `fail_on_regression` | true | metrics dropped past `match_rate_tolerance` vs `--previous` |

The error and empty checks apply with or without a baseline; the regression
check only runs when `--previous` is given. A baseline that was requested but
could not be read **blocks** rather than being skipped. A label present in the
baseline but missing from this run counts as a regression, so dropping hard
samples cannot lift the score past the gate.

### Results in S3

Each run is published to its own folder, named by dataset, integration, date and
time:

```
s3://ingoread-results/runs/invoices-ru/ingoread/2026-09-15/143205/
├── result.json
└── report.html
```

Object names are fixed, so the next build can gate against it directly:

```bash
ingoread-test run config.yaml \
  --previous s3://ingoread-results/runs/invoices-ru/ingoread/2026-09-14/091500/result.json
```

An upload failure warns but never changes the verdict.

### In CI

```bash
set -euo pipefail

ingoread-test run config.yaml --previous "$LAST_GOOD_RESULT"   # exits 1 on BLOCKED
publish_build                                                  # only reached on OK
```

## Project layout

```
src/ingoread_test/
├── cli.py          # Typer surface and the key=value output
├── config/         # TestConfig / ScorerConfig / SuiteConfig + loaders
├── dataset/        # manifest v2, sample ids, loading, curation, bootstrap
├── integration/    # Integration ABC, stub + http backends, factory, runner
├── scoring/        # stickler models, comparators, adapters, pairing, aggregate
├── pipeline/       # orchestration: run one dataset, or a whole suite
├── gate/           # release policy — pure, no I/O
├── reporting/      # sinks (local + S3) and the HTML report
├── results/        # result data models
└── utils/          # S3 transfer helpers
```

The layering is one-directional: `pipeline` composes `dataset` → `integration` →
`scoring` → `gate` → `reporting`, and nothing below `pipeline` imports it back.
See [docs/architecture.md](docs/architecture.md) for the full map.

## Development

```bash
uv sync --extra test

pytest                  # 194 tests
ruff check src tests    # lint
ruff format src tests   # format
```

Extension points:

- **New field type** — add a value to `FieldType`, map it to a comparator in
  `scoring/comparators.py`, and give it a default threshold.
- **New integration** — subclass `Integration`, add a `kind` to
  `IntegrationKind` and a branch in `integration/factory.py`.
- **New output sink** — subclass `Sink` in `reporting/sinks.py`.
- **New gate condition** — add a switch to `HistoryConfig` and a clause in
  `gate/release.py`.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `match_rate=1.000` on a real backend | You are on the stub — `integration.kind` was never set, or the key is misspelled (typos are silently ignored) |
| `scored_documents=0` + a label warning | `doc_label` in the scorer config doesn't match the manifest or the API response |
| `COMPLETED` with an empty result list | The response didn't expose its documents under `result` — check `integration/http.py` against the real payload |
| Every document `failed` | The API returned a status string outside the known vocabulary; anything unrecognized maps to `failed` |
| `FileNotFoundError` on a relative path | Paths inside a config resolve against the working directory — run from the repo root or use absolute paths |
| Run hangs forever | `batch_size: 0` is accepted but can never acquire its semaphore; use ≥ 1 |
| Two runs overwrite each other | Output filenames are second-granular; space runs apart or use distinct `--results-dir` |
