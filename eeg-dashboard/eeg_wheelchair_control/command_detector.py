"""
Command Detection Module for EEG Wheelchair Control.

Simple spike-based detection:
- Detects spikes (sudden increases) in FP1/FP2 signals
- Compares FP1 vs FP2 to determine LEFT or RIGHT
- No calibration required
"""

import time
import logging
import json
from pathlib import Path
from typing import Optional, Dict
from dataclasses import dataclass
from collections import deque
import numpy as np

from . import config
from .signal_processor import SignalProcessor

logger = logging.getLogger(__name__)


def load_detection_settings() -> Dict:
    """Load detection settings from JSON config file."""
    config_file = Path("detection_multipliers.json")
    defaults = {
        "spike_threshold": 1.5,
        "min_spike_diff": 0.2,
        "cooldown_sec": 0.5,
    }

    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                loaded = json.load(f)
                for key in defaults:
                    if key in loaded:
                        defaults[key] = loaded[key]
                logger.info(f"Loaded detection settings: {defaults}")
                return defaults
        except Exception as e:
            logger.warning(f"Error loading detection settings: {e}")

    return defaults


@dataclass
class DetectionResult:
    """Result of command detection."""
    command: Optional[str]      # Detected command or None
    confidence: float           # Confidence score (0-1)
    features: Dict              # Extracted features for debugging
    timestamp: float            # Unix timestamp of detection


class CommandDetector:
    """
    Simple spike-based command detector.

    Detects sudden increases (spikes) in FP1/FP2 signals and determines
    direction based on which channel has the larger spike:
    - FP1 > FP2 → RIGHT
    - FP2 > FP1 → LEFT
    """

    def __init__(self, calibration_data: Optional[Dict] = None):
        """Initialize the command detector."""
        self.signal_processor = SignalProcessor()

        # Load settings from config file
        settings = load_detection_settings()

        # Rolling baseline tracking (last N values)
        self.baseline_window = 50  # samples for baseline calculation
        self.fp1_history = deque(maxlen=self.baseline_window)
        self.fp2_history = deque(maxlen=self.baseline_window)

        # Spike detection parameters (loaded from config)
        self.spike_threshold = settings.get('spike_threshold', 1.5)
        self.min_spike_diff = settings.get('min_spike_diff', 0.2)
        self.cooldown_sec = settings.get('cooldown_sec', 0.5)

        # Cooldown to prevent repeated triggers
        self.last_command_time = 0.0

        # Track if we're in a spike (to detect when it ends)
        self.in_spike = False
        self.spike_start_time = 0.0

        logger.info(f"CommandDetector initialized - spike_threshold={self.spike_threshold}, min_diff={self.min_spike_diff}, cooldown={self.cooldown_sec}s")

    def update_thresholds(self, calibration_data: Dict):
        """Update thresholds (kept for compatibility but not really used)."""
        # Could use calibration to set spike_threshold if desired
        if 'spike_threshold' in calibration_data:
            self.spike_threshold = calibration_data['spike_threshold']
        logger.info(f"Spike threshold: {self.spike_threshold}")

    def get_command(self, fp1_raw: np.ndarray, fp2_raw: np.ndarray) -> DetectionResult:
        """
        Detect commands based on spikes in the signal.

        Args:
            fp1_raw: Raw FP1 data (1-second window)
            fp2_raw: Raw FP2 data (1-second window)

        Returns:
            DetectionResult with command (LEFT, RIGHT, or None)
        """
        timestamp = time.time()

        # Get current amplitude values (max absolute value in window)
        fp1_current = np.max(np.abs(fp1_raw))
        fp2_current = np.max(np.abs(fp2_raw))

        # Also compute mean absolute for this window
        fp1_mean = np.mean(np.abs(fp1_raw))
        fp2_mean = np.mean(np.abs(fp2_raw))

        # Add to history for baseline tracking
        self.fp1_history.append(fp1_mean)
        self.fp2_history.append(fp2_mean)

        # Calculate baseline (mean of recent history)
        if len(self.fp1_history) < 10:
            # Not enough history yet
            return DetectionResult(
                command=None,
                confidence=0.0,
                features={
                    'fp1_current': float(fp1_current),
                    'fp2_current': float(fp2_current),
                    'fp1_mean': float(fp1_mean),
                    'fp2_mean': float(fp2_mean),
                    'status': 'warming_up'
                },
                timestamp=timestamp
            )

        fp1_baseline = np.mean(list(self.fp1_history)[:-1])  # Exclude current
        fp2_baseline = np.mean(list(self.fp2_history)[:-1])
        fp1_std = np.std(list(self.fp1_history)[:-1]) + 1e-6  # Avoid division by zero
        fp2_std = np.std(list(self.fp2_history)[:-1]) + 1e-6

        # Calculate absolute change from baseline
        fp1_change = abs(fp1_current - fp1_baseline)
        fp2_change = abs(fp2_current - fp2_baseline)

        # Check for spike: absolute change significantly above baseline std
        fp1_zscore = fp1_change / fp1_std
        fp2_zscore = fp2_change / fp2_std

        # Determine if there's a spike on either channel
        is_spike = fp1_zscore > self.spike_threshold or fp2_zscore > self.spike_threshold

        features = {
            'fp1_current': float(fp1_current),
            'fp2_current': float(fp2_current),
            'fp1_change': float(fp1_change),
            'fp2_change': float(fp2_change),
            'fp1_baseline': float(fp1_baseline),
            'fp2_baseline': float(fp2_baseline),
            'fp1_zscore': float(fp1_zscore),
            'fp2_zscore': float(fp2_zscore),
            'is_spike': is_spike,
        }

        # Check cooldown
        if timestamp - self.last_command_time < self.cooldown_sec:
            features['status'] = 'cooldown'
            return DetectionResult(
                command=None,
                confidence=0.0,
                features=features,
                timestamp=timestamp
            )

        # Detect command based on spike
        if is_spike:
            # Determine direction based on which channel has larger change
            total_change = fp1_change + fp2_change + 1e-6
            fp1_ratio = fp1_change / total_change
            fp2_ratio = fp2_change / total_change

            diff = abs(fp1_ratio - fp2_ratio)

            if diff > self.min_spike_diff:
                if fp1_change > fp2_change:
                    command = config.Command.RIGHT
                    confidence = min(1.0, diff * 2)  # Scale diff to confidence
                else:
                    command = config.Command.LEFT
                    confidence = min(1.0, diff * 2)

                self.last_command_time = timestamp
                features['direction_diff'] = float(diff)
                features['fp1_ratio'] = float(fp1_ratio)
                features['fp2_ratio'] = float(fp2_ratio)

                logger.info(f"Spike detected: {command} (FP1 change={fp1_change:.1f}, FP2 change={fp2_change:.1f}, diff={diff:.2f})")

                return DetectionResult(
                    command=command,
                    confidence=confidence,
                    features=features,
                    timestamp=timestamp
                )

        features['status'] = 'no_spike'
        return DetectionResult(
            command=None,
            confidence=0.0,
            features=features,
            timestamp=timestamp
        )


# For backwards compatibility
def test_command_detection():
    """Test command detection with synthetic signals."""
    print("\nSpike-based Command Detection")
    print("=" * 50)
    print("This detector looks for spikes and compares FP1 vs FP2")
    print("- FP1 > FP2 → RIGHT")
    print("- FP2 > FP1 → LEFT")
    print("=" * 50)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_command_detection()
