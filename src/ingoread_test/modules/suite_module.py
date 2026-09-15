"""SuiteModule — run several datasets and decide release across all of them."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from ..config import DatasetRef, SuiteConfig, load_configs
from ..config.test_config import TestConfig
from ..integration.factory import build_integration
from ..results.models import (
    DatasetOutcome,
    MeasurementsResult,
    SuiteResult,
)
from .dataset_module import open_dataset
from .historical_scorer import compare_to_previous, evaluate_release_gate
from .logger_module import JsonFileSink, upload_run
from .scorer_module import score
from .test_module import run_test
from .visualization_module import render_html

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "dataset"


def _doc_pairs(result: MeasurementsResult) -> int:
    return sum(d.total_samples for d in result.document_results)


async def _run_dataset(ref: DatasetRef, results_dir: Path, no_viz: bool) -> DatasetOutcome:
    try:
        test_cfg, scorer_cfg = load_configs(ref.config)
        dataset = open_dataset(test_cfg)
        integration = build_integration(test_cfg)
        try:
            predictions, stats = await run_test(test_cfg, integration, dataset)
        finally:
            await integration.aclose()
        result = score(test_cfg, scorer_cfg, dataset, predictions, stats)
    except Exception as exc:  # noqa: BLE001 — a broken dataset shouldn't kill the suite
        logger.exception("dataset %s failed to run", ref.name)
        return DatasetOutcome(
            name=ref.name,
            blocking=ref.blocking,
            blocked=True,
            reasons=[f"run failed: {exc}"],
            error=str(exc),
        )

    previous, baseline_error = _load_baseline(ref)
    comparison = compare_to_previous(result, previous, test_cfg.history) if previous else None
    blocked, reasons = evaluate_release_gate(result, comparison, test_cfg.history)
    if baseline_error:
        # Fail safe: a baseline was configured, so silently skipping the
        # regression check would let the gate wave a build through unchecked.
        blocked = True
        reasons.append(baseline_error)

    dataset_dir = results_dir / _slug(ref.name)
    json_path = JsonFileSink(dataset_dir).write(result)
    html_path = None
    if not no_viz:
        html_path = render_html(
            result,
            dataset_dir,
            previous=previous,
            tolerance=test_cfg.history.match_rate_tolerance,
        )
    _upload(test_cfg, dataset.name or ref.name, result, html_path)
    return DatasetOutcome(
        name=ref.name,
        blocking=ref.blocking,
        result=result,
        comparison=comparison,
        blocked=blocked,
        reasons=reasons,
        json_path=str(json_path.resolve()),
        html_path=str(html_path.resolve()) if html_path else None,
    )


def _upload(
    test_cfg: TestConfig,
    dataset_name: str,
    result: MeasurementsResult,
    html_path: Path | None,
) -> None:
    """Publish one member's run. A failed upload must not change the suite gate."""
    try:
        upload_run(test_cfg, dataset_name, result, html_path=html_path)
    except Exception as exc:  # noqa: BLE001 - the local artifacts and the verdict still stand
        logger.error("results upload failed for dataset %s: %s", dataset_name, exc)


def _load_baseline(ref: DatasetRef) -> tuple[MeasurementsResult | None, str | None]:
    """Load the dataset's baseline result. Returns (baseline, error).

    A configured baseline that can't be read or parsed is reported as an error
    rather than ignored — the caller blocks the release on it.
    """
    if not ref.baseline:
        return None, None
    try:
        return (
            MeasurementsResult.model_validate_json(Path(ref.baseline).read_text(encoding="utf-8")),
            None,
        )
    except (OSError, ValueError) as exc:
        return None, f"baseline {ref.baseline} could not be loaded: {exc}"


def aggregate_suite(
    suite_cfg: SuiteConfig, outcomes: list[DatasetOutcome], start_date: datetime
) -> SuiteResult:
    scored = [o for o in outcomes if o.result is not None]
    macro = sum(o.result.match_rate for o in scored) / len(scored) if scored else 0.0
    total_pairs = sum(_doc_pairs(o.result) for o in scored)
    matched = sum(o.result.match_rate * _doc_pairs(o.result) for o in scored)
    micro = matched / total_pairs if total_pairs else 0.0
    return SuiteResult(
        suite_name=suite_cfg.name,
        start_date=start_date,
        datasets=outcomes,
        macro_match_rate=macro,
        micro_match_rate=micro,
        total_samples=total_pairs,
        n_passed=sum(1 for o in outcomes if not o.blocked),
        n_blocked=sum(1 for o in outcomes if o.blocked),
    )


def evaluate_suite_gate(
    suite_result: SuiteResult, suite_cfg: SuiteConfig
) -> tuple[bool, list[str]]:
    """Release is allowed only if every *blocking* dataset passed (and any
    optional suite-level macro threshold is met)."""
    reasons: list[str] = []
    for o in suite_result.datasets:
        if o.blocking and o.blocked:
            why = "; ".join(o.reasons) or "blocked"
            reasons.append(f"{o.name}: {why}")
    if (
        suite_cfg.min_macro_match_rate is not None
        and suite_result.macro_match_rate < suite_cfg.min_macro_match_rate
    ):
        reasons.append(
            f"macro match rate {suite_result.macro_match_rate:.3f} "
            f"< required {suite_cfg.min_macro_match_rate:.3f}"
        )
    return bool(reasons), reasons


async def run_suite(suite_cfg: SuiteConfig, results_dir: Path, no_viz: bool = False) -> SuiteResult:
    start = datetime.now(UTC)
    outcomes: list[DatasetOutcome] = []
    for ref in suite_cfg.datasets:
        outcomes.append(await _run_dataset(ref, results_dir, no_viz))
    return aggregate_suite(suite_cfg, outcomes, start)
