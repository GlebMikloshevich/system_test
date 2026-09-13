"""Generate one stickler `StructuredModel` per configured document type.

The scorer config *is* the comparison schema: each non-ignored `FieldConfig`
becomes a `ComparableField` carrying its comparator, threshold and weight, so
scoring a (gt, prediction) pair is a single `gt.compare_with(pred)` call.
"""

from __future__ import annotations

from pydantic import create_model
from stickler import ComparableField, StructuredModel

from ..config.scorer_config import DocumentMeasurerConfig, FieldConfig
from .comparators import annotation_for, build_comparator, threshold_for

# StructuredModel declares these itself; a scored field of the same name would
# shadow them.
_RESERVED = frozenset(StructuredModel.model_fields)

_MODEL_CACHE: dict[str, type[StructuredModel]] = {}


def scored_fields(cfg: DocumentMeasurerConfig) -> list[FieldConfig]:
    """The fields that take part in scoring, in config order."""
    return [f for f in cfg.fields if not f.ignore]


def _model_name(doc_label: str) -> str:
    safe = "".join(c if c.isalnum() else "_" for c in doc_label).strip("_")
    return f"Doc_{safe or 'document'}"


def build_document_model(cfg: DocumentMeasurerConfig) -> type[StructuredModel]:
    """Build (and cache) the StructuredModel class for one document type."""
    key = cfg.model_dump_json()
    cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached

    definitions: dict[str, tuple] = {}
    for fc in scored_fields(cfg):
        if fc.field_name in _RESERVED:
            raise ValueError(
                f"Document {cfg.doc_label!r} has a field named {fc.field_name!r}, which "
                f"stickler's StructuredModel reserves. Rename it in the scorer config "
                f"(reserved: {sorted(_RESERVED)})."
            )
        if fc.field_name in definitions:
            raise ValueError(
                f"Document {cfg.doc_label!r} configures field {fc.field_name!r} twice."
            )
        definitions[fc.field_name] = (
            annotation_for(fc),
            ComparableField(
                comparator=build_comparator(fc),
                threshold=threshold_for(fc),
                weight=fc.weight,
                clip_under_threshold=fc.clip_under_threshold,
                default=None,
            ),
        )

    model = create_model(_model_name(cfg.doc_label), __base__=StructuredModel, **definitions)
    _MODEL_CACHE[key] = model
    return model
