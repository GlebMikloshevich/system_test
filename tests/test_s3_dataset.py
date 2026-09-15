"""Datasets loaded from S3: URI handling, the local cache, and curation in place."""

from __future__ import annotations

import pytest

from ingoread_test.dataset import load_dataset, load_manifest, save_manifest
from ingoread_test.utils.s3 import build_s3_uri, is_s3_uri, parse_s3_uri

MANIFEST = """
version: 2
name: invoices-ru
samples:
  - sample_id: inv-0001
    filename: invoice_001.pdf
    documents:
      - doc_label: invoice
        fields: {total: {gt_value: "1.00"}}
"""


@pytest.fixture
def bucket(s3_client):
    s3_client.objects = {
        "datasets/invoices-ru/manifest.yaml": MANIFEST.encode(),
        "datasets/invoices-ru/invoice_001.pdf": b"%PDF-1.4",
        "datasets/invoices-ru/invoice_001.id": b"inv-0001\n",
    }
    return s3_client


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("s3://bucket/a/b", ("bucket", "a/b")),
        ("s3://bucket/a/b/", ("bucket", "a/b")),
        ("s3://bucket", ("bucket", "")),
    ],
)
def test_parse_s3_uri(uri, expected):
    assert parse_s3_uri(uri) == expected


def test_parse_s3_uri_rejects_traversal_and_missing_bucket():
    with pytest.raises(ValueError, match="must not contain"):
        parse_s3_uri("s3://bucket/a/../../etc")
    with pytest.raises(ValueError, match="missing a bucket"):
        parse_s3_uri("s3:///key")


def test_build_and_detect_uri():
    assert build_s3_uri("bucket", "a/b") == "s3://bucket/a/b"
    assert is_s3_uri("s3://bucket/a") is True
    assert is_s3_uri("/local/path") is False


def test_dataset_is_downloaded_from_s3_into_the_cache(bucket, s3_hub, tmp_path):
    dataset = load_dataset(
        "s3://test-bucket/datasets/invoices-ru", hub=s3_hub, cache_dir=tmp_path / "cache"
    )

    cached = tmp_path / "cache/test-bucket/datasets/invoices-ru"
    assert dataset.name == "invoices-ru"
    assert dataset.source_uri == "s3://test-bucket/datasets/invoices-ru"
    assert dataset.manifest_uri == "s3://test-bucket/datasets/invoices-ru/manifest.yaml"
    assert [container.sample_id for container in dataset.containers] == ["inv-0001"]
    assert dataset.containers[0].file_path == cached / "invoice_001.pdf"
    assert (cached / "invoice_001.pdf").read_bytes() == b"%PDF-1.4"
    assert dataset.containers[0].id_file_path == cached / "invoice_001.id"


def test_second_load_reuses_the_cache(bucket, s3_hub, tmp_path):
    load_dataset("s3://test-bucket/datasets/invoices-ru", hub=s3_hub, cache_dir=tmp_path / "c")
    downloads = len(bucket.downloads)

    load_dataset("s3://test-bucket/datasets/invoices-ru", hub=s3_hub, cache_dir=tmp_path / "c")

    assert len(bucket.downloads) == downloads


def test_force_download_refreshes_the_cache(bucket, s3_hub, tmp_path):
    load_dataset("s3://test-bucket/datasets/invoices-ru", hub=s3_hub, cache_dir=tmp_path / "c")
    downloads = len(bucket.downloads)

    load_dataset(
        "s3://test-bucket/datasets/invoices-ru",
        hub=s3_hub,
        cache_dir=tmp_path / "c",
        force_download=True,
    )

    assert len(bucket.downloads) > downloads


def test_curation_reads_and_writes_only_the_manifest_object(bucket, s3_hub):
    manifest, location = load_manifest("s3://test-bucket/datasets/invoices-ru", hub=s3_hub)
    manifest.samples[0].removed = True

    written = save_manifest(manifest, location)

    assert bucket.downloads == [], "curation must not pull documents"
    assert written == "s3://test-bucket/datasets/invoices-ru/manifest.yaml"
    assert "removed: true" in bucket.text("datasets/invoices-ru/manifest.yaml")


def test_a_named_manifest_inside_the_dataset_is_honoured(bucket, s3_hub):
    bucket.objects["datasets/invoices-ru/gt_v2.yaml"] = MANIFEST.encode()

    manifest, location = load_manifest(
        "s3://test-bucket/datasets/invoices-ru", "gt_v2.yaml", hub=s3_hub
    )

    assert location.manifest_uri.endswith("gt_v2.yaml")
    assert len(manifest.samples) == 1


def test_upload_dataset_preserves_layout(s3_client, s3_hub, tmp_path):
    root = tmp_path / "invoices"
    (root / "nested").mkdir(parents=True)
    (root / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "invoice_001.id").write_text("inv-0001\n", encoding="utf-8")
    (root / "nested" / "invoice_001.pdf").write_bytes(b"%PDF-1.4")

    keys = s3_hub.upload_dataset(root, "datasets/invoices-ru")

    assert keys == [
        "datasets/invoices-ru/invoice_001.id",
        "datasets/invoices-ru/manifest.yaml",
        "datasets/invoices-ru/nested/invoice_001.pdf",
    ]
    assert s3_client.objects["datasets/invoices-ru/nested/invoice_001.pdf"] == b"%PDF-1.4"


def test_upload_dataset_rejects_a_missing_or_empty_folder(s3_hub, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(NotADirectoryError):
        s3_hub.upload_dataset(tmp_path / "nope", "datasets/x")
    with pytest.raises(ValueError, match="empty"):
        s3_hub.upload_dataset(empty, "datasets/x")


def test_uploaded_dataset_round_trips_back_into_a_run(s3_client, s3_hub, tmp_path):
    """Upload then load: what was published is what a run would send."""
    root = tmp_path / "invoices"
    root.mkdir()
    (root / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "invoice_001.pdf").write_bytes(b"%PDF-1.4")
    (root / "invoice_001.id").write_text("inv-0001\n", encoding="utf-8")
    s3_hub.upload_dataset(root, "datasets/invoices-ru")

    dataset = load_dataset(
        "s3://test-bucket/datasets/invoices-ru", hub=s3_hub, cache_dir=tmp_path / "cache"
    )

    assert [container.sample_id for container in dataset.containers] == ["inv-0001"]
