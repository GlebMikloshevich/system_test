"""Release gate: publish only if metrics are not worse AND there are no errors."""

from datetime import UTC, datetime

from ingoread_test.config.test_config import HistoryConfig
from ingoread_test.gate import compare_to_previous, evaluate_release_gate
from ingoread_test.results.models import (
    ComparativeStatus,
    DocumentMeasurement,
    MeasurementsResult,
)


def _result(match_rate=1.0, failed=0, timeouts=0, with_docs=True) -> MeasurementsResult:
    docs = (
        [DocumentMeasurement(label="invoice", total_samples=2, match_rate=match_rate)]
        if with_docs
        else []
    )
    return MeasurementsResult(
        test_config_name="t",
        scorer_config_name="s",
        start_date=datetime.now(UTC),
        total_time=1.0,
        total_samples=2,
        time_per_sample=0.5,
        match_rate=match_rate,
        failed=failed,
        timeouts=timeouts,
        document_results=docs,
    )


def test_gate_allows_clean_run_without_baseline():
    blocked, reasons = evaluate_release_gate(_result(), None, HistoryConfig())
    assert not blocked
    assert reasons == []


def test_gate_blocks_on_failures_even_without_baseline():
    blocked, reasons = evaluate_release_gate(_result(failed=1), None, HistoryConfig())
    assert blocked
    assert any("errors" in r for r in reasons)


def test_gate_blocks_on_timeouts():
    blocked, reasons = evaluate_release_gate(_result(timeouts=2), None, HistoryConfig())
    assert blocked
    assert any("timeouts=2" in r for r in reasons)


def test_gate_blocks_on_empty_scoring():
    blocked, reasons = evaluate_release_gate(_result(with_docs=False), None, HistoryConfig())
    assert blocked
    assert any("nothing to validate" in r for r in reasons)


def test_gate_blocks_on_regression():
    current = _result(match_rate=0.80)
    previous = _result(match_rate=0.95)
    comparison = compare_to_previous(current, previous, HistoryConfig())
    assert comparison.status == ComparativeStatus.DEGRADED
    blocked, reasons = evaluate_release_gate(current, comparison, HistoryConfig())
    assert blocked
    assert any("regressed" in r for r in reasons)


def test_gate_allows_equal_or_better_metrics():
    current = _result(match_rate=0.97)
    previous = _result(match_rate=0.95)
    comparison = compare_to_previous(current, previous, HistoryConfig())
    blocked, reasons = evaluate_release_gate(current, comparison, HistoryConfig())
    assert not blocked
    assert reasons == []


def test_vanished_label_counts_as_regression():
    # The hard "passport" docs disappear and the overall rate even improves —
    # the gate must still flag the vanished label as a regression.
    previous = _result(match_rate=0.90)
    previous.document_results.append(
        DocumentMeasurement(label="passport", total_samples=5, match_rate=0.85)
    )
    current = _result(match_rate=0.95)
    comparison = compare_to_previous(current, previous, HistoryConfig())
    assert comparison.status == ComparativeStatus.DEGRADED
    assert comparison.per_label_delta["passport"] == -0.85
    assert any("passport" in n and "missing" in n for n in comparison.notes)


def test_gate_switches_can_disable_conditions():
    cfg = HistoryConfig(fail_on_error=False, fail_on_empty=False)
    blocked, reasons = evaluate_release_gate(_result(failed=3, with_docs=False), None, cfg)
    assert not blocked
    assert reasons == []
