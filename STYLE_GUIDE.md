# Ingoread Test Style Guide

This guide defines the shared conventions for code, tests, configuration,
documentation, and generated artifacts in `ingoread-test`. It applies to the
entire repository unless a directory contains a more specific guide.

The words **must**, **should**, and **may** describe required, recommended, and
optional practices. Existing code may predate a rule; improve nearby code when
it is safe, but keep unrelated cleanup out of focused changes.

## 1. Core principles

- Optimize for a trustworthy release gate. Incorrect or ambiguous metrics are
  more harmful than a loud failure.
- Keep behavior deterministic. The same dataset, configuration, predictions,
  and baseline must produce the same scores and gate decision.
- Prefer explicit data contracts over guessing response or configuration
  shapes.
- Keep ground-truth data, scoring policy, integrations, aggregation, and
  presentation separate.
- Make invalid states fail early with an actionable message. Do not silently
  substitute a default for malformed input.
- Preserve backward compatibility for documented CLI options, config fields,
  manifests, and result JSON. If a breaking change is necessary, document the
  migration.

## 2. Repository structure and dependencies

Production code uses the `src` layout and belongs under
`src/ingoread_test/`:

| Area | Responsibility |
| --- | --- |
| `config/` | Configuration models and loading |
| `dataset/` | Ground-truth models, loading, and bootstrap helpers |
| `integration/` | Backend contracts and implementations |
| `scoring/` | Pairing and field/document scoring |
| `modules/` | Pipeline orchestration, aggregation, reporting, and gates |
| `results/` | Stable result models |
| `cli.py` | Typer commands and user-facing terminal output |

- Put reusable behavior in the package, not in `cli.py`.
- Keep imports flowing toward lower-level models and helpers. Scoring code must
  not depend on CLI or visualization code.
- Place tests in `tests/`, mirroring the production area when practical.
- Put runnable sample configuration in `examples/` and explanatory material in
  `docs/`.
- Add runtime and test-only dependencies to the appropriate sections of
  `pyproject.toml`. Prefer the standard library when it is equally clear.
- Avoid import-time I/O, network calls, environment validation, and other side
  effects. Construct external clients at runtime and close owned resources.

## 3. Python conventions

The project targets Python 3.12 and uses Ruff with a 100-character line limit.

### Formatting and imports

- Use four spaces for indentation, UTF-8 files, and a trailing newline.
- Format and lint before submitting:

  ```bash
  ruff format .
  ruff check .
  ```

- Start new Python modules with a short module docstring followed by
  `from __future__ import annotations`.
- Group imports as standard library, third-party packages, and local imports,
  with a blank line between groups. Do not use wildcard imports.
- Prefer one expression per line. Break long calls and collections using
  trailing commas so the formatter produces stable diffs.
- Add `# noqa` only on the smallest applicable line and include the rule code.
  The code should be correct or the exception explained; disabling a rule is
  not a substitute for either.

### Names and interfaces

- Use `snake_case` for modules, functions, variables, and config keys;
  `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants.
- Prefix private implementation details with `_`. Public names should be
  intentionally exported from the package's `__init__.py` only when they form
  part of its supported interface.
- Name async functions for their operation, not their implementation. Use the
  `a` prefix only where it disambiguates a paired lifecycle method such as
  `close()` and `aclose()`.
- Choose domain names (`document`, `prediction`, `match_rate`) instead of
  placeholders (`data`, `item`, `value`) when the domain is known.
- Boolean names should read as predicates: `blocked`, `matched`, `send_file`,
  `fail_on_error`.

### Types and data models

- Type all public functions, methods, attributes whose type is not obvious, and
  non-trivial local collections.
- Use modern syntax: `str | None`, `list[str]`, and `dict[str, float]`.
- Avoid unparameterized `dict`, `list`, and `tuple` in new code. Use a precise
  type, `TypedDict`, protocol, dataclass, or Pydantic model. Use `Any` only at an
  untyped external boundary and normalize it promptly.
- Use Pydantic models for configuration, manifests, integration payloads, and
  persisted result contracts. Use plain dataclasses or named tuples only for
  internal value objects that do not need validation or serialization.
- Use `Field(default_factory=...)` for mutable defaults.
- Keep values in their native type for as long as possible. Convert text,
  numbers, booleans, and bounding boxes only at a clearly named boundary.
- Validators should normalize or reject input, not perform network or file I/O.
- Enum members use uppercase Python names and stable lowercase serialized
  values, for example `IntegrationKind.HTTP = "http"`.

### Functions and comments

- A function should have one clear responsibility. Extract helpers when a
  block introduces its own policy, error handling, or test cases.
- Prefer guard clauses over deeply nested branches.
- Docstrings explain contracts, important invariants, and surprising behavior.
  Do not restate a function's name or type annotations.
- Comments explain why a decision exists. Delete comments that merely narrate
  the next line, and update comments in the same change as the behavior.
- Mark temporary compatibility behavior explicitly and cover it with a test.
  Do not leave undocumented fallbacks that become accidental API contracts.

## 4. Domain and architecture rules

### Dataset and configuration

- A dataset stores facts: filenames, document labels, pages, bounding boxes,
  and ground-truth values. Scoring choices such as thresholds and normalization
  belong in scorer configuration.
- Resolve relative paths at the configuration or dataset-loading boundary.
  Pass resolved `Path` objects into lower layers.
- Validate identifiers, duplicate filenames, supported strategies, coordinate
  shapes, and unknown configuration keys before prediction work begins.
- Treat manifests and config examples as public interfaces. When their schema
  changes, update their models, loaders, examples, command reference, and tests
  together.
- Never write an automatically bootstrapped manifest over an existing file
  without an explicit overwrite option.

### Integrations and async code

- Every integration implements the shared `Integration` contract and returns
  normalized `IngoreadFileResult` models.
- Keep service-specific URLs, status vocabulary, authentication, and response
  parsing inside the integration layer.
- Validate external response shapes explicitly. Unknown statuses and malformed
  bodies must produce diagnostic errors rather than plausible empty results.
- Use async I/O for backend calls. Bound concurrency and every wait with the
  configured limits; never block the event loop with synchronous sleeps.
- Close clients in `finally` blocks or async context managers. An integration
  closes only clients it owns.
- Retry only operations known to be safe. Bound attempts and backoff, and
  distinguish transport failures from a backend's terminal failure status.
- Do not log authorization headers, tokens, full request bodies containing
  sensitive document data, or ground truth unless explicitly required for a
  protected debugging workflow.

### Scoring and release gates

- A scorer returns both a match decision and its supporting metrics. Matching,
  metric aggregation, and release-gate policy remain separate operations.
- Define denominators explicitly. Names and documentation must distinguish
  files, ground-truth documents, predicted documents, paired documents, and
  scored fields.
- Unparseable values, unmatched documents, invalid boxes, infinities, and empty
  predictions require an explicit policy. They must not disappear silently
  from aggregates.
- Pairing must be deterministic, including ties and unmatched half-pairs.
- Centralize geometry and normalization helpers instead of duplicating formulas
  across pairing and scoring.
- A change to matching, aggregation, or gate behavior must include regression
  tests covering both an allowed release and a blocked release. Call out any
  expected metric shift in the change description.
- Persisted result fields are compatibility-sensitive because prior results
  serve as baselines. New readers should tolerate older supported results;
  writers should not silently change the meaning of an existing field.

### Errors, logging, and CLI output

- Raise specific exceptions at the layer that detects an invalid invariant.
  Translate them into concise user-facing errors at the CLI boundary.
- Catch only exceptions the current layer can handle. If a broad catch is
  necessary to isolate one document, preserve enough context to diagnose the
  failure and never swallow cancellation.
- Error messages should state the operation, affected file or task when safe,
  and the cause. Do not include secrets.
- Use `logging` for diagnostics and Typer output for the command's stable,
  user-facing result.
- Keep machine-consumed CLI lines stable and easy to parse, using the existing
  `key=value` pattern for gate status and artifact paths.
- Commands that write files should report the resolved path. Refuse accidental
  overwrite unless replacement is an explicit part of the command contract.
- Gate failures use a non-zero exit code; invalid input and internal failures
  must not be presented as successful gate evaluations.

## 5. Testing

Use pytest for all automated tests. The default suite is:

```bash
pytest
```

- Name files `test_<subject>.py` and tests `test_<behavior>`.
- Follow arrange-act-assert. A short docstring is useful when the scenario or
  regression is not obvious from the test name.
- Test observable behavior and stable contracts rather than private call
  sequences. Direct helper tests are appropriate for pure scoring and parsing
  logic.
- Add a regression test before or alongside every bug fix.
- Cover success, invalid input, boundary values, and failure behavior. For
  scoring, include matched, mismatched, missing-GT, and missing-prediction cases
  where applicable.
- Use `pytest.approx` for non-exact floating-point results.
- Use `tmp_path` for filesystem tests and `httpx.MockTransport` for HTTP tests.
  Unit tests must not depend on the network, real services, user credentials,
  execution order, or pre-existing files.
- Keep async tests as `async def`; pytest-asyncio is configured with automatic
  mode.
- Prefer small factories and fixtures over repeated model construction, but do
  not hide the values that make a test meaningful.
- Assert both returned values and important side effects such as written
  artifacts, exit codes, request shape, warnings, and resource cleanup.
- Run the full suite for changes to shared models, loaders, pairing,
  aggregation, result serialization, or gate policy.

## 6. YAML, JSON, and examples

- Use two spaces for YAML indentation and spaces after colons. Never use tabs.
- Use lowercase `snake_case` keys that match Pydantic field names.
- Prefer block mappings and sequences. Use inline mappings only for short,
  simple values such as a single tolerance.
- Quote strings when YAML could interpret them as booleans, numbers, dates, or
  null values. Keep native numeric and boolean ground truth native when that is
  the intended field type.
- Use `[x1, y1, x2, y2]` for bounding boxes unless a documented contract says
  otherwise.
- Example files must be runnable, use non-secret placeholder values, and have a
  corresponding automated smoke test when they exercise important behavior.
- JSON artifacts are generated through result models. Do not hand-build an
  alternate output shape in a CLI or visualization module.

## 7. Documentation

- Use sentence case for headings and concise paragraphs focused on user tasks.
- Use backticks for commands, paths, config keys, classes, and literal values.
- Add a language identifier to fenced code blocks.
- Prefer relative links within the repository and verify links after moving or
  renaming files.
- Update the closest authoritative document in the same change:
  - `README.md` for installation, the main workflow, and entry points;
  - `docs/commands.md` for commands, flags, and configuration reference;
  - `docs/dataset-and-config-guide.md` for authoring datasets;
  - `docs/architecture.md` for internal flow and extension points;
  - `docs/code-review.md` when a listed weakness changes status.
- Generated reports and screenshots are examples, not normative documentation.
  Regenerate them only when the represented output intentionally changes.
- Documentation examples must agree with current endpoint paths, defaults,
  supported field types, and CLI behavior.

## 8. Change hygiene and definition of done

- Keep each change focused. Do not mix feature work with broad formatting or
  unrelated renaming.
- Do not commit virtual environments, caches, local IDE settings, credentials,
  runtime result directories, or real customer documents.
- Preserve user-authored changes already present in the working tree.
- Review serialized models, config fields, CLI text, and output filenames as
  public compatibility surfaces.
- Before considering a change complete:

  1. Run `ruff format .` and `ruff check .` for Python changes.
  2. Run the relevant tests; run `pytest` for shared or gate-critical changes.
  3. Update tests, examples, and documentation affected by the behavior.
  4. Confirm logs and fixtures contain no tokens or sensitive document data.
  5. Verify generated artifacts are intentional and no cache files were added.
  6. Summarize user-visible behavior, compatibility impact, and verification in
     the change description.

