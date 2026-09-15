from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ingoread_test.utils.s3 import S3Hub


class FakePaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, str]] = []

    def paginate(self, **kwargs: str) -> list[dict[str, Any]]:
        self.calls.append(kwargs)
        return self.pages


class FakeS3Client:
    def __init__(self, pages: list[dict[str, Any]] | None = None) -> None:
        self.paginator = FakePaginator(pages or [])
        self.downloads: list[tuple[str, str, str]] = []
        self.uploads: list[tuple[str, str, str]] = []

    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return self.paginator

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.downloads.append((bucket, key, filename))
        Path(filename).write_bytes(key.encode())

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.uploads.append((filename, bucket, key))


def test_download_dataset_preserves_subfolders_and_returns_size(tmp_path: Path) -> None:
    client = FakeS3Client(
        [
            {
                "Contents": [
                    {"Key": "datasets/invoices/", "Size": 0},
                    {"Key": "datasets/invoices/manifest.json", "Size": 12},
                ]
            },
            {
                "Contents": [
                    {"Key": "datasets/invoices/files/page.pdf", "Size": 25},
                ]
            },
        ]
    )
    hub = S3Hub("test-bucket", client=client)

    total_bytes = hub.download_dataset("/datasets/invoices/", tmp_path)

    assert total_bytes == 37
    assert client.paginator.calls == [{"Bucket": "test-bucket", "Prefix": "datasets/invoices/"}]
    assert (tmp_path / "manifest.json").read_text() == "datasets/invoices/manifest.json"
    assert (tmp_path / "files/page.pdf").read_text() == "datasets/invoices/files/page.pdf"
    assert len(client.downloads) == 2


def test_download_dataset_rejects_key_that_escapes_destination(tmp_path: Path) -> None:
    client = FakeS3Client([{"Contents": [{"Key": "datasets/invoices/../secret.txt", "Size": 1}]}])
    hub = S3Hub("test-bucket", client=client)

    with pytest.raises(ValueError, match="Unsafe S3 object key"):
        hub.download_dataset("datasets/invoices", tmp_path)


async def test_upload_results_uploads_files_and_dataframe(tmp_path: Path) -> None:
    first = tmp_path / "result.json"
    second = tmp_path / "report.html"
    first.write_text("{}")
    second.write_text("report")
    dataframe = object()
    impala_calls: list[object] = []

    async def upload_to_impala(value: object) -> None:
        impala_calls.append(value)

    client = FakeS3Client()
    hub = S3Hub(
        "test-bucket",
        client=client,
        impala_uploader=upload_to_impala,
        max_concurrency=2,
    )

    keys = await hub.upload_results([first, second], "/runs/123/", dataframe)

    assert keys == ["runs/123/result.json", "runs/123/report.html"]
    assert {(bucket, key) for _, bucket, key in client.uploads} == {
        ("test-bucket", "runs/123/result.json"),
        ("test-bucket", "runs/123/report.html"),
    }
    assert impala_calls == [dataframe]


async def test_upload_results_requires_impala_uploader(tmp_path: Path) -> None:
    result = tmp_path / "result.json"
    result.write_text("{}")
    hub = S3Hub("test-bucket", client=FakeS3Client())

    with pytest.raises(RuntimeError, match="upload_to_impala"):
        await hub.upload_results([result], "runs", object())


def test_upload_dataset_mirrors_the_folder_into_the_prefix(tmp_path: Path) -> None:
    (tmp_path / "files").mkdir()
    (tmp_path / "manifest.yaml").write_text("version: 2\n", encoding="utf-8")
    (tmp_path / "files" / "page.pdf").write_bytes(b"%PDF")
    client = FakeS3Client()
    hub = S3Hub("test-bucket", client=client)

    keys = hub.upload_dataset(tmp_path, "datasets/invoices")

    assert keys == [
        "datasets/invoices/files/page.pdf",
        "datasets/invoices/manifest.yaml",
    ]
    assert [key for _, _, key in client.uploads] == keys
