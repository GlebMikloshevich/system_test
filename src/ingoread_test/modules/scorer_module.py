"""ScorerModule — pair documents and aggregate metrics into MeasurementsResult.

Per-document-type aggregation is stickler's: every pair's `compare_with()`
output goes into `aggregate_from_comparisons`, which sums the confusion matrix
across the run and derives precision / recall / F1 / accuracy per field. On top
of that we keep the harness's own headline number, `match_rate` — the share of
documents where *every* configured field was right.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from stickler import aggregate_from_comparisons

from ..config.scorer_config import DocumentMeasurerConfig, FieldConfig, ScorerConfig
from ..config.test_config import TestConfig
from ..dataset.models import Dataset
from ..integration.schemas import IngoreadFileResult, IngoreadStatus
from ..results.models import (
    DocumentContainerPair,
    DocumentMeasurement,
    DocumentPair,
    FieldMeasurement,
    MeasurementsResult,
)
from ..scoring.models import scored_fields
from ..scoring.pairing import pair_documents
from .test_module import TestRunStats

logger = logging.getLogger(__name__)

# Aggregate metrics reported per field, straight out of stickler.
_REPORTED_METRICS = (
    "cm_precision",
    "cm_recall",
    "cm_f1",
    "cm_accuracy",
)


def _cfg_by_label(scorer_cfg: ScorerConfig) -> dict[str, DocumentMeasurerConfig]:
    return {c.doc_label: c for c in scorer_cfg.measurement_configs}


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _field_measurement(
    field_cfg: FieldConfig,
    pairs: list[DocumentPair],
    aggregate: dict,
) -> FieldMeasurement:
    entries = [
        entry
        for pair in pairs
        if (entry := pair.field_metrics.get(field_cfg.field_name)) is not None
    ]
    matches = [bool(entry.get("matched")) for entry in entries]
    stats = aggregate.get(field_cfg.field_name, {})
    metrics = {
        # Strip stickler's cm_ prefix — the report has no other precision.
        key.removeprefix("cm_"): float(stats[key])
        for key in _REPORTED_METRICS
        if key in stats
    }
    # Only the error cells, and only when they fired: tp/tn are implied by the
    # rates above, and a row of zeroes is noise in the report.
    metrics.update(
        {cell: float(stats[cell]) for cell in ("fd", "fn", "fa") if stats.get(cell)}
    )
    return FieldMeasurement(
        field_name=field_cfg.field_name,
        field_type=field_cfg.field_type,
        match_rate=_mean([float(m) for m in matches]),
        mean_score=float(stats.get("mean_score", _mean([e.get("score", 0.0) for e in entries]))),
        field_metrics=metrics,
    )


def _document_measurement(
    label: str,
    doc_cfg: DocumentMeasurerConfig,
    pairs: list[DocumentPair],
    test_stats: TestRunStats,
) -> DocumentMeasurement:
    comparisons = [p.comparison for p in pairs if p.comparison is not None]
    process = aggregate_from_comparisons(comparisons) if comparisons else None
    field_aggregate = process.field_metrics if process else {}
    matches = [p.matched for p in pairs]
    return DocumentMeasurement(
        label=label,
        total_samples=len(pairs),
        time=test_stats.total_time,
        time_per_sample=test_stats.time_per_sample,
        match_rate=_mean([float(m) for m in matches]),
        mean_score=float(
            (process.metrics or {}).get("weighted_overall_score", 0.0) if process else 0.0
        ),
        field_results=[
            _field_measurement(fc, pairs, field_aggregate) for fc in scored_fields(doc_cfg)
        ],
    )


def _warn_about_labels(
    cfg_map: dict[str, DocumentMeasurerConfig],
    seen_gt_labels: set[str],
    seen_pred_labels: set[str],
    empty_pred_files: list[str],
) -> None:
    cfg_labels = set(cfg_map)
    unconfigured_gt = seen_gt_labels - cfg_labels
    unconfigured_pred = seen_pred_labels - cfg_labels
    unused_cfg = cfg_labels - seen_gt_labels - seen_pred_labels
    if unconfigured_gt:
        logger.warning(
            "GT doc_labels with no matching scorer config (will not be scored): %s",
            sorted(unconfigured_gt),
        )
    if unconfigured_pred:
        logger.warning(
            "Predicted labels with no matching scorer config (will not be scored): %s. "
            "Scorer configs were registered for: %s",
            sorted(unconfigured_pred),
            sorted(cfg_labels),
        )
    if unused_cfg:
        logger.warning(
            "Scorer configs that never matched any GT or prediction: %s",
            sorted(unused_cfg),
        )
    if empty_pred_files:
        logger.warning(
            "%d file(s) returned status=COMPLETED with an EMPTY result list "
            "(no documents to score). Examples: %s. "
            "Likely cause: the API response body didn't expose its document list "
            "under the 'result' key — check HttpIngoreadIntegration's parsing "
            "in src/ingoread_test/integration/http.py against your real payload.",
            len(empty_pred_files),
            empty_pred_files[:5],
        )


def score(
    test_cfg: TestConfig,
    scorer_cfg: ScorerConfig,
    dataset: Dataset,
    predictions: dict[str, IngoreadFileResult],
    test_stats: TestRunStats,
) -> MeasurementsResult:
    cfg_map = _cfg_by_label(scorer_cfg)
    container_pairs: list[DocumentContainerPair] = []
    pairs_by_label: dict[str, list[DocumentPair]] = defaultdict(list)
    seen_gt_labels: set[str] = set()
    seen_pred_labels: set[str] = set()

    empty_pred_files: list[str] = []
    for container in dataset.containers:
        pred = predictions.get(container.filename)
        if pred is None:
            continue
        if not pred.result and pred.status != IngoreadStatus.FAILED:
            empty_pred_files.append(container.filename)
        file_pairs: list[DocumentPair] = []
        gt_labels = {g.doc_label for g in container.documents}
        pred_labels = {p.label for p in pred.result}
        seen_gt_labels |= gt_labels
        seen_pred_labels |= pred_labels
        for label in sorted(gt_labels | pred_labels):
            doc_cfg = cfg_map.get(label)
            if doc_cfg is None:
                continue
            sub_pairs = pair_documents(container.documents, pred.result, doc_cfg)
            file_pairs.extend(sub_pairs)
            pairs_by_label[label].extend(sub_pairs)
        container_pairs.append(
            DocumentContainerPair(
                filename=container.filename,
                gts=container,
                predictions=pred,
                document_pairs=file_pairs,
            )
        )

    _warn_about_labels(cfg_map, seen_gt_labels, seen_pred_labels, empty_pred_files)

    document_results = [
        _document_measurement(label, cfg_map[label], pairs, test_stats)
        for label, pairs in sorted(pairs_by_label.items())
    ]
    overall_matches = [p.matched for pairs in pairs_by_label.values() for p in pairs]
    all_comparisons = [
        p.comparison
        for pairs in pairs_by_label.values()
        for p in pairs
        if p.comparison is not None
    ]
    overall = aggregate_from_comparisons(all_comparisons) if all_comparisons else None

    return MeasurementsResult(
        test_config_name=test_cfg.name,
        scorer_config_name=scorer_cfg.name,
        start_date=datetime.now(UTC),
        total_time=test_stats.total_time,
        total_samples=test_stats.total_samples,
        time_per_sample=test_stats.time_per_sample,
        match_rate=_mean([float(m) for m in overall_matches]),
        mean_score=float(
            (overall.metrics or {}).get("weighted_overall_score", 0.0) if overall else 0.0
        ),
        timeouts=test_stats.timeouts,
        failed=test_stats.failed,
        document_results=document_results,
        container_pairs=container_pairs,
    )
