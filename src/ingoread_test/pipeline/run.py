"""One dataset, from config to release verdict.

`run` and `suite` are the same pipeline with different reporting: load the
dataset, send it through the integration, score it, persist and publish the
artifacts, then decide the gate. Both call :func:`execute_run`, so a step added
to a run is a step a suite member gets too — the two were written twice before
and had already drifted apart.

What stays with the caller is *policy*: how to report progress, and what to do
with a failure. `run` turns a bad config into a usage error and exits on the
gate; `suite` turns the same failure into one blocked member and keeps going.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from ..config.scorer_config import ScorerConfig
from ..config.test_config import TestConfig
from ..dataset import Dataset
from ..gate import compare_to_previous, evaluate_release_gate
from ..integration.factory import build_integration
from ..integration.runner import run_test
from ..reporting import JsonFileSink, RunArtifacts, read_result, render_html, upload_run
from ..results.models import ComparativeResult, MeasurementsResult
from ..scoring.aggregate import score
from .dataset import open_dataset

logger = logging.getLogger(__name__)


class EmptyDatasetError(ValueError):
    """Every sample is removed or excluded — there is nothing left to validate."""


@dataclass(frozen=True)
class RunRequest:
    """What to run, and what to do with the artifacts it produces."""

    test_cfg: TestConfig
    scorer_cfg: ScorerConfig
    exclude_sample_ids: Iterable[str] = ()
    force_download: bool = False
    # Prior result to gate against — a local path or an s3:// URI. None skips
    # the regression check entirely.
    baseline: str | None = None
    # Where the artifacts go; None stages them in a temp directory that is
    # discarded once they are uploaded (see RunArtifacts).
    local_dir: Path | None = None
    render_report: bool = True
    upload: bool = True
    # Used to name the results folder when the manifest doesn't name the dataset.
    dataset_name: str | None = None


@dataclass
class RunOutcome:
    """Everything a caller needs to report the run and act on its verdict."""

    dataset: Dataset
    result: MeasurementsResult
    comparison: ComparativeResult | None
    blocked: bool
    reasons: list[str]
    json_path: Path
    html_path: Path | None
    uploaded_uri: str | None = None
    upload_error: str | None = None
    discarded_dir: Path | None = None
    kept_local: bool = False


async def execute_run(
    request: RunRequest,
    *,
    on_dataset: Callable[[Dataset], None] | None = None,
    on_result: Callable[[MeasurementsResult], None] | None = None,
) -> RunOutcome:
    """Run one dataset end to end and return its outcome.

    The two callbacks exist so a long run can report progress as it happens —
    which dataset is about to be sent, and the metrics as soon as they are
    scored — without this module deciding how that progress is presented.

    Raises :class:`EmptyDatasetError` when nothing is left to send, and
    whatever loading the dataset or building the integration raises; both are
    the caller's to translate.
    """

    test_cfg = request.test_cfg
    dataset = open_dataset(
        test_cfg,
        exclude_sample_ids=request.exclude_sample_ids,
        force_download=request.force_download,
    )
    if on_dataset is not None:
        on_dataset(dataset)
    if not dataset.containers:
        raise EmptyDatasetError(
            f"dataset {dataset.name} has no samples left to run "
            f"({len(dataset.removed_sample_ids)} removed, "
            f"{len(dataset.excluded_sample_ids)} excluded)"
        )

    integration = build_integration(test_cfg)
    try:
        predictions, stats = await run_test(test_cfg, integration, dataset)
    finally:
        await integration.aclose()

    result = score(test_cfg, request.scorer_cfg, dataset, predictions, stats)
    if on_result is not None:
        on_result(result)

    previous, baseline_error = _load_baseline(request.baseline)
    artifacts = RunArtifacts(request.local_dir)
    json_path = JsonFileSink(artifacts.path).write(result)
    html_path = (
        render_html(
            result,
            artifacts.path,
            previous=previous,
            tolerance=test_cfg.history.match_rate_tolerance,
        )
        if request.render_report
        else None
    )

    uploaded_uri, upload_error = (
        _upload(test_cfg, dataset.name or request.dataset_name or "", result, html_path)
        if request.upload
        else (None, None)
    )
    discarded_dir = artifacts.discard(uploaded=uploaded_uri is not None)

    comparison = (
        compare_to_previous(result, previous, test_cfg.history) if previous is not None else None
    )
    blocked, reasons = evaluate_release_gate(result, comparison, test_cfg.history)
    if baseline_error:
        # Fail safe: a baseline was asked for, so silently skipping the
        # regression check would let the gate wave a build through unchecked.
        blocked = True
        reasons.append(baseline_error)

    return RunOutcome(
        dataset=dataset,
        result=result,
        comparison=comparison,
        blocked=blocked,
        reasons=reasons,
        json_path=json_path,
        html_path=html_path,
        uploaded_uri=uploaded_uri,
        upload_error=upload_error,
        discarded_dir=discarded_dir,
        kept_local=artifacts.keep,
    )


def _load_baseline(uri: str | None) -> tuple[MeasurementsResult | None, str | None]:
    """Read the baseline to gate against. Returns (baseline, error).

    A baseline that was asked for but can't be read is reported as an error
    rather than ignored, so the caller can block on it.
    """
    if not uri:
        return None, None
    try:
        return read_result(uri), None
    except (OSError, ValueError) as exc:
        return None, f"baseline {uri} could not be loaded: {exc}"


def _upload(
    test_cfg: TestConfig,
    dataset_name: str,
    result: MeasurementsResult,
    html_path: Path | None,
) -> tuple[str | None, str | None]:
    """Publish the run. Returns (folder uri, error) — a failure is not fatal.

    The local artifacts and the gate verdict stand on their own, so an
    unreachable bucket must not turn a green run red.
    """
    try:
        return upload_run(test_cfg, dataset_name, result, html_path=html_path), None
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never raised
        logger.error("results upload failed for dataset %s: %s", dataset_name, exc)
        return None, str(exc)
