# Guide: creating a dataset & configuring a test

A step-by-step walkthrough for building a new test from scratch: organize the
documents, write the ground-truth manifest, write the config, validate the
wiring, and (optionally) bootstrap the ground truth from the model. For the flag
reference see [commands.md](commands.md); for internals see
[architecture.md](architecture.md).

---

## Mental model

A test needs three things:

1. **A dataset in S3** — the input files (PDFs/images), each with an identifier
   file next to it.
2. **A manifest** — the **ground truth**, stored inside the dataset: for each
   sample, what should be extracted (labels, fields, values).
3. **A config** — how to run (`test`) and how to score (`scorer`).

The system sends each document to the integration, then matches the predictions
against the manifest, field by field.

> **The one rule that ties it together:** a `doc_label` must be identical in
> three places — the manifest (`documents[].doc_label`), the scorer config
> (`measurement_configs[].doc_label`), and what the integration predicts
> (`IngoreadDocument.label`). If they don't match, that document is silently not
> scored (you'll get a `WARNING` about unconfigured/unmatched labels).

---

## Step 1 — Lay out the dataset

A dataset is one S3 prefix holding the manifest, the documents, and one
identifier file per sample:

```
s3://ingoread-datasets/invoices/
├── manifest.yaml
├── invoice_001.pdf
├── invoice_001.id      # contains: inv-0001
├── invoice_002.pdf
└── invoice_002.id      # contains: inv-0002
```

Build it locally in exactly that shape and publish it once the manifest exists
(Step 2). The upload validates the dataset first — manifest, unique ids, and
every id file — so a broken dataset never reaches S3:

```bash
ingoread-test dataset upload datasets/invoices s3://ingoread-datasets/invoices
```

**The identifier file** is a UTF-8 text file whose entire content is the
sample's unique id — nothing else. It is what the harness POSTs alongside the
document, so a backend can tie its own records to the exact sample. The
convention is `<document stem>.id`; a sample can point elsewhere with `id_file`.
Any short unique token works (a UUID, a scan number, a case reference):

```bash
# One id file per document, named after it
for f in datasets/invoices/*.pdf; do
  uuidgen | tr 'A-Z' 'a-z' > "${f%.pdf}.id"
done
```

A local directory works everywhere an `s3://` URI does, which is handy while
you are still building the dataset. For the **stub** integration you don't need
real documents at all (it echoes ground truth); for the **http** integration
they must exist, and they are downloaded from S3 into `dataset.cache_dir` on
the first run.

---

## Step 2 — Write the manifest (ground truth)

The manifest names the dataset and lists its samples. Each sample has a unique
`sample_id` — the same value as in its id file — the file it refers to, and the
documents inside it with their expected field values.

`datasets/invoices/manifest.yaml`:

```yaml
version: 2
name: invoices
samples:
  - sample_id: inv-0001           # must equal the content of invoice_001.id
    filename: invoice_001.pdf
    documents:
      - doc_label: invoice        # must match scorer + prediction label
        page: 0
        fields:
          total:  {gt_value: "123.45"}
          vendor: {gt_value: "Acme"}
          paid:   {gt_value: "true"}

  - sample_id: inv-0002
    filename: invoice_002.pdf
    documents:
      - doc_label: invoice
        page: 0
        fields:
          total:  {gt_value: "10.00"}
          vendor: {gt_value: "Beta"}
          paid:   {gt_value: "false"}
```

Ids must be unique inside the dataset, and loading fails if a sample's id file
disagrees with its manifest entry — better a loud failure than a run that
reports results under the wrong identifier. If you have an older manifest (a
bare list of containers with no ids), it still loads: each filename stem becomes
the `sample_id`.

### Field value rules

`gt_value` is stored in its **native YAML form** — number, bool, or bbox list —
and read by the scorer for that field type (no lossy string conversion). The
legacy string forms still work, so old manifests keep running.

| Field kind | Write `gt_value` as | Example |
| --- | --- | --- |
| text / literal | the exact string | `"Acme"` |
| number | a number (or numeric string) | `123.45` |
| bool | a bool (or `true/false/1/0/yes/no/y/да`) | `true` |
| bbox | a 4-number list (or `"x1,y1,x2,y2"`) | `[900.0, 820.0, 1180.0, 990.0]` |
| bbox_set | a list of boxes (or `;`-separated string) | `[[348.0,2413.0,798.0,2698.0], [1877.0,2427.0,2200.0,2671.0]]` |

### Optional per-document / per-file keys

```yaml
- filename: vrc_001.pdf
  kwargs: {language: ru}            # per-file request kwargs (merged with test.kwargs)
  documents:
    - doc_label: vehicle_registration
      page: 0
      bbox: [120, 80, 1480, 1020]   # document location; used when >1 doc per file
      fields:
        signature_box: {gt_value: [900, 820, 1180, 990]}
```

- **`bbox`** (document-level) helps the Hungarian matcher pair documents when a
  file contains several of the same label.
- The dataset holds only **values** (`gt_value`). Scoring knobs
  (`field_type`, `measurer_kwargs`, tolerances, thresholds) live on the scorer
  config — see Step 3.

---

## Step 3 — Write the config

One file with `test` (how to run) and `scorer` (how to score).

`datasets/invoices/config.yaml`:

```yaml
test:
  name: invoices
  dataset:
    uri: s3://ingoread-datasets/invoices   # a local path works the same way
  results:
    uri: s3://ingoread-results/runs        # omit for local-only runs
    # local_dir: results                   # keep the run's files here; without it
    #                                      # they are staged and removed after upload
  batch_size: 4
  timeout: 60
  integration:
    kind: stub          # start with stub to validate wiring
  history:
    fail_on_regression: true
    fail_on_error: true
    fail_on_empty: true

scorer:
  name: invoices
  measurement_configs:
    - doc_label: invoice
      fields:
        - field_name: total
          field_type: number
          measurer_kwargs: {abs_tol: 0.01}   # tolerate cent rounding
        - field_name: vendor
          field_type: text
          measurer_kwargs: {strip: true, casefold: true}
        - field_name: paid
          field_type: bool
```

### Choosing a `field_type`

| Goal | Use | Notes |
| --- | --- | --- |
| Free text / OCR string | `text` | reports CER/WER; add `strip`/`casefold` to relax |
| Code/enum, exact only | `literal` | A/B/C categories, years |
| Numeric value | `number` | add `abs_tol`/`rel_tol` for tolerance |
| Yes/no flag | `bool` | recognizes `true/1/yes/y/да` |
| Region on the page | `bbox` | IoU ≥ `iou_threshold` (default 0.5) |
| Several regions (e.g. stamps) | `bbox_set` | checks presence + count + IoU together |

### Multiple boxes in one field — stamps, signatures, photos

When the recognizer returns a field holding a *list* of boxes, e.g.

```json
"stamps": [
  {"bbox": [348, 2413, 798, 2698],  "bbox_confidence": 0.85},
  {"bbox": [1877, 2427, 2200, 2671], "bbox_confidence": 0.77}
]
```

use `field_type: bbox_set`. Write the GT as a **semicolon-separated** list of
boxes (order doesn't matter — predictions are matched to GT by IoU):

```yaml
# manifest — gt_value as a list of boxes (or the "x1,y1,x2,y2; ..." string form)
fields:
  stamps: {gt_value: [[348, 2413, 798, 2698], [1877, 2427, 2200, 2671]]}
```

```yaml
# scorer config — field_type and the IoU threshold live here, not in the dataset
- {field_name: stamps, field_type: bbox_set, measurer_kwargs: {iou_threshold: 0.5}}
```

It reports `count_gt`, `count_pred`, `count_match`, `precision`, `recall`, and
mean `iou`; the field **matches** only when every expected box is found at the
threshold *and* the count is exact (so a missing or extra stamp fails it).

### Useful field options

- `ignore: true` — keep a field documented but exclude it from scoring/reporting
  (e.g. a field the model can't do yet).
- `field_group: <name>` — several fields with the same group count as a single
  "any-of" match (useful when the same value can appear under different names).

---

## Step 4 — Validate the wiring with the stub

The stub echoes ground truth, so a correctly wired test scores **1.000**. This
is the fastest way to catch label/field typos before involving a real backend.

```bash
ingoread-test run datasets/invoices/config.yaml
```

Expect:

```
scored_documents=1 overall_match_rate=1.000 total_samples=2 timeouts=0 failed=0
label=invoice n=2 match_rate=1.000
...
release_gate=OK
```

If you see `overall_match_rate=0.000` or a `WARNING: no documents were scored`,
it's almost always a **label mismatch** (Step 2's rule) or a field-type/value
format mismatch (e.g. a bbox written without 4 numbers).

---

## Step 5 — Point at the real backend

Switch the integration to `http` and provide the endpoint:

```yaml
test:
  # ...
  integration_name: ingoread
  integration:
    kind: http
    url: https://ingoread.example.com
    auth_token: "<your token>"      # literal string — no ${ENV} expansion; template it in CI
    poll_interval: 1.0
    poll_timeout: 600               # fail a stuck task instead of hanging
    # data_field_name: mapping_string   # set if kwargs must be one JSON blob
```

Now the dataset must hold the real documents; they are downloaded from
`dataset.uri` into the local cache on the first run. Each create-task POST
carries the document, the per-sample `kwargs`, and the sample's unique id in its
own form field — rename that field with `integration.sample_id_field`, or set it
to `null` if the backend has no use for it. Run the same way; the match rate now
reflects the model's real accuracy.

---

## Step 5b — Remove a sample on request

When someone asks for a document to be taken out of the dataset — a customer
deletion request, a mislabelled scan, a duplicate — remove it by id (or by
filename):

```bash
ingoread-test dataset remove s3://ingoread-datasets/invoices inv-0042 \
  --reason "customer request"
```

That is a soft delete: the entry stays in the manifest with a reason, a
timestamp, and who ran the command, and every run skips it. `dataset restore`
undoes it, `dataset list` shows what is currently removed, and `--purge` drops
the entry for good. For a skip that should not change the dataset, use
`run --exclude inv-0042` instead.

The command rewrites the manifest and nothing else — the sample's document in
S3 is deleted by another system. Taking the entry out of the manifest is what
stops the sample being sent.

Curation only reads and rewrites the manifest object, so it is quick even for a
large dataset — but a rewrite is a machine rewrite, and comments you wrote by
hand in the manifest do not survive it.

---

## Step 6 (optional) — Bootstrap ground truth from the model

Hand-labelling every field is slow. Instead, run once and let the model fill in
a draft you then correct — the "half-automatic" workflow.

```bash
# 1. Run against the real backend to capture predictions
ingoread-test run datasets/invoices/config.yaml --results-dir results

# 2. Turn those predictions into a draft manifest
ingoread-test bootstrap results/<timestamp>__invoices.json \
  --out datasets/invoices/manifest.draft.yaml

# 3. Review & correct every gt_value, then rename to manifest.yaml
```

The draft uses the first predicted value per field; **nothing is verified**, so
treat it strictly as a starting point.

---

## Step 7 — Gate future builds

Once you trust a run, keep it as the baseline and gate the next build against it:

```bash
ingoread-test run config.yaml --previous results/<good-baseline>.json
echo $?    # 0 = OK to publish, 1 = blocked (see gate_block= lines)
```

The run is blocked if metrics regressed beyond `match_rate_tolerance`, any
document errored/timed out, or nothing was scored — per the `test.history`
switches.

---

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `WARNING: no documents were scored` | `doc_label` differs between manifest, scorer, and prediction |
| Stub run isn't `1.000` | field value format (number/bbox/bool) or a label typo |
| `match_rate` fine but a field always misses | wrong `field_type`, or needs `strip`/`casefold`/`abs_tol` |
| HTTP run all `failed` | wrong `url`/`integration_name`, missing files, or unrecognized status strings |
| Run hangs on HTTP | set `poll_timeout`; the task is stuck `in_progress` |
| Bbox field never matches | `gt_value` must be `[x1, y1, x2, y2]` (the `"x1,y1,x2,y2"` string still parses); prediction must carry a 4-element bbox |
| `Sample id mismatch for ...` | the `<stem>.id` file and the manifest's `sample_id` disagree — fix whichever is wrong |
| `Duplicate sample_id(s)` | two samples share an id; ids must be unique within a dataset |
| Fewer samples ran than expected | some are removed (`dataset list --removed-only`) or excluded (`exclude_sample_ids`, `--exclude`) |
| Dataset changes in S3 don't show up | the local cache is being reused — pass `--refresh` |
| Whole document scores 0 | one required field fails — a document matches only if **all** fields match (use `ignore`/`field_group` to relax) |
