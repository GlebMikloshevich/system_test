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
    # Max seconds to keep polling a single task before giving up (None = no
    # client-side limit; the run is still bounded by TestConfig.timeout).
    poll_timeout: float | None = None
    # How to serialize per-document kwargs into the multipart request:
    #   - None (default): SPREAD mode — each kwarg becomes its own form field
    #     (e.g. kwargs={"language": "ru", "checks": [...]} ->
    #      data={"language": "ru", "checks": "[\"...\"]"}).
    #   - str: SINGLE-BLOB mode — all kwargs are JSON-encoded together under
    #     this field name (e.g. data_field_name="mapping_string" ->
    #      data={"mapping_string": "{\"language\":\"ru\", ...}"}).
    data_field_name: str | None = None
    stub_predictions_dir: Path | None = None


class HistoryConfig(BaseModel):
    """Pre-release gate: when does a run block publishing to production?

    The run exits non-zero (CI-blocking) if any enabled condition trips.
    """

    match_rate_tolerance: float = 0.02
    fail_on_regression: bool = True  # metrics got worse than the previous run
    fail_on_error: bool = True  # any document failed or timed out
    fail_on_empty: bool = True  # nothing was scored — can't validate a release


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
