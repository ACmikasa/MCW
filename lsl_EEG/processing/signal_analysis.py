"""Dependency-light EEG signal analysis for the interactive web dashboard."""

from __future__ import annotations

import numpy as np

from data.schemas import EEGChunk
from models.rule_based import RuleBasedModel


def _channel(chunk: EEGChunk, index: int) -> np.ndarray:
    if index < 0 or index >= chunk.samples.shape[0]:
        raise ValueError("所选通道不存在")
    values = np.nan_to_num(np.asarray(chunk.samples[index], dtype=np.float64))
    if values.size < 4:
        raise ValueError("样本数量不足，无法执行分析")
    return values - float(values.mean())


def _decimate(x: np.ndarray, y: np.ndarray, limit: int = 800) -> tuple[list[float], list[float]]:
    step = max(1, int(np.ceil(x.size / limit)))
    return np.round(x[::step], 5).tolist(), np.round(y[::step], 5).tolist()


def bandpass_filter(signal: np.ndarray, sample_rate: float, low: float, high: float, notch: float = 0) -> np.ndarray:
    if not 0 <= low < high < sample_rate / 2:
        raise ValueError("滤波频率必须满足 0 ≤ 低频 < 高频 < 奈奎斯特频率")
    spectrum = np.fft.rfft(signal)
    frequencies = np.fft.rfftfreq(signal.size, 1.0 / sample_rate)
    mask = (frequencies >= low) & (frequencies <= high)
    if notch > 0:
        mask &= np.abs(frequencies - notch) > 1.0
    spectrum[~mask] = 0
    return np.fft.irfft(spectrum, n=signal.size)


def analyze_signal(chunk: EEGChunk, channel_index: int = 0, low: float = 1, high: float = 45,
                   notch: float = 50) -> dict:
    signal = _channel(chunk, channel_index)
    rate = float(chunk.sample_rate)
    filtered = bandpass_filter(signal, rate, float(low), float(high), float(notch))
    times = np.arange(signal.size) / rate
    time_values, raw_values = _decimate(times, signal)
    _, filtered_values = _decimate(times, filtered)

    window = np.hanning(signal.size)
    fft_values = np.abs(np.fft.rfft(filtered * window)) * 2 / max(1, signal.size)
    fft_freq = np.fft.rfftfreq(signal.size, 1 / rate)
    keep = fft_freq <= min(80, rate / 2)
    frequency, amplitude = _decimate(fft_freq[keep], fft_values[keep], 600)

    segment_size = min(512, max(64, 2 ** int(np.floor(np.log2(max(4, signal.size // 4))))))
    segment_size = min(segment_size, signal.size)
    step = max(1, segment_size // 2)
    windows = []
    for start in range(0, signal.size - segment_size + 1, step):
        segment = filtered[start:start + segment_size] * np.hanning(segment_size)
        windows.append(np.abs(np.fft.rfft(segment)) ** 2 / (rate * np.sum(np.hanning(segment_size) ** 2)))
    if not windows:
        windows = [np.abs(np.fft.rfft(filtered)) ** 2 / max(rate * signal.size, 1)]
        segment_size = signal.size
    psd_matrix = np.asarray(windows).T
    psd_freq = np.fft.rfftfreq(segment_size, 1 / rate)
    psd_keep = psd_freq <= min(80, rate / 2)
    psd = psd_matrix.mean(axis=1)[psd_keep]
    psd_x, psd_y = _decimate(psd_freq[psd_keep], 10 * np.log10(psd + 1e-12), 600)

    spectrogram = 10 * np.log10(psd_matrix[psd_keep] + 1e-12)
    freq_step = max(1, int(np.ceil(spectrogram.shape[0] / 80)))
    time_step = max(1, int(np.ceil(spectrogram.shape[1] / 100)))
    spectrogram = spectrogram[::freq_step, ::time_step]
    spec_freq = psd_freq[psd_keep][::freq_step]
    spec_time = (np.arange(psd_matrix.shape[1]) * step + segment_size / 2) / rate
    spec_time = spec_time[::time_step]

    model_result = RuleBasedModel().predict(chunk).to_dict()
    bands = {"delta": (1, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 30), "gamma": (30, 45)}
    band_power = {}
    freq_array, psd_array = np.asarray(psd_x), np.asarray(psd_y)
    linear_psd = 10 ** (psd_array / 10)
    for name, (start, end) in bands.items():
        mask = (freq_array >= start) & (freq_array < end)
        band_power[name] = round(float(np.trapezoid(linear_psd[mask], freq_array[mask])) if mask.sum() > 1 else 0.0, 6)

    return {
        "metadata": {"sample_rate": rate, "sample_count": int(signal.size),
                     "duration_seconds": round(signal.size / rate, 3),
                     "channel_index": channel_index,
                     "channel_label": chunk.channel_labels[channel_index] if chunk.channel_labels else f"通道{channel_index+1}"},
        "waveform": {"time": time_values, "raw": raw_values, "filtered": filtered_values},
        "fft": {"frequency": frequency, "amplitude": amplitude},
        "psd": {"frequency": psd_x, "power_db": psd_y, "band_power": band_power},
        "spectrogram": {"time": np.round(spec_time, 4).tolist(), "frequency": np.round(spec_freq, 3).tolist(),
                        "power_db": np.round(spectrogram, 3).tolist()},
        "detection": model_result,
    }


def preview_chunk(chunk: EEGChunk, max_points: int = 1000) -> dict:
    times = np.asarray(chunk.timestamps, dtype=np.float64)
    step = max(1, int(np.ceil(times.size / max_points)))
    return {"sample_rate": chunk.sample_rate, "channel_labels": chunk.channel_labels,
            "duration_seconds": round(chunk.samples.shape[1] / chunk.sample_rate, 3),
            "timestamps": np.round(times[::step], 5).tolist(),
            "samples": np.round(np.nan_to_num(chunk.samples[:, ::step]), 5).tolist()}
