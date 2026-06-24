"""Per-field scoring functions matching the diagram's FieldScorer block."""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass

import jiwer

from ..config.scorer_config import FieldConfig, FieldType, PredictionSelection
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


def _text_score(gt: str, predictions: list[IngoreadField], cfg: FieldConfig) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    pred = (chosen[0].text or "") if chosen else ""
    gt = _normalize_text(gt, cfg)
    pred = _normalize_text(pred, cfg)
    if not gt and not pred:
        return FieldScoreResult(matched=True, metrics={"cer": 0.0, "wer": 0.0})
    cer = float(jiwer.cer(gt, pred)) if gt else (0.0 if not pred else 1.0)
    wer = float(jiwer.wer(gt, pred)) if gt else (0.0 if not pred else 1.0)
    matched = pred == gt
    return FieldScoreResult(matched=matched, metrics={"cer": cer, "wer": wer})


def _bool_score(gt: str, predictions: list[IngoreadField], cfg: FieldConfig) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    raw = (chosen[0].text or "") if chosen else ""
    pred = raw.strip().lower() in {"true", "1", "yes", "y", "да"}
    gt_bool = gt.strip().lower() in {"true", "1", "yes", "y", "да"}
    matched = pred == gt_bool
    return FieldScoreResult(matched=matched, metrics={"accuracy": 1.0 if matched else 0.0})


def _number_score(gt: str, predictions: list[IngoreadField], cfg: FieldConfig) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    raw = (chosen[0].text or "") if chosen else ""
    try:
        gt_val = float(gt)
        pred_val = float(raw)
    except ValueError:
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
    gt: str, predictions: list[IngoreadField], cfg: FieldConfig
) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    pred = (chosen[0].text or "") if chosen else ""
    matched = _normalize_text(pred, cfg) == _normalize_text(gt, cfg)
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


def _bbox_score(gt: str, predictions: list[IngoreadField], cfg: FieldConfig) -> FieldScoreResult:
    chosen = select_prediction(predictions, cfg)
    pred_bbox = chosen[0].bbox if chosen else None
    try:
        gt_bbox = [float(x) for x in gt.strip("[]").split(",")]
    except ValueError:
        return FieldScoreResult(matched=False, metrics={"iou": 0.0})
    if pred_bbox is None or len(pred_bbox) != 4 or len(gt_bbox) != 4:
        return FieldScoreResult(matched=False, metrics={"iou": 0.0})
    iou = _iou(gt_bbox, pred_bbox)
    threshold = float(cfg.measurer_kwargs.get("iou_threshold", 0.5))
    return FieldScoreResult(matched=iou >= threshold, metrics={"iou": iou})


def _llm_text_score(
    gt: str, predictions: list[IngoreadField], cfg: FieldConfig
) -> FieldScoreResult:
    if not os.environ.get("LLM_JUDGE_URL"):
        raise NotImplementedError(
            "LLM_TEXT scoring requires LLM_JUDGE_URL — not configured in v1"
        )
    return _text_score(gt, predictions, cfg)


FieldScorerFn = Callable[[str, list[IngoreadField], FieldConfig], FieldScoreResult]

FIELD_SCORERS: dict[FieldType, FieldScorerFn] = {
    FieldType.TEXT: _text_score,
    FieldType.BOOL: _bool_score,
    FieldType.NUMBER: _number_score,
    FieldType.LITERAL: _literal_score,
    FieldType.BBOX: _bbox_score,
    FieldType.LLM_TEXT: _llm_text_score,
}
