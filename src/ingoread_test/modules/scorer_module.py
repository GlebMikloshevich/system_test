"""ScorerModule — pair documents and aggregate metrics into MeasurementsResult."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime

from ..config.scorer_config import DocumentMeasurerConfig, FieldType, ScorerConfig
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
from ..scoring.pairing import pair_documents
from .test_module import TestRunStats

logger = logging.getLogger(__name__)


def _cfg_by_label(scorer_cfg: ScorerConfig) -> dict[str, DocumentMeasurerConfig]:
    return {c.doc_label: c for c in scorer_cfg.measurement_configs}


def _aggregate_field_metric(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _field_measurement(
    field_name: str,
    field_type: FieldType,
    pairs: list[DocumentPair],
) -> FieldMeasurement:
    metrics_by_key: dict[str, list[float]] = defaultdict(list)
    matches: list[bool] = []
    for pair in pairs:
        entry = pair.field_metrics.get(field_name)
        if not entry:
            continue
        matches.append(bool(entry.get("matched")))
        for k, v in entry.items():
            if k == "matched":
                continue
            if isinstance(v, (int, float)) and v != float("inf"):
                metrics_by_key[k].append(float(v))
    return FieldMeasurement(
        field_name=field_name,
        field_type=field_type,
        match_rate=(sum(matches) / len(matches)) if matches else 0.0,
        field_metrics={k: _aggregate_field_metric(v) for k, v in metrics_by_key.items()},
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
        labels = gt_labels | pred_labels
        for label in sorted(labels):
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

    document_results: list[DocumentMeasurement] = []
    overall_matches: list[bool] = []
    for label, pairs in sorted(pairs_by_label.items()):
        doc_cfg = cfg_map[label]
        field_results: list[FieldMeasurement] = [
            _field_measurement(fc.field_name, fc.field_type, pairs)
            for fc in doc_cfg.fields
            if not fc.ignore
        ]
        matches = [p.matched for p in pairs]
        overall_matches.extend(matches)
        document_results.append(
            DocumentMeasurement(
                label=label,
                total_samples=len(pairs),
                time=test_stats.total_time,
                time_per_sample=test_stats.time_per_sample,
                match_rate=(sum(matches) / len(matches)) if matches else 0.0,
                field_results=field_results,
            )
        )

    return MeasurementsResult(
        test_config_name=test_cfg.name,
        scorer_config_name=scorer_cfg.name,
        start_date=datetime.now(UTC),
        total_time=test_stats.total_time,
        total_samples=test_stats.total_samples,
        time_per_sample=test_stats.time_per_sample,
        match_rate=(sum(overall_matches) / len(overall_matches)) if overall_matches else 0.0,
        timeouts=test_stats.timeouts,
        failed=test_stats.failed,
        document_results=document_results,
        container_pairs=container_pairs,
    )
