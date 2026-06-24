"""HTTP client for a real ingoread instance.

Protocol (per user spec):
- POST {base_url}/integrations/{integration_name} as multipart, returns {"task_id": "..."}
- GET  {base_url}/status/{task_id} returns {"status": ..., "result": {IngoreadFileResult-shaped}}

Status strings are normalized: 'completed' → FINISHED, 'queues' → QUEUED, etc.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import httpx

from ..dataset.models import DocumentContainer
from .base import Integration
from .schemas import IngoreadFileResult, IngoreadStatus

_STATUS_MAP = {
    "queued": IngoreadStatus.QUEUED,
    "in_progress": IngoreadStatus.IN_PROGRESS,
    "completed": IngoreadStatus.COMPLETED,
    "failed": IngoreadStatus.FAILED,
    "error": IngoreadStatus.FAILED,
}


def _normalize_status(raw: str) -> IngoreadStatus:
    return _STATUS_MAP.get(raw.lower(), IngoreadStatus.FAILED)


class HttpIngoreadIntegration(Integration):
    def __init__(
        self,
        base_url: str,
        integration_name: str,
        auth_token: str | None = None,
        poll_interval: float = 1.0,
        poll_timeout: float | None = None,
        data_field_name: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("HttpIngoreadIntegration requires a base_url")
        self.base_url = base_url.rstrip("/")
        self.integration_name = integration_name
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.data_field_name = data_field_name
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0))

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def predict(
        self, container: DocumentContainer, kwargs: dict | None = None
    ) -> IngoreadFileResult:
        start = time.perf_counter()
        task_id = await self._create_task(container, kwargs)
        try:
            payload = await self._poll_until_terminal(task_id)
        except Exception as exc:  # noqa: BLE001
            return IngoreadFileResult(
                filename=container.filename,
                status=IngoreadStatus.FAILED,
                error=f"poll failed: {exc}",
                time=time.perf_counter() - start,
            )

        status = _normalize_status(payload.get("status", "failed"))
        if status != IngoreadStatus.COMPLETED:
            return IngoreadFileResult(
                filename=container.filename,
                status=status,
                error=payload.get("error"),
                time=time.perf_counter() - start,
            )

        result_body = payload.get("result", payload)
        # Accept either a raw IngoreadFileResult dict, or just its `result` list.
        if isinstance(result_body, list):
            result_body = {"filename": container.filename, "result": result_body}
        if "filename" not in result_body:
            result_body["filename"] = container.filename
        if "status" not in result_body:
            result_body["status"] = status.value
        parsed = IngoreadFileResult.model_validate(result_body)
        parsed.time = time.perf_counter() - start
        return parsed

    async def _create_task(
        self, container: DocumentContainer, kwargs: dict | None
    ) -> str:
        if container.file_path is None or not Path(container.file_path).exists():
            raise FileNotFoundError(
                f"container {container.filename} has no readable file_path"
            )

        merged_kwargs = {**(kwargs or {}), **container.kwargs}
        files = {"file": (container.filename, Path(container.file_path).read_bytes())}
        data = self._build_data(merged_kwargs)

        url = f"{self.base_url}/integrations/{self.integration_name}"
        response = await self._client.post(url, files=files, data=data)
        response.raise_for_status()
        body = response.json()
        task_id = body.get("task_id") or body.get("id")
        if not task_id:
            raise RuntimeError(f"create-task response missing task_id: {body!r}")
        return str(task_id)

    def _build_data(self, merged_kwargs: dict) -> dict[str, str]:
        """Build the multipart `data` payload from per-document kwargs.

        - `data_field_name` is None (default): SPREAD mode — each kwarg key
          becomes its own form field. Non-str values are JSON-encoded for
          transport.
        - `data_field_name` is set: SINGLE-BLOB mode — all kwargs are JSON-
          encoded together under that one field name.
        """
        if not merged_kwargs:
            return {}
        if self.data_field_name is not None:
            return {self.data_field_name: json.dumps(merged_kwargs)}
        return {
            k: v if isinstance(v, str) else json.dumps(v)
            for k, v in merged_kwargs.items()
        }

    async def _poll_until_terminal(self, task_id: str) -> dict:
        url = f"{self.base_url}/status/{task_id}"
        deadline = (
            time.perf_counter() + self.poll_timeout
            if self.poll_timeout is not None
            else None
        )
        while True:
            response = await self._client.get(url)
            response.raise_for_status()
            payload = response.json()
            status = _normalize_status(payload.get("status", ""))
            if status in (IngoreadStatus.COMPLETED, IngoreadStatus.FAILED):
                return payload
            if deadline is not None and time.perf_counter() >= deadline:
                raise TimeoutError(
                    f"task {task_id} still {status.value} after "
                    f"{self.poll_timeout}s poll_timeout"
                )
            await asyncio.sleep(self.poll_interval)
