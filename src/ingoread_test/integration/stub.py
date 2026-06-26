"""Deterministic stub integration.

Behavior:
- If `predictions_dir/<container.filename>.json` exists, return its contents.
- Otherwise: echo the ground truth back as predictions so smoke tests pass
  trivially (every field matches).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from ..dataset.models import DocumentContainer, gt_to_text
from .base import Integration
from .schemas import IngoreadDocument, IngoreadField, IngoreadFileResult, IngoreadStatus


class StubIntegration(Integration):
    def __init__(
        self,
        predictions_dir: Path | None = None,
        latency: float = 0.0,
        failure_filenames: set[str] | None = None,
        timeout_filenames: set[str] | None = None,
    ) -> None:
        self.predictions_dir = predictions_dir
        self.latency = latency
        self.failure_filenames = failure_filenames or set()
        self.timeout_filenames = timeout_filenames or set()

    async def predict(
        self, container: DocumentContainer, kwargs: dict | None = None
    ) -> IngoreadFileResult:
        start = time.perf_counter()
        if container.filename in self.timeout_filenames:
            await asyncio.sleep(3600)  # caller should asyncio.wait_for around us
        if self.latency:
            await asyncio.sleep(self.latency)
        if container.filename in self.failure_filenames:
            return IngoreadFileResult(
                filename=container.filename,
                status=IngoreadStatus.FAILED,
                error="stub configured to fail",
                time=time.perf_counter() - start,
            )

        if self.predictions_dir is not None:
            candidate = self.predictions_dir / f"{container.filename}.json"
            if candidate.exists():
                raw = json.loads(candidate.read_text(encoding="utf-8"))
                docs = [IngoreadDocument.model_validate(d) for d in raw]
                return IngoreadFileResult(
                    filename=container.filename,
                    status=IngoreadStatus.COMPLETED,
                    result=docs,
                    time=time.perf_counter() - start,
                )

        docs = [_gt_to_prediction(g) for g in container.documents]
        return IngoreadFileResult(
            filename=container.filename,
            status=IngoreadStatus.COMPLETED,
            result=docs,
            time=time.perf_counter() - start,
        )


def _parse_bbox(value: str) -> list[float] | None:
    """Return a 4-element bbox if `value` looks like one, else None.

    Lets the echo stub satisfy bbox-typed field scorers (which read .bbox, not
    .text) so its "every field matches" contract holds for those fields too.
    """
    try:
        parts = [float(x) for x in value.strip("[]").split(",")]
    except (ValueError, AttributeError):
        return None
    return parts if len(parts) == 4 else None


def _parse_box_list(value: str) -> list[list[float]] | None:
    """Return >1 boxes for a multi-box GT (``"x1,y1,x2,y2; x1,y1,x2,y2"``).

    None when the value isn't a multi-box string, so single-bbox/text fields
    keep their existing echo behavior.
    """
    if ";" not in value:
        return None
    boxes: list[list[float]] = []
    for chunk in value.split(";"):
        box = _parse_bbox(chunk)
        if box is not None:
            boxes.append(box)
    return boxes or None


def _echo_field(value) -> list[IngoreadField]:
    """Turn one GT value into the predicted field(s) the scorers expect."""
    # Native bbox / bbox_set lists are unambiguous: emit one box per GT box.
    if isinstance(value, (list, tuple)) and value:
        if isinstance(value[0], (list, tuple)):  # list of boxes (bbox_set)
            return [IngoreadField(bbox=[float(x) for x in b], bbox_confidence=1.0) for b in value]
        if len(value) == 4:  # single box (bbox)
            return [IngoreadField(bbox=[float(x) for x in value], bbox_confidence=1.0)]
    if isinstance(value, str):
        boxes = _parse_box_list(value)  # ";"-separated string -> multiple boxes
        if boxes is not None:
            return [IngoreadField(bbox=box, bbox_confidence=1.0) for box in boxes]
        # A bare "x,y,x,y" string is ambiguous (text or bbox) — echo both.
        return [IngoreadField(text=value, text_confidence=1.0, bbox=_parse_bbox(value))]
    # number / bool scalar
    return [IngoreadField(text=gt_to_text(value), text_confidence=1.0)]


def _gt_to_prediction(gt) -> IngoreadDocument:
    fields = {name: _echo_field(f.gt_value) for name, f in gt.fields.items()}
    return IngoreadDocument(
        label=gt.doc_label,
        page=gt.page,
        bbox=gt.bbox,
        fields=fields,
    )
