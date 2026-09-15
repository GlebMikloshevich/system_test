"""Run several datasets through the shared pipeline and collect their outcomes."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from ..config import DatasetRef, SuiteConfig, load_configs
from ..results.models import (
    DatasetOutcome,
    MeasurementsResult,
    SuiteResult,
)
from .run import RunRequest, execute_run

logger = logging.getLogger(__name__)


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "dataset"


def _doc_pairs(result: MeasurementsResult) -> int:
    return sum(d.total_samples for d in result.document_results)


async def _run_dataset(ref: DatasetRef, results_dir: Path, no_viz: bool) -> DatasetOutcome:
    """Run one member through the shared pipeline and record how it went.

    A member that cannot run at all is reported as blocked rather than raised:
    one broken dataset must not take the rest of the suite down with it.
    """
    try:
        test_cfg, scorer_cfg = load_configs(ref.config)
        outcome = await execute_run(
            RunRequest(
                test_cfg=test_cfg,
                scorer_cfg=scorer_cfg,
                baseline=str(ref.baseline) if ref.baseline else None,
                # Members keep their artifacts: the suite report links them.
                local_dir=results_dir / _slug(ref.name),
                render_report=not no_viz,
                dataset_name=ref.name,
            )
        )
    except Exception as exc:  # a broken dataset shouldn't kill the rest of the suite
        logger.exception("dataset %s failed to run", ref.name)
        return DatasetOutcome(
            name=ref.name,
            blocking=ref.blocking,
            blocked=True,
            reasons=[f"run failed: {exc}"],
            error=str(exc),
        )

    return DatasetOutcome(
        name=ref.name,
        blocking=ref.blocking,
        result=outcome.result,
        comparison=outcome.comparison,
        blocked=outcome.blocked,
        reasons=outcome.reasons,
        json_path=str(outcome.json_path.resolve()),
        html_path=str(outcome.html_path.resolve()) if outcome.html_path else None,
    )


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


async def run_suite(suite_cfg: SuiteConfig, results_dir: Path, no_viz: bool = False) -> SuiteResult:
    start = datetime.now(UTC)
    outcomes: list[DatasetOutcome] = []
    for ref in suite_cfg.datasets:
        outcomes.append(await _run_dataset(ref, results_dir, no_viz))
    return aggregate_suite(suite_cfg, outcomes, start)
