"""Output: where a run's artifacts are written, and what they look like.

`sinks` persists the result (locally, then to S3) and reads a baseline back;
`html` renders the self-contained report. Both consume a finished
`MeasurementsResult` and neither influences the verdict.
"""

from .html import render_html, render_suite_html
from .sinks import (
    JsonFileSink,
    RunArtifacts,
    S3ResultSink,
    Sink,
    read_result,
    run_folder_key,
    upload_run,
)

__all__ = [
    "JsonFileSink",
    "RunArtifacts",
    "S3ResultSink",
    "Sink",
    "read_result",
    "render_html",
    "render_suite_html",
    "run_folder_key",
    "upload_run",
]
