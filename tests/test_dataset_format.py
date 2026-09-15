"""The version 2 dataset format: named datasets, unique sample ids, id files."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ingoread_test.dataset import load_dataset, load_manifest, save_manifest
from ingoread_test.dataset.manifest import DatasetManifest, parse_manifest_text
from ingoread_test.dataset.sample_id import parse_sample_id, read_sample_id

V2_MANIFEST = """
version: 2
name: invoices
samples:
  - sample_id: inv-0001
    filename: invoice_001.pdf
    kwargs: {language: ru}
    documents:
      - doc_label: invoice
        page: 0
        fields:
          total: {gt_value: "123.45"}
  - sample_id: inv-0002
    filename: invoice_002.pdf
    removed: true
    removal:
      reason: customer asked for deletion
      removed_by: gleb
    documents:
      - doc_label: invoice
        fields:
          total: {gt_value: "10.00"}
"""

V1_MANIFEST = """
- filename: invoice_001.pdf
  documents:
    - doc_label: invoice
      page: 0
      fields:
        total: {gt_value: "123.45"}
"""


def _write_dataset(root: Path, manifest_text: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.yaml").write_text(manifest_text, encoding="utf-8")
    return root


def test_v2_manifest_separates_active_and_removed_samples(tmp_path):
    root = _write_dataset(tmp_path / "invoices", V2_MANIFEST)

    dataset = load_dataset(root)

    assert dataset.name == "invoices"
    assert [container.sample_id for container in dataset.containers] == ["inv-0001"]
    assert dataset.removed_sample_ids == ["inv-0002"]
    assert dataset.containers[0].kwargs == {"language": "ru"}
    assert dataset.containers[0].file_path == root / "invoice_001.pdf"


def test_v1_manifest_still_loads_and_gets_ids_from_filenames(tmp_path):
    """Version 1 manifests predate sample ids; the filename stem becomes the id."""
    root = _write_dataset(tmp_path / "legacy", V1_MANIFEST)

    dataset = load_dataset(root)

    assert [container.sample_id for container in dataset.containers] == ["invoice_001"]
    assert dataset.containers[0].filename == "invoice_001.pdf"


def test_duplicate_sample_ids_are_rejected(tmp_path):
    text = V2_MANIFEST.replace("inv-0002", "inv-0001")
    root = _write_dataset(tmp_path / "dupes", text)

    with pytest.raises(ValueError, match="Duplicate sample_id"):
        load_dataset(root)


def test_unsupported_manifest_version_is_rejected():
    with pytest.raises(ValueError, match="unsupported version"):
        parse_manifest_text("version: 99\nsamples: []\n", source="manifest.yaml")


def test_manifest_without_samples_key_is_rejected():
    with pytest.raises(ValueError, match="missing the 'samples' key"):
        parse_manifest_text("version: 2\nname: x\n", source="manifest.yaml")


def test_sample_id_comes_from_the_id_file(tmp_path):
    root = _write_dataset(tmp_path / "invoices", V2_MANIFEST)
    (root / "invoice_001.id").write_text("inv-0001\n", encoding="utf-8")

    dataset = load_dataset(root)

    assert dataset.containers[0].sample_id == "inv-0001"
    assert dataset.containers[0].id_file_path == root / "invoice_001.id"


def test_id_file_disagreeing_with_the_manifest_fails_the_load(tmp_path):
    """A mismatch would send the backend an id that names a different sample."""
    root = _write_dataset(tmp_path / "invoices", V2_MANIFEST)
    (root / "invoice_001.id").write_text("inv-9999", encoding="utf-8")

    with pytest.raises(ValueError, match="Sample id mismatch"):
        load_dataset(root)


def test_missing_id_file_falls_back_to_the_manifest_id(tmp_path):
    root = _write_dataset(tmp_path / "invoices", V2_MANIFEST)

    dataset = load_dataset(root)

    assert dataset.containers[0].sample_id == "inv-0001"
    assert dataset.containers[0].id_file_path is None


def test_id_file_override_is_honoured(tmp_path):
    text = V2_MANIFEST.replace(
        "    filename: invoice_001.pdf",
        "    filename: invoice_001.pdf\n    id_file: ids/invoice_001.txt",
    )
    root = _write_dataset(tmp_path / "invoices", text)
    (root / "ids").mkdir()
    (root / "ids" / "invoice_001.txt").write_text("inv-0001", encoding="utf-8")

    dataset = load_dataset(root)

    assert dataset.containers[0].id_file_path == root / "ids" / "invoice_001.txt"


@pytest.mark.parametrize("content", ["", "   \n", "two tokens", "a" * 201])
def test_malformed_id_files_are_rejected(content):
    with pytest.raises(ValueError):
        parse_sample_id(content, "invoice_001.id")


def test_id_file_ignores_surrounding_whitespace(tmp_path):
    path = tmp_path / "invoice_001.id"
    path.write_text("  inv-0001\n", encoding="utf-8")

    assert read_sample_id(path) == "inv-0001"


def test_exclude_sample_ids_skips_samples_for_one_run(tmp_path):
    root = _write_dataset(tmp_path / "invoices", V2_MANIFEST)

    dataset = load_dataset(root, exclude_sample_ids=["inv-0001"])

    assert dataset.containers == []
    assert dataset.excluded_sample_ids == ["inv-0001"]


def test_manifest_round_trips_through_save(tmp_path):
    root = _write_dataset(tmp_path / "invoices", V2_MANIFEST)
    manifest, location = load_manifest(root)

    save_manifest(manifest, location)
    reloaded, _ = load_manifest(root)

    assert reloaded.model_dump() == manifest.model_dump()
    stored = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
    assert stored["version"] == 2
    assert stored["name"] == "invoices"
    assert [sample["sample_id"] for sample in stored["samples"]] == ["inv-0001", "inv-0002"]


def test_manifest_keeps_bounding_boxes_inline_and_whole(tmp_path):
    """Rewritten manifests stay readable: [x1, y1, x2, y2], no trailing .0."""
    text = (
        "version: 2\nname: boxes\nsamples:\n"
        "  - sample_id: s1\n    filename: a.pdf\n"
        "    documents:\n      - doc_label: invoice\n        bbox: [10, 20, 30, 40]\n"
        "        fields: {}\n"
    )
    manifest = DatasetManifest.parse(yaml.safe_load(text), source="manifest.yaml")

    assert "bbox: [10, 20, 30, 40]" in manifest.to_yaml()
