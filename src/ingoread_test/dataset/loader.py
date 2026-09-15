"""Load a dataset from S3 (or a local folder) together with its manifest.

Datasets live in S3 and are addressed by URI:

    s3://ingoread-datasets/vehicle_registration
    ├── manifest.yaml          # the dataset format, see manifest.py
    ├── vr_0001.pdf            # the sample's document
    ├── vr_0001.id             # the sample's identifier, see sample_id.py
    └── ...

``load_dataset`` mirrors the prefix into a local cache directory once and reuses
it on later runs, so repeated runs against the same dataset do not re-download
gigabytes of documents. A local directory is accepted in the same argument for
development and tests; everything below works the same way against it.

Curation commands (``dataset list`` / ``remove`` / ``restore``) do not need the
documents at all, so they use :func:`load_manifest`, which fetches the single
manifest object and writes it back in place.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..utils.s3 import S3Hub, build_s3_uri, is_s3_uri, open_s3_uri, parse_s3_uri
from .manifest import DatasetManifest, SampleEntry, parse_manifest_text
from .models import Dataset, DocumentContainer
from .sample_id import id_file_for, read_sample_id

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_NAME = "manifest.yaml"
DEFAULT_CACHE_DIR = Path(".cache/ingoread-datasets")


@dataclass(frozen=True)
class DatasetLocation:
    """Where a dataset lives, and how to read or write its manifest.

    One object answers the two questions every caller has — "what is the URI of
    this thing?" for reporting, and "how do I read/write it?" — for both S3 and
    local datasets, so callers never branch on the scheme themselves.
    """

    source_uri: str
    manifest_uri: str
    files_uri: str
    hub: S3Hub | None = None
    manifest_key: str | None = None
    manifest_path: Path | None = None
    files_prefix: str = ""

    @property
    def is_s3(self) -> bool:
        return self.hub is not None

    def read_manifest_text(self) -> str:
        if self.hub is not None and self.manifest_key is not None:
            return self.hub.read_text(self.manifest_key)
        assert self.manifest_path is not None
        if not self.manifest_path.is_file():
            raise FileNotFoundError(f"Dataset manifest not found: {self.manifest_path}")
        return self.manifest_path.read_text(encoding="utf-8")

    def write_manifest_text(self, text: str) -> str:
        if self.hub is not None and self.manifest_key is not None:
            return self.hub.write_text(self.manifest_key, text)
        assert self.manifest_path is not None
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(text, encoding="utf-8")
        return str(self.manifest_path)

    def sample_file_uri(self, filename: str) -> str:
        """Where one sample's document lives, for reporting."""
        if self.hub is not None:
            return build_s3_uri(self.hub.bucket, f"{self.files_prefix}{filename}")
        return str(Path(self.files_uri) / filename)


def resolve_location(
    source: str | Path,
    manifest: str | Path | None = None,
    *,
    hub: S3Hub | None = None,
    endpoint_url: str | None = None,
    region_name: str | None = None,
) -> DatasetLocation:
    """Resolve a dataset URI (or local path) and its manifest into a location."""

    source_text = str(source)
    manifest_name = str(manifest) if manifest else DEFAULT_MANIFEST_NAME

    if is_s3_uri(source_text):
        bucket, prefix = parse_s3_uri(source_text)
        if hub is None:
            hub, _ = open_s3_uri(source_text, endpoint_url=endpoint_url, region_name=region_name)
        files_prefix = f"{prefix}/" if prefix else ""
        manifest_key = f"{files_prefix}{manifest_name.lstrip('/')}"
        return DatasetLocation(
            source_uri=source_text,
            manifest_uri=build_s3_uri(bucket, manifest_key),
            files_uri=build_s3_uri(bucket, prefix),
            hub=hub,
            manifest_key=manifest_key,
            files_prefix=files_prefix,
        )

    root = Path(source_text)
    # A manifest path that already resolves is used as given, so the historical
    # "files_root + full manifest path" call style keeps working.
    candidate = Path(manifest_name)
    manifest_path = (
        candidate if candidate.is_absolute() or candidate.is_file() else root / candidate
    )
    return DatasetLocation(
        source_uri=str(root),
        manifest_uri=str(manifest_path),
        files_uri=str(root),
        manifest_path=manifest_path,
    )


def load_manifest(
    source: str | Path,
    manifest: str | Path | None = None,
    *,
    name: str | None = None,
    hub: S3Hub | None = None,
    endpoint_url: str | None = None,
    region_name: str | None = None,
) -> tuple[DatasetManifest, DatasetLocation]:
    """Read a dataset's manifest without downloading any documents."""

    location = resolve_location(
        source, manifest, hub=hub, endpoint_url=endpoint_url, region_name=region_name
    )
    parsed = parse_manifest_text(
        location.read_manifest_text(),
        source=location.manifest_uri,
        name=name or _default_name(location.source_uri),
    )
    return parsed, location


def save_manifest(manifest: DatasetManifest, location: DatasetLocation) -> str:
    """Write a manifest back where it came from and return its URI."""

    return location.write_manifest_text(manifest.to_yaml())


def load_dataset(
    source: str | Path,
    manifest: str | Path | None = None,
    *,
    name: str | None = None,
    files_root: str | Path | None = None,
    cache_dir: str | Path | None = None,
    exclude_sample_ids: Iterable[str] = (),
    force_download: bool = False,
    download: bool = True,
    hub: S3Hub | None = None,
    endpoint_url: str | None = None,
    region_name: str | None = None,
) -> Dataset:
    """Load the samples a run should send.

    ``source`` is an ``s3://bucket/prefix`` URI or a local directory. Removed
    samples and ``exclude_sample_ids`` are dropped here, so nothing downstream
    has to remember to skip them.
    """

    parsed, location = load_manifest(
        source,
        manifest,
        name=name,
        hub=hub,
        endpoint_url=endpoint_url,
        region_name=region_name,
    )

    local_files_root = _materialize_files(
        location,
        files_root=files_root,
        cache_dir=cache_dir,
        force_download=force_download,
        download=download,
    )

    excluded = {str(item) for item in exclude_sample_ids}
    containers: list[DocumentContainer] = []
    skipped: list[str] = []
    for entry in parsed.active_samples:
        if entry.sample_id in excluded or entry.filename in excluded:
            skipped.append(entry.sample_id)
            continue
        containers.append(_build_container(entry, local_files_root))

    return Dataset(
        name=parsed.name,
        source_uri=location.source_uri,
        manifest_uri=location.manifest_uri,
        containers=containers,
        removed_sample_ids=[sample.sample_id for sample in parsed.removed_samples],
        excluded_sample_ids=skipped,
    )


def _build_container(entry: SampleEntry, files_root: Path) -> DocumentContainer:
    """Turn a manifest entry into a container, taking its id from the id file."""

    container = entry.to_container(files_root)
    id_path = files_root / entry.id_file if entry.id_file else id_file_for(container.file_path)

    if not id_path.is_file():
        # The manifest id is the documented fallback: it keeps datasets whose
        # documents are not on this machine (curation, stub runs) usable.
        logger.debug("sample %s has no id file at %s", entry.sample_id, id_path)
        return container

    file_sample_id = read_sample_id(id_path)
    if file_sample_id != entry.sample_id:
        raise ValueError(
            f"Sample id mismatch for {entry.filename}: manifest says "
            f"{entry.sample_id!r} but {id_path} says {file_sample_id!r}. "
            "Fix whichever is wrong — the id file is what gets sent to the backend."
        )
    container.id_file_path = id_path
    return container


def _materialize_files(
    location: DatasetLocation,
    *,
    files_root: str | Path | None,
    cache_dir: str | Path | None,
    force_download: bool,
    download: bool,
) -> Path:
    """Return the local directory holding the sample files, downloading if needed."""

    if files_root is not None:
        return Path(files_root)
    if not location.is_s3:
        return Path(location.files_uri)

    local_root = _cache_root(location, cache_dir)
    if not download:
        return local_root

    manifest_name = PurePosixPath(location.manifest_key or DEFAULT_MANIFEST_NAME).name
    already_cached = (local_root / manifest_name).is_file()
    if already_cached and not force_download:
        logger.info("Using cached dataset at %s (pass force_download to refresh)", local_root)
        return local_root

    assert location.hub is not None
    location.hub.download_dataset(location.files_prefix, local_root)
    return local_root


def _cache_root(location: DatasetLocation, cache_dir: str | Path | None) -> Path:
    """The local mirror of an S3 prefix: <cache>/<bucket>/<prefix>."""

    assert location.hub is not None
    base = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    parts = [part for part in PurePosixPath(location.files_prefix).parts if part]
    return base.joinpath(location.hub.bucket, *parts)


def _default_name(source_uri: str) -> str:
    """Name a dataset after the last segment of its URI when none is given."""

    trimmed = source_uri.rstrip("/")
    return PurePosixPath(trimmed).name or "dataset"
