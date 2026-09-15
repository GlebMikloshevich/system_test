"""Suite: grouping datasets behind one release decision."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from ingoread_test.config import load_suite
from ingoread_test.config.suite_config import DatasetRef, SuiteConfig
from ingoread_test.gate import evaluate_suite_gate
from ingoread_test.pipeline.suite import aggregate_suite, run_suite
from ingoread_test.results.models import DatasetOutcome, MeasurementsResult

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_CONFIG = REPO_ROOT / "tests" / "data" / "config.yaml"


def _result(match_rate: float, n: int = 4) -> MeasurementsResult:
    from ingoread_test.results.models import DocumentMeasurement

    return MeasurementsResult(
        test_config_name="t",
        scorer_config_name="s",
        start_date=datetime.now(UTC),
        total_time=1.0,
        total_samples=n,
        time_per_sample=0.25,
        match_rate=match_rate,
        document_results=[DocumentMeasurement(label="d", total_samples=n, match_rate=match_rate)],
    )


def _outcome(name, rate, blocking=True, blocked=False) -> DatasetOutcome:
    return DatasetOutcome(
        name=name,
        blocking=blocking,
        result=_result(rate),
        blocked=blocked,
        reasons=["metrics regressed"] if blocked else [],
    )


def _suite(outcomes, **kw) -> SuiteConfig:
    return SuiteConfig(name="s", datasets=[], **kw)


# ---- aggregation -----------------------------------------------------------


def test_macro_and_micro_match_rate():
    # dataset A: rate 1.0 over 2 docs; dataset B: rate 0.0 over 8 docs
    a = DatasetOutcome(name="a", result=_result(1.0, n=2))
    b = DatasetOutcome(name="b", result=_result(0.0, n=8))
    suite = aggregate_suite(SuiteConfig(name="s"), [a, b], datetime.now(UTC))
    assert suite.macro_match_rate == 0.5  # mean of 1.0 and 0.0
    assert suite.micro_match_rate == 0.2  # 2 matched of 10 pooled docs
    assert suite.total_samples == 10


# ---- gate ------------------------------------------------------------------


def test_gate_passes_when_all_blocking_pass():
    suite = aggregate_suite(
        SuiteConfig(name="s"), [_outcome("a", 1.0), _outcome("b", 1.0)], datetime.now(UTC)
    )
    blocked, reasons = evaluate_suite_gate(suite, SuiteConfig(name="s"))
    assert not blocked
    assert reasons == []


def test_gate_blocks_when_a_blocking_dataset_fails():
    suite = aggregate_suite(
        SuiteConfig(name="s"),
        [_outcome("a", 1.0), _outcome("b", 0.5, blocked=True)],
        datetime.now(UTC),
    )
    blocked, reasons = evaluate_suite_gate(suite, SuiteConfig(name="s"))
    assert blocked
    assert any("b:" in r for r in reasons)


def test_advisory_failure_does_not_block():
    suite = aggregate_suite(
        SuiteConfig(name="s"),
        [_outcome("a", 1.0), _outcome("exp", 0.2, blocking=False, blocked=True)],
        datetime.now(UTC),
    )
    blocked, reasons = evaluate_suite_gate(suite, SuiteConfig(name="s"))
    assert not blocked
    assert reasons == []


def test_macro_threshold_blocks():
    suite = aggregate_suite(SuiteConfig(name="s"), [_outcome("a", 0.80)], datetime.now(UTC))
    cfg = SuiteConfig(name="s", min_macro_match_rate=0.95)
    blocked, reasons = evaluate_suite_gate(suite, cfg)
    assert blocked
    assert any("macro match rate" in r for r in reasons)


# ---- loader ----------------------------------------------------------------


def test_load_suite_resolves_relative_paths(tmp_path):
    (tmp_path / "ds").mkdir()
    (tmp_path / "ds" / "config.yaml").write_text("test: {}\nscorer: {}\n")
    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text("name: s\ndatasets:\n  - name: a\n    config: ds/config.yaml\n")
    suite = load_suite(suite_file)
    assert suite.datasets[0].config == (tmp_path / "ds" / "config.yaml").resolve()


# ---- end to end ------------------------------------------------------------


def test_run_suite_end_to_end(tmp_path):
    suite_cfg = SuiteConfig(
        name="nightly",
        datasets=[
            DatasetRef(name="alpha", config=SMOKE_CONFIG),
            DatasetRef(name="beta", config=SMOKE_CONFIG, blocking=False),
        ],
    )
    suite = asyncio.run(run_suite(suite_cfg, tmp_path, no_viz=False))
    assert len(suite.datasets) == 2
    assert suite.macro_match_rate == 1.0
    assert all(not o.blocked for o in suite.datasets)
    # per-dataset reports were written into their own subdirs
    assert (tmp_path / "alpha").exists()
    assert suite.datasets[0].html_path and Path(suite.datasets[0].html_path).exists()

    blocked, reasons = evaluate_suite_gate(suite, suite_cfg)
    assert not blocked and reasons == []


def test_missing_baseline_blocks_instead_of_silently_skipping(tmp_path):
    suite_cfg = SuiteConfig(
        name="s",
        datasets=[
            DatasetRef(name="a", config=SMOKE_CONFIG, baseline=tmp_path / "gone.json"),
        ],
    )
    suite = asyncio.run(run_suite(suite_cfg, tmp_path, no_viz=True))
    outcome = suite.datasets[0]
    assert outcome.result is not None  # the dataset itself ran fine
    assert outcome.blocked
    assert any("baseline" in r for r in outcome.reasons)


def test_run_suite_isolates_a_broken_dataset(tmp_path):
    suite_cfg = SuiteConfig(
        name="s",
        datasets=[
            DatasetRef(name="good", config=SMOKE_CONFIG),
            DatasetRef(name="missing", config=tmp_path / "nope.yaml"),
        ],
    )
    suite = asyncio.run(run_suite(suite_cfg, tmp_path, no_viz=True))
    good, missing = suite.datasets
    assert not good.blocked
    assert missing.blocked and missing.error  # crashed dataset is isolated, not fatal
    blocked, _ = evaluate_suite_gate(suite, suite_cfg)
    assert blocked
