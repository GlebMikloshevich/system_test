"""Score a single (gt, prediction) document pair across all configured fields."""

from __future__ import annotations

from collections import defaultdict

from ..config.scorer_config import DocumentMeasurerConfig
from ..dataset.models import DocumentGT
from ..integration.schemas import IngoreadDocument
from ..results.models import DocumentPair
from .field_scorers import FIELD_SCORERS


def score_document_pair(
    gt: DocumentGT | None,
    prediction: IngoreadDocument | None,
    cfg: DocumentMeasurerConfig,
) -> DocumentPair:
    """Compute per-field scores. Handles unmatched (gt-only / pred-only) pairs."""
    if gt is None and prediction is None:
        return DocumentPair(matched=False)
    if gt is None:
        return DocumentPair(prediction=prediction, matched=False, field_metrics={})
    if prediction is None:
        field_metrics = {
            fc.field_name: {"matched": False} for fc in cfg.fields if not fc.ignore
        }
        return DocumentPair(gt=gt, matched=False, field_metrics=field_metrics)

    field_metrics: dict[str, dict] = {}
    group_results: dict[str, list[bool]] = defaultdict(list)
    individual_matches: list[bool] = []

    for fc in cfg.fields:
        if fc.ignore:
            continue
        scorer = FIELD_SCORERS[fc.field_type]
        gt_field = gt.fields.get(fc.field_name)
        gt_value = gt_field.gt_value if gt_field else ""
        preds = prediction.fields.get(fc.field_name, [])
        result = scorer(gt_value, preds, fc)
        field_metrics[fc.field_name] = {"matched": result.matched, **result.metrics}
        if fc.field_group:
            group_results[fc.field_group].append(result.matched)
        else:
            individual_matches.append(result.matched)

    for group_name, matches in group_results.items():
        individual_matches.append(any(matches))
        field_metrics[f"__group__{group_name}"] = {"matched": any(matches)}

    matched = all(individual_matches) if individual_matches else True
    return DocumentPair(
        gt=gt,
        prediction=prediction,
        matched=matched,
        field_metrics=field_metrics,
        document_param_metrics={
            "fraction_fields_matched": (
                sum(individual_matches) / len(individual_matches)
                if individual_matches
                else 1.0
            )
        },
    )
