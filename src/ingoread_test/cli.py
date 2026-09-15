"""`ingoread-test` — entry point invoked by TFS.

Commands:

    ingoread-test run       config.yaml
    ingoread-test suite     suite.yaml
    ingoread-test bootstrap result.json --out manifest.yaml
    ingoread-test dataset   list|upload|remove|restore s3://bucket/dataset
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import typer

from .config import load_configs, load_suite
from .config.test_config import StorageConfig
from .dataset import (
    DatasetLocation,
    DatasetManifest,
    RemovalReport,
    load_dataset,
    load_manifest,
    remove_samples,
    restore_samples,
    save_manifest,
)
from .dataset.bootstrap import result_to_manifest
from .integration.factory import build_integration
from .modules import (
    JsonFileSink,
    RunArtifacts,
    compare_to_previous,
    evaluate_release_gate,
    evaluate_suite_gate,
    open_dataset,
    read_result,
    render_html,
    render_suite_html,
    run_suite,
    run_test,
    score,
)
from .modules.logger_module import upload_run
from .utils.s3 import open_s3_uri

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

app = typer.Typer(add_completion=False, help="Ingoread test system CLI")
dataset_app = typer.Typer(add_completion=False, help="Inspect and curate datasets in S3")
app.add_typer(dataset_app, name="dataset")


@app.callback()
def _root() -> None:
    """Keep this as a multi-command app so 'run' stays a subcommand."""


@app.command()
def run(
    config: Path = typer.Argument(..., exists=True, readable=True),
    previous: str | None = typer.Option(
        None, "--previous", help="Prior result — a local path or an s3:// URI"
    ),
    no_history: bool = typer.Option(False, "--no-history"),
    no_viz: bool = typer.Option(False, "--no-viz"),
    no_upload: bool = typer.Option(False, "--no-upload", help="Skip the S3 results upload"),
    results_dir: Path | None = typer.Option(
        None, "--results-dir", help="Override results.local_dir from the config"
    ),
    exclude: list[str] = typer.Option(
        [],
        "--exclude",
        help="Skip this sample id or filename for this run only (repeatable)",
    ),
    refresh: bool = typer.Option(
        False, "--refresh", help="Re-download the dataset instead of using the local cache"
    ),
) -> None:
    test_cfg, scorer_cfg = load_configs(config)

    try:
        dataset = open_dataset(test_cfg, exclude_sample_ids=exclude, force_download=refresh)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"could not load dataset: {exc}") from exc

    typer.echo(
        f"dataset={dataset.name} source={dataset.source_uri} "
        f"samples={len(dataset)} removed={len(dataset.removed_sample_ids)} "
        f"excluded={len(dataset.excluded_sample_ids)}"
    )
    if not dataset.containers:
        typer.echo(
            "ERROR: the dataset has no samples left to run — every sample is removed or excluded.",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        integration = build_integration(test_cfg)
    except ValueError as exc:  # missing url etc. — a config problem, not a crash
        raise typer.BadParameter(str(exc)) from exc

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
        typer.echo(f"label={doc.label} n={doc.total_samples} match_rate={doc.match_rate:.3f}")
    if not result.document_results:
        typer.echo(
            "WARNING: no documents were scored. "
            "Check the warnings above for label mismatches between scorer config "
            "and ingoread responses."
        )

    prev_result = None
    if previous and not no_history:
        prev_result = read_result(previous)

    artifacts = RunArtifacts(results_dir or test_cfg.results.local_dir)
    json_path = JsonFileSink(artifacts.path).write(result)
    html_path = (
        None
        if no_viz
        else render_html(
            result,
            artifacts.path,
            previous=prev_result,
            tolerance=test_cfg.history.match_rate_tolerance,
        )
    )

    folder_uri = None if no_upload else _upload(test_cfg, dataset.name, result, html_path)
    if folder_uri:
        typer.echo(f"results_uploaded={folder_uri}")
    _report_local(artifacts, json_path, html_path, uploaded=folder_uri is not None)

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


def _report_local(
    artifacts: RunArtifacts,
    json_path: Path,
    html_path: Path | None,
    *,
    uploaded: bool,
) -> None:
    """Report the local copy, or say it was discarded once the run was uploaded."""
    discarded = artifacts.discard(uploaded=uploaded)
    if discarded is not None:
        typer.echo(f"results_local_discarded={discarded}")
        return

    typer.echo(f"results_json={json_path.resolve()}")
    if html_path is not None:
        typer.echo(f"results_html={html_path.resolve()}")
    if not artifacts.keep:
        typer.echo(
            "WARNING: the run was not uploaded, so the temporary local copy was kept. "
            "Set test.results.uri to publish runs to S3.",
            err=True,
        )


def _upload(test_cfg, dataset_name: str, result, html_path: Path | None) -> str | None:
    """Upload the run, reporting an upload failure without losing the gate verdict."""
    try:
        return upload_run(test_cfg, dataset_name, result, html_path=html_path)
    except Exception as exc:  # noqa: BLE001 - the local artifacts and the gate still stand
        typer.echo(f"WARNING: results upload failed: {exc}", err=True)
        return None


@app.command()
def bootstrap(
    result_json: Path = typer.Argument(..., exists=True, readable=True),
    out: Path = typer.Option(..., "--out", help="Where to write the draft manifest YAML"),
    name: str | None = typer.Option(None, "--name", help="Dataset name for the manifest"),
    overwrite: bool = typer.Option(False, "--overwrite", help="Replace --out if it exists"),
) -> None:
    """Draft a GT manifest from the predictions stored in a result JSON.

    Half-automatic labelling: nothing is filtered or flagged, so every
    `gt_value` still needs a human review before it is trusted as ground truth.
    """
    if out.exists() and not overwrite:
        raise typer.BadParameter(f"{out} already exists (pass --overwrite to replace it)")

    manifest = result_to_manifest(read_result(str(result_json)), name=name or "")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(manifest.to_yaml(), encoding="utf-8")

    n_docs = sum(len(sample.documents) for sample in manifest.samples)
    typer.echo(f"manifest={out.resolve()} samples={len(manifest.samples)} documents={n_docs}")
    typer.echo("NOTE: this is a draft — review every gt_value before trusting it.")


@app.command()
def suite(
    suite_config: Path = typer.Argument(..., exists=True, readable=True),
    results_dir: Path = typer.Option(Path("results"), "--results-dir"),
    no_viz: bool = typer.Option(False, "--no-viz"),
) -> None:
    """Run several datasets and gate the release across all of them at once."""
    suite_cfg = load_suite(suite_config)
    result = asyncio.run(run_suite(suite_cfg, results_dir, no_viz=no_viz))

    typer.echo(
        f"datasets={len(result.datasets)} passed={result.n_passed} "
        f"blocked={result.n_blocked} "
        f"macro_match_rate={result.macro_match_rate:.3f} "
        f"micro_match_rate={result.micro_match_rate:.3f} "
        f"total_samples={result.total_samples}"
    )
    for outcome in result.datasets:
        rate = f"{outcome.result.match_rate:.3f}" if outcome.result else "n/a"
        state = "BLOCKED" if outcome.blocked else "OK"
        scope = "blocking" if outcome.blocking else "advisory"
        typer.echo(f"dataset={outcome.name} status={state} ({scope}) match_rate={rate}")
        for reason in outcome.reasons:
            typer.echo(f"  dataset_block={outcome.name}: {reason}")

    blocked, reasons = evaluate_suite_gate(result, suite_cfg)

    if not no_viz:
        html_path = render_suite_html(result, results_dir, blocked=blocked, reasons=reasons)
        typer.echo(f"suite_html={html_path.resolve()}")

    for reason in reasons:
        typer.echo(f"gate_block={reason}")
    typer.echo(f"release_gate={'BLOCKED' if blocked else 'OK'}")
    sys.exit(1 if blocked else 0)


def _open_for_curation(
    uri: str, manifest: str | None, endpoint_url: str | None, region: str | None
) -> tuple[DatasetManifest, DatasetLocation]:
    """Read a dataset's manifest only — curation never downloads documents."""
    storage = StorageConfig(endpoint_url=endpoint_url, region_name=region)
    try:
        return load_manifest(
            uri,
            manifest,
            endpoint_url=storage.endpoint_url,
            region_name=storage.region_name,
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"could not read dataset {uri}: {exc}") from exc


@dataset_app.command("list")
def dataset_list(
    uri: str = typer.Argument(..., help="Dataset root, e.g. s3://ingoread-datasets/invoices"),
    manifest: str | None = typer.Option(
        None, "--manifest", help="Manifest name inside the dataset"
    ),
    endpoint_url: str | None = typer.Option(None, "--endpoint-url"),
    region: str | None = typer.Option(None, "--region"),
    removed_only: bool = typer.Option(False, "--removed-only"),
) -> None:
    """List a dataset's samples with their unique ids and removal state."""
    parsed, location = _open_for_curation(uri, manifest, endpoint_url, region)
    samples = parsed.removed_samples if removed_only else parsed.samples

    typer.echo(
        f"dataset={parsed.name} manifest={location.manifest_uri} "
        f"samples={len(parsed.samples)} active={len(parsed.active_samples)} "
        f"removed={len(parsed.removed_samples)}"
    )
    for sample in samples:
        state = "REMOVED" if sample.removed else "active "
        reason = f"  ({sample.removal.reason})" if sample.removed and sample.removal else ""
        typer.echo(f"{state} {sample.sample_id}  {sample.filename}{reason}")


@dataset_app.command("upload")
def dataset_upload(
    local_dir: Path = typer.Argument(..., exists=True, file_okay=False, readable=True),
    uri: str = typer.Argument(..., help="Destination, e.g. s3://ingoread-datasets/invoices"),
    manifest: str | None = typer.Option(None, "--manifest"),
    endpoint_url: str | None = typer.Option(None, "--endpoint-url"),
    region: str | None = typer.Option(None, "--region"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate only, upload nothing"),
) -> None:
    """Validate a dataset folder and upload it to S3.

    The manifest is parsed and every sample's id file checked against it first,
    so a dataset that would fail at run time is never published.
    """
    try:
        dataset = load_dataset(local_dir, manifest)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(f"dataset is not valid: {exc}") from exc

    typer.echo(
        f"dataset={dataset.name} samples={len(dataset)} removed={len(dataset.removed_sample_ids)}"
    )
    if dry_run:
        typer.echo(f"dry_run=1 not_uploaded={uri}")
        return

    hub, prefix = open_s3_uri(uri, endpoint_url=endpoint_url, region_name=region)
    keys = hub.upload_dataset(local_dir, prefix)
    typer.echo(f"uploaded_files={len(keys)}")
    typer.echo(f"dataset_uploaded={uri}")


@dataset_app.command("remove")
def dataset_remove(
    uri: str = typer.Argument(...),
    selectors: list[str] = typer.Argument(..., help="Sample ids or filenames to remove"),
    reason: str | None = typer.Option(None, "--reason", help="Why the sample is being removed"),
    manifest: str | None = typer.Option(None, "--manifest"),
    endpoint_url: str | None = typer.Option(None, "--endpoint-url"),
    region: str | None = typer.Option(None, "--region"),
    purge: bool = typer.Option(
        False, "--purge", help="Drop the entry entirely instead of marking it removed"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change, write nothing"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
) -> None:
    """Remove samples from a dataset on user request.

    The default is a soft removal: the entry stays in the manifest with a
    reason and is skipped by every run, so it can be restored. `--purge` drops
    the entry instead. Neither touches the sample's objects in S3 — another
    system owns deleting those.
    """
    parsed, location = _open_for_curation(uri, manifest, endpoint_url, region)

    if purge and not (yes or dry_run):
        typer.confirm(
            f"This will permanently drop {len(selectors)} entry/entries "
            f"from {parsed.name}. This cannot be undone. Continue?",
            abort=True,
        )

    report = remove_samples(parsed, selectors, reason=reason, purge=purge)
    _echo_report(report)
    _persist(parsed, location, report, dry_run)


@dataset_app.command("restore")
def dataset_restore(
    uri: str = typer.Argument(...),
    selectors: list[str] = typer.Argument(..., help="Sample ids or filenames to restore"),
    manifest: str | None = typer.Option(None, "--manifest"),
    endpoint_url: str | None = typer.Option(None, "--endpoint-url"),
    region: str | None = typer.Option(None, "--region"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    """Undo a soft removal, putting samples back into the dataset."""
    parsed, location = _open_for_curation(uri, manifest, endpoint_url, region)
    report = restore_samples(parsed, selectors)
    _echo_report(report)
    _persist(parsed, location, report, dry_run)


def _echo_report(report: RemovalReport) -> None:
    for sample_id in report.removed:
        typer.echo(f"removed={sample_id}")
    for sample_id in report.purged:
        typer.echo(f"purged={sample_id}")
    for sample_id in report.restored:
        typer.echo(f"restored={sample_id}")
    for sample_id in report.unchanged:
        typer.echo(f"unchanged={sample_id}")
    for selector in report.not_found:
        typer.echo(f"not_found={selector}", err=True)


def _persist(
    manifest: DatasetManifest,
    location: DatasetLocation,
    report: RemovalReport,
    dry_run: bool,
) -> None:
    if not report.changed:
        typer.echo("manifest_unchanged=1")
    elif dry_run:
        typer.echo(f"dry_run=1 manifest_not_written={location.manifest_uri}")
    else:
        typer.echo(f"manifest_written={save_manifest(manifest, location)}")
    if report.not_found:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
