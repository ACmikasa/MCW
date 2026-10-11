"""Model interface. Concrete ML frameworks stay behind this boundary."""

from __future__ import annotations

from typing import Any, Protocol

from data.schemas import AnalysisResult, EEGChunk


class EEGModel(Protocol):
    name: str

    def predict(self, chunk: EEGChunk, metadata: dict[str, Any] | None = None) -> AnalysisResult:
        """Return a normalized analysis result for one EEG segment."""
