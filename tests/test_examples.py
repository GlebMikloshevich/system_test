"""The bundled examples must stay runnable as the dataset format evolves."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ["vehicle_registration", "document_quality"]


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_manifest_is_version_2_with_unique_ids(name):
    manifest = yaml.safe_load(
        (REPO_ROOT / f"examples/{name}/dataset/manifest.yaml").read_text(encoding="utf-8")
    )

    ids = [sample["sample_id"] for sample in manifest["samples"]]
    assert manifest["version"] == 2
    assert manifest["name"] == name
    assert ids and len(ids) == len(set(ids))


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_id_files_agree_with_the_manifest(name):
    """The id file ships with the sample and is what the POST carries."""
    dataset_dir = REPO_ROOT / f"examples/{name}/dataset"
    manifest = yaml.safe_load((dataset_dir / "manifest.yaml").read_text(encoding="utf-8"))

    for sample in manifest["samples"]:
        id_file = dataset_dir / f"{Path(sample['filename']).stem}.id"
        assert id_file.is_file(), f"{id_file} is missing"
        assert id_file.read_text(encoding="utf-8").strip() == sample["sample_id"]


@pytest.mark.parametrize("name", EXAMPLES)
def test_example_runs_green(name, tmp_path):
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "ingoread_test.cli",
            "run",
            f"examples/{name}/config.yaml",
            "--results-dir",
            str(tmp_path / name),
            "--no-viz",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    assert "release_gate=OK" in proc.stdout
    assert f"dataset={name}" in proc.stdout
