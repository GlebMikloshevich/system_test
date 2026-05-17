"""TestModule — send dataset through the integration with batched concurrency."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from ..config.test_config import TestConfig
from ..dataset.models import Dataset, DocumentContainer
from ..integration.base import Integration
from ..integration.schemas import IngoreadFileResult, IngoreadStatus


@dataclass
class TestRunStats:
    __test__ = False  # tell pytest not to collect this as a test class

    total_time: float
    total_samples: int
    timeouts: int
    failed: int

    @property
    def time_per_sample(self) -> float:
        return self.total_time / self.total_samples if self.total_samples else 0.0


async def run_test(
    cfg: TestConfig, integration: Integration, dataset: Dataset
) -> tuple[dict[str, IngoreadFileResult], TestRunStats]:
    semaphore = asyncio.Semaphore(cfg.batch_size)
    results: dict[str, IngoreadFileResult] = {}
    timeouts = 0
    failed = 0

    async def _one(container: DocumentContainer) -> None:
        nonlocal timeouts, failed
        async with semaphore:
            start = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    integration.predict(container, kwargs=cfg.kwargs),
                    timeout=cfg.timeout,
                )
            except asyncio.TimeoutError:
                timeouts += 1
                result = IngoreadFileResult(
                    filename=container.filename,
                    status=IngoreadStatus.FAILED,
                    error="timeout",
                    time=time.perf_counter() - start,
                )
            except Exception as exc:  # noqa: BLE001
                failed += 1
                result = IngoreadFileResult(
                    filename=container.filename,
                    status=IngoreadStatus.FAILED,
                    error=str(exc),
                    time=time.perf_counter() - start,
                )
            else:
                if result.status == IngoreadStatus.FAILED:
                    failed += 1
            results[container.filename] = result

    overall_start = time.perf_counter()
    await asyncio.gather(*(_one(c) for c in dataset.containers))
    overall = time.perf_counter() - overall_start

    stats = TestRunStats(
        total_time=overall,
        total_samples=len(dataset),
        timeouts=timeouts,
        failed=failed,
    )
    return results, stats
