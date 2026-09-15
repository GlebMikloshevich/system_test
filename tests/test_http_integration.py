"""HTTP integration exercised against an httpx MockTransport.

This proves the create -> poll -> parse loop end-to-end without a live server.
"""

from __future__ import annotations

from pathlib import Path

import httpx

from ingoread_test.dataset.models import DocumentContainer, DocumentGT, FieldGT
from ingoread_test.integration.http import HttpIngoreadIntegration
from ingoread_test.integration.schemas import IngoreadStatus


def _make_container(tmp_path: Path) -> DocumentContainer:
    file_path = tmp_path / "vrc_001.pdf"
    file_path.write_bytes(b"%PDF-1.4 stub bytes")
    return DocumentContainer(
        filename="vrc_001.pdf",
        file_path=file_path,
        documents=[
            DocumentGT(
                doc_label="vehicle_registration",
                fields={"vin": FieldGT(gt_value="JTHBK1GG1F2123456")},
            )
        ],
    )


def _result_body(filename: str) -> dict:
    return {
        "filename": filename,
        "status": "completed",
        "result": [
            {
                "label": "vehicle_registration",
                "page": 0,
                "fields": {
                    "vin": [{"text": "JTHBK1GG1F2123456", "text_confidence": 0.99}],
                },
            }
        ],
    }


async def test_http_integration_create_poll_parse(tmp_path):
    container = _make_container(tmp_path)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/api/integrations/ingoread":
            assert b"vrc_001.pdf" in request.content
            calls.append("create")
            return httpx.Response(200, json={"task_id": "task-42"})
        if request.method == "GET" and request.url.path == "/api/status/task-42":
            calls.append("status")
            # First two polls in-progress, then completed.
            if calls.count("status") < 3:
                return httpx.Response(200, json={"status": "in_progress"})
            return httpx.Response(200, json=_result_body("vrc_001.pdf"))
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake")
    integration = HttpIngoreadIntegration(
        base_url="http://fake",
        integration_name="ingoread",
        client=client,
        poll_interval=0.0,
    )
    try:
        result = await integration.predict(container)
    finally:
        await integration.aclose()

    assert result.status == IngoreadStatus.COMPLETED
    assert result.filename == "vrc_001.pdf"
    assert len(result.result) == 1
    assert result.result[0].fields["vin"][0].text == "JTHBK1GG1F2123456"
    assert calls.count("status") == 3


async def test_http_integration_parses_single_dict_result(tmp_path):
    """Service returns one document as a dict (not a list) -> parsed as 1 doc."""
    container = _make_container(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "t"})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "result": {"label": "vehicle_registration", "fields": {"vin": "ABC123"}},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://fake")
    integration = HttpIngoreadIntegration(
        base_url="http://fake", integration_name="ingoread", client=client, poll_interval=0.0
    )
    try:
        result = await integration.predict(container)
    finally:
        await integration.aclose()

    assert result.status == IngoreadStatus.COMPLETED
    assert len(result.result) == 1
    assert result.result[0].fields["vin"][0].text == "ABC123"


async def test_string_integration_sends_no_file():
    """kind=string: input is in kwargs only; no file part is posted."""
    container = DocumentContainer(
        filename="sample_001",  # an id, not a file on disk
        kwargs={"text": "the document text", "language": "ru"},
        documents=[DocumentGT(doc_label="note", fields={"summary": FieldGT(gt_value="ok")})],
    )
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["body"] = request.content
            return httpx.Response(200, json={"task_id": "t"})
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "result": [{"label": "note", "fields": {"summary": "ok"}}],
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://fake")
    integration = HttpIngoreadIntegration(
        base_url="http://fake",
        integration_name="ingoread",
        client=client,
        poll_interval=0.0,
        send_file=False,
    )
    try:
        result = await integration.predict(container)
    finally:
        await integration.aclose()

    body = captured["body"].decode("utf-8", errors="ignore")
    assert 'name="file"' not in body  # no multipart file part
    assert "text=" in body and "language=ru" in body  # kwargs went out (form-encoded)
    assert result.status == IngoreadStatus.COMPLETED
    assert result.result[0].label == "note"


async def test_http_integration_handles_failed_status(tmp_path):
    container = _make_container(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "task-bad"})
        return httpx.Response(200, json={"status": "failed", "error": "ocr crashed"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake")
    integration = HttpIngoreadIntegration(
        base_url="http://fake",
        integration_name="ingoread",
        client=client,
        poll_interval=0.0,
    )
    try:
        result = await integration.predict(container)
    finally:
        await integration.aclose()

    assert result.status == IngoreadStatus.FAILED
    assert result.error == "ocr crashed"


async def test_http_integration_poll_timeout(tmp_path):
    """A task stuck in_progress past poll_timeout returns FAILED, not a hang."""
    container = _make_container(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "stuck"})
        return httpx.Response(200, json={"status": "in_progress"})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://fake")
    integration = HttpIngoreadIntegration(
        base_url="http://fake",
        integration_name="ingoread",
        client=client,
        poll_interval=0.0,
        poll_timeout=0.0,
    )
    try:
        result = await integration.predict(container)
    finally:
        await integration.aclose()

    assert result.status == IngoreadStatus.FAILED
    assert "poll_timeout" in (result.error or "")


async def test_http_integration_spreads_kwargs_by_default(tmp_path):
    """Default mode: each kwarg becomes its own multipart form field."""
    container = _make_container(tmp_path)
    container.kwargs = {"language": "ru", "checks": ["sig", "stamp"]}
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["body"] = request.content
            return httpx.Response(200, json={"task_id": "t"})
        return httpx.Response(200, json=_result_body("vrc_001.pdf"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://fake")
    integration = HttpIngoreadIntegration(
        base_url="http://fake",
        integration_name="ingoread",
        client=client,
        poll_interval=0.0,
    )
    try:
        await integration.predict(container, kwargs={"prompt_version": "v1"})
    finally:
        await integration.aclose()

    body = captured["body"].decode("utf-8", errors="ignore")
    # Each key is its own multipart part — no bundled "kwargs" / "mapping_string" wrapper.
    assert 'name="language"' in body
    assert "ru" in body
    assert 'name="prompt_version"' in body
    assert "v1" in body
    # Non-str values are JSON-encoded so multipart can carry them.
    assert 'name="checks"' in body
    assert '["sig", "stamp"]' in body
    assert 'name="kwargs"' not in body


async def test_http_integration_uses_custom_data_field_name(tmp_path):
    container = _make_container(tmp_path)
    container.kwargs = {"checks": ["has_signature", "has_stamp"]}
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["body"] = request.content
            return httpx.Response(200, json={"task_id": "t"})
        return httpx.Response(200, json=_result_body("vrc_001.pdf"))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://fake")
    integration = HttpIngoreadIntegration(
        base_url="http://fake",
        integration_name="ingoread",
        client=client,
        poll_interval=0.0,
        data_field_name="mapping_string",
    )
    try:
        await integration.predict(container)
    finally:
        await integration.aclose()

    body = captured["body"].decode("utf-8", errors="ignore")
    assert 'name="mapping_string"' in body
    assert 'name="kwargs"' not in body
    assert "has_signature" in body
