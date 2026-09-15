"""Load combined test+scorer config from a YAML or JSON file.

File layout:

```yaml
test:
  name: smoke
  files_root: tests/data/dataset
  manifest: tests/data/dataset/manifest.yaml
  batch_size: 4
scorer:
  name: default
  measurement_configs:
    - doc_label: invoice
      fields:
        - field_name: total
          field_type: number
```
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from .scorer_config import ScorerConfig
from .suite_config import SuiteConfig
from .test_config import TestConfig


def _read_structured(path: Path) -> Any:
    """Parse a config file as YAML or JSON, chosen by its suffix."""
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else json.loads(text)


def load_configs(path: Path) -> tuple[TestConfig, ScorerConfig]:
    raw = _read_structured(path)

    if not isinstance(raw, dict):
        raise ValueError(
            f"Config {path} must be a mapping with 'test' and 'scorer' keys; "
            f"got top-level type {type(raw).__name__}"
        )

    missing = [k for k in ("test", "scorer") if k not in raw]
    if missing:
        raise ValueError(
            f"Config {path} is missing top-level key(s) {missing}. "
            f"Found keys: {sorted(raw)}. Expected layout:\n"
            "  test:\n    name: ...\n    files_root: ...\n    manifest: ...\n"
            "  scorer:\n    measurement_configs:\n      - doc_label: ...\n"
            "        fields: [...]"
        )

    test_cfg = TestConfig.model_validate(raw["test"])
    scorer_cfg = ScorerConfig.model_validate(raw["scorer"])
    return test_cfg, scorer_cfg


def load_suite(path: Path) -> SuiteConfig:
    """Load a suite config, resolving member paths relative to the suite file.

    Writing `config: vehicle_registration/config.yaml` in a suite should mean
    "next to this suite file", not "relative to wherever the CLI was invoked",
    so each member's `config`/`baseline` is anchored to the suite's directory.
    """
    raw = _read_structured(path)
    if not isinstance(raw, dict):
        raise ValueError(
            f"Suite config {path} must be a mapping with a 'datasets' key; "
            f"got top-level type {type(raw).__name__}"
        )

    suite = SuiteConfig.model_validate(raw)
    base_dir = path.parent
    for dataset in suite.datasets:
        dataset.config = (base_dir / dataset.config).resolve()
        if dataset.baseline is not None:
            dataset.baseline = (base_dir / dataset.baseline).resolve()
    return suite
