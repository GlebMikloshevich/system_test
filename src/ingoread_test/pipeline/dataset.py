"""Turn a test config into the dataset a run will send.

The `run` and `suite` commands both need the same thing: resolve the configured
dataset URI (normally S3), apply the config's standing exclusions plus any
one-off ones from the command line, and hand back a `Dataset`. Keeping that in
one place means both commands skip removed samples the same way.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..config.test_config import TestConfig
from ..dataset import Dataset, load_dataset


def open_dataset(
    test_cfg: TestConfig,
    *,
    exclude_sample_ids: Iterable[str] = (),
    force_download: bool = False,
) -> Dataset:
    """Load the dataset named by ``test_cfg``, downloading it from S3 if needed."""

    ds_cfg = test_cfg.dataset
    if ds_cfg is None:  # pragma: no cover - TestConfig's validator guarantees it
        raise ValueError("test config has no dataset section")

    return load_dataset(
        ds_cfg.uri,
        ds_cfg.manifest,
        name=ds_cfg.name,
        files_root=ds_cfg.files_root,
        cache_dir=ds_cfg.cache_dir,
        exclude_sample_ids=[*ds_cfg.exclude_sample_ids, *exclude_sample_ids],
        force_download=ds_cfg.force_download or force_download,
        endpoint_url=test_cfg.storage.endpoint_url,
        region_name=test_cfg.storage.region_name,
    )
