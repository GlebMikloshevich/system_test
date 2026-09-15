"""Results are published to s3://.../<dataset>/<integration>/<date>/<time>/."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from ingoread_test.config.test_config import ResultsConfig, TestConfig
from ingoread_test.modules.logger_module import (
    S3ResultSink,
    read_result,
    run_folder_key,
    upload_run,
)
from ingoread_test.results.models import MeasurementsResult

START = datetime(2026, 9, 15, 14, 32, 5, tzinfo=UTC)


def _result(name: str = "invoices-ru") -> MeasurementsResult:
    return MeasurementsResult(
        test_config_name=name,
        scorer_config_name="default",
        start_date=START,
        total_time=12.0,
        total_samples=2,
        time_per_sample=6.0,
        match_rate=0.97,
    )


def _test_cfg(**results: object) -> TestConfig:
    return TestConfig(
        name="invoices-ru",
        files_root="dataset",
        manifest="manifest.yaml",
        integration_name="ingoread",
        results=ResultsConfig(**results),
    )


def test_run_folder_key_is_dataset_integration_date_time():
    key = run_folder_key("runs", "invoices-ru", "ingoread", START)

    assert key == "runs/invoices-ru/ingoread/2026-09-15/143205"


def test_run_folder_key_assumes_utc_for_a_naive_timestamp():
    naive = datetime(2026, 9, 15, 14, 32, 5)  # noqa: DTZ001 - the point of the test

    key = run_folder_key("runs", "d", "i", naive)

    assert key.endswith("2026-09-15/143205")


@pytest.mark.parametrize(
    ("dataset_name", "expected"),
    [("invoices ru", "invoices_ru"), ("a/b", "a_b"), ("   ", "unnamed")],
)
def test_run_folder_key_keeps_segments_safe(dataset_name, expected):
    assert run_folder_key("runs", dataset_name, "ingoread", START).split("/")[1] == expected


def test_sink_uploads_the_result_json_and_the_report(s3_client, s3_hub, tmp_path):
    report = tmp_path / "20260915T143205__invoices-ru.html"
    report.write_text("<html>report</html>", encoding="utf-8")
    sink = S3ResultSink("s3://test-bucket/runs", "invoices-ru", "ingoread", hub=s3_hub)

    folder_uri = sink.write(_result(), [report])

    folder = "runs/invoices-ru/ingoread/2026-09-15/143205"
    assert folder_uri == f"s3://test-bucket/{folder}"
    assert s3_client.keys_under(folder) == [
        f"{folder}/report.html",
        f"{folder}/result.json",
    ]
    stored = json.loads(s3_client.text(f"{folder}/result.json"))
    assert stored["match_rate"] == 0.97
    assert s3_client.text(f"{folder}/report.html") == "<html>report</html>"


def test_two_integrations_do_not_share_a_folder(s3_client, s3_hub):
    for integration in ("ingoread", "ingoread_next"):
        S3ResultSink("s3://test-bucket/runs", "invoices-ru", integration, hub=s3_hub).write(
            _result()
        )

    assert s3_client.keys_under("runs/invoices-ru") == [
        "runs/invoices-ru/ingoread/2026-09-15/143205/result.json",
        "runs/invoices-ru/ingoread_next/2026-09-15/143205/result.json",
    ]


def test_upload_run_is_a_no_op_without_a_results_uri():
    assert upload_run(_test_cfg(), "invoices-ru", _result()) is None


def test_upload_run_uploads_the_configured_artifacts(s3_client, s3_hub, tmp_path):
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")

    folder_uri = upload_run(
        _test_cfg(uri="s3://test-bucket/runs"),
        "invoices-ru",
        _result(),
        html_path=report,
        hub=s3_hub,
    )

    folder = "runs/invoices-ru/ingoread/2026-09-15/143205"
    assert folder_uri == f"s3://test-bucket/{folder}"
    assert s3_client.keys_under(folder) == [f"{folder}/report.html", f"{folder}/result.json"]


def test_upload_run_can_skip_the_html(s3_client, s3_hub, tmp_path):
    report = tmp_path / "report.html"
    report.write_text("<html></html>", encoding="utf-8")

    upload_run(
        _test_cfg(uri="s3://test-bucket/runs", upload_html=False),
        "invoices-ru",
        _result(),
        html_path=report,
        hub=s3_hub,
    )

    folder = "runs/invoices-ru/ingoread/2026-09-15/143205"
    assert s3_client.keys_under(folder) == [f"{folder}/result.json"]


def test_read_result_accepts_a_local_path_and_an_s3_uri(s3_client, s3_hub, tmp_path):
    """A run's baseline is often the S3 result of the previous run."""
    local = tmp_path / "result.json"
    local.write_text(_result().model_dump_json(), encoding="utf-8")
    s3_client.objects["runs/result.json"] = _result("from-s3").model_dump_json().encode()

    assert read_result(local).test_config_name == "invoices-ru"
    assert (
        read_result("s3://test-bucket/runs/result.json", hub=s3_hub).test_config_name == "from-s3"
    )


async def test_a_real_run_lands_in_its_own_s3_folder(s3_client, s3_hub, tmp_path):
    """End to end: dataset in S3 -> stub run -> result published under its folder."""
    from ingoread_test.config.scorer_config import (
        DocumentMeasurerConfig,
        FieldConfig,
        FieldType,
        ScorerConfig,
    )
    from ingoread_test.dataset import load_dataset
    from ingoread_test.integration.stub import StubIntegration
    from ingoread_test.modules import run_test, score

    s3_client.objects = {
        "datasets/invoices-ru/manifest.yaml": (
            b"version: 2\n"
            b"name: invoices-ru\n"
            b"samples:\n"
            b"  - sample_id: inv-0001\n"
            b"    filename: invoice_001.pdf\n"
            b"    documents:\n"
            b"      - doc_label: invoice\n"
            b'        fields: {total: {gt_value: "1.00"}}\n'
        ),
        "datasets/invoices-ru/invoice_001.pdf": b"%PDF-1.4",
        "datasets/invoices-ru/invoice_001.id": b"inv-0001\n",
    }
    dataset = load_dataset(
        "s3://test-bucket/datasets/invoices-ru", hub=s3_hub, cache_dir=tmp_path / "cache"
    )
    test_cfg = _test_cfg(uri="s3://test-bucket/runs")
    scorer_cfg = ScorerConfig(
        name="default",
        measurement_configs=[
            DocumentMeasurerConfig(
                doc_label="invoice",
                fields=[FieldConfig(field_name="total", field_type=FieldType.NUMBER)],
            )
        ],
    )

    predictions, stats = await run_test(test_cfg, StubIntegration(), dataset)
    result = score(test_cfg, scorer_cfg, dataset, predictions, stats)
    folder_uri = upload_run(test_cfg, dataset.name, result, hub=s3_hub)

    assert set(predictions) == {"inv-0001"}, "predictions are keyed by sample id"
    assert result.match_rate == 1.0
    date, time = result.start_date.strftime("%Y-%m-%d"), result.start_date.strftime("%H%M%S")
    assert folder_uri == f"s3://test-bucket/runs/invoices-ru/ingoread/{date}/{time}"
    stored = json.loads(s3_client.text(f"runs/invoices-ru/ingoread/{date}/{time}/result.json"))
    assert stored["container_pairs"][0]["sample_id"] == "inv-0001"
