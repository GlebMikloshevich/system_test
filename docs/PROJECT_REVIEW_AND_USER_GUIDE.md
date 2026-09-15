# Project review and user guide

Review date: 2026-08-09
Reviewed state: the complete working tree, including its uncommitted files and changes

## 1. Executive summary

`ingoread-test` is a compact Python 3.12 test harness for document-extraction
services. It loads labelled manifests, calls a stub or asynchronous HTTP backend,
pairs expected and predicted documents, calculates field/document metrics, writes
JSON and HTML reports, and can use those results as a release gate. The main design
is understandable, the data models are sensibly separated, and the current test
suite is useful: all 75 tests pass.

The project is not yet safe to treat as an unattended production release gate.
Several configurations fail open or are accepted even though they cannot be
executed correctly. The most important examples are:

1. Pydantic ignores unknown configuration keys. A misspelled `integration` block
   silently selects the default `stub`, which echoes ground truth and normally
   produces a perfect result.
2. A run with real scored documents and `match_rate == 0.0` is allowed when no
   baseline is supplied. There is no absolute minimum-accuracy gate.
3. Empty or fully ignored field configurations can make paired documents match
   automatically. A misspelled text/literal GT field can also match when both the
   expected and predicted values are absent.
4. `batch_size: 0` is accepted and makes every task wait on a semaphore that can
   never be acquired. The per-document timeout starts only after acquisition, so
   it does not rescue the run.
5. Baseline comparisons check rates but not dataset identity, configuration
   identity, schema, or sample coverage. The wrong JSON file or a truncated
   dataset can therefore produce a misleading green gate.

The recommended immediate work is strict configuration validation, explicit gate
coverage/threshold policies, validation of scorer and manifest schemas, and unique
sample identifiers. Until those changes land, use the operational safeguards in
section 8.

## 2. What was inspected and verified

The review covered:

- package metadata and dependency declarations in `pyproject.toml`;
- every module under `src/ingoread_test`;
- the current tests and example configurations;
- the README and all Markdown documentation;
- CLI behavior, configuration edge cases, and invocation outside the repository;
- test and lint execution.

Verification results:

| Check | Result |
| --- | --- |
| `uv run --extra test pytest -q` | **Pass: 75 tests** |
| `uv run --with ruff ruff check .` | **Fail: 15 findings** |
| CLI discovery (`uv run ingoread-test --help`) | Pass; `run`, `bootstrap`, and `suite` are registered |
| Run a bundled config outside the repository root | Fail; relative manifest path is resolved against the current directory |
| Config typo probe (`integraton` instead of `integration`) | Accepted; integration defaults to `stub` |
| `batch_size: 0` validation probe | Accepted |
| Empty scorer and duplicate-label probes | Accepted |
| `selection: all` validation probe | Accepted, although scoring later raises `NotImplementedError` |

The test result is encouraging, but it should not be read as proof that release
decisions are correct. Most of the fail-open and malformed-config cases above do
not currently have tests.

## 3. Architecture at a glance

The normal single-dataset flow is:

```text
config file -> TestConfig + ScorerConfig
manifest    -> Dataset
Dataset + Integration -> run_test() -> predictions + run statistics
predictions + GT       -> score()    -> MeasurementsResult
MeasurementsResult     -> JSON + HTML + optional baseline comparison
comparison + run state -> release gate exit code
```

The package has five useful conceptual areas:

- `config/`: Pydantic configuration models and YAML/JSON loading;
- `dataset/`: ground-truth models, manifest loading, and bootstrap conversion;
- `integration/`: the backend interface, deterministic stub, and HTTP client;
- `scoring/`: field scorers, document scoring, geometry, and Hungarian pairing;
- `modules/` and `results/`: orchestration, aggregation, reporting, and output
  models.

The separation between ground-truth data and scoring policy is a good decision:
the manifest owns values while `FieldConfig` owns types and tolerances. The
integration interface also makes HTTP-independent tests straightforward.

## 4. Findings by priority

### Critical: release correctness and fail-open behavior

#### C1. Configuration typos can silently activate the perfect-score stub

Evidence:

- `IntegrationConfig.kind` defaults to `stub` in
  `src/ingoread_test/config/test_config.py`.
- The config models do not use `ConfigDict(extra="forbid")`.
- A direct validation probe with `integraton: {kind: http}` was accepted and
  produced `integration.kind == "stub"`.
- `StubIntegration` echoes the ground truth when no prediction file exists.

Impact: a misspelled production configuration can test the harness against its
own labels instead of testing the service, report `1.000`, and allow a release.

Recommendation:

- forbid extra keys on every config and manifest model;
- require an explicit integration kind for CLI/release runs;
- add a safety switch such as `--allow-stub`, disabled in gate mode by default;
- print the selected integration kind and URL host in the run summary;
- add a test that a misspelled key fails before any I/O.

#### C2. Zero accuracy can pass without a baseline

`evaluate_release_gate()` blocks on errors, no document results, or regression
against a supplied comparison. It has no `min_match_rate` or equivalent absolute
threshold. A valid run that scores documents at `0.0`, returns no backend errors,
and has no `--previous` result therefore exits successfully.

Recommendation: add configurable absolute thresholds at the overall, label, and
important-field levels. In release mode, require either a baseline or explicit
absolute thresholds; do not allow an unconfigured accuracy policy.

#### C3. Empty scoring policies and missing GT fields can receive credit

`score_document_pair()` defines an empty list of field decisions as a match:
`all([])` is replaced explicitly with `True`. This affects document configs with
no fields and configs whose fields are all `ignore: true`.

For a configured field missing from the manifest, the scorer substitutes `""`.
That absent value matches an absent prediction for `text` and `literal`; an empty
`bbox_set` also matches when no boxes are predicted. Consequently, a field-name
typo can award credit instead of reporting invalid ground truth.

Recommendation:

- require at least one non-ignored field per document scorer;
- validate that every configured required field exists in every applicable GT
  document, unless a separate `optional` policy is explicitly configured;
- distinguish missing, null, and intentionally empty values;
- treat schema violations as gate-blocking input errors, not scoring outcomes.

#### C4. Baseline compatibility and coverage are not checked

The history comparison accepts any parseable `MeasurementsResult`. It does not
verify `test_config_name`, `scorer_config_name`, field schema, dataset version,
sample identifiers, or sample counts. Per-label and overall rates are compared,
but a large per-label sample-count drop is not a regression by itself.

Impact: selecting the wrong baseline or evaluating a truncated manifest can allow
a release on incomparable evidence.

Recommendation: store a deterministic dataset/config fingerprint, compare it
before metrics, and add `min_sample_ratio` plus exact/allowed sample membership
policies. A baseline mismatch should block with a precise reason.

#### C5. `batch_size: 0` can hang indefinitely

`run_test()` constructs `asyncio.Semaphore(cfg.batch_size)`. A zero value is valid
to `asyncio` but can never be acquired. `asyncio.wait_for()` is inside the
semaphore context, so `TestConfig.timeout` never starts.

Recommendation: validate `batch_size >= 1`; also validate `timeout > 0`,
`poll_interval >= 0`, tolerances, and thresholds at configuration-load time.

#### C6. Unconfigured GT labels are only warnings

The scorer warns and skips a GT label when it has no corresponding
`DocumentMeasurerConfig`. `fail_on_empty` catches the special case where nothing
is scored, but a partially configured dataset can score one label, ignore another,
and pass.

Recommendation: make unmatched GT labels a configuration error by default. If
partial scoring is needed, require an explicit allowlist and report coverage.

### High: incorrect, incomplete, or fragile results

#### H1. Duplicate filenames silently overwrite predictions

`run_test()` returns `dict[str, IngoreadFileResult]` keyed only by filename, and
`score()` performs the same lookup. Two containers with the same filename race to
write one dictionary entry. `group_id` exists on `DocumentContainer` but is not
used to establish identity.

Recommendation: validate filename uniqueness or use a stable sample ID/index
throughout prediction, scoring, results, and reports.

#### H2. Relative paths depend on the caller's working directory

`load_configs()` leaves `test.files_root` and `test.manifest` unchanged. Running a
bundled config by absolute path from `/tmp` reproduced a `FileNotFoundError` for
`examples/vehicle_registration/dataset/manifest.yaml`. Suite member config paths
are anchored to the suite file, but paths *inside* each member config are still
working-directory-relative.

Recommendation: resolve `files_root`, `manifest`, and
`stub_predictions_dir` relative to the config file. Document and test that rule.

#### H3. Accepted options fail only after inference has run

`PredictionSelection.TOP_N` and `ALL` are public enum values, and the legacy
`take_first: false` maps to `ALL`, but the scorer implements only `FIRST`.
`LLM_TEXT` is also exposed: without `LLM_JUDGE_URL` it raises during scoring, and
with the variable set it does not call an LLM service at all—it simply delegates
to exact text scoring.

Impact: costly backend work completes before the configuration crashes or, for
`LLM_TEXT`, produces metrics with semantics different from the type name.

Recommendation: remove placeholder values from the accepted schema until they
exist, or reject them during config validation. Keep `LLM_TEXT` disabled until an
actual judge client, response schema, failure policy, and tests are implemented.

#### H4. Bootstrap loses multi-box ground truth

`bbox_set` scoring intentionally consumes every predicted box, but bootstrap
always writes only `preds[0]` for each field. Bootstrapping a stamps/photos field
therefore discards all but the first box and creates incorrect draft GT.

Recommendation: make bootstrap field-type-aware, preserve all boxes for
`bbox_set`, and add a round-trip test with two or more boxes.

#### H5. Metric denominators are inconsistent

- `MeasurementsResult.total_samples` is the number of file containers, while
  overall `match_rate` is calculated over document pairs.
- Per-label `time` and `time_per_sample` repeat global wall-clock/file values.
- Prediction-only half-pairs lower document accuracy but contain no field metrics,
  so they are absent from field-level denominators.
- Invalid numeric values emit infinite MAE/MSE, but aggregation silently drops
  infinity. The error average can therefore look good while invalid values lower
  match rate.

Recommendation: publish explicit counts for files, GT documents, predicted
documents, pairs, and scored values. Give every metric its own `n_valid` and
`n_invalid`; do not silently discard values. Report actual per-request latency
percentiles rather than wall time divided by concurrent file count.

#### H6. Geometry and numeric configuration are weakly validated

A box is considered valid when it merely has four values. Coordinate order,
finite numbers, positive area, coordinate convention, and page bounds are not
validated. Invalid boxes can silently score zero or force arbitrary Hungarian
assignments. GT box conversion can also raise during scoring for malformed nested
values because `FieldGT.gt_value` is `Any`.

Numeric tolerances and IoU thresholds are stored in an untyped dictionary and
converted to floats late in scoring. Non-numeric or negative tolerances can crash
after the backend run.

Recommendation: introduce typed per-field option models and validated box types;
require finite values, `x1 < x2`, `y1 < y2`, and thresholds in `[0, 1]`.

#### H7. HTTP behavior is brittle under service evolution and transient failure

- Unknown task status strings normalize directly to `FAILED`.
- Create and poll calls have no bounded retry/backoff.
- Completed responses without a recognized `result` shape become empty results.
- The parser supports several inferred shapes rather than one explicit versioned
  contract.
- Every uploaded file is read fully into memory; `run_test()` also creates a
  coroutine for every manifest entry at once, even though the semaphore limits
  active requests.

Recommendation: model create/status responses explicitly, reject unknown status
values with diagnostic context, add configurable retries for safe operations,
stream uploads where possible, and use a bounded worker queue for large datasets.

#### H8. Report artifacts contain full source and extracted values

`MeasurementsResult.container_pairs` embeds the complete manifest containers,
predictions, field values, and pair details. This is helpful for debugging but can
store sensitive document data and makes baselines unnecessarily large.

Recommendation: document the data classification, restrict artifact access and
retention, redact sensitive fields, and offer separate compact summary and verbose
debug artifacts. History comparison should need only the compact form.

### Medium: maintainability, usability, and observability

#### M1. Output files can overwrite each other

JSON, dataset HTML, and suite HTML names use timestamps with one-second
resolution. Runs with the same test name in the same second overwrite files.
Writes are not atomic.

Use microseconds or a run UUID, refuse unexpected replacement, and write to a
temporary file followed by an atomic rename.

#### M2. Suite configuration can be empty and still allow a release

`SuiteConfig.datasets` defaults to an empty list. With no macro threshold, the
suite gate has no reason to block. Require at least one blocking dataset for
release mode. Also define whether failed advisory datasets should count in the
displayed `n_blocked` and macro metrics; current labels can be confusing because
advisory failure is counted as blocked but does not block the suite.

#### M3. Broad exception handling obscures root causes

`run_test()` converts every exception into a per-file failed result containing
only `str(exc)`. That keeps a batch running, but it conflates service failures,
bad response schemas, local file errors, and harness programming bugs. The HTML
report shows the message, but traceback/type information is lost at this layer.

Catch expected transport and input failures explicitly. For unexpected errors,
record exception type and traceback in logs and use a distinct harness-error gate
reason.

#### M4. Config uniqueness and conflicts are not validated

Duplicate document labels are accepted and later collapsed by a dictionary, so
the last definition wins. Duplicate field names overwrite `field_metrics` while
still affecting the internal match list more than once. `take_first` can silently
override an explicitly supplied `selection`.

Reject duplicate labels/fields and conflicting legacy/current options.

#### M5. Secrets are literal configuration values

`auth_token` has no environment-variable or secret-provider resolution. Keeping
tokens in YAML invites accidental commits. Prefer an environment variable name,
secret reference, or CLI injection; ensure logs never print the value.

#### M6. Packaging and reproducibility metadata are incomplete

The project description and authors are empty; there is no declared license,
project URL, classifier set, or packaged type marker. `uv.lock` exists locally but
is ignored by `.gitignore`, which undermines reproducible application/CI installs.
There is no visible CI configuration.

For an application/release tool, commit the lockfile, fill package metadata, add
a license, and run tests/lint in CI. The `.gitignore` entry `.DS_store` also has
the wrong capitalization for the `.DS_Store` file currently present.

#### M7. Logging is configured as an import side effect

`logging.basicConfig()` executes when `ingoread_test.cli` is imported. Library
consumers should control root logging. Configure logging in the executable entry
path or expose a verbosity option.

### Redundant or unfinished abstractions

These are not immediate correctness bugs, but they make the public surface larger
than the implemented behavior:

- `Sink` has one implementation (`JsonFileSink`), while HTML output bypasses it.
  Either make JSON/HTML proper sink implementations and orchestrate a sink list,
  or remove the abstract base until multiple interchangeable sinks are needed.
- `PredictionSelection.TOP_N`, `PredictionSelection.ALL`, `take_first`, and
  `LLM_TEXT` advertise functionality that is not implemented.
- `group_id` is stored but unused even where it would solve sample identity.
- `DocumentMeasurement.time` and `time_per_sample` duplicate global run values
  for every label rather than representing label-specific measurements.
- YAML/JSON loading logic appears separately in config and dataset loaders.
- Single-run and suite-run orchestration duplicate load/run/score/report logic.
  A `run_dataset()` service returning an outcome would reduce divergence.
- The `modules/` layer mostly contains free functions. Names such as
  `TestModule`/`ScorerModule` in documentation imply a stronger component
  abstraction than the code provides.

Avoid adding more framework layers. First remove placeholders, define a single
dataset-run pipeline, and make result identities/metric contracts explicit.

## 5. Documentation review

The current documentation is much more extensive than the tracked baseline and
contains useful examples, but several statements do not match the code:

1. README and `docs/commands.md` install with `uv sync --extra dev`; the declared
   optional dependency is named `test`, not `dev`.
2. HTTP documentation shows `/integrations/{name}` and `/status/{id}`; the client
   calls `/api/integrations/{name}` and `/api/status/{id}`.
3. The `string` integration kind is implemented but missing from the principal
   integration reference sections.
4. `docs/architecture.md` tells extension authors to modify
   `cli._build_integration`, which no longer exists; the factory is
   `integration/factory.py:build_integration()`.
5. The architecture field table omits `bbox_set`.
6. The README comment `ignore: true  # documented but not scored yet` is
   ambiguous: ignored fields are intentionally and currently excluded from
   scoring.
7. `docs/code-review.md` begins with an accidental `¬` character and contains
   line references/status notes that can drift as uncommitted changes evolve.
8. User docs do not warn clearly that a no-baseline, zero-accuracy run can pass,
   that the default integration is the GT-echo stub, or that result artifacts may
   contain sensitive values.

Keep one authoritative command/config reference, generate CLI help/reference
where practical, and turn review findings into tracked issues rather than leaving
changing implementation status in a static audit.

## 6. Test and code-quality review

### What is covered well

The 75 passing tests cover the central happy paths and several useful edges:

- async batching, timeout counting, and failed status counting;
- stub end-to-end scoring;
- HTTP create/poll parsing, string-only requests, kwargs serialization, failed
  status, and polling timeout;
- text, literal, boolean, number, bbox, and multi-box scoring;
- Hungarian pairing, half-pairs, malformed document bbox fallback, and page
  grouping;
- release gate switches and vanished labels;
- suite macro/micro metrics, advisory datasets, baselines, and isolation;
- result schema normalization, bootstrap, and native GT values.

### Important missing tests

Add focused tests for:

- unknown config keys and missing explicit integration kind;
- zero/negative batch sizes, timeouts, tolerances, and invalid thresholds;
- empty/all-ignored scorer configs and missing required GT fields;
- partial unconfigured-label coverage;
- duplicate filenames, labels, and field names;
- wrong/incompatible baseline and reduced sample coverage;
- zero accuracy without a baseline and absolute threshold behavior;
- `bbox_set` bootstrap with multiple boxes;
- invalid/non-finite boxes and numeric values;
- unknown HTTP statuses, schema drift, retries, and create-task timeout behavior;
- output filename collision;
- empty suite release behavior;
- execution of a config from a directory other than the repository root.

### Lint status

Ruff currently reports 15 issues: Typer default-call warnings, unsorted imports
and `__all__`, two exception-type recommendations, one unnecessary `return None`,
one stale `noqa`, and a test `subprocess.run()` without explicit `check`.

For Typer, prefer `typing.Annotated` declarations or configure a narrow B008
ignore if the installed Typer version requires call-style defaults. Fix the
remaining findings and make `ruff check .` a CI gate.

## 7. Growth roadmap

### Phase 1: make the gate trustworthy

1. Strictly validate all config and manifest models; require explicit integration.
2. Validate scorer coverage, uniqueness, field presence, bounds, and box types.
3. Add absolute thresholds, baseline identity, and sample-coverage checks.
4. Reject empty policies/suites and make stub use explicit in release mode.
5. Replace filename-only identity with a stable sample ID.

### Phase 2: make results interpretable

1. Separate file/document/field denominators and expose valid/invalid counts.
2. Add field-level and coverage gates for business-critical fields.
3. Report true request-latency percentiles and harness/service error categories.
4. Split compact baseline artifacts from sensitive verbose diagnostics.
5. Use collision-resistant run IDs and atomic artifact writes.

### Phase 3: harden integrations and scale

1. Version and validate HTTP response contracts.
2. Add bounded retries/backoff, diagnostic status handling, and streaming uploads.
3. Replace one-coroutine-per-input gathering with a bounded worker queue.
4. Allow safe environment/secret injection and sanitized observability.
5. Parallelize suite members only after defining service concurrency limits.

### Phase 4: simplify and extend deliberately

1. Consolidate the dataset run pipeline shared by `run` and `suite`.
2. Either complete or remove placeholder selection/LLM APIs.
3. Rationalize output sinks and repeated timing fields.
4. Add versioned result schemas and migration/compatibility tests.
5. Complete packaging metadata, commit the lockfile, and publish CI artifacts.

## 8. User guide

### 8.1 Prerequisites and installation

Requirements:

- Python 3.12 or newer;
- `uv` (recommended), or a recent `pip`;
- access to the target Ingoread service for `http`/`string` runs.

From the repository root:

```bash
# Runtime environment
uv sync

# Runtime plus test dependencies
uv sync --extra test

# Run without activating the virtual environment
uv run ingoread-test --help
```

Pip alternative:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
ingoread-test --help
```

Do not use `--extra dev` unless a `dev` extra is added to `pyproject.toml`.

### 8.2 Important safety rules for the current version

Until the critical findings are fixed:

1. Run commands from the repository root because internal config paths are
   currently working-directory-relative.
2. Always write `test.integration.kind` explicitly and confirm the CLI is not
   using `stub` for a production gate.
3. Always use a reviewed `--previous` baseline for release decisions. Remember
   that this still does not validate sample coverage or baseline identity.
4. Review the console summary and JSON artifact; do not trust exit code alone.
5. Verify every GT label has one scorer config and every required field exists.
6. Never use zero `batch_size`; use positive timeouts and valid thresholds.
7. Protect the results directory as sensitive data because JSON/HTML can contain
   ground truth and extracted values.
8. Keep auth tokens out of committed YAML. Generate a temporary config from your
   CI secret store until native environment-secret support exists.

### 8.3 Minimal dataset layout

```text
datasets/invoices/
├── config.yaml
├── manifest.yaml
└── files/
    ├── invoice_001.pdf
    └── invoice_002.pdf
```

Because paths are currently interpreted from the command's working directory,
the examples below use repository-root-relative paths.

`datasets/invoices/manifest.yaml`:

```yaml
- filename: invoice_001.pdf
  kwargs:
    language: en
  documents:
    - doc_label: invoice
      page: 0
      bbox: [10, 20, 1000, 1400]
      fields:
        vendor: {gt_value: "Acme Ltd"}
        total: {gt_value: 123.45}
        paid: {gt_value: true}
        signature_box: {gt_value: [800, 1100, 950, 1300]}
        stamps:
          gt_value:
            - [100, 900, 240, 1040]
            - [300, 900, 440, 1040]
```

The `filename` is joined to `test.files_root` unless `file_path` is supplied.
Keep filenames unique. `kwargs` are request parameters for that container and
override keys of the same name in global `test.kwargs`.

### 8.4 Single-test configuration

`datasets/invoices/config.yaml`:

```yaml
test:
  name: invoices
  files_root: datasets/invoices/files
  manifest: datasets/invoices/manifest.yaml
  integration_name: invoice_extractor
  batch_size: 4
  timeout: 120
  kwargs:
    model_version: stable
  integration:
    kind: stub
  history:
    match_rate_tolerance: 0.02
    fail_on_regression: true
    fail_on_error: true
    fail_on_empty: true

scorer:
  name: invoices_v1
  measurement_configs:
    - doc_label: invoice
      multipage_matching: false
      fields:
        - field_name: vendor
          field_type: text
          measurer_kwargs: {strip: true, casefold: true}
        - field_name: total
          field_type: number
          measurer_kwargs: {abs_tol: 0.01}
        - field_name: paid
          field_type: bool
        - field_name: signature_box
          field_type: bbox
          measurer_kwargs: {iou_threshold: 0.5}
        - field_name: stamps
          field_type: bbox_set
          measurer_kwargs: {iou_threshold: 0.5}
```

Start with the stub only to validate dataset/scorer wiring:

```bash
uv run ingoread-test run datasets/invoices/config.yaml \
  --results-dir results/smoke
```

The stub echoes GT, so a normal smoke run should score `1.000`. This proves that
the harness can parse and score the dataset; it does **not** evaluate a model or
service.

### 8.5 Real HTTP and string-only integrations

File upload mode:

```yaml
test:
  integration_name: invoice_extractor
  integration:
    kind: http
    url: https://ingoread.example.com
    auth_token: "replace-at-runtime"
    poll_interval: 1.0
    poll_timeout: 110
```

The implemented protocol is:

```text
POST {url}/api/integrations/{integration_name}
GET  {url}/api/status/{task_id}
```

`kind: http` sends the file as multipart field `file`. `kind: string` sends no
file; all input must be supplied through global/container `kwargs`:

```yaml
test:
  files_root: datasets/string_inputs
  manifest: datasets/string_inputs/manifest.yaml
  integration_name: text_extractor
  integration:
    kind: string
    url: https://ingoread.example.com
```

Request kwargs use spread mode by default: each key becomes a form field and
non-string values are JSON encoded. To send one JSON blob instead:

```yaml
integration:
  kind: http
  url: https://ingoread.example.com
  data_field_name: mapping_string
```

The overall CLI timeout wraps the entire per-container operation. Set
`poll_timeout` below `test.timeout` so a stuck remote task returns a useful poll
error before the outer timeout fires.

### 8.6 Field types and matching semantics

| Type | Match rule | Reported metrics | Options |
| --- | --- | --- | --- |
| `text` | exact normalized string | CER, WER | `strip`, `casefold` |
| `literal` | exact normalized string | accuracy | `strip`, `casefold` |
| `number` | `math.isclose` | MAE, MSE | `abs_tol`, `rel_tol` |
| `bool` | recognized true/false token equals GT | accuracy | none |
| `bbox` | first predicted box IoU meets threshold | IoU | `iou_threshold` |
| `bbox_set` | exact count and every assigned box meets threshold | mean IoU, counts, precision, recall | `iou_threshold` |
| `llm_text` | **not safely implemented** | do not use | — |

Recognized boolean tokens are `true/1/yes/y/да` and
`false/0/no/n/нет`, case-insensitively. Unknown/missing values do not match.

Document matching is strict: every non-grouped required field and every field
group must match. Fields with the same `field_group` count as one any-of group.
`ignore: true` removes a field from scoring and reporting. Use only
`selection: first`; `top_n` and `all` currently fail at scoring time.

With multiple documents of the same label, the Hungarian algorithm chooses a
one-to-one assignment. It uses document-box IoU when every candidate has a
four-element bbox; otherwise it uses fraction of configured fields matched.
`multipage_matching: false` limits pairing to the same page.

### 8.7 Run, inspect, and gate one dataset

Run against the configured integration:

```bash
uv run ingoread-test run datasets/invoices/config.yaml \
  --results-dir results/invoices
```

Useful flags:

| Flag | Meaning |
| --- | --- |
| `--previous PATH` | compare against a previous result and enable regression evaluation |
| `--no-history` | ignore `--previous` |
| `--no-viz` | skip HTML output |
| `--results-dir DIR` | choose artifact directory |

The command prints artifact paths, metrics, gate reasons, and finally
`release_gate=OK` or `release_gate=BLOCKED`. Exit code `0` means allowed by the
configured checks; exit code `1` means blocked.

Create a reviewed baseline by preserving a known-good result JSON, then run:

```bash
uv run ingoread-test run datasets/invoices/config.yaml \
  --previous baselines/invoices.json \
  --results-dir results/invoices
```

Current gate conditions are:

- failed/timed-out containers when `fail_on_error` is true;
- no document measurement rows when `fail_on_empty` is true;
- overall or per-label match-rate regression beyond tolerance when a baseline is
  supplied and `fail_on_regression` is true.

There is currently no absolute minimum score or coverage check. A clean `0.0`
run without a baseline can exit zero; account for this in CI policy.

### 8.8 Bootstrap a draft manifest

Bootstrap converts predictions embedded in a result JSON into draft GT:

```bash
uv run ingoread-test bootstrap results/invoices/<run>.json \
  --out datasets/invoices/manifest.draft.yaml
```

Use `--overwrite` only when intentionally replacing the target. Review every
value manually—the model is being used to seed labels, not establish truth.

Current limitation: bootstrap keeps only the first prediction for each field and
therefore loses additional boxes in `bbox_set`. Repair those fields manually or
avoid bootstrap for multi-box labels until H4 is fixed.

### 8.9 Run a suite

`suite.yaml`:

```yaml
name: nightly
min_macro_match_rate: 0.90
datasets:
  - name: invoices
    config: datasets/invoices/config.yaml
    baseline: baselines/invoices.json
    blocking: true
  - name: experimental_receipts
    config: datasets/receipts/config.yaml
    blocking: false
    tags: [experimental]
```

Member `config` and `baseline` paths are resolved relative to the suite file.
Internal `manifest`/`files_root` paths are not, so run from the repository root:

```bash
uv run ingoread-test suite suite.yaml --results-dir results/nightly
```

Each member receives its own JSON and optional HTML report. The suite writes an
HTML index but no standalone suite JSON. A failing advisory member is reported
but does not block; every failing blocking member does. The optional macro
threshold covers datasets that produced results, so do not rely on it alone to
detect missing advisory datasets.

### 8.10 CI example

Use a reviewed baseline, preserve artifacts whether the gate passes or fails,
and publish only after the command exits zero. A generic shell step is:

```bash
set -o pipefail
uv sync --frozen --extra test
uv run --frozen ingoread-test run datasets/invoices/config.yaml \
  --previous baselines/invoices.json \
  --results-dir artifacts/ingoread-test
```

`uv --frozen` is meaningful only when the project commits a current `uv.lock`;
the repository should stop ignoring that file first. Add separate CI steps for:

```bash
uv run --extra test pytest -q
uv run --with ruff ruff check .
```

The lint step currently fails and must be cleaned up before making it required.

### 8.11 Troubleshooting

| Symptom | Check |
| --- | --- |
| Manifest `FileNotFoundError` | run from repository root; paths are currently CWD-relative |
| Stub unexpectedly scores `1.000` | verify `integration.kind`; check for a misspelled/ignored config block |
| `release_gate=OK` at `0.000` | no absolute threshold exists; supply a baseline and enforce CI policy |
| Run never starts | ensure `batch_size >= 1` |
| No documents scored | align manifest/prediction `doc_label` with scorer configs |
| One dataset label is absent from results | inspect warnings for an unconfigured GT label |
| HTTP immediately fails every task | verify `/api` endpoint contract and returned status vocabulary |
| Completed HTTP tasks produce empty results | compare the real response body with the expected `result` shape |
| Bbox always misses | require `[x1, y1, x2, y2]`, ordered coordinates, and a suitable IoU threshold |
| Config with `all`/`top_n` crashes after requests | use `selection: first` only |
| `llm_text` crashes or behaves like exact text | do not use it; the judge is not implemented |
| Baseline looks suspiciously good | verify names, sample set, schema, and counts manually |
| Files disappear after rapid repeated runs | second-resolution artifact names can collide |

## 9. Developer instructions

Run from the repository root:

```bash
uv sync --extra test
uv run --extra test pytest -q
uv run --with ruff ruff check .
```

When changing scoring or gate behavior:

1. Add a focused unit test for the exact edge case.
2. Add an end-to-end test showing the final gate exit behavior.
3. State metric denominator and missing/invalid-value semantics explicitly.
4. Keep result schema compatibility in mind; old JSON files are baselines.
5. Update the authoritative user reference and examples.
6. Test from outside the repository root when path behavior is involved.

For a new field type, implement and test its typed configuration, GT validation,
prediction selection, per-sample metrics, aggregation, report rendering, and
missing/invalid semantics before adding it to `FieldType`. For a new integration,
implement `Integration`, add it to `integration/factory.py`, define a versioned
response contract, and test failure/retry/timeout/resource-closing paths.

## 10. Final assessment

The project has a sound small-system foundation and good momentum: its core flow
is readable, async integration tests are practical, matching/scoring are separated,
and the current suite passes. The central weakness is not code complexity but
trust boundaries. Configuration, ground truth, baselines, and service responses
are treated as more valid and comparable than they really are. For a reporting
tool that is inconvenient; for a release gate it is decisive.

Make invalid or incomplete evidence block by default, give every metric an
explicit denominator, and require the gate policy to be intentional. Those
changes would provide more value than adding new scorer types or abstractions.
