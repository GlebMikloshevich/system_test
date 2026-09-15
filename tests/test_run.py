"""End-to-end: dataset -> stub integration -> stickler scoring -> report."""

from __future__ import annotations

import asyncio
import json

import pytest
import yaml

from ingoread_test.config.loader import load_configs
from ingoread_test.dataset.loader import load_dataset
from ingoread_test.integration.runner import run_test
from ingoread_test.integration.stub import StubIntegration
from ingoread_test.reporting import render_html
from ingoread_test.results.models import MeasurementsResult
from ingoread_test.scoring.aggregate import score

MANIFEST = [
    {
        "filename": "invoice_001.pdf",
        "documents": [
            {
                "doc_label": "invoice",
                "page": 0,
                "fields": {
                    "total": {"gt_value": 123.45},
                    "seller": {"gt_value": "ACME Ltd"},
                    "paid": {"gt_value": True},
                    "stamp": {"gt_value": [0, 0, 10, 10]},
                },
            }
        ],
    },
    {
        "filename": "invoice_002.pdf",
        "documents": [
            {
                "doc_label": "invoice",
                "page": 0,
                "fields": {
                    "total": {"gt_value": 10.0},
                    "seller": {"gt_value": "Globex"},
                    "paid": {"gt_value": False},
                    "stamp": {"gt_value": [1, 1, 9, 9]},
                },
            }
        ],
    },
]

SCORER = {
    "name": "stickler",
    "measurement_configs": [
        {
            "doc_label": "invoice",
            "fields": [
                {
                    "field_name": "total",
                    "field_type": "number",
                    "weight": 2.0,
                    "measurer_kwargs": {"abs_tol": 0.01},
                },
                {"field_name": "seller", "field_type": "text"},
                {"field_name": "paid", "field_type": "bool"},
                {
                    "field_name": "stamp",
                    "field_type": "bbox",
                    "measurer_kwargs": {"iou_threshold": 0.5},
                },
            ],
        }
    ],
}


@pytest.fixture
def run_dir(tmp_path):
    files = tmp_path / "dataset"
    files.mkdir()
    for entry in MANIFEST:
        (files / entry["filename"]).touch()
    (files / "manifest.yaml").write_text(yaml.safe_dump(MANIFEST), encoding="utf-8")
    config = tmp_path / "config.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "test": {
                    "name": "smoke",
                    "files_root": str(files),
                    "manifest": str(files / "manifest.yaml"),
                    "batch_size": 2,
                },
                "scorer": SCORER,
            }
        ),
        encoding="utf-8",
    )
    return tmp_path, config


def run(config_path, predictions_dir=None):
    test_cfg, scorer_cfg = load_configs(config_path)
    dataset = load_dataset(test_cfg.files_root, test_cfg.manifest)
    integration = StubIntegration(predictions_dir=predictions_dir)
    predictions, stats = asyncio.run(run_test(test_cfg, integration, dataset))
    return test_cfg, score(test_cfg, scorer_cfg, dataset, predictions, stats)


def test_echoed_ground_truth_scores_a_perfect_run(run_dir):
    _, result = run(run_dir[1])
    assert result.match_rate == 1.0
    assert result.mean_score == 1.0
    assert result.total_samples == 2
    assert [d.label for d in result.document_results] == ["invoice"]
    assert all(f.match_rate == 1.0 for f in result.document_results[0].field_results)


def test_wrong_predictions_lose_the_match_but_keep_partial_credit(run_dir):
    tmp_path, config = run_dir
    predictions = tmp_path / "predictions"
    predictions.mkdir()
    (predictions / "invoice_001.pdf.json").write_text(
        json.dumps(
            [
                {
                    "label": "invoice",
                    "page": 0,
                    "fields": {
                        "total": "123.99",  # outside abs_tol
                        "seller": {"text": "ACME Ltd"},
                        "paid": "нет",  # ground truth says true
                        "stamp": [{"bbox": [0, 0, 10, 8]}],  # IoU 0.8, still a match
                    },
                }
            ]
        ),
        encoding="utf-8",
    )
    (predictions / "invoice_002.pdf.json").write_text("[]", encoding="utf-8")

    _, result = run(config, predictions_dir=predictions)
    by_field = {f.field_name: f for f in result.document_results[0].field_results}

    assert result.match_rate == 0.0
    assert 0.0 < result.mean_score < 1.0
    assert by_field["total"].match_rate == 0.0
    assert by_field["seller"].match_rate == 0.5  # right in one file, missing in the other
    assert by_field["stamp"].mean_score == pytest.approx(0.4)  # IoU 0.8, then nothing
    # The empty second file is a missed document, not a document that vanished.
    assert by_field["total"].field_metrics["fn"] == 1
    assert by_field["total"].field_metrics["fd"] == 1


def test_results_round_trip_through_json_and_html(run_dir, tmp_path):
    tmp_path, config = run_dir
    _, result = run(config)

    restored = MeasurementsResult.model_validate_json(result.model_dump_json())
    assert restored.match_rate == result.match_rate
    assert restored.mean_score == result.mean_score

    html = render_html(result, tmp_path / "out").read_text(encoding="utf-8")
    assert "Mean similarity" in html
    assert "invoice" in html
