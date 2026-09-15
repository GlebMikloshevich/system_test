"""Compare a run against its baseline, per document label and overall."""

from __future__ import annotations

from ..config.test_config import HistoryConfig
from ..results.models import ComparativeResult, ComparativeStatus, MeasurementsResult


def compare_to_previous(
    current: MeasurementsResult,
    previous: MeasurementsResult,
    cfg: HistoryConfig,
) -> ComparativeResult:
    """Diff a run against its baseline, per document label and overall.

    Labels are walked as the *union* of both runs, not just the current one: a
    document type that disappears entirely (the model stopped producing it, or
    all of its documents errored out) is a regression even though it has no
    current row. Dropping hard samples can otherwise lift the overall match
    rate and wave a genuinely worse build through the gate.
    """
    overall_delta = current.match_rate - previous.match_rate
    prev_by_label = {d.label: d for d in previous.document_results}
    curr_by_label = {d.label: d for d in current.document_results}

    per_label: dict[str, float] = {}
    notes: list[str] = []
    degraded = overall_delta < -cfg.match_rate_tolerance
    if degraded:
        notes.append(
            f"overall match_rate dropped by {-overall_delta:.3f} "
            f"(tolerance {cfg.match_rate_tolerance:.3f})"
        )

    for label in sorted(set(prev_by_label) | set(curr_by_label)):
        prev = prev_by_label.get(label)
        curr = curr_by_label.get(label)

        if prev is None:
            # New label — nothing to compare against, so it can't regress.
            per_label[label] = 0.0
            continue

        if curr is None:
            # Scored in the baseline, absent now: count its whole rate as lost.
            per_label[label] = -prev.match_rate
            degraded = True
            notes.append(
                f"{label}: missing from this run "
                f"(baseline scored {prev.total_samples} sample(s) at "
                f"match_rate {prev.match_rate:.3f})"
            )
            continue

        delta = curr.match_rate - prev.match_rate
        per_label[label] = delta
        if delta < -cfg.match_rate_tolerance:
            degraded = True
            notes.append(
                f"{label}: match_rate dropped by {-delta:.3f} "
                f"(tolerance {cfg.match_rate_tolerance:.3f})"
            )

    return ComparativeResult(
        status=ComparativeStatus.DEGRADED if degraded else ComparativeStatus.OK,
        overall_delta=overall_delta,
        per_label_delta=per_label,
        notes=notes,
    )
