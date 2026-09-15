"""Build an Integration from a TestConfig (shared by the run and suite commands)."""

from __future__ import annotations

from ..config.test_config import IntegrationKind, TestConfig
from .base import Integration
from .http import HttpIngoreadIntegration
from .stub import StubIntegration


def build_integration(test_cfg: TestConfig) -> Integration:
    cfg = test_cfg.integration
    if cfg.kind == IntegrationKind.STUB:
        return StubIntegration(predictions_dir=cfg.stub_predictions_dir)
    if cfg.kind in (IntegrationKind.HTTP, IntegrationKind.STRING):
        if not cfg.url:
            raise ValueError(f"integration.url is required for kind={cfg.kind.value}")
        return HttpIngoreadIntegration(
            base_url=cfg.url,
            integration_name=test_cfg.integration_name,
            auth_token=cfg.auth_token,
            poll_interval=cfg.poll_interval,
            poll_timeout=cfg.poll_timeout,
            data_field_name=cfg.data_field_name,
            sample_id_field=cfg.sample_id_field,
            send_file=cfg.kind == IntegrationKind.HTTP,
        )
    raise NotImplementedError(f"Integration kind {cfg.kind} not implemented")
