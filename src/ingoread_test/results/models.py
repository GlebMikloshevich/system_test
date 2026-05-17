"""Outputs: pairings + measurements + historical comparison."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from ..config.scorer_config import FieldType
from ..dataset.models import DocumentContainer, DocumentGT
from ..integration.schemas import IngoreadDocument, IngoreadFileResult


class DocumentPair(BaseModel):
    gt: DocumentGT | None = None
    prediction: IngoreadDocument | None = None
    matched: bool = False
    field_metrics: dict[str, dict] = Field(default_factory=dict)
    document_param_metrics: dict = Field(default_factory=dict)


class DocumentContainerPair(BaseModel):
    filename: str
    gts: DocumentContainer
    predictions: IngoreadFileResult
    document_pairs: list[DocumentPair] = Field(default_factory=list)


class FieldMeasurement(BaseModel):
    field_name: str
    field_type: FieldType
    match_rate: float
    field_metrics: dict = Field(default_factory=dict)


class DocumentMeasurement(BaseModel):
    label: str
    total_samples: int
    time: float = 0.0
    time_per_sample: float = 0.0
    match_rate: float = 0.0
    field_results: list[FieldMeasurement] = Field(default_factory=list)


class MeasurementsResult(BaseModel):
    test_config_name: str
    scorer_config_name: str
    start_date: datetime
    total_time: float
    total_samples: int
    time_per_sample: float
    match_rate: float
    timeouts: int = 0
    failed: int = 0
    document_results: list[DocumentMeasurement] = Field(default_factory=list)
    container_pairs: list[DocumentContainerPair] = Field(default_factory=list)


class ComparativeStatus(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"


class ComparativeResult(BaseModel):
    status: ComparativeStatus
    overall_delta: float
    per_label_delta: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
