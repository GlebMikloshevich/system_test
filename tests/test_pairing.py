"""Pairing gt documents to predicted ones inside one file."""

from __future__ import annotations

from conftest import doc_config, ground_truth, prediction

from ingoread_test.config.scorer_config import FieldConfig, FieldType
from ingoread_test.scoring import pair_documents

TOTAL = FieldConfig(field_name="total", field_type=FieldType.NUMBER)
SELLER = FieldConfig(field_name="seller", field_type=FieldType.TEXT)


def totals(pairs):
    return {
        (
            pair.gt.fields["total"].gt_value if pair.gt else None,
            pair.prediction.fields["total"][0].text if pair.prediction else None,
        )
        for pair in pairs
    }


def test_predictions_are_matched_to_the_gt_they_answer_not_their_order():
    cfg = doc_config(TOTAL, SELLER)
    pairs = pair_documents(
        [ground_truth(total=1.0, seller="Acme"), ground_truth(total=2.0, seller="Globex")],
        [prediction(total="2.0", seller="Globex"), prediction(total="1.0", seller="Acme")],
        cfg,
    )
    assert totals(pairs) == {(1.0, "1.0"), (2.0, "2.0")}
    assert all(pair.matched for pair in pairs)


def test_page_bboxes_drive_the_assignment_when_every_document_has_one():
    """With boxes on the page, position decides — even when the fields disagree."""
    cfg = doc_config(TOTAL)
    pairs = pair_documents(
        [
            ground_truth(bbox=[0, 0, 10, 10], total=1.0),
            ground_truth(bbox=[100, 100, 110, 110], total=2.0),
        ],
        [
            prediction(bbox=[100, 100, 110, 111], total="2.0"),
            prediction(bbox=[0, 0, 10, 9], total="1.0"),
        ],
        cfg,
    )
    assert totals(pairs) == {(1.0, "1.0"), (2.0, "2.0")}


def test_extra_prediction_is_reported_as_invented():
    cfg = doc_config(TOTAL)
    pairs = pair_documents(
        [ground_truth(total=1.0)],
        [prediction(total="1.0"), prediction(total="99.0")],
        cfg,
    )
    assert totals(pairs) == {(1.0, "1.0"), (None, "99.0")}
    assert sum(pair.matched for pair in pairs) == 1


def test_missing_prediction_is_reported_as_missed():
    cfg = doc_config(TOTAL)
    pairs = pair_documents(
        [ground_truth(total=1.0), ground_truth(total=2.0)], [prediction(total="1.0")], cfg
    )
    assert totals(pairs) == {(1.0, "1.0"), (2.0, None)}


def test_other_labels_are_left_to_their_own_config():
    cfg = doc_config(TOTAL, label="invoice")
    pairs = pair_documents(
        [ground_truth(label="receipt", total=1.0)],
        [prediction(label="receipt", total="1.0")],
        cfg,
    )
    assert pairs == []


def test_pages_are_kept_apart_unless_multipage_matching_is_on():
    single = doc_config(TOTAL, multipage_matching=False)
    gts = [ground_truth(page=0, total=1.0), ground_truth(page=1, total=2.0)]
    preds = [prediction(page=1, total="2.0"), prediction(page=0, total="1.0")]

    per_page = pair_documents(gts, preds, single)
    assert totals(per_page) == {(1.0, "1.0"), (2.0, "2.0")}

    # A prediction that landed on the wrong page can't be paired across pages.
    misplaced = pair_documents(gts, [prediction(page=1, total="1.0")], single)
    assert totals(misplaced) == {(1.0, None), (2.0, "1.0")}


def test_multipage_matching_pairs_across_pages():
    cfg = doc_config(TOTAL, multipage_matching=True)
    pairs = pair_documents(
        [ground_truth(page=0, total=1.0)], [prediction(page=7, total="1.0")], cfg
    )
    assert totals(pairs) == {(1.0, "1.0")}
    assert all(pair.matched for pair in pairs)


def test_split_below_match_threshold_breaks_up_an_unconvincing_pair():
    gts = [ground_truth(total=1.0, seller="Acme")]
    preds = [prediction(total="999.0", seller="Globex")]

    kept = pair_documents(gts, preds, doc_config(TOTAL, SELLER))
    assert totals(kept) == {(1.0, "999.0")}

    split = pair_documents(
        gts, preds, doc_config(TOTAL, SELLER, split_below_match_threshold=True)
    )
    assert totals(split) == {(1.0, None), (None, "999.0")}
