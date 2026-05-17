"""Hungarian pairing between ground-truth and predicted documents.

The diagram says: when |gt| != |pred|, pair every-with-every and use the
Hungarian algorithm to minimize the cost (1 - match_rate, or 1 - IoU when
all candidates have bboxes). Unmatched gts and preds are kept as
half-pairs so they show up in the final report.
"""

from __future__ import annotations

from itertools import groupby

import numpy as np
from scipy.optimize import linear_sum_assignment

from ..config.scorer_config import DocumentMeasurerConfig
from ..dataset.models import DocumentGT
from ..integration.schemas import IngoreadDocument
from ..results.models import DocumentPair
from .document_scorer import score_document_pair


def _all_have_bbox(gts: list[DocumentGT], preds: list[IngoreadDocument]) -> bool:
    return all(g.bbox is not None for g in gts) and all(p.bbox is not None for p in preds)


def _iou(a: list[float], b: list[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    inter_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _group_key(doc, multipage: bool) -> tuple:
    """Group documents by (page,) by default; ignore page for multipage matching."""
    if multipage:
        return ()
    return (getattr(doc, "page", 0),)


def _pair_within_group(
    gts: list[DocumentGT],
    preds: list[IngoreadDocument],
    cfg: DocumentMeasurerConfig,
) -> list[DocumentPair]:
    if not gts and not preds:
        return []
    if not preds:
        return [score_document_pair(g, None, cfg) for g in gts]
    if not gts:
        return [score_document_pair(None, p, cfg) for p in preds]
    if len(gts) == 1 and len(preds) == 1:
        return [score_document_pair(gts[0], preds[0], cfg)]

    n, m = len(gts), len(preds)
    use_iou = _all_have_bbox(gts, preds)
    cost = np.ones((n, m), dtype=float)
    pair_cache: dict[tuple[int, int], DocumentPair] = {}
    for i, g in enumerate(gts):
        for j, p in enumerate(preds):
            if use_iou:
                cost[i, j] = 1.0 - _iou(g.bbox, p.bbox)
            else:
                pair = score_document_pair(g, p, cfg)
                pair_cache[(i, j)] = pair
                cost[i, j] = 1.0 - pair.document_param_metrics.get(
                    "fraction_fields_matched", 0.0
                )

    row_ind, col_ind = linear_sum_assignment(cost)
    matched_gts: set[int] = set()
    matched_preds: set[int] = set()
    pairs: list[DocumentPair] = []
    for i, j in zip(row_ind, col_ind):
        matched_gts.add(int(i))
        matched_preds.add(int(j))
        pair = pair_cache.get((int(i), int(j))) or score_document_pair(gts[i], preds[j], cfg)
        pairs.append(pair)

    for i, g in enumerate(gts):
        if i not in matched_gts:
            pairs.append(score_document_pair(g, None, cfg))
    for j, p in enumerate(preds):
        if j not in matched_preds:
            pairs.append(score_document_pair(None, p, cfg))
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
