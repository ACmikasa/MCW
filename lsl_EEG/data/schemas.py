"""Small, stable data contracts shared by realtime and offline pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class EEGChunk:
    """A channel-major EEG segment: ``(channels, samples)``."""

    samples: np.ndarray
    timestamps: np.ndarray
    sample_rate: float
    channel_labels: list[str] = field(default_factory=list)
    source: str = "unknown"


@dataclass
class AnalysisResult:
    """Serializable result shape returned by every model adapter."""

    score: float
    label: str
    confidence: float
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(float(self.score), 2),
            "label": self.label,
            "confidence": round(float(self.confidence), 3),
            "metrics": self.metrics,
            "warnings": self.warnings,
        }
