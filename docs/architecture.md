# Architecture & internals

How `ingoread-test` is put together: the pipeline, the modules, the data models,
and the scoring algorithm. For task-oriented usage see
[commands.md](commands.md) and the [dataset & config guide](dataset-and-config-guide.md);
for known weaknesses see [code-review.md](code-review.md).

---

## 1. What the system does

It is a **pre-release gate** for an intelligent document-processing (IDP)
integration. Given a labelled dataset and a config, it:

1. sends each document through an extraction backend,
2. scores the predictions against ground truth, field by field,
3. produces metrics (JSON + HTML),
4. compares them to a previous run, and
5. exits non-zero to block publishing if the build is worse or errored.

---

## 2. Pipeline

```
config.yaml   ─▶ load_configs ─▶ TestConfig + ScorerConfig
s3://dataset/ ─▶ load_dataset ─▶ Dataset (samples ▸ documents ▸ fields)
                    │              cached locally; removed samples dropped here
                                   │
                 TestModule.run_test (batched async I/O)
                                   │  predictions: dict[sample_id → IngoreadFileResult]
                 ScorerModule.score (stickler pairing + comparison)
                                   │  MeasurementsResult
            ┌──────────────────────┼──────────────────┬────────────────────┐
   JsonFileSink (.json)  VisualizationModule    S3ResultSink       HistoricalScorer
                              (.html)     (<dataset>/<integration>/  (vs --previous)
                                   │       <date>/<time>/)
                          evaluate_release_gate ─▶ exit 0 | 1
```

The orchestration lives in [`cli.py`](../src/ingoread_test/cli.py); everything
else is a library module it calls.

---

## 3. Package layout

```
src/ingoread_test/
├── cli.py                 # Typer entry point: `run`, `bootstrap`, `suite`, `dataset`
├── config/
│   ├── loader.py          # load_configs() / load_suite()
│   ├── test_config.py     # TestConfig, DatasetConfig, ResultsConfig, StorageConfig,
│   │                      #   IntegrationConfig, HistoryConfig (the gate)
│   ├── scorer_config.py   # ScorerConfig, DocumentMeasurerConfig, FieldConfig, enums
│   └── suite_config.py    # SuiteConfig, DatasetRef (grouping datasets)
├── dataset/
│   ├── models.py          # Dataset, DocumentContainer, DocumentGT, FieldGT
│   ├── manifest.py        # DatasetManifest/SampleEntry: the persisted format (v2)
│   ├── sample_id.py       # the per-sample identifier file
│   ├── loader.py          # load_dataset()/load_manifest(): S3 or local → Dataset
│   ├── removal.py         # remove_samples() / restore_samples() on user request
│   └── bootstrap.py       # predictions → draft GT manifest (half-automatic)
├── integration/
│   ├── base.py            # Integration ABC (predict / aclose)
│   ├── factory.py         # build_integration(test_cfg) → Integration
│   ├── schemas.py         # IngoreadFileResult/Document/Field + normalization
│   ├── stub.py            # deterministic echo / file-backed integration
│   └── http.py            # live HTTP integration (create → poll → parse)
├── scoring/              # comparison, delegated to stickler
│   ├── models.py          # scorer config → stickler StructuredModel
│   ├── comparators.py     # FieldType → stickler comparator + threshold
│   ├── adapters.py        # GT / prediction → model instances (applies `selection`)
│   ├── evaluator.py       # compare_models() / score_document_pair()
│   └── pairing.py         # stickler's Hungarian GT↔prediction matching
├── modules/
│   ├── test_module.py     # run_test(): async batched prediction + TestRunStats
│   ├── scorer_module.py   # score(): aggregate pairs into MeasurementsResult
│   ├── historical_scorer.py # compare_to_previous() + evaluate_release_gate()
│   ├── suite_module.py    # run_suite() / aggregate_suite() / evaluate_suite_gate()
│   ├── dataset_module.py  # open_dataset(): TestConfig → Dataset
│   ├── logger_module.py   # Sink ABC, JsonFileSink, S3ResultSink, upload_run()
│   └── visualization_module.py # render_html() + render_suite_html()
├── results/
│   └── models.py          # DocumentPair, *Measurement, MeasurementsResult,
│                          # Comparative*, DatasetOutcome, SuiteResult
└── utils/
    └── s3.py              # S3Hub + s3:// URI helpers (datasets in, results out)
```

---

## 4. Data models

### Ground truth (input) — `dataset/models.py`
- **`FieldGT`** — `gt_value` only (native form: str/number/bool/bbox list).
  Scoring policy lives on the scorer config, not on the GT.
- **`DocumentGT`** — `doc_label`, `page`, optional `bbox`, `fields: dict[str, FieldGT]`.
- **`DocumentContainer`** — one sample at runtime: `sample_id`, `filename`,
  `file_path`, `id_file_path`, `kwargs`, `documents: list[DocumentGT]`, optional
  `group_id`. `key` (the sample id) is what predictions and results are keyed by.
- **`Dataset`** — `name`, `source_uri`, `manifest_uri`, the active
  `containers: list[DocumentContainer]`, plus `removed_sample_ids` and
  `excluded_sample_ids` for the run's report.

### The dataset as stored — `dataset/manifest.py`
- **`SampleEntry`** — one sample in the manifest: `sample_id`, `filename`,
  optional `id_file`, `kwargs`, `documents`, and the removal state
  (`removed`, `removal: RemovalInfo`).
- **`DatasetManifest`** — `version`, `name`, `samples`, with unique-id
  validation, `active_samples` / `removed_samples`, and `to_yaml()`.
  Version 1 (a bare list) is migrated on read; writing is always version 2.
  Curation (`dataset remove|restore`) works on this model, not on `Dataset`.

### Predictions (from the integration) — `integration/schemas.py`
- **`IngoreadField`** — `text`, `text_confidence`, `bbox`, `bbox_confidence`.
  A validator coerces non-string scalars (numbers, bools) to `text` strings.
- **`IngoreadDocument`** — `label`, `page`, `bbox`, `fields: dict[str, list[IngoreadField]]`.
  A validator normalizes three field shapes (bare scalar, single dict, list) to
  a list.
- **`IngoreadFileResult`** — `filename`, `status` (`IngoreadStatus`), `result:
  list[IngoreadDocument]`, `time`, `error`.

### Results (output) — `results/models.py`
- **`DocumentPair`** — a matched (or half-) pairing of one `DocumentGT` and one
  `IngoreadDocument`, with `matched`, `field_metrics`, `document_param_metrics`.
- **`FieldMeasurement` / `DocumentMeasurement`** — per-field / per-label
  aggregates (`match_rate`, averaged metrics).
- **`MeasurementsResult`** — the whole run: overall `match_rate`, timings,
  `timeouts`, `failed`, `document_results`, and `container_pairs` (full detail).
- **`ComparativeResult`** — output of the history comparison (`OK`/`DEGRADED`,
  deltas, notes).

---

## 5. Execution flow

### 5.1 Config + dataset loading
`load_configs(path)` reads one YAML/JSON file, requires top-level `test` and
`scorer` keys, and validates each into `TestConfig` / `ScorerConfig`.
`open_dataset(test_cfg)` then resolves `dataset.uri`: for an `s3://` URI the
prefix is mirrored into `dataset.cache_dir` (once — later runs reuse it unless
`--refresh`), for a local path it is read in place. `load_dataset` parses the
manifest, drops removed and excluded samples, and resolves each sample's
`file_path` and id file, failing if an id file disagrees with the manifest.

### 5.2 Running predictions — `run_test`
`run_test` ([`modules/test_module.py`](../src/ingoread_test/modules/test_module.py))
fans out over containers with an `asyncio.Semaphore(batch_size)`, wrapping each
`integration.predict()` in `asyncio.wait_for(timeout)`. It records:
- a `dict[sample_id → IngoreadFileResult]` (keying by id, not filename, so two
  samples may share a filename), and
- `TestRunStats` (wall-clock `total_time`, `total_samples` = #containers,
  `timeouts`, `failed`).

Timeouts and exceptions become `FAILED` results and increment the counters; a
backend that returns `status=FAILED` also counts as `failed`.

### 5.3 Integrations
All implement the `Integration` ABC: `async predict(container, kwargs) ->
IngoreadFileResult` and `async aclose()`.
- **Stub** — returns `stub_predictions_dir/<filename>.json` if present, else
  echoes GT (so every field matches, including bbox-shaped values). Supports
  injected `latency`, `failure_filenames`, `timeout_filenames` for tests.
- **HTTP** — `POST /integrations/{name}` (multipart) → `task_id`, then polls
  `GET /status/{task_id}` until terminal. Normalizes status strings, tolerates a
  couple of response shapes, and honors `poll_interval` / `poll_timeout`.

### 5.4 Scoring — `score`
`score` ([`modules/scorer_module.py`](../src/ingoread_test/modules/scorer_module.py))
walks each container, pairs GT and predicted documents per `doc_label`, then
aggregates. It also emits warnings for labels with no scorer config, configs
that never matched, and COMPLETED-but-empty predictions. Output is a
`MeasurementsResult`.

### 5.5 Reporting + gate
`JsonFileSink.write` and `render_html` persist the result locally, and
`upload_run` publishes it to
`<results.uri>/<dataset>/<integration>/<date>/<time>/` as `result.json` (+
`report.html`) when `results.uri` is set; an upload failure is logged and does
not change the verdict. `compare_to_previous` diffs against `--previous` (a
local path or an `s3://` URI); `evaluate_release_gate` decides the exit code
(see §7).

---

## 6. Scoring algorithm

Comparison is delegated to [stickler](https://github.com/awslabs/stickler)
(`stickler-eval`). The scorer config *is* the comparison schema: each
`doc_label` becomes a stickler `StructuredModel`, each field a `ComparableField`
carrying its comparator, threshold and weight.

### 6.1 Building the model — `scoring/models.py`, `scoring/comparators.py`
`build_document_model` turns a `DocumentMeasurerConfig` into a
`StructuredModel`; `build_comparator` maps each `FieldType` (or an explicit
`comparator:` name) to the stickler comparator that scores it, with
`threshold_for` supplying the default threshold. `scored_fields` is the single
definition of which fields count — everything not marked `ignore`.

### 6.2 Adapting the data — `scoring/adapters.py`
`gt_to_model` and `prediction_to_model` populate that model from a `DocumentGT`
and an `IngoreadDocument`. This is where `selection` is applied (`first`, `all`,
`top_n`) and where native GT values (numbers, bools, boxes) meet the
comparators.

### 6.3 Scoring a pair — `scoring/evaluator.py`
`compare_models` is one `compare_with()` call; `score_document_pair` turns its
result into a `DocumentPair`. A document is `matched` only when **every** scored
field (or any-of `field_group`) matched; `mean_score` keeps stickler's weighted
similarity so a near miss still earns partial credit.

### 6.4 Pairing — `scoring/pairing.py`
For each `doc_label`, GT and predicted documents are grouped (by page unless
`multipage_matching`), then matched within each group with stickler's
`HungarianMatcher`:
- similarity comes from `BBoxIoUComparator` when *every* document carries a
  valid 4-element page bbox — position is then the more reliable signal;
- otherwise from `StructuredModelComparator`, i.e. whole-document similarity
  field by field;
- unmatched GTs and predictions are kept as **half pairs** (one side `None`) so
  misses and hallucinations show up in the report.

### 6.5 Aggregation — `modules/scorer_module.py`
Every pair's comparison feeds `aggregate_from_comparisons`, which sums the
confusion matrix across the run and derives precision / recall / F1 / accuracy
per field, plus the error cells `fd` (wrong value), `fn` (missing) and `fa`
(invented). On top of that the harness keeps its own headline `match_rate`: the
share of documents where every scored field was right.

---

## 7. Release gate

`evaluate_release_gate(result, comparison, cfg)` returns `(blocked, reasons)`.
Publishing is allowed only when metrics are not worse **and** no document
errored. It blocks when any enabled `HistoryConfig` switch trips:

| Switch | Blocks when |
| --- | --- |
| `fail_on_error` | `result.failed` or `result.timeouts` > 0 |
| `fail_on_empty` | no documents were scored |
| `fail_on_regression` | `comparison.status == DEGRADED` (needs `--previous`) |

The CLI prints `gate_block=<reason>` lines and a final `release_gate=OK|BLOCKED`,
then exits `0` or `1`.

---

## 8. Extension points

- **New field type** — add a value to `FieldType`, write a
  `(*) -> FieldScoreResult` function, and register it in `FIELD_SCORERS`.
- **New integration** — subclass `Integration`, implement `predict` / `aclose`,
  add a branch in `cli._build_integration` and a `kind` to `IntegrationKind`.
- **New output sink** — subclass `Sink` and call it alongside `JsonFileSink`.
- **New gate condition** — add a switch to `HistoryConfig` and a clause to
  `evaluate_release_gate`.
