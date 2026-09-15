"""LoggerModule — pluggable persistence for MeasurementsResult.

Results belong in S3. The local sink writes the same artifacts to a working
directory first, because the JSON and the HTML report have to exist as files
before they can be uploaded — but that copy is scratch space, and
:class:`RunArtifacts` discards it once the run is safely published.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from ..config.test_config import TestConfig
from ..results.models import MeasurementsResult
from ..utils.s3 import S3Hub, build_s3_uri, is_s3_uri, open_s3_uri, parse_s3_uri

logger = logging.getLogger(__name__)

RESULT_OBJECT_NAME = "result.json"
REPORT_OBJECT_NAME = "report.html"


class Sink(ABC):
    @abstractmethod
    def write(self, result: MeasurementsResult) -> Path | str | None: ...


class JsonFileSink(Sink):
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def write(self, result: MeasurementsResult) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = result.start_date.strftime("%Y%m%dT%H%M%S")
        out = self.directory / f"{stamp}__{result.test_config_name}.json"
        out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return out


class RunArtifacts:
    """The local working directory for one run's artifacts.

    With no directory asked for, the run stages its files in a temporary
    directory and :meth:`discard` removes them once they are in S3. Pass a
    directory (``--results-dir`` / ``results.local_dir``) to keep them instead.

    A copy is never discarded unless it was uploaded: a run that could not be
    published — no ``results.uri``, ``--no-upload``, or a failed upload — keeps
    its local files rather than becoming no copy at all.
    """

    def __init__(self, local_dir: Path | str | None) -> None:
        self.keep = local_dir is not None
        self.path = (
            Path(local_dir)
            if local_dir is not None
            else Path(tempfile.mkdtemp(prefix="ingoread-run-"))
        )
        self.path.mkdir(parents=True, exist_ok=True)

    def discard(self, *, uploaded: bool) -> Path | None:
        """Remove the staged copy when it is both temporary and published.

        Returns the directory that was removed, or None if it was kept.
        """

        if self.keep or not uploaded:
            return None
        shutil.rmtree(self.path, ignore_errors=True)
        logger.info("Removed the temporary local copy at %s", self.path)
        return self.path


def upload_run(
    test_cfg: TestConfig,
    dataset_name: str,
    result: MeasurementsResult,
    *,
    html_path: Path | None = None,
    hub: S3Hub | None = None,
) -> str | None:
    """Publish a run to S3 if the config asks for it; return the folder URI.

    Returns None when `results.uri` is unset, which is how a purely local run is
    expressed — not an error.
    """

    cfg = test_cfg.results
    if not cfg.uri:
        return None

    sink = S3ResultSink(
        cfg.uri,
        dataset_name,
        test_cfg.integration_name,
        hub=hub,
        endpoint_url=test_cfg.storage.endpoint_url,
        region_name=test_cfg.storage.region_name,
    )
    artifacts = [html_path] if html_path and cfg.upload_html else []
    return sink.write(result, artifacts)


def read_result(uri: str | Path, *, hub: S3Hub | None = None) -> MeasurementsResult:
    """Read a stored result from a local path or an ``s3://`` URI.

    A run's baseline is usually the S3 result of an earlier run, so
    ``--previous`` accepts both without the caller checking the scheme.
    """

    if not is_s3_uri(str(uri)):
        return MeasurementsResult.model_validate_json(Path(uri).read_text(encoding="utf-8"))

    key = parse_s3_uri(str(uri))[1]
    hub = hub or open_s3_uri(str(uri))[0]
    return MeasurementsResult.model_validate_json(hub.read_text(key))


def run_folder_key(
    base_prefix: str, dataset_name: str, integration_name: str, start_date: datetime
) -> str:
    """The S3 key prefix for one run: ``<base>/<dataset>/<integration>/<date>/<time>``.

    Dataset first, then integration, then the run's UTC date and time: a
    dataset's runs sort chronologically under one prefix, and two integrations
    scored against the same dataset never write into the same folder.
    """

    moment = start_date.astimezone(UTC) if start_date.tzinfo else start_date.replace(tzinfo=UTC)
    parts = [
        base_prefix.strip("/"),
        _slug(dataset_name),
        _slug(integration_name),
        moment.strftime("%Y-%m-%d"),
        moment.strftime("%H%M%S"),
    ]
    return "/".join(part for part in parts if part)


class S3ResultSink(Sink):
    """Upload a run's artifacts into its own S3 folder.

    The result JSON is always named ``result.json`` and the report
    ``report.html``, so a baseline can be referenced by a predictable URI:
    ``s3://bucket/runs/<dataset>/<integration>/<date>/<time>/result.json``.
    """

    def __init__(
        self,
        uri: str,
        dataset_name: str,
        integration_name: str,
        *,
        hub: S3Hub | None = None,
        endpoint_url: str | None = None,
        region_name: str | None = None,
    ) -> None:
        if hub is None:
            hub, base_prefix = open_s3_uri(uri, endpoint_url=endpoint_url, region_name=region_name)
        else:
            _, base_prefix = parse_s3_uri(uri)
        self.hub = hub
        self.base_prefix = base_prefix
        self.dataset_name = dataset_name
        self.integration_name = integration_name

    def folder_key(self, start_date: datetime) -> str:
        return run_folder_key(
            self.base_prefix, self.dataset_name, self.integration_name, start_date
        )

    def write(self, result: MeasurementsResult, artifacts: Iterable[Path] = ()) -> str:
        """Upload the result JSON plus any local artifacts; return the folder URI.

        The result is uploaded from memory — it is the authoritative artifact and
        must not depend on the local sink having run first.
        """
        folder = self.folder_key(result.start_date)
        self.hub.write_text(f"{folder}/{RESULT_OBJECT_NAME}", result.model_dump_json(indent=2))
        for artifact in artifacts:
            path = Path(artifact)
            name = REPORT_OBJECT_NAME if path.suffix == ".html" else path.name
            self.hub.upload_file(path, f"{folder}/{name}")
        folder_uri = build_s3_uri(self.hub.bucket, folder)
        logger.info("Uploaded run artifacts to %s", folder_uri)
        return folder_uri


def _slug(name: str) -> str:
    """Keep S3 key segments readable: no slashes, spaces, or empty segments."""

    cleaned = "".join(char if char.isalnum() or char in "._-" else "_" for char in name.strip())
    return cleaned.strip("_") or "unnamed"
