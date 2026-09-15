# Commands & instructions

A complete reference of every command for `ingoread-test`, plus the config and
dataset formats they rely on. For the conceptual overview see the
[README](../README.md); for known weaknesses see
[code-review.md](code-review.md).

---

## 1. Setup

Requires Python ≥ 3.12.

```bash
# with uv (recommended)
uv sync --extra dev

# or with pip (editable install + dev tools)
pip install -e ".[dev]"
```

After install the CLI is available two ways:

```bash
ingoread-test ...                 # console script
python -m ingoread_test.cli ...   # module form (identical)
```

Show help for any command:

```bash
ingoread-test --help
ingoread-test run --help
ingoread-test bootstrap --help
```

---

## 2. `run` — execute a test and score it

Sends the dataset through the integration, scores predictions against ground
truth, writes a JSON (+ HTML) report, optionally compares to a previous run, and
exits with a release-gate code.

```bash
ingoread-test run CONFIG [OPTIONS]
```

### Arguments

| Argument | Required | Meaning |
| --- | --- | --- |
| `CONFIG` | yes | Path to the combined `test` + `scorer` YAML/JSON config. Must exist and be readable. |

### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--previous URI` | – | Prior result to diff against (enables the regression gate). A local path or an `s3://` URI. |
| `--no-history` | off | Skip the comparison even if `--previous` is given. |
| `--no-viz` | off | Skip HTML rendering (JSON only). |
| `--no-upload` | off | Skip the S3 results upload for this run. |
| `--results-dir DIR` | `results.local_dir` | Keep the run's files in this directory instead of discarding the staging copy. |
| `--exclude ID` | – | Skip this sample id (or filename) for this run only. Repeatable. |
| `--refresh` | off | Re-download the dataset instead of using the local cache. |

`--exclude` is a one-off skip that leaves the dataset untouched. To take a
sample out of the dataset itself, use [`dataset remove`](#3c-dataset--curate-a-dataset).

### Outputs

**Results live in S3; the local copy is temporary.** A run renders its JSON and
HTML into a staging directory (they have to be files before they can be
uploaded), publishes them, and then removes that directory —
`results_local_discarded=<dir>` says so. The local copy is kept only when:

- a directory was asked for (`--results-dir` or `results.local_dir`) — then the
  files stay there as `<timestamp>__<test_name>.json` / `.html`; or
- the run was **not** uploaded (no `results.uri`, `--no-upload`, or a failed
  upload). Deleting it then would leave no copy of the run at all, so the
  staging directory survives and the command warns.

When `test.results.uri` is set, the run is uploaded to

```
<results.uri>/<dataset name>/<integration name>/<YYYY-MM-DD>/<HHMMSS>/
├── result.json     # the full MeasurementsResult
└── report.html     # unless --no-viz or results.upload_html: false
```

The object names are fixed, so a baseline has a predictable URI:
`--previous s3://ingoread-results/runs/invoices-ru/ingoread/2026-09-14/091500/result.json`.
An upload failure warns but does not change the gate verdict — the local
artifacts and the exit code still stand.

The command also prints, line by line:

```
dataset=<name> source=<uri> samples=<n> removed=<n> excluded=<n>
scored_documents=<n> overall_match_rate=<r> total_samples=<n> timeouts=<n> failed=<n>
label=<doc_label> n=<n> match_rate=<r>          # one per document type
results_uploaded=<s3 folder uri>                 # only when results.uri is set
results_local_discarded=<dir>                    # the staging copy was removed
results_json=<abs path>                          # only when the local copy is kept
results_html=<abs path>                          # only when kept, and unless --no-viz
history_status=OK|DEGRADED                        # only with --previous
history_delta=<+/-d>                              # only with --previous
history_note=<reason>                             # zero or more, only with --previous
gate_block=<reason>                               # zero or more, only if blocked
release_gate=OK|BLOCKED                           # always last
```

### Exit codes (the release gate)

- `0` — OK to publish.
- `1` — blocked. The `gate_block=` lines say why.

### Examples

```bash
# Basic run against the bundled stub example
ingoread-test run examples/vehicle_registration/config.yaml

# CI gate: run, diff against the last good baseline, publish only on success
ingoread-test run config.yaml --previous last_good.json && publish

# JSON only, custom output dir
ingoread-test run config.yaml --no-viz --results-dir build/metrics
```

---

## 3. `bootstrap` — draft ground truth from predictions (half-automatic)

Turns the predictions stored in a prior result JSON into a draft GT manifest.
The first predicted value per field becomes `gt_value` (bbox-only fields become
an `"x1,y1,x2,y2"` string). Nothing is filtered or flagged — **review every
`gt_value` before trusting it as ground truth.**

The draft is written in the version 2 format, and each sample keeps the id it
ran with, so it lines up with the id files already next to the documents.

```bash
ingoread-test bootstrap RESULT_JSON --out PATH [--name NAME] [--overwrite]
```

### Arguments

| Argument | Required | Meaning |
| --- | --- | --- |
| `RESULT_JSON` | yes | A result JSON produced by `run` (its `container_pairs` hold the predictions). |

### Options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--out PATH` | required | Where to write the draft manifest YAML. |
| `--name NAME` | the test config name | Dataset name written into the manifest. |
| `--overwrite` | off | Replace `--out` if it already exists (otherwise the command errors). |

### Workflow

```bash
# 1. Capture predictions by running once (a real integration is most useful here)
ingoread-test run config.yaml --results-dir results

# 2. Draft a manifest from those predictions
ingoread-test bootstrap results/<timestamp>__<name>.json --out dataset/manifest.yaml

# 3. Hand-correct dataset/manifest.yaml, then point a config at it and run normally
```

---

## 3b. `suite` — run several datasets, one release decision

Groups multiple dataset configs and gates the release across all of them with a
single command and exit code. Each dataset is run, scored, and gated
independently (against its own baseline); **the release is allowed only if every
*blocking* dataset passes.**

```bash
ingoread-test suite SUITE_CONFIG [--results-dir DIR] [--no-viz]
```

### Suite config

Member `config`/`baseline` paths resolve relative to the suite file.

```yaml
name: nightly
# min_macro_match_rate: 0.95     # optional extra gate on the mean across datasets
datasets:
  - name: vehicle_registration
    config: vehicle_registration/config.yaml
    baseline: baselines/vehicle_registration.json   # optional, per dataset
    blocking: true
  - name: experimental_passports
    config: passports/config.yaml
    blocking: false               # reported but never blocks the release
```

### Outputs

- per-dataset JSON + HTML under `results/<dataset-name>/` (same as `run`),
- a suite index `results/<ts>__<suite>__suite.html` (macro/micro rates, a pass/block
  table linking each report),
- printed summary ending in `release_gate=OK|BLOCKED`; exit `0`/`1`.

**Macro** match rate = mean across datasets (each weighted equally, so a small
dataset's regression isn't hidden). **Micro** = pooled over every document.

```bash
ingoread-test suite examples/suite.yaml
```

---

## 3c. `dataset` — curate a dataset

Datasets live in S3 (a local directory works the same way). These commands read
and write only the manifest object, so they never download documents.

```bash
ingoread-test dataset list    URI [--manifest NAME] [--removed-only]
ingoread-test dataset upload  LOCAL_DIR URI [--dry-run]
ingoread-test dataset remove  URI SELECTOR... [--reason TEXT] [--purge] [--dry-run] [--yes]
ingoread-test dataset restore URI SELECTOR... [--dry-run]
```

`URI` is the dataset root (`s3://ingoread-datasets/invoices-ru` or a local
path). A `SELECTOR` is a **sample id or a filename** — whichever you have in
front of you.

| Flag | Applies to | Meaning |
| --- | --- | --- |
| `--manifest NAME` | all | Manifest name inside the dataset (default `manifest.yaml`). |
| `--endpoint-url URL`, `--region NAME` | all | Non-default S3 endpoint/region. |
| `--removed-only` | `list` | Show only samples that are currently removed. |
| `--reason TEXT` | `remove` | Recorded in the manifest next to the removal. |
| `--purge` | `remove` | Drop the entry entirely instead of marking it removed. Irreversible. |
| `--dry-run` | `upload`, `remove`, `restore` | Report what would change; write nothing. |
| `--yes` / `-y` | `remove` | Skip the confirmation prompt for `--purge`. |

`dataset upload` publishes a local dataset folder, keeping its layout. It parses
the manifest and checks every sample's id file against it first, so a dataset
that would fail at run time is never published:

```bash
ingoread-test dataset upload datasets/invoices-ru s3://ingoread-datasets/invoices-ru
```

Removal is a **soft delete** by default: the sample keeps its ground truth,
gains a `removal` record, and is skipped by every run — so it can be restored.

These commands change the manifest only. **The sample's objects in S3 are left
alone** — deleting those is another system's job; removing the entry here is
what stops the sample being sent.

```bash
# A customer asks for their document to be dropped
ingoread-test dataset remove s3://ingoread-datasets/invoices-ru inv-0042 \
  --reason "customer request"

# It was a mistake — put it back
ingoread-test dataset restore s3://ingoread-datasets/invoices-ru inv-0042

# Drop the entry for good (the document itself is deleted by another system)
ingoread-test dataset remove s3://ingoread-datasets/invoices-ru inv-0042 --purge
```

Output lines: `removed=<id>`, `purged=<id>`, `restored=<id>`, `unchanged=<id>`,
`manifest_written=<uri>`, `manifest_unchanged=1`,
`dry_run=1 manifest_not_written=<uri>`, and `not_found=<selector>` on stderr.
A selector that matches nothing exits `1`.

Note: rewriting a manifest is a machine rewrite — comments in a hand-written
manifest do not survive it.

---

## 4. Config file format

A single YAML (or JSON) file with two top-level keys, `test` and `scorer`.

```yaml
test:
  name: vehicle_registration
  dataset:
    uri: s3://ingoread-datasets/vehicle_registration  # or a local directory
    manifest: manifest.yaml        # name inside the dataset (default)
    name: vehicle_registration     # default: last segment of `uri`
    cache_dir: .cache/ingoread-datasets   # local mirror of the S3 prefix
    files_root: null               # override where documents are read from
    exclude_sample_ids: []         # skipped on every run of this config
    force_download: false          # always re-download, ignoring the cache
  results:
    local_dir: null                # null = stage in a temp dir and discard after upload;
                                   #   set a path to keep the run's files there
    uri: s3://ingoread-results/runs   # null = local only (nothing is discarded)
    upload_html: true
  storage:
    endpoint_url: null             # non-AWS S3 endpoint
    region_name: null
  integration_name: ingoread
  batch_size: 4            # max concurrent in-flight documents
  timeout: 60             # per-document hard timeout (seconds)
  kwargs: {}              # global per-request kwargs (merged with per-file kwargs)
  integration:
    kind: stub            # stub | http
    # --- http only ---
    url: https://ingoread.example.com
    auth_token: <token>
    poll_interval: 1.0     # seconds between status polls
    poll_timeout: null     # max seconds to poll one task (null = no client cap)
    data_field_name: null  # null = spread kwargs as form fields; str = JSON blob under this field
    sample_id_field: sample_id  # form field carrying the sample's unique id; null = don't send it
    # --- stub only ---
    stub_predictions_dir: null   # dir of <filename>.json predictions; else echoes GT
  history:                 # the release gate
    match_rate_tolerance: 0.02
    fail_on_regression: true
    fail_on_error: true
    fail_on_empty: true

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
          measurer_kwargs: {abs_tol: 0.5}
        - field_name: signature_box
          field_type: bbox
          measurer_kwargs: {iou_threshold: 0.5}
        - field_name: chassis
          field_type: text
          ignore: true        # kept in config but not scored or reported
```

### Version 1 configs

A config that still uses the old pair

```yaml
test:
  files_root: examples/vehicle_registration/dataset
  manifest: examples/vehicle_registration/dataset/manifest.yaml
```

keeps working: the two keys are folded into `dataset.uri` / `dataset.manifest`
at load time. New configs should use the `dataset` section.

### Release-gate switches (`test.history`)

The run exits non-zero (blocking CI) when any enabled condition trips:

| Key | Default | Blocks when |
| --- | --- | --- |
| `fail_on_error` | true | any document `failed` or `timed out` |
| `fail_on_empty` | true | nothing was scored (no evidence to validate) |
| `fail_on_regression` | true | metrics dropped past `match_rate_tolerance` vs `--previous` |
| `match_rate_tolerance` | 0.02 | (threshold used by `fail_on_regression`) |

Error/empty checks apply with or without a baseline; the regression check runs
only when `--previous` is given.

### Integration kinds (`test.integration.kind`)

- **`stub`** — deterministic, no network. Returns `stub_predictions_dir/<filename>.json`
  if present, otherwise echoes ground truth back (every field matches). For smoke tests.
- **`http`** — talks to a live instance:
  - `POST {url}/integrations/{integration_name}` (multipart) → `{"task_id": ...}`
  - `GET {url}/status/{task_id}` polled until `completed`/`failed`
  - Every create-task POST also carries the sample's unique id in its own form
    field (`sample_id_field`, default `sample_id`) — see
    [the manifest format](#6-dataset-format-version-2).
  - Needs the documents on disk; they are downloaded from `dataset.uri` into
    `dataset.cache_dir` on the first run.

### Passing per-document kwargs

`test.kwargs` (global) and a container's `kwargs` (per file in the manifest) are
merged and sent as multipart form data:

- `data_field_name` **unset** (default) — *spread*: each kwarg is its own form
  field; non-string values are JSON-encoded.
- `data_field_name` **set** — *single-blob*: all kwargs JSON-encoded together
  under that one field name.

---

## 5. Field types

| `field_type` | Matched when | Metrics | `measurer_kwargs` |
| --- | --- | --- | --- |
| `text` | exact string equality | `cer`, `wer` | `strip`, `casefold` |
| `literal` | exact string equality | `accuracy` | `strip`, `casefold` |
| `number` | values equal (or within tolerance) | `mae`, `mse` | `abs_tol`, `rel_tol` |
| `bool` | truthiness matches (`true/1/yes/y/да`) | `accuracy` | – |
| `bbox` | IoU ≥ threshold | `iou` | `iou_threshold` (0.5) |
| `bbox_set` | a field with several boxes (e.g. stamps): all GT boxes found at threshold AND count exact | `iou` (mean), `count_gt`, `count_pred`, `count_match`, `precision`, `recall` | `iou_threshold` (0.5) |
| `llm_text` | requires `LLM_JUDGE_URL` (not in v1) | – | – |

`gt_value` is stored natively: a number (`184`), a bool (`true`), a `bbox` list
(`[x1, y1, x2, y2]`), or a `bbox_set` list of boxes (`[[..], [..]]`). The legacy
string forms (`"x1,y1,x2,y2"`, `"…; …"`) are still accepted. For `bbox_set`, all
predicted boxes in the field are matched one-to-one to the GT boxes by IoU
(Hungarian), so order doesn't matter.

Extra per-field keys:

- `ignore: true` — exclude the field from scoring, aggregation, and the report.
- `field_group: <name>` — several fields with the same group count as one
  "any-of" match.
- `selection: first` — which predicted value to score (only `first` in v1;
  `top_n`/`all` are reserved). `take_first: true|false` is a legacy alias.

---

## 6. Dataset format (version 2)

A dataset is an S3 prefix holding a manifest, the documents, and one id file per
sample:

```
s3://ingoread-datasets/vehicle_registration
├── manifest.yaml
├── vrc_001.pdf
├── vrc_001.id          # "vrc_001" — the unique id sent with the document
└── ...
```

### `manifest.yaml`

```yaml
version: 2
name: vehicle_registration
samples:
  - sample_id: vrc_001              # unique within the dataset
    filename: vrc_001.pdf
    # id_file: ids/vrc_001.txt      # optional; default "<stem>.id"
    # kwargs: {language: ru}        # per-file request kwargs
    documents:
      - doc_label: vehicle_registration
        page: 0
        bbox: [120, 80, 1480, 1020]   # used by the Hungarian matcher when >1 doc/file
        fields:
          vin: {gt_value: "JTHBK1GG1F2123456"}
          engine_power: {gt_value: "184.0"}
          signature_box:
            gt_value: [900, 820, 1180, 990]
            measurer_kwargs: {iou_threshold: 0.5}

  - sample_id: vrc_002
    filename: vrc_002.pdf
    removed: true                   # skipped by every run, kept for the record
    removal:
      reason: customer request
      removed_at: 2026-09-15T08:12:44Z
      removed_by: gleb
    documents: []
```

| Key | Required | Meaning |
| --- | --- | --- |
| `version` | yes | `2`. A version 1 manifest (a bare list) still loads; see below. |
| `name` | no | Dataset name; also the first folder of the uploaded results. |
| `sample_id` | yes | Unique id. Keys predictions and results, is sent to the backend, and names the sample in `dataset remove`. |
| `filename` | yes | The document, relative to the dataset root. |
| `id_file` | no | Where the id file lives (default `<stem>.id`). |
| `removed` / `removal` | no | Written by `dataset remove`; removed samples are never sent. |

### The id file

Each sample ships a small UTF-8 text file whose entire content is the sample's
identifier (`vrc_001.id` → `vrc_001`). That file is the source of the id that
the create-task POST carries, so the identifier travels with the data rather
than living only in the manifest. Loading fails if the id file and the manifest
disagree; when there is no id file, the manifest id is used.

### Version 1 manifests

The old top-level list still loads unchanged — each entry's filename stem
becomes its `sample_id`:

```yaml
- filename: vrc_001.pdf
  documents: [...]
```

Anything written by the tool (`dataset remove`, `bootstrap`) is version 2.

---

## 7. Development

```bash
pytest                 # run the test suite
pytest -q tests/test_release_gate.py   # a single file
ruff check .           # lint
ruff check --fix .     # lint + autofix
```

---

## 8. Quick recipes

```bash
# Smoke-test the whole pipeline with no backend
ingoread-test run examples/vehicle_registration/config.yaml

# Establish a baseline, then gate the next build against it
ingoread-test run config.yaml --results-dir results
ingoread-test run config.yaml --previous results/<baseline>.json

# Seed ground truth from the model, then refine by hand
ingoread-test run config.yaml --results-dir results
ingoread-test bootstrap results/<run>.json --out dataset/manifest.yaml

# Drop a sample a customer asked us to delete, then re-run
ingoread-test dataset remove s3://ingoread-datasets/invoices-ru inv-0042 --reason "customer request"
ingoread-test run config.yaml

# Gate against the previous run straight out of S3
ingoread-test run config.yaml \
  --previous s3://ingoread-results/runs/invoices-ru/ingoread/2026-09-14/091500/result.json
```
