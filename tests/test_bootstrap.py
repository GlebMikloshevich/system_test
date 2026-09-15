"""Bootstrapping a draft GT manifest from a result JSON's predictions."""

from datetime import UTC, datetime

import yaml

from ingoread_test.dataset import load_dataset
from ingoread_test.dataset.bootstrap import result_to_manifest_entries
from ingoread_test.dataset.models import DocumentContainer
from ingoread_test.integration.schemas import (
    IngoreadDocument,
    IngoreadField,
    IngoreadFileResult,
)
from ingoread_test.results.models import DocumentContainerPair, MeasurementsResult


def _result_with_prediction(pred: IngoreadFileResult) -> MeasurementsResult:
    return MeasurementsResult(
        test_config_name="t",
        scorer_config_name="s",
        start_date=datetime.now(UTC),
        total_time=1.0,
        total_samples=1,
        time_per_sample=1.0,
        match_rate=1.0,
        container_pairs=[
            DocumentContainerPair(
                filename=pred.filename,
                gts=DocumentContainer(filename=pred.filename),
                predictions=pred,
            )
        ],
    )


def _prediction() -> IngoreadFileResult:
    return IngoreadFileResult(
        filename="vrc_001.pdf",
        result=[
            IngoreadDocument(
                label="vehicle_registration",
                page=1,
                bbox=[10, 20, 30, 40],
                fields={
                    "vin": [IngoreadField(text="JTHBK1GG1F2123456", text_confidence=0.4)],
                    "signature_box": [IngoreadField(bbox=[900, 820, 1180, 990])],
                    "blank": [],
                },
            )
        ],
    )


def test_result_to_manifest_entries():
    entries = result_to_manifest_entries(_result_with_prediction(_prediction()))
    assert entries == [
        {
            "filename": "vrc_001.pdf",
            "documents": [
                {
                    "doc_label": "vehicle_registration",
                    "page": 1,
                    "bbox": [10, 20, 30, 40],
                    "fields": {
                        # first prediction's text, regardless of confidence (emit as-is)
                        "vin": {"gt_value": "JTHBK1GG1F2123456"},
                        # bbox-only field serialized to the manifest bbox string form
                        "signature_box": {"gt_value": "900,820,1180,990"},
                        # no predictions -> empty draft value for the human to fill
                        "blank": {"gt_value": ""},
                    },
                }
            ],
        }
    ]


def test_bootstrap_round_trips_through_loader(tmp_path):
    entries = result_to_manifest_entries(_result_with_prediction(_prediction()))
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(yaml.safe_dump(entries), encoding="utf-8")

    dataset = load_dataset(tmp_path, manifest)
    gt = dataset.containers[0].documents[0]
    assert gt.doc_label == "vehicle_registration"
    assert gt.page == 1
    assert gt.bbox == [10, 20, 30, 40]
    assert gt.fields["vin"].gt_value == "JTHBK1GG1F2123456"
    assert gt.fields["signature_box"].gt_value == "900,820,1180,990"
