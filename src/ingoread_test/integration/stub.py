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

from ..dataset.models import DocumentContainer
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
                    status=IngoreadStatus.FINISHED,
                    result=docs,
                    time=time.perf_counter() - start,
                )

        docs = [_gt_to_prediction(g) for g in container.documents]
        return IngoreadFileResult(
            filename=container.filename,
            status=IngoreadStatus.FINISHED,
            result=docs,
            time=time.perf_counter() - start,
        )


def _gt_to_prediction(gt) -> IngoreadDocument:
    fields = {
        name: [IngoreadField(text=f.gt_value, text_confidence=1.0)]
        for name, f in gt.fields.items()
    }
    return IngoreadDocument(
        label=gt.doc_label,
        page=gt.page,
        bbox=gt.bbox,
        fields=fields,
    )
