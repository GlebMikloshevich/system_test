"""ScorerConfig, DocumentMeasurerConfig, FieldConfig + enums."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class FieldType(str, Enum):
    NUMBER = "number"
    TEXT = "text"
    BOOL = "bool"
    LLM_TEXT = "llm_text"
    BBOX = "bbox"
    LITERAL = "literal"


class PredictionSelection(str, Enum):
    """How to handle multiple predicted values for one field.

    v1 implements FIRST only. TOP_N and ALL are placeholders for future
    aggregation strategies (best-of-N, mean-over-N, etc.).
    """

    FIRST = "first"
    TOP_N = "top_n"
    ALL = "all"


class FieldConfig(BaseModel):
    field_name: str
    field_type: FieldType
    measurer_kwargs: dict = Field(default_factory=dict)
    selection: PredictionSelection = PredictionSelection.FIRST
    top_n: int | None = None
    field_group: str | None = None
    take_first: bool | None = None

    @model_validator(mode="after")
    def _coerce_take_first(self) -> "FieldConfig":
        if self.take_first is None:
            return self
        # Legacy alias: take_first=True == FIRST; False == ALL.
        self.selection = PredictionSelection.FIRST if self.take_first else PredictionSelection.ALL
        return self


class DocumentMeasurerConfig(BaseModel):
    doc_label: str
    fields: list[FieldConfig] = Field(default_factory=list)
    multipage_matching: bool = True


class ScorerConfig(BaseModel):
    name: str = "default"
    measurement_configs: list[DocumentMeasurerConfig] = Field(default_factory=list)
