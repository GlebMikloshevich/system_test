import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args, cwd=REPO_ROOT) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ingoread_test.cli", *args]
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)


def test_cli_run_writes_json_and_html(tmp_path):
    proc = _run_cli(
        "run",
        "tests/data/config.yaml",
        "--results-dir",
        str(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    json_files = list(tmp_path.glob("*.json"))
    html_files = list(tmp_path.glob("*.html"))
    assert len(json_files) == 1
    assert len(html_files) == 1
    data = json.loads(json_files[0].read_text())
    assert data["match_rate"] == 1.0
    assert data["total_samples"] == 2


def test_cli_history_ok_then_degraded(tmp_path):
    # First baseline run.
    proc = _run_cli("run", "tests/data/config.yaml", "--results-dir", str(tmp_path), "--no-viz")
    assert proc.returncode == 0
    baseline = next(tmp_path.glob("*.json"))

    # Re-run with --previous = self: identical => OK.
    out_dir2 = tmp_path / "run2"
    proc = _run_cli(
        "run",
        "tests/data/config.yaml",
        "--results-dir",
        str(out_dir2),
        "--no-viz",
        "--previous",
        str(baseline),
    )
    assert proc.returncode == 0
    assert "history_status=OK" in proc.stdout

    # Fudge the baseline to look better than current => current degraded.
    # match_rate is unconstrained float in the model, so we can push above 1.0.
    data = json.loads(baseline.read_text())
    data["match_rate"] = data["match_rate"] + 0.5
    for d in data["document_results"]:
        d["match_rate"] = d["match_rate"] + 0.5
    baseline.write_text(json.dumps(data))

    out_dir3 = tmp_path / "run3"
    proc = _run_cli(
        "run",
        "tests/data/config.yaml",
        "--results-dir",
        str(out_dir3),
        "--no-viz",
        "--previous",
        str(baseline),
    )
    assert proc.returncode == 1
    assert "history_status=DEGRADED" in proc.stdout
