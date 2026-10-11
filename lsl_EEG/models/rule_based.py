"""Deterministic baseline model used until a trained model is configured."""

from __future__ import annotations

from typing import Any

import numpy as np

from data.schemas import AnalysisResult, EEGChunk


class RuleBasedModel:
    name = "rule-based-baseline"

    def predict(self, chunk: EEGChunk, metadata: dict[str, Any] | None = None) -> AnalysisResult:
        samples = np.asarray(chunk.samples, dtype=np.float64)
        if samples.ndim != 2 or samples.size == 0:
            return AnalysisResult(0, "无有效数据", 0, warnings=["没有可分析的 EEG 样本"])

        finite = np.isfinite(samples)
        valid_ratio = float(finite.mean())
        safe = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
        rms = float(np.sqrt(np.mean(np.square(safe))))
        artifact_ratio = float(np.mean(np.abs(safe) > max(100.0, rms * 8.0)))
        score = float(np.clip(100.0 - artifact_ratio * 100.0 - (1.0 - valid_ratio) * 100.0, 0.0, 100.0))
        label = "良好" if score >= 75 else ("需关注" if score >= 50 else "较差")
        return AnalysisResult(
            score=score,
            label=label,
            confidence=float(np.clip(valid_ratio, 0.0, 1.0)),
            metrics={
                "channel_count": int(samples.shape[0]),
                "sample_count": int(samples.shape[1]),
                "rms_uv": round(rms, 4),
                "artifact_ratio": round(artifact_ratio, 4),
            },
        )
