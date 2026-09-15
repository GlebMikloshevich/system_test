"""Release policy: is this build good enough to publish?

`history` diffs a run against its baseline; `release` turns that diff — plus the
run's own errors and coverage — into a blocked/allowed verdict. Nothing here
does I/O, so the rules that gate a release stay readable and directly testable.
"""

from .history import compare_to_previous
from .release import evaluate_release_gate, evaluate_suite_gate

__all__ = [
    "compare_to_previous",
    "evaluate_release_gate",
    "evaluate_suite_gate",
]
