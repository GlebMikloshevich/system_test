"""Helpers for transferring datasets and result artifacts to and from S3."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from . import personal_information

logger = logging.getLogger(__name__)


class _Paginator(Protocol):
    def paginate(self, **kwargs: str) -> Iterable[dict[str, Any]]: ...


class _S3Client(Protocol):
    def get_paginator(self, operation_name: str) -> _Paginator: ...

    def download_file(self, bucket: str, key: str, filename: str) -> None: ...

    def upload_file(self, filename: str, bucket: str, key: str) -> None: ...

    def get_object(self, **kwargs: str) -> dict[str, Any]: ...

    def put_object(self, **kwargs: Any) -> dict[str, Any]: ...


ImpalaUploader = Callable[[Any], object | Awaitable[object]]

S3_SCHEME = "s3://"


def is_s3_uri(uri: str | Path) -> bool:
    """True for ``s3://bucket/key`` locations; local paths return False."""

    return str(uri).startswith(S3_SCHEME)


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split ``s3://bucket/key`` into ``(bucket, key)``; the key may be empty."""

    if not is_s3_uri(uri):
        raise ValueError(f"Not an S3 URI: {uri!r}")
    remainder = uri[len(S3_SCHEME) :]
    bucket, _, key = remainder.partition("/")
    if not bucket:
        raise ValueError(f"S3 URI is missing a bucket: {uri!r}")
    key = key.strip("/")
    if key and ".." in PurePosixPath(key).parts:
        raise ValueError(f"S3 URI must not contain '..': {uri!r}")
    return bucket, key


def build_s3_uri(bucket: str, key: str) -> str:
    """Join a bucket and key back into an ``s3://`` URI."""

    return f"{S3_SCHEME}{bucket}/{key.lstrip('/')}" if key else f"{S3_SCHEME}{bucket}"


class S3Hub:
    """Transfer dataset folders and result files between local storage and S3.

    ``client`` and ``impala_uploader`` are injectable to keep credentials and
    environment-specific Impala code outside this reusable module. When an
    uploader is not injected, :meth:`upload_results` looks for a callable named
    ``upload_to_impala`` in ``personal_information.py``.
    """

    def __init__(
        self,
        bucket: str,
        *,
        client: _S3Client | None = None,
        impala_uploader: ImpalaUploader | None = None,
        max_concurrency: int = 8,
        endpoint_url: str | None = None,
        region_name: str | None = None,
    ) -> None:
        if not bucket.strip():
            raise ValueError("S3 bucket must not be empty")
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be at least 1")

        self.bucket = bucket
        self._client = client or self._build_client(endpoint_url, region_name)
        self._impala_uploader = impala_uploader
        self._max_concurrency = max_concurrency

    @property
    def client(self) -> _S3Client:
        """The underlying S3 client, for callers that need a raw API call."""

        return self._client

    def download_dataset(self, s3_folder: str, destination: str | Path) -> int:
        """Download every object below ``s3_folder`` and return its total bytes.

        S3 folder markers are ignored and nested key paths are reproduced below
        ``destination``. The objects are listed first, so the logged and returned
        byte count is the estimated required local storage before downloading.
        """

        prefix = self._folder_prefix(s3_folder)
        objects = self._list_objects(prefix)
        total_bytes = sum(int(item.get("Size", 0)) for item in objects)
        logger.info(
            "Downloading %d S3 object(s) (%s) from s3://%s/%s to %s",
            len(objects),
            self._format_size(total_bytes),
            self.bucket,
            prefix,
            destination,
        )

        destination_path = Path(destination).expanduser().resolve()
        destination_path.mkdir(parents=True, exist_ok=True)
        for item in objects:
            key = item.get("Key")
            if not isinstance(key, str) or key.endswith("/"):
                continue
            local_path = self._safe_destination(destination_path, key, prefix)
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self._client.download_file(self.bucket, key, str(local_path))

        return total_bytes

    def read_text(self, key: str, encoding: str = "utf-8") -> str:
        """Read one object as text — used for manifests, not for bulk data."""

        response = self._client.get_object(Bucket=self.bucket, Key=key)
        body = response["Body"].read()
        return body.decode(encoding) if isinstance(body, bytes) else str(body)

    def write_text(self, key: str, text: str, encoding: str = "utf-8") -> str:
        """Write one object from text and return its ``s3://`` URI."""

        self._client.put_object(Bucket=self.bucket, Key=key, Body=text.encode(encoding))
        return build_s3_uri(self.bucket, key)

    def upload_file(self, local_file: str | Path, key: str) -> str:
        """Upload a single file to ``key`` and return its ``s3://`` URI."""

        path = Path(local_file).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"File does not exist or is not a file: {path}")
        self._client.upload_file(str(path), self.bucket, key)
        return build_s3_uri(self.bucket, key)

    async def upload_files(self, files: Iterable[str | Path], s3_folder: str) -> list[str]:
        """Upload files into ``s3_folder`` concurrently and return their keys.

        File basenames become object names below ``s3_folder``. Duplicate
        basenames are rejected because they would overwrite the same S3 object.
        """

        local_files = [Path(file).expanduser().resolve() for file in files]
        self._validate_upload_files(local_files)
        prefix = self._folder_prefix(s3_folder)
        semaphore = asyncio.Semaphore(self._max_concurrency)

        async def upload_one(local_file: Path) -> str:
            key = f"{prefix}{local_file.name}"
            async with semaphore:
                await asyncio.to_thread(
                    self._client.upload_file,
                    str(local_file),
                    self.bucket,
                    key,
                )
            return key

        return list(await asyncio.gather(*(upload_one(file) for file in local_files)))

    async def upload_results(
        self,
        files: Iterable[str | Path],
        s3_folder: str,
        dataframe: Any,
    ) -> list[str]:
        """Upload result files to S3 and a DataFrame to Impala concurrently.

        File basenames become object names below ``s3_folder``. Duplicate
        basenames are rejected because they would overwrite the same S3 object.
        The Impala callback may be synchronous or asynchronous; synchronous
        callbacks run in a worker thread and do not block the event loop.
        """

        uploader = self._resolve_impala_uploader()
        uploaded_keys, _ = await asyncio.gather(
            self.upload_files(files, s3_folder),
            self._send_to_impala(uploader, dataframe),
        )
        return list(uploaded_keys)

    def upload_dataset(self, local_folder: str | Path, s3_folder: str) -> list[str]:
        """Upload a dataset folder to ``s3_folder``, preserving its layout.

        The mirror image of :meth:`download_dataset`: nested paths are kept, so
        a manifest, its documents, and their id files land under one prefix.
        Existing objects with the same key are replaced; objects that are only
        in S3 are left alone — removing a sample is
        :mod:`ingoread_test.dataset.removal`'s job, not a side effect of an
        upload.
        """

        root = Path(local_folder).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(f"Dataset folder does not exist: {root}")

        files = sorted(path for path in root.rglob("*") if path.is_file())
        if not files:
            raise ValueError(f"Dataset folder is empty: {root}")

        prefix = self._folder_prefix(s3_folder)
        total_bytes = sum(path.stat().st_size for path in files)
        logger.info(
            "Uploading %d file(s) (%s) from %s to s3://%s/%s",
            len(files),
            self._format_size(total_bytes),
            root,
            self.bucket,
            prefix,
        )

        keys: list[str] = []
        for path in files:
            key = f"{prefix}{path.relative_to(root).as_posix()}"
            self._client.upload_file(str(path), self.bucket, key)
            keys.append(key)
        return keys

    @staticmethod
    def _build_client(endpoint_url: str | None = None, region_name: str | None = None) -> _S3Client:
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "boto3 is required to create an S3Hub without an injected client"
            ) from exc
        return boto3.client("s3", endpoint_url=endpoint_url, region_name=region_name)

    @staticmethod
    def _folder_prefix(folder: str) -> str:
        prefix = folder.strip().strip("/")
        if not prefix:
            return ""
        if ".." in PurePosixPath(prefix).parts:
            raise ValueError("S3 folder must not contain '..'")
        return f"{prefix}/"

    def _list_objects(self, prefix: str) -> list[dict[str, Any]]:
        paginator = self._client.get_paginator("list_objects_v2")
        objects: list[dict[str, Any]] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            contents = page.get("Contents", [])
            if not isinstance(contents, list):
                raise ValueError("S3 list_objects_v2 returned invalid Contents")
            objects.extend(item for item in contents if isinstance(item, dict))
        return objects

    @staticmethod
    def _safe_destination(destination: Path, key: str, prefix: str) -> Path:
        if prefix and not key.startswith(prefix):
            raise ValueError(f"S3 key {key!r} is outside prefix {prefix!r}")
        relative_key = key[len(prefix) :] if prefix else key
        relative = PurePosixPath(relative_key)
        if not relative_key or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Unsafe S3 object key: {key!r}")

        local_path = destination.joinpath(*relative.parts).resolve()
        if not local_path.is_relative_to(destination):
            raise ValueError(f"S3 object key escapes destination: {key!r}")
        return local_path

    @staticmethod
    def _validate_upload_files(files: list[Path]) -> None:
        missing = [str(file) for file in files if not file.is_file()]
        if missing:
            raise FileNotFoundError(f"Result files do not exist or are not files: {missing}")

        names = [file.name for file in files]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"Result files have duplicate basenames: {duplicates}")

    def _resolve_impala_uploader(self) -> ImpalaUploader:
        uploader = self._impala_uploader or getattr(
            personal_information,
            "upload_to_impala",
            None,
        )
        if not callable(uploader):
            raise RuntimeError(
                "Define upload_to_impala(dataframe) in personal_information.py "
                "or pass impala_uploader to S3Hub"
            )
        return uploader

    @staticmethod
    async def _send_to_impala(uploader: ImpalaUploader, dataframe: Any) -> None:
        if inspect.iscoroutinefunction(uploader):
            await uploader(dataframe)
            return

        result = await asyncio.to_thread(uploader, dataframe)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if size < 1024 or unit == "TiB":
                return f"{size:.1f} {unit}"
            size /= 1024
        raise AssertionError("unreachable")


def open_s3_uri(
    uri: str,
    *,
    client: _S3Client | None = None,
    endpoint_url: str | None = None,
    region_name: str | None = None,
) -> tuple[S3Hub, str]:
    """Split an ``s3://bucket/key`` URI into a hub for its bucket and that key."""

    bucket, key = parse_s3_uri(uri)
    hub = S3Hub(bucket, client=client, endpoint_url=endpoint_url, region_name=region_name)
    return hub, key
