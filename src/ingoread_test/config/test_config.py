"""TestConfig — knobs for the TestModule run."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class IntegrationKind(str, Enum):
    STUB = "stub"
    HTTP = "http"


class IntegrationConfig(BaseModel):
    kind: IntegrationKind = IntegrationKind.STUB
    url: str | None = None
    auth_token: str | None = None
    poll_interval: float = 1.0
    stub_predictions_dir: Path | None = None


class HistoryConfig(BaseModel):
    match_rate_tolerance: float = 0.02
    fail_on_regression: bool = True


class TestConfig(BaseModel):
    __test__ = False  # tell pytest not to collect this as a test class

    name: str = "default"
    files_root: Path
    manifest: Path
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)
    integration_name: str = "ingoread"
    batch_size: int = 6
    timeout: float = 300.0
    kwargs: dict = Field(default_factory=dict)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
