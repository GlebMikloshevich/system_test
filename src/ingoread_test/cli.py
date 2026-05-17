"""`ingoread-test run config.yaml` — entry point invoked by TFS."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer

from .config import IntegrationKind, load_configs
from .dataset import load_dataset
from .integration.stub import StubIntegration
from .modules import JsonFileSink, compare_to_previous, render_html, run_test, score
from .results.models import ComparativeStatus, MeasurementsResult

app = typer.Typer(add_completion=False, help="Ingoread test system CLI")


@app.callback()
def _root() -> None:
    """Keep this as a multi-command app so 'run' stays a subcommand."""


def _build_integration(kind: IntegrationKind, predictions_dir: Path | None):
    if kind == IntegrationKind.STUB:
        return StubIntegration(predictions_dir=predictions_dir)
    raise NotImplementedError(f"Integration kind {kind} not implemented in v1")


@app.command()
def run(
    config: Path = typer.Argument(..., exists=True, readable=True),
    previous: Path | None = typer.Option(None, "--previous", help="Path to a prior result JSON"),
    no_history: bool = typer.Option(False, "--no-history"),
    no_viz: bool = typer.Option(False, "--no-viz"),
    results_dir: Path = typer.Option(Path("results"), "--results-dir"),
) -> None:
    test_cfg, scorer_cfg = load_configs(config)
    dataset = load_dataset(test_cfg.files_root, test_cfg.manifest)
    integration = _build_integration(
        test_cfg.integration.kind, test_cfg.integration.stub_predictions_dir
    )

    predictions, stats = asyncio.run(run_test(test_cfg, integration, dataset))
    result = score(test_cfg, scorer_cfg, dataset, predictions, stats)

    sink = JsonFileSink(results_dir)
    json_path = sink.write(result)
    typer.echo(f"results_json={json_path.resolve()}")

    if not no_viz:
        html_path = render_html(result, results_dir)
        typer.echo(f"results_html={html_path.resolve()}")

    exit_code = 0
    if previous and not no_history:
        prev_result = MeasurementsResult.model_validate_json(previous.read_text(encoding="utf-8"))
        comparison = compare_to_previous(result, prev_result, test_cfg.history)
        typer.echo(f"history_status={comparison.status.value}")
        typer.echo(f"history_delta={comparison.overall_delta:+.4f}")
        for note in comparison.notes:
            typer.echo(f"history_note={note}")
        if (
            comparison.status == ComparativeStatus.DEGRADED
            and test_cfg.history.fail_on_regression
        ):
            exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    app()
