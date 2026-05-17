"""LoggerModule — pluggable persistence for MeasurementsResult."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..results.models import MeasurementsResult


class Sink(ABC):
    @abstractmethod
    def write(self, result: MeasurementsResult) -> Path | None: ...


class JsonFileSink(Sink):
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def write(self, result: MeasurementsResult) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = result.start_date.strftime("%Y%m%dT%H%M%S")
        out = self.directory / f"{stamp}__{result.test_config_name}.json"
        out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return out
