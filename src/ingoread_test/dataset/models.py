"""Ground-truth dataset models — what the test system sends and compares against."""

from __future__ import annotations

from pathlib import Path
from typing import Hashable

from pydantic import BaseModel, Field


class FieldGT(BaseModel):
    gt_value: str
    measurer_kwargs: dict = Field(default_factory=dict)


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
