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
    filename: str
    file_path: Path | None = None
    kwargs: dict = Field(default_factory=dict)
    documents: list[DocumentGT] = Field(default_factory=list)
    group_id: Hashable | None = None


class Dataset(BaseModel):
    containers: list[DocumentContainer] = Field(default_factory=list)

    def __iter__(self):  # type: ignore[override]
        return iter(self.containers)

    def __len__(self) -> int:
        return len(self.containers)
