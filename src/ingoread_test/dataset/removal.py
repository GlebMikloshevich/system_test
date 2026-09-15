"""Remove samples from a dataset on user request.

Removal is a *soft delete* by default: the sample keeps its ground truth and
gains a ``removal`` record (reason, timestamp, who asked for it), and runs skip
it. That keeps the manifest an audit trail — a dataset's history explains why it
shrank — and lets :func:`restore_samples` undo a bad call.

``purge=True`` drops the entry outright, which is irreversible, so the CLI
confirms first.

Nothing here touches the objects in S3: deleting a sample's document is another
system's job. These functions curate the manifest, which is what decides whether
a sample is sent.

Samples are addressed by ``sample_id`` or by ``filename``: a person reading a
report has one of the two in front of them, and should not have to translate.
"""

from __future__ import annotations

import getpass
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .manifest import DatasetManifest, RemovalInfo, SampleEntry

logger = logging.getLogger(__name__)


@dataclass
class RemovalReport:
    """What a remove/restore call actually did, for the CLI to print."""

    removed: list[str] = field(default_factory=list)
    purged: list[str] = field(default_factory=list)
    restored: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    not_found: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True when the manifest needs to be written back."""

        return bool(self.removed or self.purged or self.restored)


def remove_samples(
    manifest: DatasetManifest,
    selectors: list[str],
    *,
    reason: str | None = None,
    removed_by: str | None = None,
    purge: bool = False,
) -> RemovalReport:
    """Exclude the selected samples. ``manifest`` is mutated in place.

    Persist the result with :func:`ingoread_test.dataset.loader.save_manifest`;
    nothing is written here, so ``--dry-run`` is just "don't save". The sample's
    objects in S3 are left alone — another system owns their deletion.
    """

    report = RemovalReport()
    matched = _select(manifest, selectors, report)
    actor = removed_by or _current_user()
    now = datetime.now(UTC)

    for sample in matched:
        if purge:
            manifest.samples.remove(sample)
            report.purged.append(sample.sample_id)
            continue
        if sample.removed:
            report.unchanged.append(sample.sample_id)
            continue
        sample.removed = True
        sample.removal = RemovalInfo(reason=reason, removed_at=now, removed_by=actor)
        report.removed.append(sample.sample_id)

    return report


def restore_samples(manifest: DatasetManifest, selectors: list[str]) -> RemovalReport:
    """Undo a soft removal. A purged sample is gone and cannot be restored here."""

    report = RemovalReport()
    for sample in _select(manifest, selectors, report):
        if not sample.removed:
            report.unchanged.append(sample.sample_id)
            continue
        sample.removed = False
        sample.removal = None
        report.restored.append(sample.sample_id)
    return report


def _select(
    manifest: DatasetManifest, selectors: list[str], report: RemovalReport
) -> list[SampleEntry]:
    """Resolve selectors to samples, recording the ones that match nothing."""

    matched: list[SampleEntry] = []
    for selector in selectors:
        hits = manifest.find(selector)
        if not hits:
            report.not_found.append(selector)
            continue
        matched.extend(hit for hit in hits if hit not in matched)

    if report.not_found:
        logger.warning(
            "no sample matched %s in dataset %s",
            sorted(report.not_found),
            manifest.name or "<unnamed>",
        )
    return matched


def _current_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - containers and CI often have no login name
        return "unknown"
