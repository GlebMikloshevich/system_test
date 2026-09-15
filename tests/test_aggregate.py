from ingoread_test.config.scorer_config import (
    DocumentMeasurerConfig,
    FieldConfig,
    FieldType,
    ScorerConfig,
)
from ingoread_test.config.test_config import TestConfig
from ingoread_test.dataset.models import Dataset, DocumentContainer, DocumentGT, FieldGT
from ingoread_test.integration.runner import run_test
from ingoread_test.integration.stub import StubIntegration
from ingoread_test.scoring.aggregate import score


def _make_dataset() -> Dataset:
    return Dataset(
        containers=[
            DocumentContainer(
                filename="a",
                documents=[
                    DocumentGT(
                        doc_label="invoice",
                        fields={
                            "total": FieldGT(gt_value="10.0"),
                            "vendor": FieldGT(gt_value="Acme"),
                        },
                    )
                ],
            ),
            DocumentContainer(
                filename="b",
                documents=[
                    DocumentGT(
                        doc_label="invoice",
                        fields={
                            "total": FieldGT(gt_value="20.0"),
                            "vendor": FieldGT(gt_value="Beta"),
                        },
                    )
                ],
            ),
        ]
    )


def _scorer_cfg() -> ScorerConfig:
    return ScorerConfig(
        measurement_configs=[
            DocumentMeasurerConfig(
                doc_label="invoice",
                fields=[
                    FieldConfig(field_name="total", field_type=FieldType.NUMBER),
                    FieldConfig(field_name="vendor", field_type=FieldType.TEXT),
                ],
            )
        ]
    )


def _test_cfg() -> TestConfig:
    return TestConfig(name="t", files_root="/tmp", manifest="/tmp/m.yaml")


async def test_end_to_end_with_stub():
    dataset = _make_dataset()
    integration = StubIntegration()  # echoes GT — everything matches
    predictions, stats = await run_test(_test_cfg(), integration, dataset)
    result = score(_test_cfg(), _scorer_cfg(), dataset, predictions, stats)
    assert result.total_samples == 2
    assert result.match_rate == 1.0
    assert len(result.document_results) == 1
    invoice = result.document_results[0]
    assert invoice.label == "invoice"
    assert invoice.match_rate == 1.0
    assert {f.field_name for f in invoice.field_results} == {"total", "vendor"}


async def test_ignored_field_does_not_affect_match_or_report():
    """An ignored field that would otherwise fail must not lower the match rate
    nor appear in the per-field results."""
    dataset = _make_dataset()
    # Predict a wrong value for `vendor`, but mark it ignore=True.
    scorer_cfg = ScorerConfig(
        measurement_configs=[
            DocumentMeasurerConfig(
                doc_label="invoice",
                fields=[
                    FieldConfig(field_name="total", field_type=FieldType.NUMBER),
                    FieldConfig(field_name="vendor", field_type=FieldType.TEXT, ignore=True),
                    FieldConfig(field_name="missing", field_type=FieldType.TEXT, ignore=True),
                ],
            )
        ]
    )
    # Stub echoes GT for total/vendor; the ignored `missing` field has no
    # prediction, which would normally count as a miss.
    predictions, stats = await run_test(_test_cfg(), StubIntegration(), dataset)
    result = score(_test_cfg(), scorer_cfg, dataset, predictions, stats)
    assert result.match_rate == 1.0
    invoice = result.document_results[0]
    assert {f.field_name for f in invoice.field_results} == {"total"}
