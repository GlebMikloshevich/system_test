"""Removing samples from a dataset on user request — soft, purge, and restore."""

from __future__ import annotations

import pytest

from ingoread_test.dataset import (
    load_dataset,
    load_manifest,
    remove_samples,
    restore_samples,
    save_manifest,
)

MANIFEST = """
version: 2
name: invoices
samples:
  - sample_id: inv-0001
    filename: invoice_001.pdf
    documents:
      - doc_label: invoice
        fields: {total: {gt_value: "1.00"}}
  - sample_id: inv-0002
    filename: invoice_002.pdf
    documents:
      - doc_label: invoice
        fields: {total: {gt_value: "2.00"}}
"""


@pytest.fixture
def dataset_root(tmp_path):
    root = tmp_path / "invoices"
    root.mkdir()
    (root / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / "invoice_001.pdf").write_bytes(b"one")
    (root / "invoice_001.id").write_text("inv-0001\n", encoding="utf-8")
    return root


def test_soft_removal_records_the_reason_and_keeps_the_entry(dataset_root):
    manifest, location = load_manifest(dataset_root)

    report = remove_samples(manifest, ["inv-0002"], reason="duplicate scan")
    save_manifest(manifest, location)

    assert report.removed == ["inv-0002"]
    assert report.changed is True
    reloaded, _ = load_manifest(dataset_root)
    removed = reloaded.find("inv-0002")[0]
    assert removed.removed is True
    assert removed.removal is not None
    assert removed.removal.reason == "duplicate scan"
    assert removed.removal.removed_at is not None
    assert removed.documents, "ground truth is kept so the removal can be undone"


def test_removed_samples_are_not_sent_by_a_run(dataset_root):
    manifest, location = load_manifest(dataset_root)
    remove_samples(manifest, ["inv-0002"], reason="duplicate scan")
    save_manifest(manifest, location)

    dataset = load_dataset(dataset_root)

    assert [container.sample_id for container in dataset.containers] == ["inv-0001"]
    assert dataset.removed_sample_ids == ["inv-0002"]


def test_samples_can_be_addressed_by_filename(dataset_root):
    manifest, _ = load_manifest(dataset_root)

    report = remove_samples(manifest, ["invoice_002.pdf"])

    assert report.removed == ["inv-0002"]


def test_removing_an_already_removed_sample_changes_nothing(dataset_root):
    manifest, _ = load_manifest(dataset_root)
    remove_samples(manifest, ["inv-0002"])

    report = remove_samples(manifest, ["inv-0002"])

    assert report.removed == []
    assert report.unchanged == ["inv-0002"]
    assert report.changed is False


def test_unknown_selectors_are_reported_and_nothing_else_breaks(dataset_root):
    manifest, _ = load_manifest(dataset_root)

    report = remove_samples(manifest, ["inv-0001", "nope"])

    assert report.removed == ["inv-0001"]
    assert report.not_found == ["nope"]


def test_restore_undoes_a_soft_removal(dataset_root):
    manifest, location = load_manifest(dataset_root)
    remove_samples(manifest, ["inv-0002"], reason="mistake")

    report = restore_samples(manifest, ["inv-0002"])
    save_manifest(manifest, location)

    assert report.restored == ["inv-0002"]
    reloaded, _ = load_manifest(dataset_root)
    restored = reloaded.find("inv-0002")[0]
    assert restored.removed is False
    assert restored.removal is None


def test_restoring_an_active_sample_changes_nothing(dataset_root):
    manifest, _ = load_manifest(dataset_root)

    report = restore_samples(manifest, ["inv-0001"])

    assert report.restored == []
    assert report.unchanged == ["inv-0001"]


def test_purge_drops_the_entry_entirely(dataset_root):
    manifest, location = load_manifest(dataset_root)

    report = remove_samples(manifest, ["inv-0002"], purge=True)
    save_manifest(manifest, location)

    assert report.purged == ["inv-0002"]
    reloaded, _ = load_manifest(dataset_root)
    assert [sample.sample_id for sample in reloaded.samples] == ["inv-0001"]


def test_purge_leaves_the_objects_alone(s3_client, s3_hub):
    """Deleting a sample's files is another system's job; we only curate the manifest."""
    s3_client.objects = {
        "invoices/manifest.yaml": MANIFEST.encode(),
        "invoices/invoice_001.pdf": b"one",
        "invoices/invoice_001.id": b"inv-0001\n",
    }
    manifest, location = load_manifest("s3://test-bucket/invoices", hub=s3_hub)

    report = remove_samples(manifest, ["inv-0001"], purge=True)
    save_manifest(manifest, location)

    assert report.purged == ["inv-0001"]
    assert s3_client.objects["invoices/invoice_001.pdf"] == b"one"
    assert s3_client.objects["invoices/invoice_001.id"] == b"inv-0001\n"
    assert "inv-0001" not in s3_client.text("invoices/manifest.yaml")


def test_soft_removal_leaves_local_files_alone(dataset_root):
    manifest, _ = load_manifest(dataset_root)

    remove_samples(manifest, ["inv-0001"])

    assert (dataset_root / "invoice_001.pdf").exists()
    assert (dataset_root / "invoice_001.id").exists()
