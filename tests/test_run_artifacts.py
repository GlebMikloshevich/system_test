"""The local copy of a run is staging space: kept only when it must be."""

from __future__ import annotations

from pathlib import Path

import yaml
from typer.testing import CliRunner

from ingoread_test import cli
from ingoread_test.modules import run_module
from ingoread_test.modules.logger_module import RunArtifacts

runner = CliRunner()

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
"""


def _config(tmp_path: Path, **results: object) -> Path:
    dataset = tmp_path / "invoices"
    dataset.mkdir()
    (dataset / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "test": {
                    "name": "invoices",
                    "dataset": {"uri": str(dataset)},
                    "results": results,
                    "integration": {"kind": "stub"},
                },
                "scorer": {
                    "name": "default",
                    "measurement_configs": [
                        {
                            "doc_label": "invoice",
                            "fields": [{"field_name": "total", "field_type": "number"}],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    return config


def test_a_temporary_copy_is_discarded_once_it_is_uploaded():
    artifacts = RunArtifacts(None)
    (artifacts.path / "result.json").write_text("{}", encoding="utf-8")

    discarded = artifacts.discard(uploaded=True)

    assert discarded == artifacts.path
    assert not artifacts.path.exists()


def test_a_temporary_copy_survives_when_the_run_was_not_uploaded():
    """Discarding an unpublished run would leave no copy of it at all."""
    artifacts = RunArtifacts(None)

    assert artifacts.discard(uploaded=False) is None
    assert artifacts.path.is_dir()


def test_a_requested_directory_is_never_discarded(tmp_path):
    artifacts = RunArtifacts(tmp_path / "results")

    assert artifacts.keep is True
    assert artifacts.discard(uploaded=True) is None
    assert artifacts.path.is_dir()


def test_run_removes_the_local_copy_after_uploading(tmp_path, monkeypatch):
    uploaded: dict[str, Path | None] = {}

    def fake_upload(test_cfg, dataset_name, result, *, html_path=None):
        uploaded["html_path"] = html_path
        assert html_path is not None and html_path.is_file(), "html must exist to be uploaded"
        return f"s3://ingoread-results/runs/{dataset_name}/ingoread/2026-09-15/120000"

    monkeypatch.setattr(run_module, "upload_run", fake_upload)
    config = _config(tmp_path, uri="s3://ingoread-results/runs")

    result = runner.invoke(cli.app, ["run", str(config)])

    assert result.exit_code == 0, result.output
    assert "results_uploaded=s3://ingoread-results/runs/invoices/" in result.output
    staged = _discarded_dir(result.output)
    assert not staged.exists()
    assert "results_json=" not in result.output


def test_run_keeps_the_local_copy_when_a_directory_was_asked_for(tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "upload_run", lambda *args, **kwargs: "s3://runs/x")
    config = _config(tmp_path, uri="s3://ingoread-results/runs")
    out_dir = tmp_path / "keep"

    result = runner.invoke(cli.app, ["run", str(config), "--results-dir", str(out_dir), "--no-viz"])

    assert result.exit_code == 0, result.output
    assert "results_local_discarded" not in result.output
    assert list(out_dir.glob("*.json"))


def test_run_keeps_the_local_copy_when_the_upload_fails(tmp_path, monkeypatch):
    def failing_upload(*args, **kwargs):
        raise RuntimeError("bucket unreachable")

    monkeypatch.setattr(run_module, "upload_run", failing_upload)
    config = _config(tmp_path, uri="s3://ingoread-results/runs")

    result = runner.invoke(cli.app, ["run", str(config), "--no-viz"])

    assert result.exit_code == 0, result.output
    assert "results upload failed: bucket unreachable" in result.output
    assert "the run was not uploaded" in result.output
    assert Path(result.output.split("results_json=")[1].splitlines()[0]).is_file()


def _discarded_dir(output: str) -> Path:
    line = next(li for li in output.splitlines() if li.startswith("results_local_discarded="))
    return Path(line.split("=", 1)[1])
