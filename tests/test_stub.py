"""StubIntegration's echo contract: every configured field type matches GT."""

from ingoread_test.config.scorer_config import (
    DocumentMeasurerConfig,
    FieldConfig,
    FieldType,
    ScorerConfig,
)
from ingoread_test.config.test_config import TestConfig
from ingoread_test.dataset.models import Dataset, DocumentContainer, DocumentGT, FieldGT
from ingoread_test.integration.stub import StubIntegration, _parse_bbox
from ingoread_test.modules.scorer_module import score
from ingoread_test.modules.test_module import run_test


def test_parse_bbox():
    assert _parse_bbox("900,820,1180,990") == [900.0, 820.0, 1180.0, 990.0]
    assert _parse_bbox("[1,2,3,4]") == [1.0, 2.0, 3.0, 4.0]
    assert _parse_bbox("white") is None
    assert _parse_bbox("1,2,3") is None


async def test_stub_echo_matches_bbox_field():
    """The echo stub must satisfy bbox-typed fields, not just text ones."""
    dataset = Dataset(
        containers=[
            DocumentContainer(
                filename="a",
                documents=[
                    DocumentGT(
                        doc_label="vrc",
                        fields={
                            "vin": FieldGT(gt_value="JTHBK1GG1F2123456"),
                            "signature_box": FieldGT(gt_value="900,820,1180,990"),
                        },
                    )
                ],
            )
        ]
    )
    scorer_cfg = ScorerConfig(
        measurement_configs=[
            DocumentMeasurerConfig(
                doc_label="vrc",
                fields=[
                    FieldConfig(field_name="vin", field_type=FieldType.TEXT),
                    FieldConfig(field_name="signature_box", field_type=FieldType.BBOX),
                ],
            )
        ]
    )
    test_cfg = TestConfig(name="t", files_root="/tmp", manifest="/tmp/m.yaml")
    predictions, stats = await run_test(test_cfg, StubIntegration(), dataset)
    result = score(test_cfg, scorer_cfg, dataset, predictions, stats)
    assert result.match_rate == 1.0
