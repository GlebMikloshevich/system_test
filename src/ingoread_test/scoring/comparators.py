"""Map a `FieldConfig` onto a stickler comparator + the value shape it compares.

Every field type here is a thin preset over a stickler comparator; the legacy
``measurer_kwargs`` names are translated to that comparator's constructor
kwargs so existing scorer configs keep working unchanged. Anything stickler
knows that has no preset is reachable via ``comparator: <ClassName>``.
"""

from __future__ import annotations

from typing import Any

from stickler.comparators.base import BaseComparator
from stickler.structured_object_evaluator.models.comparator_registry import (
    get_comparator_class,
    get_global_registry,
    register_comparator,
)

from ..config.scorer_config import FieldConfig, FieldType, PredictionSelection

# Comparator each field type is shorthand for.
_COMPARATOR_BY_TYPE: dict[FieldType, str] = {
    FieldType.TEXT: "ExactComparator",
    FieldType.LITERAL: "ExactComparator",
    FieldType.BOOL: "ExactComparator",
    FieldType.NUMBER: "NumericComparator",
    FieldType.FUZZY_TEXT: "LevenshteinComparator",
    FieldType.BBOX: "BBoxIoUComparator",
    FieldType.BBOX_SET: "BBoxIoUComparator",
    FieldType.DATE: "DateComparator",
    FieldType.PHONE: "PhoneComparator",
    FieldType.LLM_TEXT: "LLMComparator",
}

# Match threshold applied when the field config doesn't set one. Exact-match
# types demand 1.0; graded ones (similarity, IoU) accept partial credit.
DEFAULT_THRESHOLDS: dict[FieldType, float] = {
    FieldType.TEXT: 1.0,
    FieldType.LITERAL: 1.0,
    FieldType.BOOL: 1.0,
    FieldType.NUMBER: 1.0,
    FieldType.DATE: 1.0,
    FieldType.PHONE: 1.0,
    FieldType.FUZZY_TEXT: 0.7,
    FieldType.LLM_TEXT: 0.7,
    FieldType.BBOX: 0.5,
    FieldType.BBOX_SET: 0.5,
}

# A box is compared as a flat [x1, y1, x2, y2]; bbox fields always hold a list
# of them (one for BBOX) because stickler reads a bare List[float] as four
# independent numbers rather than as a box.
_Box = list[float]

# Number predictions arrive as raw API text. Keeping the str in the union lets
# NumericComparator parse "10.00" itself and, crucially, keeps unparsable junk
# distinguishable from an absent value: "abc" scores as a wrong answer (false
# discovery), None as a missing one (false negative).
_Number = float | str

_BASE_TYPES: dict[FieldType, Any] = {
    FieldType.TEXT: str,
    FieldType.LITERAL: str,
    FieldType.FUZZY_TEXT: str,
    FieldType.LLM_TEXT: str,
    FieldType.DATE: str,
    FieldType.PHONE: str,
    FieldType.BOOL: bool,
    FieldType.NUMBER: _Number,
    FieldType.BBOX: _Box,
    FieldType.BBOX_SET: _Box,
}

# Legacy measurer_kwargs -> comparator constructor kwargs, per field type.
_LEGACY_KWARGS: dict[FieldType, dict[str, str]] = {
    FieldType.NUMBER: {"abs_tol": "absolute_tolerance", "rel_tol": "relative_tolerance"},
    FieldType.BBOX: {"iou_threshold": "threshold"},
    FieldType.BBOX_SET: {"iou_threshold": "threshold"},
}

BBOX_TYPES = frozenset({FieldType.BBOX, FieldType.BBOX_SET})
TEXT_TYPES = frozenset(
    {
        FieldType.TEXT,
        FieldType.LITERAL,
        FieldType.FUZZY_TEXT,
        FieldType.LLM_TEXT,
        FieldType.DATE,
        FieldType.PHONE,
    }
)


def threshold_for(cfg: FieldConfig) -> float:
    """The similarity at or above which this field counts as matched."""
    if cfg.threshold is not None:
        return cfg.threshold
    # A bbox iou_threshold doubles as the match threshold.
    legacy_iou = cfg.measurer_kwargs.get("iou_threshold")
    if cfg.field_type in BBOX_TYPES and legacy_iou is not None:
        return float(legacy_iou)
    return DEFAULT_THRESHOLDS[cfg.field_type]


def is_multi_valued(cfg: FieldConfig) -> bool:
    """True when the field holds a list stickler matches set-to-set.

    Note that `clip_under_threshold` has no effect on such a field: its score is
    the share of list elements that paired up, and an element scoring under the
    comparator's threshold doesn't pair at all.
    """
    return (
        cfg.field_type in BBOX_TYPES
        or cfg.selection in (PredictionSelection.ALL, PredictionSelection.TOP_N)
    )


def annotation_for(cfg: FieldConfig) -> Any:
    """The pydantic annotation the generated model should use for this field."""
    base = _BASE_TYPES[cfg.field_type]
    return list[base] | None if is_multi_valued(cfg) else base | None


def _comparator_kwargs(cfg: FieldConfig) -> dict:
    kwargs: dict[str, Any] = {}
    for legacy, target in _LEGACY_KWARGS.get(cfg.field_type, {}).items():
        if legacy in cfg.measurer_kwargs:
            kwargs[target] = cfg.measurer_kwargs[legacy]
    # ExactComparator is the only text preset that is case-sensitive by default;
    # the fuzzy ones already normalize case themselves.
    if (
        cfg.field_type in TEXT_TYPES
        and cfg.measurer_kwargs.get("casefold")
        and _comparator_name(cfg) == "ExactComparator"
    ):
        kwargs["case_sensitive"] = False
    if cfg.field_type in BBOX_TYPES:
        kwargs.setdefault("threshold", threshold_for(cfg))
    kwargs.update(cfg.comparator_kwargs)
    return kwargs


def _comparator_name(cfg: FieldConfig) -> str:
    return cfg.comparator or _COMPARATOR_BY_TYPE[cfg.field_type]


def _resolve_comparator_class(field_name: str, name: str) -> type[BaseComparator]:
    registry = get_global_registry()
    if name == "LLMComparator" and not registry.is_registered(name):
        # Lives behind the `llm` extra, so it isn't pre-registered.
        try:
            from stickler.comparators.llm import LLMComparator
        except ImportError as exc:
            raise ValueError(
                f"Field {field_name!r} asks for LLMComparator, which ships in "
                "stickler's 'llm' extra. Install it with: "
                "pip install 'ingoread-test[llm]'"
            ) from exc
        register_comparator(name, LLMComparator)
    try:
        return get_comparator_class(name)
    except KeyError as exc:
        raise ValueError(
            f"Field {field_name!r} asks for comparator {name!r}, which stickler's "
            f"registry doesn't know. Available: {sorted(registry.list_available())}."
        ) from exc


def build_comparator(cfg: FieldConfig) -> BaseComparator:
    """Instantiate the stickler comparator this field is configured for.

    Constructed directly rather than through ``create_comparator``, which falls
    back to a no-arg constructor when kwargs don't fit — that would quietly drop
    a mistyped tolerance instead of reporting it.
    """
    name = _comparator_name(cfg)
    kwargs = _comparator_kwargs(cfg)
    comparator_class = _resolve_comparator_class(cfg.field_name, name)
    try:
        return comparator_class(**kwargs)
    except TypeError as exc:
        raise ValueError(
            f"Field {cfg.field_name!r}: {name} rejected {kwargs!r} ({exc})."
        ) from exc
    except ImportError as exc:
        # Comparators behind a stickler extra (LLM, semantic) only pull their
        # backend in when constructed.
        raise ValueError(
            f"Field {cfg.field_name!r} needs {name}, whose backend isn't installed "
            f"({exc}). Install the matching extra, e.g. pip install 'ingoread-test[llm]' "
            "or 'ingoread-test[semantic]'."
        ) from exc
