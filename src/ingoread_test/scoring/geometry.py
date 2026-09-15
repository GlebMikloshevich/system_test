"""Bounding-box geometry shared by the field scorers and the document pairer.

Boxes are ``[x1, y1, x2, y2]`` in pixel coordinates, top-left origin.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linear_sum_assignment

Box = list[float]


def is_valid_box(box: Box | None) -> bool:
    """A box is usable only if it is present and has all four coordinates."""
    return box is not None and len(box) == 4


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two boxes; 0.0 when they don't overlap."""
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    overlap_w = max(0.0, min(ax2, bx2) - max(ax1, bx1))
    overlap_h = max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = overlap_w * overlap_h
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def best_matching_ious(gt_boxes: list[Box], pred_boxes: list[Box]) -> list[float]:
    """IoU of the best one-to-one assignment between GT and predicted boxes.

    Returns one IoU per assigned pair (``min(len(gt), len(pred))`` of them),
    chosen by the Hungarian algorithm to maximize total overlap. Predictions in
    a different order than the GT are therefore still paired correctly.
    """
    if not gt_boxes or not pred_boxes:
        return []
    # linear_sum_assignment minimizes, so feed it 1 - IoU (a "cost").
    cost = np.array(
        [[1.0 - iou(gt, pred) for pred in pred_boxes] for gt in gt_boxes],
        dtype=float,
    )
    rows, cols = linear_sum_assignment(cost)
    return [iou(gt_boxes[i], pred_boxes[j]) for i, j in zip(rows, cols, strict=True)]
