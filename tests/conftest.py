from __future__ import annotations

from ingoread_test.config.scorer_config import DocumentMeasurerConfig, FieldConfig
from ingoread_test.dataset.models import DocumentGT, FieldGT
from ingoread_test.integration.schemas import IngoreadDocument


def doc_config(*fields: FieldConfig, label: str = "invoice", **kwargs) -> DocumentMeasurerConfig:
    return DocumentMeasurerConfig(doc_label=label, fields=list(fields), **kwargs)


def ground_truth(label: str = "invoice", page: int = 0, bbox=None, **values) -> DocumentGT:
    return DocumentGT(
        doc_label=label,
        page=page,
        bbox=bbox,
        fields={name: FieldGT(gt_value=value) for name, value in values.items()},
    )


def prediction(label: str = "invoice", page: int = 0, bbox=None, **values) -> IngoreadDocument:
    """Build a prediction from raw field values, in any shape the API emits."""
    return IngoreadDocument.model_validate(
        {"label": label, "page": page, "bbox": bbox, "fields": values}
    )


def boxes(*values: list[float]) -> list[dict]:
    """Predicted bbox candidates."""
    return [{"bbox": list(box)} for box in values]
