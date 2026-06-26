"""Per-field scoring functions matching the diagram's FieldScorer block."""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jiwer
import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config.scorer_config import FieldConfig, FieldType, PredictionSelection
from ..dataset.models import gt_to_boxes, gt_to_text
from ..integration.schemas import IngoreadField


@dataclass
class FieldScoreResult:
    matched: bool
    metrics: dict


def _normalize_text(value: str, cfg: FieldConfig) -> str:
    """Apply optional text normalization before exact comparison.

    Off by default (exact match). Enable per-field via measurer_kwargs:
        measurer_kwargs: {strip: true, casefold: true}
    """
    if cfg.measurer_kwargs.get("strip"):
        value = value.strip()
    if cfg.measurer_kwargs.get("casefold"):
        value = value.casefold()
    return value


def select_prediction(
    predictions: list[IngoreadField], cfg: FieldConfig
) -> list[IngoreadField]:
    """Apply the configured selection strategy. v1 implements FIRST."""
    if cfg.selection == PredictionSelection.FIRST:
        return predictions[:1]
    if cfg.selection in (PredictionSelection.TOP_N, PredictionSelection.ALL):
        raise NotImplementedError(
            f"Field {cfg.field_name!r} requested selection={cfg.selection.value!r}, "
            "but v1 only implements 'first'. Set selection: first (or take_first: true)."
        )
    raise ValueError(f"Unknown selection: {cfg.selection}")


def _text_score(gt: Any, predictions: list[IngoreadField], cfg: FieldConfig) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    pred = (chosen[0].text or "") if chosen else ""
    gt = _normalize_text(gt_to_text(gt), cfg)
    pred = _normalize_text(pred, cfg)
    if not gt and not pred:
        return FieldScoreResult(matched=True, metrics={"cer": 0.0, "wer": 0.0})
    cer = float(jiwer.cer(gt, pred)) if gt else (0.0 if not pred else 1.0)
    wer = float(jiwer.wer(gt, pred)) if gt else (0.0 if not pred else 1.0)
    matched = pred == gt
    return FieldScoreResult(matched=matched, metrics={"cer": cer, "wer": wer})


_TRUTHY = {"true", "1", "yes", "y", "да"}


def _bool_score(gt: Any, predictions: list[IngoreadField], cfg: FieldConfig) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    raw = (chosen[0].text or "") if chosen else ""
    pred = raw.strip().lower() in _TRUTHY
    gt_bool = gt if isinstance(gt, bool) else gt_to_text(gt).strip().lower() in _TRUTHY
    matched = pred == gt_bool
    return FieldScoreResult(matched=matched, metrics={"accuracy": 1.0 if matched else 0.0})


def _number_score(gt: Any, predictions: list[IngoreadField], cfg: FieldConfig) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    raw = (chosen[0].text or "") if chosen else ""
    try:
        gt_val = float(gt)
        pred_val = float(raw)
    except (TypeError, ValueError):
        return FieldScoreResult(matched=False, metrics={"mae": float("inf"), "mse": float("inf")})
    diff = pred_val - gt_val
    # Exact match by default; opt into tolerance via measurer_kwargs: {abs_tol, rel_tol}.
    abs_tol = float(cfg.measurer_kwargs.get("abs_tol", 0.0))
    rel_tol = float(cfg.measurer_kwargs.get("rel_tol", 0.0))
    matched = math.isclose(pred_val, gt_val, abs_tol=abs_tol, rel_tol=rel_tol)
    return FieldScoreResult(
        matched=matched,
        metrics={"mae": abs(diff), "mse": diff * diff},
    )


def _literal_score(
    gt: Any, predictions: list[IngoreadField], cfg: FieldConfig
) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    pred = (chosen[0].text or "") if chosen else ""
    matched = _normalize_text(pred, cfg) == _normalize_text(gt_to_text(gt), cfg)
    return FieldScoreResult(matched=matched, metrics={"accuracy": 1.0 if matched else 0.0})


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _bbox_score(gt: Any, predictions: list[IngoreadField], cfg: FieldConfig) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    pred_bbox = chosen[0].bbox if chosen else None
    gt_boxes = gt_to_boxes(gt)
    gt_bbox = gt_boxes[0] if gt_boxes else None
    if pred_bbox is None or gt_bbox is None or len(pred_bbox) != 4:
        return FieldScoreResult(matched=False, metrics={"iou": 0.0})
    iou = _iou(gt_bbox, pred_bbox)
    threshold = float(cfg.measurer_kwargs.get("iou_threshold", 0.5))
    return FieldScoreResult(matched=iou >= threshold, metrics={"iou": iou})


def _matched_ious(gt_boxes: list[list[float]], pred_boxes: list[list[float]]) -> list[float]:
    """IoU of the best one-to-one assignment between GT and predicted boxes.

    Returns one IoU per assigned pair (``min(len(gt), len(pred))`` of them),
    chosen by the Hungarian algorithm to maximize total overlap.
    """
    if not gt_boxes or not pred_boxes:
        return []
    cost = np.ones((len(gt_boxes), len(pred_boxes)), dtype=float)
    for i, g in enumerate(gt_boxes):
        for j, p in enumerate(pred_boxes):
            cost[i, j] = 1.0 - _iou(g, p)
    rows, cols = linear_sum_assignment(cost)
    return [_iou(gt_boxes[i], pred_boxes[j]) for i, j in zip(rows, cols, strict=True)]


def _bbox_set_score(
    gt: Any, predictions: list[IngoreadField], cfg: FieldConfig
) -> FieldScoreResult:
    """Score a field that holds *several* boxes (e.g. two stamps).

    Uses ALL predicted boxes (not just the first), matches them one-to-one to
    the GT boxes by IoU, and reports presence/count/localization together:

    - ``matched`` — every GT box was found (count is exact AND each matched pair
      clears ``iou_threshold``). This is the strict "both stamps present and
      well-localized" signal.
    - metrics: ``iou`` (mean over matched pairs), ``count_gt``, ``count_pred``,
      ``count_match``, ``precision``, ``recall``.
    """
    gt_boxes = gt_to_boxes(gt)
    pred_boxes = [p.bbox for p in predictions if p.bbox is not None and len(p.bbox) == 4]
    threshold = float(cfg.measurer_kwargs.get("iou_threshold", 0.5))

    n_gt, n_pred = len(gt_boxes), len(pred_boxes)
    ious = _matched_ious(gt_boxes, pred_boxes)
    found = sum(1 for v in ious if v >= threshold)  # true positives at threshold
    mean_iou = sum(ious) / len(ious) if ious else 0.0
    count_match = n_gt == n_pred

    if n_gt == 0:
        # No boxes expected: correct only if none predicted.
        matched = n_pred == 0
        precision = 1.0 if n_pred == 0 else 0.0
        recall = 1.0
    else:
        matched = count_match and found == n_gt
        precision = found / n_pred if n_pred else 0.0
        recall = found / n_gt

    return FieldScoreResult(
        matched=matched,
        metrics={
            "iou": mean_iou,
            "count_gt": float(n_gt),
            "count_pred": float(n_pred),
            "count_match": 1.0 if count_match else 0.0,
            "precision": precision,
            "recall": recall,
        },
    )


def _llm_text_score(
    gt: Any, predictions: list[IngoreadField], cfg: FieldConfig
) -> FieldScoreResult:
    if not os.environ.get("LLM_JUDGE_URL"):
        raise NotImplementedError(
            "LLM_TEXT scoring requires LLM_JUDGE_URL — not configured in v1"
        )
    return _text_score(gt, predictions, cfg)


FieldScorerFn = Callable[[Any, list[IngoreadField], FieldConfig], FieldScoreResult]

FIELD_SCORERS: dict[FieldType, FieldScorerFn] = {
    FieldType.TEXT: _text_score,
    FieldType.BOOL: _bool_score,
    FieldType.NUMBER: _number_score,
    FieldType.LITERAL: _literal_score,
    FieldType.BBOX: _bbox_score,
    FieldType.BBOX_SET: _bbox_set_score,
    FieldType.LLM_TEXT: _llm_text_score,
}
