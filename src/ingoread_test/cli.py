"""`ingoread-test run config.yaml` — entry point invoked by TFS."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer
import yaml

from .config import IntegrationKind, load_configs
from .config.test_config import IntegrationConfig, TestConfig
from .dataset import load_dataset
from .integration.base import Integration
from .integration.http import HttpIngoreadIntegration
from .integration.stub import StubIntegration
from .modules import (
    JsonFileSink,
    compare_to_previous,
    evaluate_release_gate,
    render_html,
    run_test,
    score,
)
from .results.models import MeasurementsResult

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = typer.Typer(add_completion=False, help="Ingoread test system CLI")


@app.callback()
def _root() -> None:
    """Keep this as a multi-command app so 'run' stays a subcommand."""


def _build_integration(test_cfg: TestConfig) -> Integration:
    cfg: IntegrationConfig = test_cfg.integration
    if cfg.kind == IntegrationKind.STUB:
        return StubIntegration(predictions_dir=cfg.stub_predictions_dir)
    if cfg.kind == IntegrationKind.HTTP:
        if not cfg.url:
            raise typer.BadParameter("integration.url is required for kind=http")
        return HttpIngoreadIntegration(
            base_url=cfg.url,
            integration_name=test_cfg.integration_name,
            auth_token=cfg.auth_token,
            poll_interval=cfg.poll_interval,
            poll_timeout=cfg.poll_timeout,
            data_field_name=cfg.data_field_name,
        )
    raise NotImplementedError(f"Integration kind {cfg.kind} not implemented")


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
    integration = _build_integration(test_cfg)

    async def _run() -> tuple:
        try:
            outcome = await run_test(test_cfg, integration, dataset)
        finally:
            await integration.aclose()
        return outcome

    predictions, stats = asyncio.run(_run())
    result = score(test_cfg, scorer_cfg, dataset, predictions, stats)

    typer.echo(
        f"scored_documents={len(result.document_results)} "
        f"overall_match_rate={result.match_rate:.3f} "
        f"total_samples={result.total_samples} "
        f"timeouts={result.timeouts} failed={result.failed}"
    )
    for doc in result.document_results:
        typer.echo(
            f"label={doc.label} n={doc.total_samples} "
            f"match_rate={doc.match_rate:.3f}"
        )
    if not result.document_results:
        typer.echo(
            "WARNING: no documents were scored. "
            "Check the warnings above for label mismatches between scorer config "
            "and ingoread responses."
        )

    prev_result = None
    if previous and not no_history:
        prev_result = MeasurementsResult.model_validate_json(previous.read_text(encoding="utf-8"))

    sink = JsonFileSink(results_dir)
    json_path = sink.write(result)
    typer.echo(f"results_json={json_path.resolve()}")

    if not no_viz:
        html_path = render_html(
            result,
            results_dir,
            previous=prev_result,
            tolerance=test_cfg.history.match_rate_tolerance,
        )
        typer.echo(f"results_html={html_path.resolve()}")

    comparison = None
    if prev_result is not None:
        comparison = compare_to_previous(result, prev_result, test_cfg.history)
        typer.echo(f"history_status={comparison.status.value}")
        typer.echo(f"history_delta={comparison.overall_delta:+.4f}")
        for note in comparison.notes:
            typer.echo(f"history_note={note}")

    blocked, reasons = evaluate_release_gate(result, comparison, test_cfg.history)
    for reason in reasons:
        typer.echo(f"gate_block={reason}")
    typer.echo(f"release_gate={'BLOCKED' if blocked else 'OK'}")
    sys.exit(1 if blocked else 0)


if __name__ == "__main__":
    app()
