"""Score one (gt, prediction) document pair with stickler.

`compare_with` does all the per-field work: similarity per comparator, the
threshold decision, the weighted document score and a per-field confusion
matrix. This module only translates that into the report's `DocumentPair`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from stickler import StructuredModel

from ..config.scorer_config import DocumentMeasurerConfig, FieldConfig
from ..dataset.models import DocumentGT
from ..integration.schemas import IngoreadDocument
from ..results.models import DocumentPair
from .adapters import empty_model, gt_to_model, prediction_to_model
from .models import build_document_model, scored_fields

# Confusion-matrix cells that mean "this field did not come out right":
# fd = wrong value, fn = missing value, fa = value invented out of nothing.
_MISS_CELLS = ("fd", "fn", "fa")


def _field_cells(comparison: dict, field_name: str) -> dict[str, int]:
    cells = (
        comparison.get("confusion_matrix", {})
        .get("fields", {})
        .get(field_name, {})
        .get("overall", {})
    )
    return {k: int(cells.get(k, 0)) for k in ("tp", "tn", "fd", "fn", "fa")}


def _field_metrics(comparison: dict, cfg_fields: list[FieldConfig]) -> dict[str, dict]:
    scores = comparison.get("field_scores", {})
    metrics: dict[str, dict] = {}
    for fc in cfg_fields:
        cells = _field_cells(comparison, fc.field_name)
        metrics[fc.field_name] = {
            "matched": not any(cells[c] for c in _MISS_CELLS),
            "score": float(scores.get(fc.field_name, 0.0)),
            "weight": fc.weight,
            **cells,
        }
    return metrics


def _apply_groups(
    field_metrics: dict[str, dict], cfg_fields: list[FieldConfig]
) -> tuple[bool, float]:
    """Fold `field_group` members together: a group matches when any member does.

    Returns (document matched, fraction of independent checks that matched).
    """
    groups: dict[str, list[bool]] = defaultdict(list)
    checks: list[bool] = []
    for fc in cfg_fields:
        matched = bool(field_metrics[fc.field_name]["matched"])
        if fc.field_group:
            groups[fc.field_group].append(matched)
        else:
            checks.append(matched)
    for name, members in groups.items():
        field_metrics[f"__group__{name}"] = {"matched": any(members)}
        checks.append(any(members))
    if not checks:
        return True, 1.0
    return all(checks), sum(checks) / len(checks)


def build_pair(
    gt: DocumentGT | None,
    prediction: IngoreadDocument | None,
    cfg_fields: list[FieldConfig],
    comparison: dict,
    *,
    forced_miss: bool,
) -> DocumentPair:
    field_metrics = _field_metrics(comparison, cfg_fields)
    matched, fraction = _apply_groups(field_metrics, cfg_fields)
    if forced_miss:
        # One side of the pair is absent, so the document is a miss whatever the
        # fields say (an all-empty gt would otherwise "match" an empty answer).
        matched = False
    return DocumentPair(
        gt=gt,
        prediction=prediction,
        matched=matched,
        field_metrics=field_metrics,
        document_param_metrics={
            "score": float(comparison.get("overall_score", 0.0)),
            "all_fields_matched": bool(comparison.get("all_fields_matched", False)),
            "fraction_fields_matched": fraction,
        },
        comparison=comparison,
    )


def compare_models(gt_model: StructuredModel, pred_model: StructuredModel) -> dict[str, Any]:
    """Run stickler's comparison over two already-built document models."""
    return gt_model.compare_with(
        pred_model,
        include_confusion_matrix=True,
        document_non_matches=True,
    )


def score_document_pair(
    gt: DocumentGT | None,
    prediction: IngoreadDocument | None,
    cfg: DocumentMeasurerConfig,
) -> DocumentPair:
    """Compute per-field scores. Handles unmatched (gt-only / pred-only) pairs.

    A half pair is still scored — against an all-missing counterpart — so its
    fields land in the aggregate as false negatives (a document we missed) or
    false alarms (one we invented) instead of silently vanishing.
    """
    if gt is None and prediction is None:
        return DocumentPair(matched=False)

    cfg_fields = scored_fields(cfg)
    model = build_document_model(cfg)
    gt_model = gt_to_model(gt, cfg_fields, model) if gt else empty_model(cfg_fields, model)
    pred_model = (
        prediction_to_model(prediction, cfg_fields, model)
        if prediction
        else empty_model(cfg_fields, model)
    )
    return build_pair(
        gt,
        prediction,
        cfg_fields,
        compare_models(gt_model, pred_model),
        forced_miss=gt is None or prediction is None,
    )
