"""Load a dataset from a local folder + sidecar manifest.

Manifest format (JSON or YAML) — list of containers:

```yaml
- filename: invoice_001.pdf
  documents:
    - doc_label: invoice
      page: 0
      fields:
        total: {gt_value: "123.45"}
```
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from .models import Dataset, DocumentContainer


def load_dataset(files_root: Path, manifest_path: Path) -> Dataset:
    text = manifest_path.read_text(encoding="utf-8")
    raw = yaml.safe_load(text) if manifest_path.suffix in {".yaml", ".yml"} else json.loads(text)

    containers: list[DocumentContainer] = []
    for entry in raw:
        container = DocumentContainer.model_validate(entry)
        if container.file_path is None:
            container.file_path = files_root / container.filename
        containers.append(container)
    return Dataset(containers=containers)
