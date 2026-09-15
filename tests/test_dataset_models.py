"""FieldGT stores gt_value in its native form; scorers read it via helpers."""

import asyncio

import pytest

from ingoread_test.config.scorer_config import (
    DocumentMeasurerConfig,
    FieldConfig,
    FieldType,
    ScorerConfig,
)
from ingoread_test.config.test_config import TestConfig
from ingoread_test.dataset import load_dataset
from ingoread_test.dataset.models import FieldGT, gt_to_boxes, gt_to_text
from ingoread_test.integration.runner import run_test
from ingoread_test.integration.stub import StubIntegration
from ingoread_test.scoring.aggregate import score


@pytest.mark.parametrize(
    "value",
    [
        "Acme",  # str
        184,  # int (kept as int)
        184.5,  # float
        True,  # bool
        [348.0, 2413.0, 798.0, 2698.0],  # one bbox (floats)
        [[348.0, 2413.0, 798.0, 2698.0], [1877.0, 2427.0, 2200.0, 2671.0]],  # stamps
    ],
)
def test_gt_value_kept_native(value):
    # No lossy intermediate string: the value round-trips unchanged.
    assert FieldGT(gt_value=value).gt_value == value


def test_gt_to_text():
    assert gt_to_text(True) == "true"
    assert gt_to_text(False) == "false"
    assert gt_to_text(184.0) == "184.0"
    assert gt_to_text(None) == ""
    assert gt_to_text("x") == "x"


def test_gt_to_boxes_native_and_string():
    assert gt_to_boxes([348.0, 2413.0, 798.0, 2698.0]) == [[348.0, 2413.0, 798.0, 2698.0]]
    assert gt_to_boxes([[1, 2, 3, 4], [5, 6, 7, 8]]) == [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]
    assert gt_to_boxes("348,2413,798,2698; 1877,2427,2200,2671") == [
        [348.0, 2413.0, 798.0, 2698.0],
        [1877.0, 2427.0, 2200.0, 2671.0],
    ]
    assert gt_to_boxes("Acme") == []
    assert gt_to_boxes(42) == []


def test_manifest_native_bbox_scores(tmp_path):
    """A manifest using native float lists loads and scores via the stub."""
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "- filename: f.pdf\n"
        "  documents:\n"
        "  - doc_label: vrc\n"
        "    fields:\n"
        "      signature_box: {gt_value: [900.0, 820.0, 1180.0, 990.0]}\n"
        "      stamps: {gt_value: [[348.0, 2413.0, 798.0, 2698.0], "
        "[1877.0, 2427.0, 2200.0, 2671.0]]}\n",
        encoding="utf-8",
    )
    ds = load_dataset(tmp_path, manifest)
    fields = ds.containers[0].documents[0].fields
    assert fields["signature_box"].gt_value == [900.0, 820.0, 1180.0, 990.0]  # native, not a string
    assert fields["stamps"].gt_value[1] == [1877.0, 2427.0, 2200.0, 2671.0]

    scorer_cfg = ScorerConfig(
        measurement_configs=[
            DocumentMeasurerConfig(
                doc_label="vrc",
                fields=[
                    FieldConfig(field_name="signature_box", field_type=FieldType.BBOX),
                    FieldConfig(field_name="stamps", field_type=FieldType.BBOX_SET),
                ],
            )
        ]
    )
    test_cfg = TestConfig(name="t", files_root=str(tmp_path), manifest=str(manifest))
    preds, stats = asyncio.run(run_test(test_cfg, StubIntegration(), ds))
    result = score(test_cfg, scorer_cfg, ds, preds, stats)
    assert result.match_rate == 1.0
