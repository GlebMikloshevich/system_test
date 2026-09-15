"""The dataset manifest — the persisted dataset format.

Version 2 replaces the bare list of containers with a mapping that names the
dataset, states its format version, and gives every sample a unique
``sample_id``:

```yaml
version: 2
name: vehicle_registration
samples:
  - sample_id: vr-0001
    filename: vr_0001.pdf
    id_file: vr_0001.id        # optional; defaults to "<stem>.id"
    kwargs: {}
    documents:
      - doc_label: vehicle_registration
        page: 0
        fields:
          vin: {gt_value: XTA210740D0918697}
  - sample_id: vr-0002
    filename: vr_0002.pdf
    removed: true              # kept for the record, never sent to a backend
    removal:
      reason: customer asked for deletion
      removed_at: 2026-09-15T08:12:44Z
      removed_by: gleb
    documents: []
```

The identifier is the sample's stable name: it keys predictions and results (so
two samples may share a filename), it is sent to the integration with the
document, and it is what `ingoread-test dataset remove` accepts.

Version 1 — a top-level list of containers, no ids — still loads unchanged: each
entry's filename stem becomes its ``sample_id``. Writing always emits version 2.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from .models import DocumentContainer, DocumentGT

MANIFEST_VERSION = 2
SUPPORTED_VERSIONS = (1, 2)


class RemovalInfo(BaseModel):
    """Why a sample left the dataset, kept so the manifest stays an audit trail."""

    reason: str | None = None
    removed_at: datetime | None = None
    removed_by: str | None = None


class SampleEntry(BaseModel):
    """One sample: a file, its ground truth, and its removal state."""

    sample_id: str
    filename: str
    # Where the identifier file lives, relative to the dataset's files root.
    # None means the convention: the document's name with a ".id" suffix.
    id_file: str | None = None
    kwargs: dict[str, Any] = Field(default_factory=dict)
    group_id: str | None = None
    removed: bool = False
    removal: RemovalInfo | None = None
    documents: list[DocumentGT] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_identity(self) -> SampleEntry:
        if not self.sample_id.strip():
            raise ValueError(f"sample_id must not be empty (filename={self.filename!r})")
        if not self.filename.strip():
            raise ValueError(f"filename must not be empty (sample_id={self.sample_id!r})")
        if self.removed and self.removal is None:
            self.removal = RemovalInfo()
        return self

    def to_container(self, files_root: Path) -> DocumentContainer:
        """Build the runtime container the pipeline sends to an integration."""

        return DocumentContainer(
            sample_id=self.sample_id,
            filename=self.filename,
            file_path=files_root / self.filename,
            kwargs=dict(self.kwargs),
            documents=list(self.documents),
            group_id=self.group_id,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialize back to manifest form, omitting defaults that add no meaning."""

        entry: dict[str, Any] = {"sample_id": self.sample_id, "filename": self.filename}
        if self.id_file:
            entry["id_file"] = self.id_file
        if self.group_id:
            entry["group_id"] = self.group_id
        if self.kwargs:
            entry["kwargs"] = self.kwargs
        if self.removed:
            entry["removed"] = True
            entry["removal"] = (
                self.removal.model_dump(mode="json", exclude_none=True) if (self.removal) else {}
            )
        entry["documents"] = [
            document.model_dump(mode="json", exclude_none=True) for document in self.documents
        ]
        return _compact_numbers(entry)


class DatasetManifest(BaseModel):
    """The whole dataset as stored — including samples that were removed."""

    version: int = MANIFEST_VERSION
    name: str = ""
    samples: list[SampleEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> DatasetManifest:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for sample in self.samples:
            if sample.sample_id in seen:
                duplicates.add(sample.sample_id)
            seen.add(sample.sample_id)
        if duplicates:
            raise ValueError(
                f"Duplicate sample_id(s) in dataset {self.name or '<unnamed>'}: "
                f"{sorted(duplicates)}"
            )
        return self

    @property
    def active_samples(self) -> list[SampleEntry]:
        return [sample for sample in self.samples if not sample.removed]

    @property
    def removed_samples(self) -> list[SampleEntry]:
        return [sample for sample in self.samples if sample.removed]

    def find(self, selector: str) -> list[SampleEntry]:
        """Samples addressed by ``selector`` — a sample id or a filename."""

        by_id = [sample for sample in self.samples if sample.sample_id == selector]
        if by_id:
            return by_id
        return [sample for sample in self.samples if sample.filename == selector]

    @classmethod
    def parse(cls, raw: Any, *, source: str, name: str = "") -> DatasetManifest:
        """Build a manifest from parsed YAML/JSON, accepting version 1 and 2."""

        if isinstance(raw, list):
            return cls(
                version=MANIFEST_VERSION,
                name=name,
                samples=[_legacy_entry(entry, source, index) for index, entry in enumerate(raw)],
            )
        if not isinstance(raw, dict):
            raise ValueError(
                f"Manifest {source} must be a mapping with a 'samples' key "
                f"(or a version 1 list of containers); got {type(raw).__name__}"
            )

        version = raw.get("version", MANIFEST_VERSION)
        if version not in SUPPORTED_VERSIONS:
            raise ValueError(
                f"Manifest {source} has unsupported version {version!r}; "
                f"this build understands {list(SUPPORTED_VERSIONS)}"
            )
        if "samples" not in raw:
            raise ValueError(
                f"Manifest {source} is missing the 'samples' key. Found keys: {sorted(raw)}"
            )

        manifest = cls.model_validate({**raw, "version": MANIFEST_VERSION})
        if not manifest.name:
            manifest.name = name
        return manifest

    def to_yaml(self) -> str:
        """Render the manifest for storage.

        Rewriting is how curation persists a change, so the output aims to stay
        readable and diffable: keys in declaration order, coordinates inline as
        ``[x1, y1, x2, y2]``, and whole numbers without a trailing ``.0``.
        Comments in a hand-written manifest do not survive a rewrite.
        """

        payload = {
            "version": MANIFEST_VERSION,
            "name": self.name,
            "samples": [sample.to_mapping() for sample in self.samples],
        }
        return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=None)


def _compact_numbers(value: Any) -> Any:
    """Write 120 instead of 120.0 — pydantic widens coordinates to float."""

    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, dict):
        return {key: _compact_numbers(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_compact_numbers(item) for item in value]
    return value


def _legacy_entry(entry: Any, source: str, index: int) -> SampleEntry:
    """Read a version 1 container, giving it the id the format now requires."""

    if not isinstance(entry, dict):
        raise ValueError(
            f"Manifest {source}: entry #{index} must be a mapping, got {type(entry).__name__}"
        )
    filename = entry.get("filename")
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError(f"Manifest {source}: entry #{index} has no 'filename'")
    return SampleEntry.model_validate(
        {**entry, "sample_id": entry.get("sample_id") or Path(filename).stem}
    )


def parse_manifest_text(text: str, *, source: str, name: str = "") -> DatasetManifest:
    """Parse manifest text; JSON is read for ``.json`` sources, YAML otherwise."""

    raw = json.loads(text) if source.endswith(".json") else yaml.safe_load(text)
    if raw is None:
        raise ValueError(f"Manifest {source} is empty")
    return DatasetManifest.parse(raw, source=source, name=name)
