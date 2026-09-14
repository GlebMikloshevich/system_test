"""Document-level scoring: field groups, ignored fields and half pairs."""

from __future__ import annotations

import pytest
from conftest import doc_config, ground_truth, prediction

from ingoread_test.config.scorer_config import FieldConfig, FieldType
from ingoread_test.scoring import score_document_pair

TOTAL = FieldConfig(field_name="total", field_type=FieldType.NUMBER)
SELLER = FieldConfig(field_name="seller", field_type=FieldType.TEXT)


def test_document_matches_only_when_every_field_does():
    cfg = doc_config(TOTAL, SELLER)
    good = score_document_pair(
        ground_truth(total=1.0, seller="Acme"), prediction(total="1.0", seller="Acme"), cfg
    )
    bad = score_document_pair(
        ground_truth(total=1.0, seller="Acme"), prediction(total="1.0", seller="Globex"), cfg
    )
    assert good.matched is True
    assert bad.matched is False
    assert bad.document_param_metrics["fraction_fields_matched"] == pytest.approx(0.5)


def test_weight_shifts_the_document_score_not_the_match():
    heavy = doc_config(
        FieldConfig(field_name="total", field_type=FieldType.NUMBER, weight=9.0), SELLER
    )
    light = doc_config(TOTAL, SELLER)
    gt, pred = ground_truth(total=1.0, seller="Acme"), prediction(total="2.0", seller="Acme")
    heavy_score = score_document_pair(gt, pred, heavy).document_param_metrics["score"]
    light_score = score_document_pair(gt, pred, light).document_param_metrics["score"]
    assert heavy_score < light_score
    assert score_document_pair(gt, pred, heavy).matched is False


def test_ignored_fields_are_left_out_entirely():
    cfg = doc_config(
        TOTAL, FieldConfig(field_name="note", field_type=FieldType.TEXT, ignore=True)
    )
    pair = score_document_pair(
        ground_truth(total=1.0, note="expected"), prediction(total="1.0", note="wrong"), cfg
    )
    assert pair.matched is True
    assert "note" not in pair.field_metrics


def test_field_group_matches_when_any_member_does():
    cfg = doc_config(
        FieldConfig(field_name="inn", field_type=FieldType.TEXT, field_group="id"),
        FieldConfig(field_name="ogrn", field_type=FieldType.TEXT, field_group="id"),
    )
    pair = score_document_pair(
        ground_truth(inn="123", ogrn="456"), prediction(inn="123", ogrn="WRONG"), cfg
    )
    assert pair.matched is True
    assert pair.field_metrics["__group__id"]["matched"] is True
    assert pair.field_metrics["ogrn"]["matched"] is False


def test_field_group_fails_when_no_member_matches():
    cfg = doc_config(
        FieldConfig(field_name="inn", field_type=FieldType.TEXT, field_group="id"),
        FieldConfig(field_name="ogrn", field_type=FieldType.TEXT, field_group="id"),
    )
    pair = score_document_pair(
        ground_truth(inn="123", ogrn="456"), prediction(inn="X", ogrn="Y"), cfg
    )
    assert pair.matched is False


def test_missed_document_scores_every_field_as_missing():
    cfg = doc_config(TOTAL, SELLER)
    pair = score_document_pair(ground_truth(total=1.0, seller="Acme"), None, cfg)
    assert pair.matched is False
    assert all(entry["fn"] == 1 for entry in pair.field_metrics.values())


def test_invented_document_scores_every_field_as_a_false_alarm():
    cfg = doc_config(TOTAL, SELLER)
    pair = score_document_pair(None, prediction(total="1.0", seller="Acme"), cfg)
    assert pair.matched is False
    assert all(entry["fa"] == 1 for entry in pair.field_metrics.values())


def test_an_empty_ground_truth_never_matches_a_missing_prediction():
    """A half pair is a miss even when there is nothing in it to compare."""
    pair = score_document_pair(ground_truth(), None, doc_config(TOTAL))
    assert pair.matched is False


def test_raw_comparison_is_kept_in_memory_but_not_serialized():
    cfg = doc_config(TOTAL)
    pair = score_document_pair(ground_truth(total=1.0), prediction(total="1.0"), cfg)
    assert pair.comparison is not None
    assert "comparison" not in pair.model_dump()
