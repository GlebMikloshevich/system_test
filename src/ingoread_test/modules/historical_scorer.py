"""HistoricalScorerModule — compare current run to a previous MeasurementsResult."""

from __future__ import annotations

from ..config.test_config import HistoryConfig
from ..results.models import ComparativeResult, ComparativeStatus, MeasurementsResult


def compare_to_previous(
    current: MeasurementsResult,
    previous: MeasurementsResult,
    cfg: HistoryConfig,
) -> ComparativeResult:
    overall_delta = current.match_rate - previous.match_rate
    prev_by_label = {d.label: d for d in previous.document_results}
    per_label: dict[str, float] = {}
    notes: list[str] = []
    degraded = overall_delta < -cfg.match_rate_tolerance
    if degraded:
        notes.append(
            f"overall match_rate dropped by {-overall_delta:.3f} "
            f"(tolerance {cfg.match_rate_tolerance:.3f})"
        )

    for doc in current.document_results:
        prev = prev_by_label.get(doc.label)
        if prev is None:
            per_label[doc.label] = 0.0
            continue
        delta = doc.match_rate - prev.match_rate
        per_label[doc.label] = delta
        if delta < -cfg.match_rate_tolerance:
            degraded = True
            notes.append(
                f"{doc.label}: match_rate dropped by {-delta:.3f} "
                f"(tolerance {cfg.match_rate_tolerance:.3f})"
            )

    status = ComparativeStatus.DEGRADED if degraded else ComparativeStatus.OK
    return ComparativeResult(
        status=status,
        overall_delta=overall_delta,
        per_label_delta=per_label,
        notes=notes,
    )


def evaluate_release_gate(
    result: MeasurementsResult,
    comparison: ComparativeResult | None,
    cfg: HistoryConfig,
) -> tuple[bool, list[str]]:
    """Decide whether this run may be published to production.

    Returns ``(blocked, reasons)``. A release is allowed only when metrics are
    not worse than the baseline AND no document errored — so the gate blocks on
    any enabled condition, with or without a `--previous` baseline.
    """
    reasons: list[str] = []

    if cfg.fail_on_error and (result.failed or result.timeouts):
        reasons.append(
            f"errors present (failed={result.failed}, timeouts={result.timeouts})"
        )

    if cfg.fail_on_empty and not result.document_results:
        reasons.append("no documents were scored — nothing to validate")

    if (
        comparison is not None
        and cfg.fail_on_regression
        and comparison.status == ComparativeStatus.DEGRADED
    ):
        reasons.append(f"metrics regressed vs previous ({'; '.join(comparison.notes)})")

    return bool(reasons), reasons
