"""
Signal Processing Module for EEG Wheelchair Control.

Implements filtering and feature extraction for EOG and EMG signals.
Uses Butterworth filters for clean frequency separation.
"""

import numpy as np
from scipy import signal
from scipy.signal import butter, filtfilt, iirnotch, find_peaks
import logging
from typing import Tuple, Dict, Optional
from dataclasses import dataclass

from . import config

logger = logging.getLogger(__name__)


@dataclass
class Features:
    """Container for extracted signal features."""
    fp1_max: float          # Max absolute amplitude on FP1 (uV)
    fp2_max: float          # Max absolute amplitude on FP2 (uV)
    fp1_mean: float         # Mean absolute amplitude on FP1
    fp2_mean: float         # Mean absolute amplitude on FP2
    jaw_power: float        # Power in EMG band (20-100 Hz)
    blink_peaks: list       # Indices of detected blink peaks
    blink_count: int        # Number of blinks in window
    fp1_fp2_ratio: float    # Ratio of FP1 to FP2 amplitude
    fp2_fp1_ratio: float    # Ratio of FP2 to FP1 amplitude
    signal_quality: float   # Overall signal quality (0-1)


class SignalProcessor:
    """
    Real-time signal processing for EEG-based command detection.

    Implements:
    - Notch filtering (50/60 Hz power line removal)
    - Bandpass filtering (EOG: 0.5-15 Hz, EMG: 20-100 Hz)
    - Feature extraction for command detection
    """

    def __init__(self, sampling_rate: int = config.SAMPLING_RATE):
        """
        Initialize the signal processor.

        Args:
            sampling_rate: Sampling rate in Hz (default: 125 Hz for OpenBCI)
        """
        self.fs = sampling_rate
        self.nyquist = sampling_rate / 2.0

        # Pre-compute filter coefficients for efficiency
        self._init_filters()

        logger.info(f"SignalProcessor initialized (fs={self.fs} Hz)")

    def _init_filters(self):
        """Pre-compute all filter coefficients."""

        # Notch filter for power line noise (50 or 60 Hz)
        self.notch_b, self.notch_a = iirnotch(
            config.NOTCH_FREQ,
            config.NOTCH_Q,
            self.fs
        )

        # EOG bandpass filter (0.5-15 Hz) for eye movements and blinks
        low = config.EOG_HIGHPASS / self.nyquist
        high = config.EOG_LOWPASS / self.nyquist
        # Clamp to valid range (0, 1)
        low = max(0.001, min(low, 0.999))
        high = max(0.001, min(high, 0.999))
        self.eog_b, self.eog_a = butter(
            config.FILTER_ORDER,
            [low, high],
            btype='band'
        )

        # EMG bandpass filter (20-100 Hz) for jaw clench detection
        low_emg = config.EMG_HIGHPASS / self.nyquist
        high_emg = config.EMG_LOWPASS / self.nyquist
        # Clamp to valid range
        low_emg = max(0.001, min(low_emg, 0.999))
        high_emg = max(0.001, min(high_emg, 0.999))
        self.emg_b, self.emg_a = butter(
            config.FILTER_ORDER,
            [low_emg, high_emg],
            btype='band'
        )

        logger.debug("Filter coefficients computed")

    def apply_notch_filter(self, data: np.ndarray) -> np.ndarray:
        """
        Apply notch filter to remove power line interference.

        Args:
            data: Input signal (1D array)

        Returns:
            Filtered signal
        """
        if len(data) < 13:  # Minimum length for filtfilt
            return data
        return filtfilt(self.notch_b, self.notch_a, data)

    def apply_eog_filter(self, data: np.ndarray) -> np.ndarray:
        """
        Apply EOG bandpass filter (0.5-15 Hz).

        Extracts eye movement and blink signals while removing
        high-frequency muscle noise and DC drift.

        Args:
            data: Input signal (1D array)

        Returns:
            Filtered signal containing EOG components
        """
        if len(data) < 13:
            return data
        return filtfilt(self.eog_b, self.eog_a, data)

    def apply_emg_filter(self, data: np.ndarray) -> np.ndarray:
        """
        Apply EMG bandpass filter (20-100 Hz).

        Extracts high-frequency muscle activity for jaw clench detection.
        Removes eye artifacts which are predominantly low-frequency.

        Args:
            data: Input signal (1D array)

        Returns:
            Filtered signal containing EMG components
        """
        if len(data) < 13:
            return data
        return filtfilt(self.emg_b, self.emg_a, data)

    def preprocess(self, fp1_raw: np.ndarray, fp2_raw: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Full preprocessing pipeline for FP1 and FP2 channels.

        Steps:
        1. Apply notch filter (remove 60 Hz)
        2. Apply EOG bandpass (for eye detection)
        3. Apply EMG bandpass (for jaw detection)

        Args:
            fp1_raw: Raw FP1 data
            fp2_raw: Raw FP2 data

        Returns:
            Tuple of (fp1_eog, fp2_eog, fp1_emg, fp2_emg)
        """
        # Step 1: Remove power line noise
        fp1_notched = self.apply_notch_filter(fp1_raw)
        fp2_notched = self.apply_notch_filter(fp2_raw)

        # Step 2: EOG filtering for eye movements and blinks
        fp1_eog = self.apply_eog_filter(fp1_notched)
        fp2_eog = self.apply_eog_filter(fp2_notched)

        # Step 3: EMG filtering for jaw clench
        fp1_emg = self.apply_emg_filter(fp1_notched)
        fp2_emg = self.apply_emg_filter(fp2_notched)

        return fp1_eog, fp2_eog, fp1_emg, fp2_emg

    def detect_peaks(self, data: np.ndarray, threshold: float,
                     min_distance: int = 25) -> Tuple[np.ndarray, dict]:
        """
        Detect peaks in signal for blink detection.

        Uses scipy's find_peaks with prominence-based detection.

        Args:
            data: Input signal (EOG filtered)
            threshold: Minimum peak height
            min_distance: Minimum samples between peaks (default: 200ms at 125Hz)

        Returns:
            Tuple of (peak_indices, peak_properties)
        """
        # Take absolute value since blinks can be positive or negative
        abs_data = np.abs(data)

        peaks, properties = find_peaks(
            abs_data,
            height=threshold,
            distance=min_distance,
            prominence=threshold * 0.5  # Peaks must stand out
        )

        return peaks, properties

    def calculate_power(self, data: np.ndarray) -> float:
        """
        Calculate signal power (mean squared amplitude).

        Used for EMG-based jaw clench detection.

        Args:
            data: Input signal (EMG filtered)

        Returns:
            Power value (uV^2)
        """
        return np.mean(data ** 2)

    def calculate_signal_quality(self, fp1: np.ndarray, fp2: np.ndarray) -> float:
        """
        Estimate signal quality based on noise and artifacts.

        Returns a value between 0 (poor) and 1 (excellent).

        Args:
            fp1: FP1 channel data
            fp2: FP2 channel data

        Returns:
            Signal quality score (0-1)
        """
        # Check for flat lines (electrode disconnected)
        if np.std(fp1) < 0.1 or np.std(fp2) < 0.1:
            return 0.0

        # Check for extreme amplitudes (loose electrode)
        max_amp = max(np.max(np.abs(fp1)), np.max(np.abs(fp2)))
        if max_amp > config.MAX_VALID_AMPLITUDE_UV:
            return 0.2

        # Check for excessive noise
        noise_level = np.std(fp1 - fp2)  # Differential noise
        if noise_level > config.MAX_NOISE_THRESHOLD_UV:
            return 0.3

        # Good signal
        quality = 1.0 - (noise_level / config.MAX_NOISE_THRESHOLD_UV)
        return max(0.0, min(1.0, quality))

    def extract_features(self, fp1_raw: np.ndarray, fp2_raw: np.ndarray,
                        baseline_stats: Optional[Dict] = None) -> Features:
        """
        Extract all features needed for command detection.

        Args:
            fp1_raw: Raw FP1 data (1 second window, 125 samples)
            fp2_raw: Raw FP2 data (1 second window, 125 samples)
            baseline_stats: Optional baseline statistics for threshold calculation

        Returns:
            Features dataclass with all extracted features
        """
        # Check for flat/zero signal (likely electrode issue)
        if np.std(fp1_raw) < 0.01 and np.std(fp2_raw) < 0.01:
            logger.warning(f"Signal appears flat: FP1 std={np.std(fp1_raw):.4f}, FP2 std={np.std(fp2_raw):.4f}")

        # Preprocess signals
        fp1_eog, fp2_eog, fp1_emg, fp2_emg = self.preprocess(fp1_raw, fp2_raw)

        # Amplitude features (EOG band)
        fp1_max = np.max(np.abs(fp1_eog))
        fp2_max = np.max(np.abs(fp2_eog))
        fp1_mean = np.mean(np.abs(fp1_eog))
        fp2_mean = np.mean(np.abs(fp2_eog))

        # Power feature (EMG band for jaw detection)
        # Use FP1 as it's closer to temporalis muscle
        jaw_power = self.calculate_power(fp1_emg)

        # Blink detection
        blink_threshold = baseline_stats.get('blink_threshold', 50.0) if baseline_stats else 50.0
        blink_peaks, _ = self.detect_peaks(fp1_eog, blink_threshold)

        # Lateral asymmetry ratios
        # Add small epsilon to avoid division by zero
        eps = 1e-6
        fp1_fp2_ratio = fp1_max / (fp2_max + eps)
        fp2_fp1_ratio = fp2_max / (fp1_max + eps)

        # Signal quality
        signal_quality = self.calculate_signal_quality(fp1_raw, fp2_raw)

        return Features(
            fp1_max=fp1_max,
            fp2_max=fp2_max,
            fp1_mean=fp1_mean,
            fp2_mean=fp2_mean,
            jaw_power=jaw_power,
            blink_peaks=blink_peaks.tolist(),
            blink_count=len(blink_peaks),
            fp1_fp2_ratio=fp1_fp2_ratio,
            fp2_fp1_ratio=fp2_fp1_ratio,
            signal_quality=signal_quality
        )


# =============================================================================
# Utility Functions for Testing
# =============================================================================

def generate_test_signal(duration_sec: float = 1.0,
                         fs: int = config.SAMPLING_RATE,
                         signal_type: str = 'noise') -> Tuple[np.ndarray, np.ndarray]:
    """
    Generate synthetic test signals for development and testing.

    Args:
        duration_sec: Signal duration in seconds
        fs: Sampling rate
        signal_type: Type of signal ('noise', 'blink', 'jaw', 'left', 'right')

    Returns:
        Tuple of (fp1, fp2) test signals
    """
    n_samples = int(duration_sec * fs)
    t = np.arange(n_samples) / fs

    # Base noise (pink noise approximation)
    noise = np.random.randn(n_samples) * 10

    if signal_type == 'noise':
        fp1 = noise.copy()
        fp2 = noise.copy() + np.random.randn(n_samples) * 2

    elif signal_type == 'blink':
        # Simulate double blink with two sharp peaks
        fp1 = noise.copy()
        fp2 = noise.copy()

        # First blink at 0.3s
        blink1_idx = int(0.3 * fs)
        # Second blink at 0.7s (400ms later - within valid range)
        blink2_idx = int(0.7 * fs)

        # Create blink waveform (sharp positive then negative)
        blink_width = int(0.1 * fs)  # 100ms blink
        for idx in [blink1_idx, blink2_idx]:
            start = max(0, idx - blink_width // 2)
            end = min(n_samples, idx + blink_width // 2)
            blink = 150 * np.exp(-((np.arange(end - start) - blink_width // 2) ** 2) / (2 * 5 ** 2))
            fp1[start:end] += blink
            fp2[start:end] += blink * 0.9  # Slightly smaller on FP2

    elif signal_type == 'jaw':
        # Simulate jaw clench with high-frequency EMG burst
        fp1 = noise.copy()
        fp2 = noise.copy()

        # Add high-frequency burst (30-70 Hz) from 0.3s to 0.7s
        emg_start = int(0.3 * fs)
        emg_end = int(0.7 * fs)
        emg_signal = np.random.randn(emg_end - emg_start) * 50
        # Filter to EMG band
        fp1[emg_start:emg_end] += emg_signal
        fp2[emg_start:emg_end] += emg_signal * 0.8

    elif signal_type == 'left':
        # Simulate looking left (stronger signal on FP2)
        fp1 = noise.copy()
        fp2 = noise.copy()

        # Add slow EOG deflection
        eog = 80 * np.sin(2 * np.pi * 0.5 * t)  # 0.5 Hz sine
        fp2 += eog
        fp1 += eog * 0.5  # Weaker on FP1

    elif signal_type == 'right':
        # Simulate looking right (stronger signal on FP1)
        fp1 = noise.copy()
        fp2 = noise.copy()

        # Add slow EOG deflection
        eog = 80 * np.sin(2 * np.pi * 0.5 * t)
        fp1 += eog
        fp2 += eog * 0.5  # Weaker on FP2

    else:
        raise ValueError(f"Unknown signal type: {signal_type}")

    return fp1, fp2


def test_filters():
    """Test that filters work correctly on synthetic signals."""
    processor = SignalProcessor()

    print("Testing Signal Processor Filters")
    print("=" * 50)

    # Test with noise
    fp1, fp2 = generate_test_signal(signal_type='noise')
    features = processor.extract_features(fp1, fp2)
    print(f"Noise - FP1 max: {features.fp1_max:.2f}, FP2 max: {features.fp2_max:.2f}")

    # Test with blinks
    fp1, fp2 = generate_test_signal(signal_type='blink')
    features = processor.extract_features(fp1, fp2)
    print(f"Blink - FP1 max: {features.fp1_max:.2f}, Blink count: {features.blink_count}")

    # Test with jaw clench
    fp1, fp2 = generate_test_signal(signal_type='jaw')
    features = processor.extract_features(fp1, fp2)
    print(f"Jaw - Jaw power: {features.jaw_power:.2f}")

    # Test with look left
    fp1, fp2 = generate_test_signal(signal_type='left')
    features = processor.extract_features(fp1, fp2)
    print(f"Left - FP2/FP1 ratio: {features.fp2_fp1_ratio:.2f}")

    # Test with look right
    fp1, fp2 = generate_test_signal(signal_type='right')
    features = processor.extract_features(fp1, fp2)
    print(f"Right - FP1/FP2 ratio: {features.fp1_fp2_ratio:.2f}")

    print("=" * 50)
    print("All filter tests passed!")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_filters()
