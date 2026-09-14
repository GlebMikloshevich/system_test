"""Turn ground truth and ingoread predictions into stickler model instances.

This is the only place that knows about the two wire shapes — `DocumentGT`
fields hold a native YAML value, `IngoreadDocument` fields hold a list of
`IngoreadField` candidates — so everything downstream compares plain values
through stickler.
"""

from __future__ import annotations

from typing import Any

from stickler import StructuredModel

from ..config.scorer_config import FieldConfig, FieldType, PredictionSelection
from ..dataset.models import DocumentGT, gt_to_boxes, gt_to_text
from ..integration.schemas import IngoreadDocument, IngoreadField
from .comparators import BBOX_TYPES, TEXT_TYPES, is_multi_valued
from .models import scored_fields

_TRUTHY = {"true", "1", "yes", "y", "да"}
_FALSY = {"false", "0", "no", "n", "нет"}


def _normalize_text(value: str, cfg: FieldConfig) -> str:
    """Optional pre-comparison cleanup. Off by default (comparators normalize
    what they can themselves; `casefold` becomes ExactComparator's
    case_sensitive=False, so only stripping has to happen here)."""
    if cfg.measurer_kwargs.get("strip"):
        value = value.strip()
    return value


def _to_bool(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    text = gt_to_text(value).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    # Anything else (including an empty string) is "no answer", which stickler
    # reports as a missing value rather than as a wrong one.
    return None


def _scalar(value: Any, cfg: FieldConfig) -> Any:
    """Coerce one raw value into what this field's comparator expects."""
    if value is None:
        return None
    if cfg.field_type in TEXT_TYPES:
        return _normalize_text(gt_to_text(value), cfg)
    if cfg.field_type == FieldType.BOOL:
        return _to_bool(value)
    if cfg.field_type == FieldType.NUMBER:
        # Numbers stay as-is: NumericComparator parses numeric strings, and a
        # non-numeric string stays visible as a wrong answer.
        return value if isinstance(value, (int, float, str)) else gt_to_text(value)
    raise AssertionError(f"bbox types are handled separately, got {cfg.field_type}")


def _select(candidates: list[IngoreadField], cfg: FieldConfig) -> list[IngoreadField]:
    """Apply the configured selection to the predicted candidates."""
    if cfg.field_type == FieldType.BBOX_SET:
        # A bbox_set is the set of boxes the model found; every candidate is
        # part of the answer, so selection doesn't narrow it.
        return candidates
    if cfg.selection == PredictionSelection.FIRST:
        return candidates[:1]
    if cfg.selection == PredictionSelection.TOP_N:
        return candidates[: cfg.top_n]
    return candidates


def _boxes(values: list[list[float] | None], cfg: FieldConfig) -> list[list[float]] | None:
    boxes = [[float(x) for x in b] for b in values if b is not None and len(b) == 4]
    if cfg.field_type == FieldType.BBOX:
        boxes = boxes[:1]  # a single-box field, still carried as a one-item list
    return boxes or None


def gt_value_for(gt: DocumentGT, cfg: FieldConfig) -> Any:
    """Read one ground-truth field in the shape its comparator expects."""
    field = gt.fields.get(cfg.field_name)
    raw = field.gt_value if field is not None else None
    if cfg.field_type in BBOX_TYPES:
        return _boxes(gt_to_boxes(raw), cfg)
    if is_multi_valued(cfg):
        if raw is None:
            return None
        items = raw if isinstance(raw, (list, tuple)) else [raw]
        return [_scalar(v, cfg) for v in items] or None
    return _scalar(raw, cfg)


def prediction_value_for(prediction: IngoreadDocument, cfg: FieldConfig) -> Any:
    """Read one predicted field in the shape its comparator expects."""
    chosen = _select(prediction.fields.get(cfg.field_name, []), cfg)
    if cfg.field_type in BBOX_TYPES:
        return _boxes([p.bbox for p in chosen], cfg)
    values = [_scalar(p.text, cfg) for p in chosen]
    if is_multi_valued(cfg):
        return [v for v in values if v is not None] or None
    return values[0] if values else None


def gt_to_model(
    gt: DocumentGT, cfg_fields: list[FieldConfig], model: type[StructuredModel]
) -> StructuredModel:
    return model(**{fc.field_name: gt_value_for(gt, fc) for fc in cfg_fields})


def prediction_to_model(
    prediction: IngoreadDocument,
    cfg_fields: list[FieldConfig],
    model: type[StructuredModel],
) -> StructuredModel:
    return model(
        **{fc.field_name: prediction_value_for(prediction, fc) for fc in cfg_fields}
    )


def empty_model(
    cfg_fields: list[FieldConfig], model: type[StructuredModel]
) -> StructuredModel:
    """An all-missing instance — the stand-in for the absent side of a half pair."""
    return model(**{fc.field_name: None for fc in cfg_fields})


__all__ = [
    "empty_model",
    "gt_to_model",
    "gt_value_for",
    "prediction_to_model",
    "prediction_value_for",
    "scored_fields",
]
