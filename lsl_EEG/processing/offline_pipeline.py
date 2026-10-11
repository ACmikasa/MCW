"""Offline analysis orchestration independent of HTTP and file storage."""

from __future__ import annotations

from typing import Any

from data.schemas import AnalysisResult, EEGChunk
from models.base import EEGModel


def analyze_offline(chunk: EEGChunk, model: EEGModel, metadata: dict[str, Any] | None = None) -> AnalysisResult:
    """Run the selected model on normalized EEG data."""
    return model.predict(chunk, metadata or {})
