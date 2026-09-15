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
    # Stickler's raw compare_with() output, kept for the aggregation step only.
    # Excluded from serialization: it restates field_metrics in full, and
    # persisting it would multiply the size of every result file.
    comparison: dict | None = Field(default=None, exclude=True, repr=False)


class DocumentContainerPair(BaseModel):
    """One sample's ground truth paired with what the backend returned.

    ``sample_id`` is the sample's unique identity; ``filename`` is kept because
    reports and older result JSON are read by it.
    """

    sample_id: str = ""
    filename: str
    gts: DocumentContainer
    predictions: IngoreadFileResult
    document_pairs: list[DocumentPair] = Field(default_factory=list)


class FieldMeasurement(BaseModel):
    field_name: str
    field_type: FieldType
    match_rate: float
    # Mean stickler similarity over the pairs this field was scored in.
    mean_score: float = 0.0
    field_metrics: dict = Field(default_factory=dict)


class DocumentMeasurement(BaseModel):
    label: str
    total_samples: int
    time: float = 0.0
    time_per_sample: float = 0.0
    match_rate: float = 0.0
    # Weighted stickler similarity across this document type — partial credit,
    # where match_rate is the all-or-nothing view.
    mean_score: float = 0.0
    field_results: list[FieldMeasurement] = Field(default_factory=list)


class MeasurementsResult(BaseModel):
    test_config_name: str
    scorer_config_name: str
    start_date: datetime
    total_time: float
    total_samples: int
    time_per_sample: float
    match_rate: float
    mean_score: float = 0.0
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


class DatasetOutcome(BaseModel):
    """One suite member: how its own run + gate turned out.

    `result` is None only when the dataset failed to run at all (see `error`);
    `blocking` records whether this member's verdict can block the release.
    """

    name: str
    blocking: bool = True
    result: MeasurementsResult | None = None
    comparison: ComparativeResult | None = None
    blocked: bool = False
    reasons: list[str] = Field(default_factory=list)
    error: str | None = None
    json_path: str | None = None
    html_path: str | None = None


class SuiteResult(BaseModel):
    """Several datasets rolled up into one release decision.

    Two headline rates, because they answer different questions:
    `macro_match_rate` weights every dataset equally (so a small dataset's
    regression can't hide behind a large one), `micro_match_rate` pools every
    document (so it reflects the overall document population).
    """

    suite_name: str
    start_date: datetime
    datasets: list[DatasetOutcome] = Field(default_factory=list)
    macro_match_rate: float = 0.0
    micro_match_rate: float = 0.0
    total_samples: int = 0
    n_passed: int = 0
    n_blocked: int = 0
