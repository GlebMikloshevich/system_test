"""The per-sample identifier file.

Every sample carries its own unique identifier in a small sidecar file that
ships with the document, so the identifier travels with the data instead of
living only in the manifest. The identifier is what the integration is asked to
echo back, and it is what a person names when they ask for a sample to be
removed.

Format — deliberately the simplest thing that can be read by hand, by ``cat``,
and by any other tool in the pipeline:

- One UTF-8 text file per sample, named ``<document stem>.id`` next to the
  document (``invoice_001.pdf`` -> ``invoice_001.id``). A manifest entry may
  point somewhere else with ``id_file``.
- Its entire content is the identifier. Surrounding whitespace and a trailing
  newline are ignored; nothing else is.
- The identifier is a single token of printable characters (a UUID, a scan
  number, a case reference — the dataset owner chooses) and is unique inside
  its dataset.

Anything else — an empty file, several lines, embedded whitespace — is a
labelling mistake that would be sent to the backend as a bogus identifier, so
it is rejected at load time rather than normalized.
"""

from __future__ import annotations

from pathlib import Path

ID_FILE_SUFFIX = ".id"

# A sample id ends up in a form field and in S3 key components; keep it short
# enough to stay readable in logs and reports.
MAX_SAMPLE_ID_LENGTH = 200


def id_file_for(file_path: Path) -> Path:
    """The default identifier file for a sample's document."""

    return file_path.with_suffix(ID_FILE_SUFFIX)


def parse_sample_id(text: str, source: str) -> str:
    """Validate the content of an identifier file. ``source`` names it in errors."""

    sample_id = text.strip()
    if not sample_id:
        raise ValueError(f"Sample id file {source} is empty")
    if len(sample_id.split()) > 1:
        raise ValueError(
            f"Sample id file {source} must contain exactly one identifier, got {sample_id!r}"
        )
    if len(sample_id) > MAX_SAMPLE_ID_LENGTH:
        raise ValueError(f"Sample id in {source} is longer than {MAX_SAMPLE_ID_LENGTH} characters")
    return sample_id


def read_sample_id(path: Path) -> str:
    """Read and validate the identifier stored in ``path``."""

    return parse_sample_id(path.read_text(encoding="utf-8"), str(path))


def write_sample_id(path: Path, sample_id: str) -> Path:
    """Write an identifier file (used when seeding or repairing a dataset)."""

    parse_sample_id(sample_id, str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{sample_id}\n", encoding="utf-8")
    return path
