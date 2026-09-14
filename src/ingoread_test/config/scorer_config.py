"""ScorerConfig, DocumentMeasurerConfig, FieldConfig + enums.

Scoring is delegated to stickler (``stickler-eval``): every field type maps to
a stickler comparator, every document type to a generated ``StructuredModel``.
``field_type`` stays the ergonomic front door — ``comparator`` /
``comparator_kwargs`` are the escape hatch to any comparator stickler knows.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class FieldType(str, Enum):
    """Shorthand for a stickler comparator + the value shape it compares."""

    NUMBER = "number"  # NumericComparator
    TEXT = "text"  # ExactComparator
    BOOL = "bool"  # ExactComparator over booleans
    FUZZY_TEXT = "fuzzy_text"  # LevenshteinComparator (graded similarity)
    LLM_TEXT = "llm_text"  # LLMComparator — needs stickler-eval[llm]
    BBOX = "bbox"  # BBoxIoUComparator, one box
    BBOX_SET = "bbox_set"  # BBoxIoUComparator, many boxes matched set-to-set
    LITERAL = "literal"  # ExactComparator
    DATE = "date"  # DateComparator
    PHONE = "phone"  # PhoneComparator


class PredictionSelection(str, Enum):
    """How to handle multiple predicted values for one field.

    FIRST scores a single value. TOP_N and ALL turn the field into a list that
    stickler matches set-to-set (Hungarian), so order doesn't matter and extra
    or missing values show up as false alarms / false negatives.
    """

    FIRST = "first"
    TOP_N = "top_n"
    ALL = "all"


class FieldConfig(BaseModel):
    field_name: str
    field_type: FieldType
    # --- stickler knobs -------------------------------------------------
    # Relative importance in the document's overall score (stickler weight).
    weight: float = 1.0
    # Similarity at or above which the field counts as matched. None keeps the
    # per-type default (see scoring.comparators.DEFAULT_THRESHOLDS).
    threshold: float | None = None
    # Zero out similarity scores that fall under the threshold.
    clip_under_threshold: bool = True
    # Escape hatch: name any comparator stickler's registry knows
    # (LevenshteinComparator, FuzzyComparator, SemanticComparator, ...).
    # Overrides the comparator implied by field_type.
    comparator: str | None = None
    # Kwargs passed to the comparator constructor; wins over measurer_kwargs.
    comparator_kwargs: dict = Field(default_factory=dict)
    # Legacy per-type knobs, still honored: {strip, casefold} for text,
    # {abs_tol, rel_tol} for number, {iou_threshold} for bbox/bbox_set.
    measurer_kwargs: dict = Field(default_factory=dict)
    selection: PredictionSelection = PredictionSelection.FIRST
    top_n: int | None = None
    field_group: str | None = None
    take_first: bool | None = None
    # When true the field is not scored at all: it's left out of the generated
    # model, doesn't contribute to the document match, and isn't reported. Use
    # to keep a field documented in the config while excluding noisy ones.
    ignore: bool = False

    @model_validator(mode="after")
    def _coerce_take_first(self) -> FieldConfig:
        if self.take_first is not None:
            # Legacy alias: take_first=True == FIRST; False == ALL.
            self.selection = (
                PredictionSelection.FIRST if self.take_first else PredictionSelection.ALL
            )
        if self.selection == PredictionSelection.TOP_N and not self.top_n:
            raise ValueError(
                f"Field {self.field_name!r} uses selection=top_n but no top_n was set."
            )
        return self


class DocumentMeasurerConfig(BaseModel):
    doc_label: str
    fields: list[FieldConfig] = Field(default_factory=list)
    multipage_matching: bool = True
    # Similarity at or above which a (gt, prediction) document counts as a
    # match when pairing many-to-many; drives stickler's Hungarian assignment.
    match_threshold: float = 0.7
    # When true, an assignment scoring under `match_threshold` is not reported
    # as a pair at all: the gt becomes a missed document and the prediction a
    # hallucinated one. Off by default — keeping the pair usually shows more
    # clearly which fields went wrong.
    split_below_match_threshold: bool = False


class ScorerConfig(BaseModel):
    name: str = "default"
    measurement_configs: list[DocumentMeasurerConfig] = Field(default_factory=list)
