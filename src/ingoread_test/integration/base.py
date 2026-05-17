"""Integration interface — what TestModule talks to."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..dataset.models import DocumentContainer
from .schemas import IngoreadFileResult


class Integration(ABC):
    """Abstract integration that takes a document container and returns predictions."""

    @abstractmethod
    async def predict(
        self, container: DocumentContainer, kwargs: dict | None = None
    ) -> IngoreadFileResult: ...

    async def aclose(self) -> None:
        """Release any held resources. Default: no-op."""
        return None
