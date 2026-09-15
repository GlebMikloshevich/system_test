"""`run` and `suite` share one pipeline, so they share its behaviour."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ingoread_test import cli
from ingoread_test.config.loader import load_configs
from ingoread_test.config.suite_config import DatasetRef, SuiteConfig
from ingoread_test.modules import EmptyDatasetError, RunRequest, execute_run
from ingoread_test.modules.suite_module import run_suite

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

CONFIG = {
    "test": {
        "name": "invoices",
        "dataset": {"uri": None},
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


@pytest.fixture
def dataset_root(tmp_path) -> Path:
    root = tmp_path / "invoices"
    root.mkdir()
    (root / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return root


@pytest.fixture
def config_path(tmp_path, dataset_root) -> Path:
    config = dict(CONFIG)
    config["test"] = {**CONFIG["test"], "dataset": {"uri": str(dataset_root)}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _request(config_path: Path, tmp_path: Path, **overrides) -> RunRequest:
    test_cfg, scorer_cfg = load_configs(config_path)
    return RunRequest(
        test_cfg=test_cfg,
        scorer_cfg=scorer_cfg,
        local_dir=tmp_path / "out",
        render_report=False,
        **overrides,
    )


async def test_pipeline_runs_scores_and_gates(config_path, tmp_path):
    outcome = await execute_run(_request(config_path, tmp_path))

    assert outcome.result.match_rate == 1.0
    assert outcome.blocked is False
    assert outcome.json_path.is_file()
    assert outcome.html_path is None
    assert outcome.dataset.name == "invoices"


async def test_pipeline_reports_progress_before_publishing(config_path, tmp_path):
    """The callbacks let a long run print as it goes, not only at the end."""
    seen: list[str] = []

    await execute_run(
        _request(config_path, tmp_path),
        on_dataset=lambda dataset: seen.append(f"dataset:{dataset.name}"),
        on_result=lambda result: seen.append(f"result:{result.match_rate}"),
    )

    assert seen == ["dataset:invoices", "result:1.0"]


async def test_pipeline_refuses_a_dataset_with_nothing_to_send(config_path, tmp_path, dataset_root):
    manifest = yaml.safe_load((dataset_root / "manifest.yaml").read_text(encoding="utf-8"))
    manifest["samples"][0]["removed"] = True
    (dataset_root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    with pytest.raises(EmptyDatasetError, match="no samples left to run"):
        await execute_run(_request(config_path, tmp_path))


async def test_an_unreadable_baseline_blocks_the_release(config_path, tmp_path):
    """Fail safe: a baseline was asked for, so skipping the check can't pass a build."""
    outcome = await execute_run(
        _request(config_path, tmp_path, baseline=str(tmp_path / "missing.json"))
    )

    assert outcome.blocked is True
    assert any("could not be loaded" in reason for reason in outcome.reasons)
    assert outcome.result.match_rate == 1.0, "the run itself still produced metrics"


def test_cli_reports_an_unreadable_baseline_instead_of_crashing(config_path, tmp_path):
    result = runner.invoke(
        cli.app,
        [
            "run",
            str(config_path),
            "--results-dir",
            str(tmp_path / "out"),
            "--no-viz",
            "--previous",
            str(tmp_path / "missing.json"),
        ],
    )

    assert result.exit_code == 1
    assert "gate_block=baseline" in result.output
    assert "release_gate=BLOCKED" in result.output


def test_a_suite_member_with_nothing_to_send_is_blocked_not_fatal(
    config_path, dataset_root, tmp_path
):
    """The suite inherits the pipeline's empty-dataset rule, one member at a time."""
    manifest = yaml.safe_load((dataset_root / "manifest.yaml").read_text(encoding="utf-8"))
    manifest["samples"][0]["removed"] = True
    (dataset_root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    suite_cfg = SuiteConfig(
        name="nightly", datasets=[DatasetRef(name="invoices", config=config_path)]
    )

    suite_result = asyncio.run(run_suite(suite_cfg, tmp_path / "suite", no_viz=True))

    outcome = suite_result.datasets[0]
    assert outcome.blocked is True
    assert "no samples left to run" in " ".join(outcome.reasons)


def test_a_suite_member_keeps_its_artifacts(config_path, tmp_path):
    """Members are written under the suite's results dir — the report links them."""
    suite_cfg = SuiteConfig(
        name="nightly", datasets=[DatasetRef(name="invoices", config=config_path)]
    )

    suite_result = asyncio.run(run_suite(suite_cfg, tmp_path / "suite", no_viz=True))

    outcome = suite_result.datasets[0]
    assert outcome.blocked is False
    assert Path(outcome.json_path).is_file()
