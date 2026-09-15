import pytest

from ingoread_test.config.scorer_config import (
    FieldConfig,
    FieldType,
    PredictionSelection,
)
from ingoread_test.integration.schemas import IngoreadField
from ingoread_test.scoring.field_scorers import FIELD_SCORERS, select_prediction


def _cfg(field_type: FieldType, **kw) -> FieldConfig:
    return FieldConfig(field_name="x", field_type=field_type, **kw)


def test_text_scorer_exact_match():
    res = FIELD_SCORERS[FieldType.TEXT](
        "hello", [IngoreadField(text="hello")], _cfg(FieldType.TEXT)
    )
    assert res.matched
    assert res.metrics["cer"] == 0.0


def test_text_scorer_partial():
    res = FIELD_SCORERS[FieldType.TEXT](
        "hello", [IngoreadField(text="helli")], _cfg(FieldType.TEXT)
    )
    assert not res.matched
    assert 0 < res.metrics["cer"] <= 1


def test_bool_scorer():
    cfg = _cfg(FieldType.BOOL)
    assert FIELD_SCORERS[FieldType.BOOL]("true", [IngoreadField(text="да")], cfg).matched
    assert not FIELD_SCORERS[FieldType.BOOL]("true", [IngoreadField(text="no")], cfg).matched
    assert FIELD_SCORERS[FieldType.BOOL]("false", [IngoreadField(text="нет")], cfg).matched


def test_bool_scorer_unrecognized_prediction_gets_no_credit():
    # Garbage or a missing prediction must not count as predicting "false".
    cfg = _cfg(FieldType.BOOL)
    assert not FIELD_SCORERS[FieldType.BOOL]("false", [IngoreadField(text="???")], cfg).matched
    assert not FIELD_SCORERS[FieldType.BOOL]("false", [], cfg).matched


def test_number_scorer():
    cfg = _cfg(FieldType.NUMBER)
    res = FIELD_SCORERS[FieldType.NUMBER]("10", [IngoreadField(text="11")], cfg)
    assert not res.matched
    assert res.metrics["mae"] == 1.0
    assert res.metrics["mse"] == 1.0


def test_number_scorer_abs_tolerance():
    cfg = _cfg(FieldType.NUMBER, measurer_kwargs={"abs_tol": 0.5})
    res = FIELD_SCORERS[FieldType.NUMBER]("10.0", [IngoreadField(text="10.3")], cfg)
    assert res.matched
    assert res.metrics["mae"] == pytest.approx(0.3)


def test_text_scorer_strip_and_casefold():
    cfg = _cfg(FieldType.TEXT, measurer_kwargs={"strip": True, "casefold": True})
    res = FIELD_SCORERS[FieldType.TEXT]("Acme", [IngoreadField(text="  acme  ")], cfg)
    assert res.matched
    assert res.metrics["cer"] == 0.0


def test_text_scorer_exact_by_default():
    cfg = _cfg(FieldType.TEXT)
    res = FIELD_SCORERS[FieldType.TEXT]("Acme", [IngoreadField(text=" acme ")], cfg)
    assert not res.matched


def test_bbox_scorer_iou():
    cfg = _cfg(FieldType.BBOX, measurer_kwargs={"iou_threshold": 0.5})
    pred = IngoreadField(bbox=[0, 0, 10, 10])
    res = FIELD_SCORERS[FieldType.BBOX]("0,0,10,10", [pred], cfg)
    assert res.matched
    assert res.metrics["iou"] == 1.0


def _stamps(*boxes):
    return [IngoreadField(bbox=list(b)) for b in boxes]


def test_bbox_set_two_stamps_found():
    """Both stamps present and well-localized -> matched, with mean IoU."""
    cfg = _cfg(FieldType.BBOX_SET, measurer_kwargs={"iou_threshold": 0.5})
    gt = "348,2413,798,2698; 1877,2427,2200,2671"
    # Predictions in the opposite order -> matcher still pairs them correctly.
    preds = _stamps([1877, 2427, 2200, 2671], [348, 2413, 798, 2698])
    res = FIELD_SCORERS[FieldType.BBOX_SET](gt, preds, cfg)
    assert res.matched
    assert res.metrics["count_gt"] == 2.0
    assert res.metrics["count_pred"] == 2.0
    assert res.metrics["count_match"] == 1.0
    assert res.metrics["iou"] == 1.0
    assert res.metrics["recall"] == 1.0


def test_bbox_set_only_one_found():
    """Missing the second stamp -> not matched; recall reflects 1 of 2."""
    cfg = _cfg(FieldType.BBOX_SET, measurer_kwargs={"iou_threshold": 0.5})
    gt = "348,2413,798,2698; 1877,2427,2200,2671"
    res = FIELD_SCORERS[FieldType.BBOX_SET](gt, _stamps([348, 2413, 798, 2698]), cfg)
    assert not res.matched
    assert res.metrics["count_match"] == 0.0
    assert res.metrics["recall"] == 0.5


def test_bbox_set_right_count_bad_localization():
    """Two found but one below the IoU threshold -> not matched."""
    cfg = _cfg(FieldType.BBOX_SET, measurer_kwargs={"iou_threshold": 0.5})
    gt = "0,0,10,10; 100,100,110,110"
    # second box barely overlaps its GT
    preds = _stamps([0, 0, 10, 10], [105, 105, 130, 130])
    res = FIELD_SCORERS[FieldType.BBOX_SET](gt, preds, cfg)
    assert res.metrics["count_match"] == 1.0
    assert not res.matched
    assert res.metrics["recall"] == 0.5


def test_bbox_set_over_detection_fails_count():
    """Three predicted for two GT -> count mismatch, not matched."""
    cfg = _cfg(FieldType.BBOX_SET, measurer_kwargs={"iou_threshold": 0.5})
    gt = "0,0,10,10; 100,100,110,110"
    preds = _stamps([0, 0, 10, 10], [100, 100, 110, 110], [200, 200, 210, 210])
    res = FIELD_SCORERS[FieldType.BBOX_SET](gt, preds, cfg)
    assert res.metrics["count_match"] == 0.0
    assert not res.matched


def test_selection_first_is_default():
    cfg = _cfg(FieldType.TEXT)
    preds = [IngoreadField(text="a"), IngoreadField(text="b")]
    assert select_prediction(preds, cfg) == preds[:1]


def test_selection_top_n_placeholder():
    cfg = _cfg(FieldType.TEXT, selection=PredictionSelection.TOP_N, top_n=2)
    with pytest.raises(NotImplementedError):
        select_prediction([IngoreadField(text="a")], cfg)


def test_selection_all_placeholder():
    cfg = _cfg(FieldType.TEXT, selection=PredictionSelection.ALL)
    with pytest.raises(NotImplementedError):
        select_prediction([IngoreadField(text="a")], cfg)


def test_take_first_legacy_alias():
    cfg = FieldConfig(field_name="x", field_type=FieldType.TEXT, take_first=False)
    assert cfg.selection == PredictionSelection.ALL
    cfg_true = FieldConfig(field_name="x", field_type=FieldType.TEXT, take_first=True)
    assert cfg_true.selection == PredictionSelection.FIRST
