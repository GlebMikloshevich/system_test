"""The `dataset` commands and the run flags that depend on them."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

MANIFEST = """
version: 2
name: invoices
samples:
  - sample_id: inv-0001
    filename: invoice_001.pdf
    documents:
      - doc_label: invoice
        fields:
          total: {gt_value: "123.45"}
  - sample_id: inv-0002
    filename: invoice_002.pdf
    documents:
      - doc_label: invoice
        fields:
          total: {gt_value: "10.00"}
"""

CONFIG = """
test:
  name: invoices
  dataset:
    uri: {root}
  batch_size: 2
  integration:
    kind: stub
scorer:
  name: default
  measurement_configs:
    - doc_label: invoice
      fields:
        - field_name: total
          field_type: number
"""


def _cli(*args: str) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ingoread_test.cli", *args]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)


@pytest.fixture
def dataset_root(tmp_path) -> Path:
    root = tmp_path / "invoices"
    root.mkdir()
    (root / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return root


@pytest.fixture
def config_path(tmp_path, dataset_root) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG.format(root=dataset_root), encoding="utf-8")
    return path


def _samples(dataset_root: Path) -> dict[str, dict]:
    stored = yaml.safe_load((dataset_root / "manifest.yaml").read_text(encoding="utf-8"))
    return {sample["sample_id"]: sample for sample in stored["samples"]}


def test_dataset_list_shows_ids_and_state(dataset_root):
    proc = _cli("dataset", "list", str(dataset_root))

    assert proc.returncode == 0, proc.stderr
    assert "samples=2 active=2 removed=0" in proc.stdout
    assert "active  inv-0001  invoice_001.pdf" in proc.stdout


def test_dataset_remove_then_run_skips_the_sample(dataset_root, config_path, tmp_path):
    removal = _cli(
        "dataset", "remove", str(dataset_root), "inv-0002", "--reason", "customer request"
    )
    assert removal.returncode == 0, removal.stderr
    assert "removed=inv-0002" in removal.stdout

    stored = _samples(dataset_root)
    assert stored["inv-0002"]["removed"] is True
    assert stored["inv-0002"]["removal"]["reason"] == "customer request"

    run = _cli("run", str(config_path), "--results-dir", str(tmp_path / "out"), "--no-viz")
    assert run.returncode == 0, run.stderr
    assert "samples=1 removed=1" in run.stdout
    assert "total_samples=1" in run.stdout


def test_dataset_remove_dry_run_writes_nothing(dataset_root):
    proc = _cli("dataset", "remove", str(dataset_root), "inv-0001", "--dry-run")

    assert proc.returncode == 0, proc.stderr
    assert "manifest_not_written=" in proc.stdout
    assert _samples(dataset_root)["inv-0001"].get("removed", False) is False


def test_dataset_remove_reports_unknown_selectors_and_exits_nonzero(dataset_root):
    proc = _cli("dataset", "remove", str(dataset_root), "nope")

    assert proc.returncode == 1
    assert "not_found=nope" in proc.stderr


def test_dataset_restore_puts_a_sample_back(dataset_root):
    _cli("dataset", "remove", str(dataset_root), "inv-0001")

    proc = _cli("dataset", "restore", str(dataset_root), "inv-0001")

    assert proc.returncode == 0, proc.stderr
    assert "restored=inv-0001" in proc.stdout
    assert _samples(dataset_root)["inv-0001"].get("removed", False) is False


def test_purge_needs_confirmation_and_drops_the_entry(dataset_root):
    declined = _cli("dataset", "remove", str(dataset_root), "inv-0001", "--purge")
    assert declined.returncode != 0
    assert "inv-0001" in _samples(dataset_root)

    proc = _cli("dataset", "remove", str(dataset_root), "inv-0001", "--purge", "--yes")

    assert proc.returncode == 0, proc.stderr
    assert "purged=inv-0001" in proc.stdout
    assert "inv-0001" not in _samples(dataset_root)


def test_run_exclude_skips_a_sample_for_one_run_only(config_path, dataset_root, tmp_path):
    proc = _cli(
        "run",
        str(config_path),
        "--results-dir",
        str(tmp_path / "out"),
        "--no-viz",
        "--exclude",
        "inv-0001",
    )

    assert proc.returncode == 0, proc.stderr
    assert "samples=1 removed=0 excluded=1" in proc.stdout
    assert _samples(dataset_root)["inv-0001"].get("removed", False) is False


def test_run_fails_when_every_sample_is_removed(config_path, dataset_root, tmp_path):
    _cli("dataset", "remove", str(dataset_root), "inv-0001", "inv-0002")

    proc = _cli("run", str(config_path), "--results-dir", str(tmp_path / "out"), "--no-viz")

    assert proc.returncode == 1
    assert "no samples left to run" in proc.stderr


def test_dataset_upload_dry_run_validates_without_uploading(dataset_root):
    proc = _cli("dataset", "upload", str(dataset_root), "s3://bucket/x", "--dry-run")

    assert proc.returncode == 0, proc.stderr
    assert "dataset=invoices samples=2" in proc.stdout
    assert "dry_run=1 not_uploaded=s3://bucket/x" in proc.stdout


def test_dataset_upload_refuses_a_dataset_with_a_bad_id_file(dataset_root):
    """A dataset that would fail at run time must not reach S3."""
    (dataset_root / "invoice_001.id").write_text("wrong-id", encoding="utf-8")

    proc = _cli("dataset", "upload", str(dataset_root), "s3://bucket/x", "--dry-run")

    assert proc.returncode != 0
    assert "Sample id mismatch" in proc.stderr
