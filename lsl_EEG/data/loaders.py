"""File loaders that normalize offline recordings to :class:`EEGChunk`."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .schemas import EEGChunk


def load_eeg_file(path: str | Path, sample_rate: float = 500.0) -> EEGChunk:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        return _load_csv(file_path, sample_rate)
    if suffix in {".edf", ".bdf"}:
        raise ValueError("EDF/BDF 读取需要安装并配置 pyedflib；当前版本先支持 CSV")
    raise ValueError(f"不支持的文件格式：{suffix or '无扩展名'}")


def _load_csv(path: Path, sample_rate: float) -> EEGChunk:
    try:
        values = np.genfromtxt(path, delimiter=",", dtype=np.float64, comments="#")
    except (OSError, ValueError) as exc:
        raise ValueError(f"CSV 文件无法读取：{exc}") from exc

    if values.ndim == 1:
        values = values.reshape(-1, 1)
    if values.ndim != 2 or values.size == 0:
        raise ValueError("CSV 文件没有二维数值样本")

    # A header row becomes NaN with genfromtxt; discard rows that contain no data.
    values = values[~np.all(np.isnan(values), axis=1)]
    if values.size == 0:
        raise ValueError("CSV 文件没有有效数值")

    samples = np.nan_to_num(values, nan=0.0).T
    timestamps = np.arange(samples.shape[1], dtype=np.float64) / float(sample_rate)
    labels = [f"通道{i + 1}" for i in range(samples.shape[0])]
    return EEGChunk(samples, timestamps, float(sample_rate), labels, source=str(path))
