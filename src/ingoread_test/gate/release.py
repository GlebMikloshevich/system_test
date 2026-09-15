"""Release policy — decide whether a run, or a whole suite, may be published.

This is the system's reason for existing: every other layer produces evidence,
and these two functions turn that evidence into the exit code that blocks or
allows a release. They are deliberately pure — given a result and a config they
return `(blocked, reasons)` and touch nothing else — so the policy can be read,
tested and changed without going near I/O.
"""

from __future__ import annotations

from ..config.suite_config import SuiteConfig
from ..config.test_config import HistoryConfig
from ..results.models import (
    ComparativeResult,
    ComparativeStatus,
    MeasurementsResult,
    SuiteResult,
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
        reasons.append(f"errors present (failed={result.failed}, timeouts={result.timeouts})")

    if cfg.fail_on_empty and not result.document_results:
        reasons.append("no documents were scored — nothing to validate")

    if (
        comparison is not None
        and cfg.fail_on_regression
        and comparison.status == ComparativeStatus.DEGRADED
    ):
        reasons.append(f"metrics regressed vs previous ({'; '.join(comparison.notes)})")

    return bool(reasons), reasons


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
