"""SuiteConfig — group several dataset configs behind one release decision."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class DatasetRef(BaseModel):
    """One member of a suite: a reference to an existing test+scorer config."""

    name: str
    config: Path  # path to a combined test+scorer config file
    baseline: Path | None = None  # prior result JSON to gate this dataset against
    blocking: bool = True  # if False, failures are reported but don't block the suite
    tags: list[str] = Field(default_factory=list)


class SuiteConfig(BaseModel):
    name: str = "suite"
    datasets: list[DatasetRef] = Field(default_factory=list)
    # Optional suite-level gate: block release if the macro-average match rate
    # (mean across datasets) falls below this. None disables it.
    min_macro_match_rate: float | None = None
