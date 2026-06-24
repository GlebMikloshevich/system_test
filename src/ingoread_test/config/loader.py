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

import yaml

from .scorer_config import ScorerConfig
from .test_config import TestConfig


def load_configs(path: Path) -> tuple[TestConfig, ScorerConfig]:
    text = path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else json.loads(text)

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
