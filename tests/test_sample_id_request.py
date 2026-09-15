"""The sample's unique id travels with the document on the create-task POST."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ingoread_test.config.test_config import IntegrationConfig, IntegrationKind, TestConfig
from ingoread_test.dataset.models import DocumentContainer
from ingoread_test.integration.factory import build_integration
from ingoread_test.integration.http import HttpIngoreadIntegration


def _container(tmp_path: Path, sample_id: str = "inv-0001") -> DocumentContainer:
    file_path = tmp_path / "invoice_001.pdf"
    file_path.write_bytes(b"%PDF-1.4 stub bytes")
    return DocumentContainer(
        sample_id=sample_id,
        filename="invoice_001.pdf",
        file_path=file_path,
        kwargs={"language": "ru"},
        documents=[],
    )


@pytest.fixture
def capture():
    """A transport that records the create-task request and completes the task."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            requests.append(request)
            return httpx.Response(200, json={"task_id": "task-1"})
        return httpx.Response(
            200,
            json={"status": "completed", "result": [], "filename": "invoice_001.pdf"},
        )

    return requests, httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_post_carries_the_sample_id_alongside_the_file(tmp_path, capture):
    requests, client = capture
    integration = HttpIngoreadIntegration("http://x", "ingoread", client=client)

    await integration.predict(_container(tmp_path))

    body = requests[0].content.decode("latin-1")
    assert 'name="sample_id"' in body
    assert "inv-0001" in body
    assert 'name="file"' in body


async def test_sample_id_field_name_is_configurable(tmp_path, capture):
    requests, client = capture
    integration = HttpIngoreadIntegration(
        "http://x", "ingoread", sample_id_field="document_uid", client=client
    )

    await integration.predict(_container(tmp_path))

    body = requests[0].content.decode("latin-1")
    assert 'name="document_uid"' in body
    assert 'name="sample_id"' not in body


async def test_sample_id_can_be_switched_off(tmp_path, capture):
    requests, client = capture
    integration = HttpIngoreadIntegration(
        "http://x", "ingoread", sample_id_field=None, client=client
    )

    await integration.predict(_container(tmp_path))

    assert 'name="sample_id"' not in requests[0].content.decode("latin-1")


async def test_sample_id_stays_outside_the_single_blob_kwargs_payload(tmp_path, capture):
    """In SINGLE-BLOB mode the id identifies the sample, so it keeps its own field."""
    requests, client = capture
    integration = HttpIngoreadIntegration(
        "http://x", "ingoread", data_field_name="mapping_string", client=client
    )

    await integration.predict(_container(tmp_path))

    body = requests[0].content.decode("latin-1")
    assert 'name="sample_id"' in body
    blob = body.split('name="mapping_string"')[1]
    payload = json.loads(blob.split("\r\n\r\n")[1].split("\r\n")[0])
    assert payload == {"language": "ru"}


async def test_string_kind_sends_the_sample_id_without_a_file(tmp_path, capture):
    requests, client = capture
    integration = HttpIngoreadIntegration("http://x", "ingoread", send_file=False, client=client)

    await integration.predict(_container(tmp_path))

    body = requests[0].content.decode("latin-1")
    assert "sample_id" in body
    assert 'name="file"' not in body


async def test_a_container_without_an_id_still_runs(tmp_path, capture, caplog):
    requests, client = capture
    integration = HttpIngoreadIntegration("http://x", "ingoread", client=client)

    result = await integration.predict(_container(tmp_path, sample_id=""))

    assert result.filename == "invoice_001.pdf"
    assert 'name="sample_id"' not in requests[0].content.decode("latin-1")
    assert "has no sample_id" in caplog.text


def test_factory_passes_the_configured_field_name():
    test_cfg = TestConfig(
        name="t",
        files_root="dataset",
        manifest="manifest.yaml",
        integration=IntegrationConfig(
            kind=IntegrationKind.HTTP, url="http://x", sample_id_field="document_uid"
        ),
    )

    integration = build_integration(test_cfg)

    assert isinstance(integration, HttpIngoreadIntegration)
    assert integration.sample_id_field == "document_uid"
