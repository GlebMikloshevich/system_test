"""Pair ground-truth and predicted documents with stickler's Hungarian matcher.

When a file holds several documents of the same type, which prediction answers
which ground truth is itself a matching problem: score every gt against every
prediction and take the assignment that maximizes total similarity. Similarity
comes from `StructuredModelComparator` (the whole document, field by field), or
from `BBoxIoUComparator` when every document carries a page box — then position
on the page is the more reliable signal. Unmatched gts and predictions are kept
as half pairs so they still show up in the report.
"""

from __future__ import annotations

from itertools import groupby

from stickler import BBoxIoUComparator, StructuredModel, StructuredModelComparator
from stickler.algorithms.hungarian import HungarianMatcher

from ..config.scorer_config import DocumentMeasurerConfig
from ..dataset.models import DocumentGT
from ..integration.schemas import IngoreadDocument
from ..results.models import DocumentPair
from .adapters import empty_model, gt_to_model, prediction_to_model
from .evaluator import build_pair, compare_models
from .models import build_document_model, scored_fields


def _valid_bbox(bbox: list[float] | None) -> bool:
    return bbox is not None and len(bbox) == 4


def _all_have_bbox(gts: list[DocumentGT], preds: list[IngoreadDocument]) -> bool:
    """True only if every doc carries a usable 4-element bbox.

    Length is validated here so the IoU path can't be handed a malformed box; a
    doc with one falls back to whole-document similarity instead.
    """
    return all(_valid_bbox(g.bbox) for g in gts) and all(_valid_bbox(p.bbox) for p in preds)


def _group_key(doc, multipage: bool) -> tuple:
    """Group documents by (page,) by default; ignore page for multipage matching."""
    if multipage:
        return ()
    return (getattr(doc, "page", 0),)


def _assign(
    gts: list[DocumentGT],
    preds: list[IngoreadDocument],
    gt_models: list[StructuredModel],
    pred_models: list[StructuredModel],
    cfg: DocumentMeasurerConfig,
) -> tuple[list[tuple[int, int]], list[list[float]]]:
    """Optimal (gt index, prediction index) assignment plus its similarity matrix."""
    if _all_have_bbox(gts, preds):
        comparator = BBoxIoUComparator(threshold=cfg.match_threshold)
        left, right = [g.bbox for g in gts], [p.bbox for p in preds]
    else:
        comparator = StructuredModelComparator(threshold=cfg.match_threshold)
        left, right = gt_models, pred_models
    matcher = HungarianMatcher(comparator=comparator, match_threshold=cfg.match_threshold)
    indices, similarity = matcher.match(left, right)
    return [(int(i), int(j)) for i, j in indices], similarity.tolist()


def _pair_within_group(
    gts: list[DocumentGT],
    preds: list[IngoreadDocument],
    cfg: DocumentMeasurerConfig,
) -> list[DocumentPair]:
    if not gts and not preds:
        return []

    cfg_fields = scored_fields(cfg)
    model = build_document_model(cfg)
    blank = empty_model(cfg_fields, model)
    gt_models = [gt_to_model(g, cfg_fields, model) for g in gts]
    pred_models = [prediction_to_model(p, cfg_fields, model) for p in preds]

    def missed(index: int) -> DocumentPair:
        """A ground truth no prediction answered."""
        return build_pair(
            gts[index],
            None,
            cfg_fields,
            compare_models(gt_models[index], blank),
            forced_miss=True,
        )

    def invented(index: int) -> DocumentPair:
        """A predicted document no ground truth asked for."""
        return build_pair(
            None,
            preds[index],
            cfg_fields,
            compare_models(blank, pred_models[index]),
            forced_miss=True,
        )

    if not preds:
        return [missed(i) for i in range(len(gts))]
    if not gts:
        return [invented(j) for j in range(len(preds))]

    assignment, similarity = _assign(gts, preds, gt_models, pred_models, cfg)

    pairs: list[DocumentPair] = []
    matched_gts: set[int] = set()
    matched_preds: set[int] = set()
    for i, j in assignment:
        if cfg.split_below_match_threshold and similarity[i][j] < cfg.match_threshold:
            # Too dissimilar to be the same document: report the gt as missed
            # and the prediction as invented, rather than as one bad pair.
            continue
        matched_gts.add(i)
        matched_preds.add(j)
        pairs.append(
            build_pair(
                gts[i],
                preds[j],
                cfg_fields,
                compare_models(gt_models[i], pred_models[j]),
                forced_miss=False,
            )
        )

    pairs.extend(missed(i) for i in range(len(gts)) if i not in matched_gts)
    pairs.extend(invented(j) for j in range(len(preds)) if j not in matched_preds)
    return pairs


def pair_documents(
    gts: list[DocumentGT],
    preds: list[IngoreadDocument],
    cfg: DocumentMeasurerConfig,
) -> list[DocumentPair]:
    """Pair gt and predicted documents that share `cfg.doc_label`.

    Groups by page unless `cfg.multipage_matching` is True.
    """
    gts = [g for g in gts if g.doc_label == cfg.doc_label]
    preds = [p for p in preds if p.label == cfg.doc_label]

    multipage = cfg.multipage_matching
    gts_sorted = sorted(gts, key=lambda d: _group_key(d, multipage))
    preds_sorted = sorted(preds, key=lambda d: _group_key(d, multipage))

    gt_groups = {k: list(v) for k, v in groupby(gts_sorted, key=lambda d: _group_key(d, multipage))}
    pred_groups = {
        k: list(v) for k, v in groupby(preds_sorted, key=lambda d: _group_key(d, multipage))
    }
    all_keys = set(gt_groups) | set(pred_groups)
    pairs: list[DocumentPair] = []
    for key in sorted(all_keys):
        pairs.extend(
            _pair_within_group(gt_groups.get(key, []), pred_groups.get(key, []), cfg)
        )
    return pairs
