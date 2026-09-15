"""Ground-truth dataset models — what the test system sends and compares against."""

from __future__ import annotations

import json
from collections.abc import Hashable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def gt_to_text(value: Any) -> str:
    """Read a GT value as text (for text/literal/bool/number scorers)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_boxes_str(value: str) -> list[list[float]]:
    """Parse a string GT into boxes: ``"x1,y1,x2,y2; ..."`` or a JSON array."""
    value = value.strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            data = json.loads(value)
        except ValueError:
            return []
        if data and isinstance(data[0], (list, tuple)):
            return [[float(x) for x in b] for b in data if len(b) == 4]
        return [[float(x) for x in data]] if len(data) == 4 else []
    boxes: list[list[float]] = []
    for chunk in value.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            parts = [float(x) for x in chunk.split(",")]
        except ValueError:
            continue
        if len(parts) == 4:
            boxes.append(parts)
    return boxes


def gt_to_boxes(value: Any) -> list[list[float]]:
    """Read a GT value as a list of 4-element boxes (for bbox/bbox_set scorers).

    Accepts the native forms directly — a single box ``[x1,y1,x2,y2]`` or a list
    of boxes ``[[...], [...]]`` — and still parses the legacy string forms.
    """
    if isinstance(value, str):
        return _parse_boxes_str(value)
    if isinstance(value, (list, tuple)) and value:
        if isinstance(value[0], (list, tuple)):
            return [[float(x) for x in b] for b in value if len(b) == 4]
        if len(value) == 4:
            return [[float(x) for x in value]]
    return []


class FieldGT(BaseModel):
    # The ground-truth value, in its native YAML form — str, number, bool, a bbox
    # list, or a list of bbox lists. Scorers read it via gt_to_text() /
    # gt_to_boxes(); no lossy intermediate string is built.
    #
    # Scoring policy (field_type, tolerances, iou_threshold, ...) lives on the
    # scorer-config FieldConfig, not here: the dataset holds values, the scorer
    # holds the schema.
    gt_value: Any


class DocumentGT(BaseModel):
    doc_label: str
    page: int = 0
    bbox: list[float] | None = None
    fields: dict[str, FieldGT] = Field(default_factory=dict)


class DocumentContainer(BaseModel):
    """One sample at runtime: the file to send plus the ground truth for it.

    ``sample_id`` is the sample's stable identity — it keys predictions and
    results (two samples may share a filename), it is sent to the integration
    alongside the document, and it is how a person asks for the sample to be
    removed. It comes from the sample's identifier file, falling back to the
    manifest; see :mod:`ingoread_test.dataset.sample_id`.
    """

    sample_id: str = ""
    filename: str
    file_path: Path | None = None
    id_file_path: Path | None = None
    kwargs: dict = Field(default_factory=dict)
    documents: list[DocumentGT] = Field(default_factory=list)
    group_id: Hashable | None = None

    @property
    def key(self) -> str:
        """The identity used to key predictions and results."""

        return self.sample_id or self.filename


class Dataset(BaseModel):
    """The samples a run will actually send, plus where they came from.

    Removed samples are not in ``containers`` — they never reach an integration.
    ``removed_sample_ids`` keeps their count and ids for the run's report.
    """

    name: str = ""
    source_uri: str = ""
    manifest_uri: str = ""
    containers: list[DocumentContainer] = Field(default_factory=list)
    removed_sample_ids: list[str] = Field(default_factory=list)
    excluded_sample_ids: list[str] = Field(default_factory=list)

    def __iter__(self):  # type: ignore[override]
        return iter(self.containers)

    def __len__(self) -> int:
        return len(self.containers)
