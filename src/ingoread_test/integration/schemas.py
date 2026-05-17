"""Response schemas returned by the ingoread integration."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class IngoreadStatus(str, Enum):
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class IngoreadField(BaseModel):
    text: str | None = None
    text_confidence: float | None = None
    bbox: list[float] | None = None
    bbox_confidence: float | None = None


class IngoreadDocument(BaseModel):
    label: str
    page: int = 0
    bbox: list[float] | None = None
    deg: float = 0.0
    fields: dict[str, list[IngoreadField]] = Field(default_factory=dict)
    monitoring: bool | None = None
    forgery: bool | None = None


class IngoreadFileResult(BaseModel):
    filename: str
    status: IngoreadStatus = IngoreadStatus.FINISHED
    result: list[IngoreadDocument] = Field(default_factory=list)
    time: float = 0.0
    error: str | None = None
