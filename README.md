# ingoread-test

A testing harness for the **ingoread** document-extraction integration. It sends a
labelled dataset through an extraction backend (a real HTTP service or a local
stub), scores the predictions against ground truth field-by-field, and emits a
JSON + HTML report. It can also compare a run to a previous one and fail CI on a
regression.

**Documentation**
- [docs/dataset-and-config-guide.md](docs/dataset-and-config-guide.md) — step-by-step guide to creating a dataset and configuring a test
- [docs/commands.md](docs/commands.md) — full command, flag, config, and field-type reference
- [docs/architecture.md](docs/architecture.md) — internals: pipeline, modules, data models, scoring algorithm
- [docs/code-review.md](docs/code-review.md) — known weaknesses & proposed fixes

## Pipeline

```
config.yaml   ─▶ load_configs ─▶ TestConfig + ScorerConfig
s3://dataset/ ─▶ load_dataset ─▶ Dataset (samples ▸ documents ▸ fields)
                                   │        removed samples are dropped here
                 TestModule.run_test (batched async I/O)
                                   │  predictions: dict[sample_id → IngoreadFileResult]
                 ScorerModule.score (Hungarian pairing + per-field scoring)
                                   │  MeasurementsResult
            ┌──────────────────────┼──────────────────┬──────────────────────┐
   JsonFileSink (.json)  VisualizationModule   S3ResultSink        HistoricalScorer
                              (.html)      (s3://…/<dataset>/       (vs --previous)
                                            <integration>/<date>/<time>/)
```

## Install

```bash
# with uv (recommended)
uv sync --extra dev

# or with pip
pip install -e ".[dev]"
```

Requires Python ≥ 3.12.

## Usage

```bash
ingoread-test run config.yaml
```

Options:

| Flag | Default | Meaning |
| --- | --- | --- |
| `--previous URI` | – | Prior result to diff against — a local path or an `s3://` URI (enables regression gate) |
| `--no-history` | off | Skip the comparison even if `--previous` is given |
| `--no-viz` | off | Skip HTML rendering |
| `--no-upload` | off | Skip the S3 results upload |
| `--results-dir DIR` | `results.local_dir` | Where the local JSON/HTML are written |
| `--exclude ID` | – | Skip this sample id/filename for this run only (repeatable) |
| `--refresh` | off | Re-download the dataset instead of using the local cache |

Local outputs are written as `<timestamp>__<test_name>.json` / `.html`.

## Datasets live in S3

A dataset is an S3 prefix holding a manifest, the documents, and one identifier
file per sample:

```
s3://ingoread-datasets/invoices-ru
├── manifest.yaml       # version 2: named dataset, one unique sample_id per sample
├── invoice_001.pdf
├── invoice_001.id      # "inv-0001" — sent with the document on every request
└── ...
```

```yaml
test:
  dataset:
    uri: s3://ingoread-datasets/invoices-ru
  results:
    uri: s3://ingoread-results/runs
```

Publish a dataset with `ingoread-test dataset upload <local dir> <uri>`; it
validates the manifest and every id file before uploading anything.

The prefix is mirrored into a local cache (`.cache/ingoread-datasets`) on the
first run and reused afterwards; `--refresh` re-downloads it. A local directory
works everywhere an `s3://` URI does, which is how the bundled examples run
without a bucket. Version 1 manifests and the old `files_root` + `manifest`
config keys still load — see
[docs/commands.md](docs/commands.md#6-dataset-format-version-2).

### The sample identifier

Every sample carries a unique `sample_id`, stored both in the manifest and in a
`<stem>.id` file next to the document. The id is sent to the backend as its own
form field on the create-task POST (`integration.sample_id_field`, default
`sample_id`), keys the predictions and results, and names the sample when you
curate the dataset. A mismatch between the id file and the manifest fails the
run rather than sending an ambiguous identifier.

### Removing samples on request

Removal is a soft delete by default: the sample keeps its ground truth, gains a
reason and timestamp, and is skipped by every run — so it can be restored.

```bash
ingoread-test dataset list    s3://ingoread-datasets/invoices-ru
ingoread-test dataset remove  s3://ingoread-datasets/invoices-ru inv-0042 --reason "customer request"
ingoread-test dataset restore s3://ingoread-datasets/invoices-ru inv-0042

# Drop the entry for good
ingoread-test dataset remove  s3://ingoread-datasets/invoices-ru inv-0042 --purge
```

Curation rewrites the manifest and nothing else: the sample's objects in S3 are
deleted by another system. Use `run --exclude inv-0042` for a one-off skip that
leaves the dataset alone.

## Results are uploaded to S3

Each run is published to its own folder, named by dataset, integration, date,
and time:

```
s3://ingoread-results/runs/invoices-ru/ingoread/2026-09-15/143205/
├── result.json
└── report.html
```

The local copy is temporary: a run renders its JSON and HTML into a staging
directory, uploads them, and then removes it. Pass `--results-dir DIR` (or set
`results.local_dir`) to keep the files on this machine instead. A run that was
not uploaded — no `results.uri`, `--no-upload`, or a failed upload — always
keeps its local copy, so a run is never left with no copy at all.

Object names are fixed, so the next run can gate against it directly:

```bash
ingoread-test run config.yaml \
  --previous s3://ingoread-results/runs/invoices-ru/ingoread/2026-09-14/091500/result.json
```

An upload failure warns but never changes the gate verdict.

### Bootstrapping ground truth (half-automatic)

You can seed a GT dataset from the recognizer's own output instead of labelling
from scratch: run once, then turn the predictions in a result JSON into a draft
manifest that you review and correct by hand.

```bash
ingoread-test run config.yaml --results-dir results            # 1. capture predictions
ingoread-test bootstrap results/<timestamp>__<name>.json \     # 2. draft GT from them
  --out dataset/manifest.yaml
```

The draft uses the first predicted value per field as `gt_value` (bbox-only
fields become an `"x1,y1,x2,y2"` string); nothing is filtered or flagged, so
**review every `gt_value` before trusting it as ground truth**. Pass
`--overwrite` to replace an existing file.

### Release gate (exit code)

This tool is meant to run as a **pre-release gate**: it produces metrics, then
decides whether the new build may be published to production. The rule is
*publish only if metrics are not worse than the baseline **and** no document
errored.* The process exits non-zero — blocking CI — when any enabled condition
in `test.history` trips:

| Condition | Default | Blocks when |
| --- | --- | --- |
| `fail_on_error` | true | any document `failed` or `timed out` |
| `fail_on_empty` | true | nothing was scored (no evidence to validate) |
| `fail_on_regression` | true | metrics dropped past `match_rate_tolerance` vs `--previous` |

The error/empty checks apply with or without a baseline; the regression check
only runs when `--previous` is given. The final line of output is
`release_gate=OK` or `release_gate=BLOCKED` (with one `gate_block=<reason>` line
per failing condition). Wire the exit code straight into CI:

```bash
ingoread-test run config.yaml --previous last_good.json && publish
```

Try it against the bundled stub example:

```bash
ingoread-test run examples/vehicle_registration/config.yaml
```

## Configuration

A single YAML (or JSON) file with two top-level keys, `test` and `scorer`.

```yaml
test:
  name: vehicle_registration
  dataset:
    uri: s3://ingoread-datasets/vehicle_registration   # or a local directory
    manifest: manifest.yaml       # name inside the dataset (default)
    cache_dir: .cache/ingoread-datasets
    exclude_sample_ids: []        # skipped on every run of this config
  results:
    local_dir: results
    uri: s3://ingoread-results/runs   # omit for local-only runs
    upload_html: true
  integration_name: ingoread
  batch_size: 4            # max concurrent in-flight documents
  timeout: 60             # per-document hard timeout (seconds)
  integration:
    kind: stub            # stub | http
    sample_id_field: sample_id   # form field carrying the sample's unique id
  history:
    match_rate_tolerance: 0.02
    fail_on_regression: true

scorer:
  name: vehicle_registration
  measurement_configs:
    - doc_label: vehicle_registration
      multipage_matching: true
      fields:
        - field_name: vin
          field_type: text
        - field_name: engine_power
          field_type: number
          measurer_kwargs: {abs_tol: 0.5}     # optional numeric tolerance
        - field_name: signature_box
          field_type: bbox
          measurer_kwargs: {iou_threshold: 0.5}
```

### Integrations (`test.integration.kind`)

- **`stub`** — deterministic, no network. If
  `stub_predictions_dir/<filename>.json` exists it is returned; otherwise the
  ground truth is echoed back (every field matches), which is handy for smoke
  tests.
- **`http`** — talks to a live ingoread instance:
  - `POST {url}/integrations/{integration_name}` (multipart) → `{"task_id": ...}`
  - `GET  {url}/status/{task_id}` polled until `completed`/`failed`

  Relevant keys: `url`, `auth_token`, `poll_interval`, `poll_timeout`
  (client-side cap so a stuck task fails instead of hanging), and
  `data_field_name` (see below). The `http` mode needs real files on disk at
  `files_root/<filename>` — the bundled example datasets ship manifests only.

#### Passing per-document kwargs

`test.kwargs` (global) and a container's `kwargs` (per file) are merged and sent
as multipart form data. Serialization depends on `data_field_name`:

- **unset (default)** — *spread* mode: each kwarg becomes its own form field;
  non-string values are JSON-encoded.
- **set** — *single-blob* mode: all kwargs are JSON-encoded together under that
  one field name.

### Field types

Each `field_type` is shorthand for a stickler comparator:

| `field_type` | Comparator | Default threshold | Notes |
| --- | --- | --- | --- |
| `text` | `ExactComparator` | 1.0 | `measurer_kwargs: {strip, casefold}` |
| `literal` | `ExactComparator` | 1.0 | |
| `fuzzy_text` | `LevenshteinComparator` | 0.7 | graded similarity, case-insensitive |
| `number` | `NumericComparator` | 1.0 | `measurer_kwargs: {abs_tol, rel_tol}` |
| `bool` | `ExactComparator` | 1.0 | parses `true/1/yes/да` and `false/0/no/нет` |
| `date` | `DateComparator` | 1.0 | format-independent |
| `phone` | `PhoneComparator` | 1.0 | `comparator_kwargs: {region: RU}` |
| `bbox` | `BBoxIoUComparator` | 0.5 | `measurer_kwargs: {iou_threshold}` |
| `bbox_set` | `BBoxIoUComparator` | 0.5 | many boxes, matched set-to-set |
| `llm_text` | `LLMComparator` | 0.7 | needs `pip install 'ingoread-test[llm]'` |

Anything else stickler ships — `FuzzyComparator`, `SemanticComparator`, … — is
reachable with `comparator: <ClassName>` plus `comparator_kwargs`.

Per-field knobs: `weight`, `threshold`, `clip_under_threshold` (zero out
sub-threshold similarity — on by default), `comparator`, `comparator_kwargs`,
`field_group`, `ignore`, and `selection`.

`selection` decides what to do when the API returns several candidates for one
field: `first` (default) scores one value; `all` and `top_n` (with `top_n: N`)
turn the field into a set that stickler matches element by element, so order
doesn't matter and extra or missing values surface as `fa` / `fn`.

### Dataset manifest (version 2)

A named dataset whose samples each carry a unique id:

```yaml
version: 2
name: vehicle_registration
samples:
  - sample_id: vrc_001              # also stored in vrc_001.id next to the file
    filename: vrc_001.pdf
    documents:
      - doc_label: vehicle_registration
        page: 0
        bbox: [120, 80, 1480, 1020]   # used by the Hungarian matcher when >1 doc/file
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

The version 1 format (a bare list of containers, no ids) still loads: each
entry's filename stem becomes its `sample_id`.

## Scoring model

Comparison is delegated to [stickler](https://github.com/awslabs/stickler)
(`stickler-eval`). The scorer config *is* the comparison schema: each
`doc_label` becomes a stickler `StructuredModel`, each field a `ComparableField`
carrying its comparator, threshold and weight. Scoring one document is then a
single `compare_with()` call, and pairing several documents of the same type
inside one file is stickler's Hungarian matcher.

Two headline numbers come out of that:

| Metric | Meaning |
| --- | --- |
| `match_rate` | share of documents where **every** scored field was right — all or nothing |
| `mean_score` | stickler's weighted similarity — partial credit, so a near miss still counts |

Per field the report carries precision, recall, F1 and accuracy, plus the error
cells that fired: `fd` (wrong value), `fn` (missing value), `fa` (value
invented where the ground truth had none).

Documents of the same `doc_label` inside one file are paired with
stickler's Hungarian matcher — by page bbox IoU when every document carries
one, otherwise by whole-document similarity. Unmatched documents on either
side are kept as half pairs so they still appear in the report.

## Development

```bash
pytest          # run the suite
ruff check .    # lint
```
