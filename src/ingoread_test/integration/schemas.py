"""Response schemas returned by the ingoread integration."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class IngoreadStatus(str, Enum):
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    FAILED = "FAILED"
    COMPLETED = "COMPLETED"


class IngoreadField(BaseModel):
    text: str | None = None
    text_confidence: float | None = None
    bbox: list[float] | None = None
    bbox_confidence: float | None = None

    @field_validator("text", mode="before")
    @classmethod
    def _coerce_text_to_str(cls, value: Any) -> Any:
        """The ingoread API returns numbers/bools as raw scalars for some fields
        (engine_power=194.4, monitoring=False). Downstream scorers expect str."""
        if value is None or isinstance(value, str):
            return value
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)


class IngoreadDocument(BaseModel):
    label: str
    page: int = 0
    bbox: list[float] | None = None
    deg: float = 0.0
    fields: dict[str, list[IngoreadField]] = Field(default_factory=dict)
    monitoring: bool | None = None
    forgery: bool | None = None

    @field_validator("fields", mode="before")
    @classmethod
    def _normalize_fields(cls, value: Any) -> Any:
        """The API returns three different shapes per field entry:

            field1: "answer"                   # bare scalar -> wrap as IngoreadField.text
            field2: {"text": "true"}           # single IngoreadField dict -> wrap as 1-list
            field3: [{"text": "x"}, {...}]     # already canonical

        Normalize them all to list[IngoreadField]-compatible dicts so
        downstream scorers and field selectors only see one shape.
        """
        if not isinstance(value, dict):
            return value
        normalized: dict[str, Any] = {}
        for name, entry in value.items():
            if isinstance(entry, list):
                normalized[name] = entry
            elif isinstance(entry, dict):
                normalized[name] = [entry]
            else:
                # Bare scalar — wrap as a single IngoreadField with `text`.
                normalized[name] = [{"text": entry}]
        return normalized


class IngoreadFileResult(BaseModel):
    filename: str
    status: IngoreadStatus = IngoreadStatus.COMPLETED
    result: list[IngoreadDocument] = Field(default_factory=list)
    time: float = 0.0
    error: str | None = None
