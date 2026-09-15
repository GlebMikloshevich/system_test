"""Orchestration: the sequence a run goes through, for one dataset or many.

`run.execute_run` is the single pipeline — open the dataset, send it, score it,
publish the artifacts, decide the gate — and `suite.run_suite` drives it once
per member. Both leave *policy* (how to report progress, what to do with a
failure) to the caller.
"""

from .dataset import open_dataset
from .run import EmptyDatasetError, RunOutcome, RunRequest, execute_run
from .suite import aggregate_suite, run_suite

__all__ = [
    "EmptyDatasetError",
    "RunOutcome",
    "RunRequest",
    "aggregate_suite",
    "execute_run",
    "open_dataset",
    "run_suite",
]
