import time

from ingoread_test.config.test_config import IntegrationConfig, IntegrationKind, TestConfig
from ingoread_test.dataset.models import Dataset, DocumentContainer, DocumentGT, FieldGT
from ingoread_test.integration.stub import StubIntegration
from ingoread_test.modules.test_module import run_test


def _container(filename: str) -> DocumentContainer:
    return DocumentContainer(
        filename=filename,
        documents=[
            DocumentGT(
                doc_label="invoice",
                fields={"total": FieldGT(gt_value="1.0")},
            )
        ],
    )


def _cfg(batch_size: int = 2, timeout: float = 5.0) -> TestConfig:
    return TestConfig(
        name="t",
        files_root="/tmp",
        manifest="/tmp/m.yaml",
        batch_size=batch_size,
        timeout=timeout,
        integration=IntegrationConfig(kind=IntegrationKind.STUB),
    )


async def test_stub_echoes_gt():
    dataset = Dataset(containers=[_container("a"), _container("b")])
    integration = StubIntegration()
    results, stats = await run_test(_cfg(), integration, dataset)
    assert set(results) == {"a", "b"}
    assert stats.total_samples == 2
    assert stats.failed == 0
    assert stats.timeouts == 0


async def test_batch_size_limits_concurrency():
    dataset = Dataset(containers=[_container(f"f{i}") for i in range(4)])
    integration = StubIntegration(latency=0.2)
    start = time.perf_counter()
    _, stats = await run_test(_cfg(batch_size=2, timeout=10), integration, dataset)
    elapsed = time.perf_counter() - start
    # With batch_size=2 and 4 files at 0.2s each => >= 2 batches => >= 0.4s.
    assert elapsed >= 0.35
    assert stats.total_samples == 4


async def test_timeout_counted():
    dataset = Dataset(containers=[_container("slow"), _container("fast")])
    integration = StubIntegration(timeout_filenames={"slow"})
    _, stats = await run_test(_cfg(batch_size=2, timeout=0.2), integration, dataset)
    assert stats.timeouts == 1
    assert stats.failed == 0


async def test_failure_counted():
    dataset = Dataset(containers=[_container("bad")])
    integration = StubIntegration(failure_filenames={"bad"})
    _, stats = await run_test(_cfg(), integration, dataset)
    assert stats.failed == 1
