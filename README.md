# ingoread-test

Runs a ground-truth dataset through an ingoread integration and scores what
comes back.

```bash
python -m ingoread_test.cli run config.yaml --results-dir results
```

The run writes a JSON result and an HTML report, compares against a previous
run when given `--previous`, and exits non-zero when the release gate blocks.

## Scoring

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

### Config

```yaml
scorer:
  name: default
  measurement_configs:
    - doc_label: invoice
      match_threshold: 0.7          # similarity at which a gt/prediction pair is the same document
      multipage_matching: true      # pair across pages instead of within a page
      fields:
        - field_name: total
          field_type: number
          weight: 2.0               # counts double in the document score
          measurer_kwargs: {abs_tol: 0.01}
        - field_name: seller
          field_type: text
          measurer_kwargs: {strip: true, casefold: true}
        - field_name: buyer
          field_type: fuzzy_text
          threshold: 0.85           # matches at 85% similarity or better
        - field_name: signatures
          field_type: bbox_set
          measurer_kwargs: {iou_threshold: 0.5}
        - field_name: inn
          field_type: text
          field_group: id           # a group matches when any of its members does
        - field_name: note
          field_type: text
          ignore: true              # documented, never scored
```

Per-field knobs: `weight`, `threshold`, `clip_under_threshold` (zero out
sub-threshold similarity — on by default), `comparator`, `comparator_kwargs`,
`field_group`, `ignore`, and `selection`.

`selection` decides what to do when the API returns several candidates for one
field: `first` (default) scores one value; `all` and `top_n` (with `top_n: N`)
turn the field into a set that stickler matches element by element, so order
doesn't matter and extra or missing values surface as `fa` / `fn`.

## Tests

```bash
pytest
```
