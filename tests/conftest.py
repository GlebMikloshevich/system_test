"""Shared test helpers and fixtures.

The builders below are imported by name (`from conftest import ...`) thanks to
`pythonpath = ["src", "tests"]`. `FakeS3Client` is the in-memory stand-in for
S3: datasets are read from it and results written to it, so the suite stays
offline and free of credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ingoread_test.config.scorer_config import DocumentMeasurerConfig, FieldConfig
from ingoread_test.dataset.models import DocumentGT, FieldGT
from ingoread_test.integration.schemas import IngoreadDocument
from ingoread_test.utils.s3 import S3Hub


def doc_config(*fields: FieldConfig, label: str = "invoice", **kwargs) -> DocumentMeasurerConfig:
    return DocumentMeasurerConfig(doc_label=label, fields=list(fields), **kwargs)


def ground_truth(label: str = "invoice", page: int = 0, bbox=None, **values) -> DocumentGT:
    return DocumentGT(
        doc_label=label,
        page=page,
        bbox=bbox,
        fields={name: FieldGT(gt_value=value) for name, value in values.items()},
    )


def prediction(label: str = "invoice", page: int = 0, bbox=None, **values) -> IngoreadDocument:
    """Build a prediction from raw field values, in any shape the API emits."""
    return IngoreadDocument.model_validate(
        {"label": label, "page": page, "bbox": bbox, "fields": values}
    )


def boxes(*values: list[float]) -> list[dict]:
    """Predicted bbox candidates."""
    return [{"bbox": list(box)} for box in values]


class FakeS3Client:
    """The slice of the boto3 S3 client that `S3Hub` uses, backed by a dict."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects: dict[str, bytes] = dict(objects or {})
        self.downloads: list[str] = []
        self.uploads: list[tuple[str, str]] = []

    # --- reads -----------------------------------------------------------
    def get_paginator(self, operation_name: str) -> FakePaginator:
        assert operation_name == "list_objects_v2"
        return FakePaginator(self)

    def get_object(self, **kwargs: str) -> dict[str, Any]:
        key = kwargs["Key"]
        if key not in self.objects:
            raise FileNotFoundError(f"no such key: {key}")
        return {"Body": _Body(self.objects[key])}

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.downloads.append(key)
        Path(filename).write_bytes(self.objects[key])

    # --- writes ----------------------------------------------------------
    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        body = kwargs["Body"]
        self.objects[kwargs["Key"]] = body if isinstance(body, bytes) else str(body).encode()
        return {}

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.uploads.append((filename, key))
        self.objects[key] = Path(filename).read_bytes()

    # --- helpers for tests ----------------------------------------------
    def text(self, key: str) -> str:
        return self.objects[key].decode()

    def keys_under(self, prefix: str) -> list[str]:
        return sorted(key for key in self.objects if key.startswith(prefix))


class FakePaginator:
    def __init__(self, client: FakeS3Client) -> None:
        self.client = client

    def paginate(self, **kwargs: str) -> list[dict[str, Any]]:
        prefix = kwargs.get("Prefix", "")
        contents = [
            {"Key": key, "Size": len(body)}
            for key, body in sorted(self.client.objects.items())
            if key.startswith(prefix)
        ]
        return [{"Contents": contents}]


class _Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload


@pytest.fixture
def s3_client() -> FakeS3Client:
    return FakeS3Client()


@pytest.fixture
def s3_hub(s3_client: FakeS3Client) -> S3Hub:
    return S3Hub("test-bucket", client=s3_client)
