"""TestConfig — knobs for the TestModule run."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class IntegrationKind(str, Enum):
    STUB = "stub"
    HTTP = "http"
    STRING = "string"  # HTTP, but the input is in kwargs only — no file is sent


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
    # Form field carrying the sample's unique identifier (read from the sample's
    # id file) on every create-task request. Set to null to send no identifier.
    sample_id_field: str | None = "sample_id"
    stub_predictions_dir: Path | None = None


class HistoryConfig(BaseModel):
    """Pre-release gate: when does a run block publishing to production?

    The run exits non-zero (CI-blocking) if any enabled condition trips.
    """

    match_rate_tolerance: float = 0.02
    fail_on_regression: bool = True  # metrics got worse than the previous run
    fail_on_error: bool = True  # any document failed or timed out
    fail_on_empty: bool = True  # nothing was scored — can't validate a release


class StorageConfig(BaseModel):
    """How to reach S3. Defaults use the ambient AWS configuration."""

    endpoint_url: str | None = None
    region_name: str | None = None


class DatasetConfig(BaseModel):
    """Which dataset to run, and where it lives.

    ``uri`` is normally ``s3://bucket/prefix``; a local directory is accepted
    for development. The manifest is read from inside the dataset, and S3
    documents are mirrored into ``cache_dir`` so repeated runs do not
    re-download them.
    """

    uri: str
    manifest: str | None = None  # default: manifest.yaml inside the dataset
    files_root: Path | None = None  # override where documents are read from
    cache_dir: Path | None = None
    name: str | None = None  # default: the last segment of `uri`
    # Samples skipped for every run of this config, by sample id or filename.
    # A one-off skip belongs on the command line (`run --exclude`); a sample
    # that should leave the dataset belongs in `dataset remove`.
    exclude_sample_ids: list[str] = Field(default_factory=list)
    force_download: bool = False


class ResultsConfig(BaseModel):
    """Where a run's artifacts are written and uploaded.

    Uploaded runs are laid out as
    ``<uri>/<dataset name>/<integration>/<date>/<time>/`` so a dataset's history
    reads in order and two integrations never share a folder.

    ``local_dir`` is optional because the local copy is staging, not storage:
    left unset, a run renders its artifacts in a temporary directory that is
    removed once they are uploaded. Set it (or pass ``--results-dir``) to keep
    them on this machine.
    """

    local_dir: Path | None = None
    uri: str | None = None  # e.g. s3://ingoread-results/runs
    upload_html: bool = True


class TestConfig(BaseModel):
    __test__ = False  # tell pytest not to collect this as a test class

    name: str = "default"
    dataset: DatasetConfig | None = None
    # Version 1 config: a local files root and manifest path. Still supported —
    # they are folded into `dataset` below — but new configs use `dataset.uri`.
    files_root: Path | None = None
    manifest: Path | None = None
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)
    integration_name: str = "ingoread"
    batch_size: int = 6
    timeout: float = 300.0
    kwargs: dict = Field(default_factory=dict)
    history: HistoryConfig = Field(default_factory=HistoryConfig)
    results: ResultsConfig = Field(default_factory=ResultsConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @model_validator(mode="after")
    def _resolve_dataset(self) -> TestConfig:
        """Accept either the `dataset` section or the legacy pair, never neither."""

        if self.dataset is not None:
            return self
        if self.files_root is None or self.manifest is None:
            raise ValueError(
                "test config needs a 'dataset' section with a 'uri' "
                "(e.g. dataset: {uri: s3://ingoread-datasets/invoices}); "
                "the version 1 'files_root' + 'manifest' pair also still works"
            )
        self.dataset = DatasetConfig(
            uri=str(self.files_root),
            manifest=str(self.manifest),
            name=self.name,
        )
        return self
