"""Bootstrap a draft ground-truth manifest from a recognizer's own output.

This supports the "half-automatic" annotation workflow: run the integration
once, then turn its predictions into a draft GT manifest that a human reviews
and corrects. The source of predictions here is a prior result JSON
(``MeasurementsResult.container_pairs[].predictions``).

Conversion is intentionally lossless-but-flat: the first predicted value per
field becomes ``gt_value`` (matching the FIRST selection used at scoring time),
bbox-only fields are serialized to an ``"x1,y1,x2,y2"`` string, and nothing is
filtered or flagged — the human owns correctness from here.
"""

from __future__ import annotations

from pathlib import Path

from ..integration.schemas import IngoreadField, IngoreadFileResult
from ..results.models import MeasurementsResult
from .manifest import DatasetManifest, SampleEntry


def _num(value: float) -> int | float:
    """Drop a trailing ``.0`` on integral coordinates so output reads cleanly."""
    return int(value) if float(value).is_integer() else value


def _field_to_gt_value(field: IngoreadField) -> str:
    """Render one predicted field as a GT string.

    Prefers text; falls back to a comma-joined bbox so bbox-typed fields round
    trip through the manifest format. Empty when neither is present.
    """
    if field.text is not None:
        return field.text
    if field.bbox is not None:
        return ",".join(str(_num(x)) for x in field.bbox)
    return ""


def _document_to_gt(document) -> dict:
    fields: dict[str, dict] = {}
    for name, preds in document.fields.items():
        # FIRST selection: the first predicted value is the draft GT.
        value = _field_to_gt_value(preds[0]) if preds else ""
        fields[name] = {"gt_value": value}

    entry: dict = {"doc_label": document.label}
    if document.page:
        entry["page"] = document.page
    if document.bbox is not None:
        entry["bbox"] = [_num(x) for x in document.bbox]
    entry["fields"] = fields
    return entry


def prediction_to_manifest_entry(pred: IngoreadFileResult) -> dict:
    """Convert one file's prediction into a manifest container entry."""
    return {
        "filename": pred.filename,
        "documents": [_document_to_gt(d) for d in pred.result],
    }


def result_to_manifest_entries(result: MeasurementsResult) -> list[dict]:
    """Harvest every container's predictions from a result JSON into draft GT."""
    return [prediction_to_manifest_entry(cp.predictions) for cp in result.container_pairs]


def result_to_manifest(result: MeasurementsResult, *, name: str = "") -> DatasetManifest:
    """Draft a version 2 manifest from a result JSON's predictions.

    Each sample keeps the id it ran with, so a bootstrapped manifest lines up
    with the id files already sitting next to the documents.
    """
    samples = [
        SampleEntry.model_validate(
            {
                "sample_id": pair.sample_id or pair.gts.sample_id or Path(pair.filename).stem,
                **prediction_to_manifest_entry(pair.predictions),
            }
        )
        for pair in result.container_pairs
    ]
    return DatasetManifest(name=name or result.test_config_name, samples=samples)
