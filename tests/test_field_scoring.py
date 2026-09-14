"""Per-field scoring: does each field type reach the right stickler comparator?"""

from __future__ import annotations

import pytest
from conftest import boxes, doc_config, ground_truth, prediction

from ingoread_test.config.scorer_config import FieldConfig, FieldType, PredictionSelection
from ingoread_test.scoring import score_document_pair


def field_result(field: FieldConfig, gt_value, predicted) -> dict:
    cfg = doc_config(field)
    pair = score_document_pair(
        ground_truth(**{field.field_name: gt_value}),
        prediction(**{field.field_name: predicted}),
        cfg,
    )
    return pair.field_metrics[field.field_name]


@pytest.mark.parametrize(
    ("gt_value", "predicted", "matched"),
    [
        ("ACME Ltd", "ACME Ltd", True),
        ("ACME Ltd", "acme ltd", False),  # text is exact, case included
        ("ACME Ltd", "ACME Lt", False),
    ],
)
def test_text_is_exact_by_default(gt_value, predicted, matched):
    field = FieldConfig(field_name="seller", field_type=FieldType.TEXT)
    assert field_result(field, gt_value, predicted)["matched"] is matched


def test_text_honors_legacy_strip_and_casefold():
    field = FieldConfig(
        field_name="seller",
        field_type=FieldType.TEXT,
        measurer_kwargs={"strip": True, "casefold": True},
    )
    assert field_result(field, "  ACME Ltd ", "acme ltd")["matched"] is True


def test_fuzzy_text_gives_partial_credit():
    field = FieldConfig(field_name="who", field_type=FieldType.FUZZY_TEXT, threshold=0.8)
    result = field_result(field, "Ivan Petrov", "Ivan Petrow")
    assert result["matched"] is True
    assert 0.8 < result["score"] < 1.0


@pytest.mark.parametrize(
    ("predicted", "matched"),
    [("123.45", True), ("123.452", True), ("123.99", False), ("n/a", False)],
)
def test_number_tolerance(predicted, matched):
    field = FieldConfig(
        field_name="total", field_type=FieldType.NUMBER, measurer_kwargs={"abs_tol": 0.01}
    )
    assert field_result(field, 123.45, predicted)["matched"] is matched


def test_unparsable_number_is_a_wrong_answer_not_a_missing_one():
    field = FieldConfig(field_name="total", field_type=FieldType.NUMBER)
    result = field_result(field, 123.45, "n/a")
    assert result["fd"] == 1 and result["fn"] == 0


def test_absent_field_is_a_missing_value():
    cfg = doc_config(FieldConfig(field_name="total", field_type=FieldType.NUMBER))
    pair = score_document_pair(ground_truth(total=123.45), prediction(), cfg)
    result = pair.field_metrics["total"]
    assert result["matched"] is False
    assert result["fn"] == 1 and result["fd"] == 0


def test_invented_field_is_a_false_alarm():
    cfg = doc_config(FieldConfig(field_name="total", field_type=FieldType.NUMBER))
    pair = score_document_pair(ground_truth(), prediction(total="123.45"), cfg)
    assert pair.field_metrics["total"]["fa"] == 1


@pytest.mark.parametrize(
    ("gt_value", "predicted", "matched"),
    [(True, "true", True), (True, "да", True), (False, "нет", True), (True, "нет", False)],
)
def test_bool_parses_truthy_text(gt_value, predicted, matched):
    field = FieldConfig(field_name="paid", field_type=FieldType.BOOL)
    assert field_result(field, gt_value, predicted)["matched"] is matched


def test_bbox_scores_iou_against_its_threshold():
    field = FieldConfig(
        field_name="stamp", field_type=FieldType.BBOX, measurer_kwargs={"iou_threshold": 0.5}
    )
    near = field_result(field, [0, 0, 10, 10], boxes([0, 0, 10, 8]))
    far = field_result(field, [0, 0, 10, 10], boxes([0, 0, 10, 4]))
    assert near["matched"] is True and near["score"] == pytest.approx(0.8)
    assert far["matched"] is False


def test_sub_threshold_similarity_is_clipped_unless_asked_otherwise():
    """No partial credit below the threshold by default — that's stickler's rule."""
    clipped = FieldConfig(field_name="who", field_type=FieldType.FUZZY_TEXT, threshold=0.95)
    graded = clipped.model_copy(update={"clip_under_threshold": False})
    assert field_result(clipped, "Ivan Petrov", "Ivan Petrow")["score"] == 0.0
    assert field_result(graded, "Ivan Petrov", "Ivan Petrow")["score"] == pytest.approx(
        0.909, abs=0.01
    )


def test_bbox_set_matches_boxes_regardless_of_order():
    field = FieldConfig(field_name="signatures", field_type=FieldType.BBOX_SET)
    result = field_result(
        field, [[0, 0, 5, 5], [20, 20, 25, 25]], boxes([20, 20, 25, 25], [0, 0, 5, 5])
    )
    assert result["matched"] is True and result["tp"] == 2


def test_bbox_set_reports_a_missing_box():
    field = FieldConfig(field_name="signatures", field_type=FieldType.BBOX_SET)
    result = field_result(field, [[0, 0, 5, 5], [20, 20, 25, 25]], boxes([0, 0, 5, 5]))
    assert result["matched"] is False
    assert result["tp"] == 1 and result["fn"] == 1
    assert result["score"] == pytest.approx(0.5)


def test_selection_first_ignores_later_candidates():
    field = FieldConfig(field_name="tag", field_type=FieldType.TEXT)
    assert field_result(field, "a", [{"text": "a"}, {"text": "zzz"}])["matched"] is True


def test_selection_all_compares_the_whole_set():
    field = FieldConfig(
        field_name="tags", field_type=FieldType.TEXT, selection=PredictionSelection.ALL
    )
    unordered = field_result(field, ["b", "a"], [{"text": "a"}, {"text": "b"}])
    incomplete = field_result(field, ["a", "b", "c"], [{"text": "a"}, {"text": "b"}])
    assert unordered["matched"] is True
    assert incomplete["matched"] is False and incomplete["fn"] == 1


def test_selection_top_n_truncates_candidates():
    field = FieldConfig(
        field_name="amounts",
        field_type=FieldType.NUMBER,
        selection=PredictionSelection.TOP_N,
        top_n=2,
    )
    result = field_result(field, [1.0, 2.0], [{"text": "2.0"}, {"text": "1.0"}, {"text": "99"}])
    assert result["matched"] is True


def test_take_first_false_still_means_all():
    field = FieldConfig(field_name="tags", field_type=FieldType.TEXT, take_first=False)
    assert field.selection == PredictionSelection.ALL


def test_top_n_selection_requires_a_count():
    with pytest.raises(ValueError, match="top_n"):
        FieldConfig(
            field_name="a", field_type=FieldType.TEXT, selection=PredictionSelection.TOP_N
        )


def test_date_and_phone_normalize_formatting():
    issued = FieldConfig(field_name="issued", field_type=FieldType.DATE)
    tel = FieldConfig(
        field_name="tel", field_type=FieldType.PHONE, comparator_kwargs={"region": "RU"}
    )
    assert field_result(issued, "2024-01-05", "05.01.2024")["matched"] is True
    assert field_result(tel, "+7 495 123-45-67", "84951234567")["matched"] is True


def test_comparator_escape_hatch_reaches_any_stickler_comparator():
    field = FieldConfig(
        field_name="name",
        field_type=FieldType.TEXT,
        comparator="FuzzyComparator",
        comparator_kwargs={"method": "token_sort_ratio"},
        threshold=0.9,
    )
    assert field_result(field, "Acme Corp", "Corp Acme")["matched"] is True


def test_unknown_comparator_is_rejected_with_the_available_ones():
    field = FieldConfig(
        field_name="a", field_type=FieldType.TEXT, comparator="NopeComparator"
    )
    with pytest.raises(ValueError, match="ExactComparator"):
        score_document_pair(ground_truth(a="1"), prediction(a="1"), doc_config(field))


def test_mistyped_comparator_kwarg_is_reported_not_ignored():
    field = FieldConfig(
        field_name="total", field_type=FieldType.NUMBER, comparator_kwargs={"abs_tol": 1}
    )
    with pytest.raises(ValueError, match="abs_tol"):
        score_document_pair(ground_truth(total=1), prediction(total="1"), doc_config(field))


def test_reserved_field_name_is_rejected():
    field = FieldConfig(field_name="extra_fields", field_type=FieldType.TEXT)
    with pytest.raises(ValueError, match="reserves"):
        score_document_pair(ground_truth(), prediction(), doc_config(field))
