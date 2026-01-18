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
        "ch1_threshold": 0.2,         # 20% change to trigger FORWARD
        "ch3_threshold": 0.2,         # 20% change to trigger RIGHT
        "coupled_reduction": 0.5,     # Reduce other threshold by 50% when one triggers
        "change_duration_sec": 0.1,   # How long change must be sustained
        "cooldown_sec": 0.5,          # Time between commands
        "dedupe_reset_sec": 1.0,      # Silence needed before same command can retrigger
    }

    if config_file.exists():
        try:
            with open(config_file, 'r') as f:
                loaded = json.load(f)
                # Update defaults with loaded values
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

        # Detection parameters (loaded from config) - separate thresholds per channel
        self.ch1_threshold = settings.get('ch1_threshold', 0.2)  # Ch1/FP1 -> LEFT
        self.ch3_threshold = settings.get('ch3_threshold', 0.2)  # Ch3/FP2 -> RIGHT
        self.coupled_reduction = settings.get('coupled_reduction', 0.5)  # Reduce other side when one triggers
        self.change_duration_sec = settings.get('change_duration_sec', 0.1)  # How long change must be sustained
        self.cooldown_sec = settings.get('cooldown_sec', 0.5)

        # Cooldown to prevent repeated triggers
        self.last_command_time = 0.0

        # Track when each channel started showing a change
        self.fp1_change_start = None
        self.fp2_change_start = None

        # Deduplication: suppress consecutive same-commands
        self.last_output_command = None  # Last command we actually output
        self.command_end_time = 0.0      # When the last command "ended" (signal dropped)
        self.dedupe_reset_sec = settings.get('dedupe_reset_sec', 1.0)  # After this silence, allow same command again

        logger.info(f"CommandDetector initialized - Ch1={self.ch1_threshold*100:.0f}% (LEFT), Ch3={self.ch3_threshold*100:.0f}% (RIGHT), coupled_reduction={self.coupled_reduction*100:.0f}%, duration={self.change_duration_sec}s, cooldown={self.cooldown_sec}s")

    def update_thresholds(self, calibration_data: Dict):
        """Update thresholds (kept for compatibility but not really used)."""
        # Could use calibration to set spike_threshold if desired
        if 'spike_threshold' in calibration_data:
            self.spike_threshold = calibration_data['spike_threshold']
        logger.info(f"Spike threshold: {self.spike_threshold}")

    def get_cooldown_remaining(self) -> float:
        """Get remaining cooldown time in seconds."""
        import time
        elapsed = time.time() - self.last_command_time
        remaining = self.cooldown_sec - elapsed
        return max(0.0, remaining)

    def get_command(self, fp1_raw: np.ndarray, fp2_raw: np.ndarray) -> DetectionResult:
        """
        Detect commands based on percentage change from baseline.

        Args:
            fp1_raw: Raw FP1 data (1-second window)
            fp2_raw: Raw FP2 data (1-second window)

        Returns:
            DetectionResult with command (LEFT, RIGHT, both, or None)
        """
        timestamp = time.time()

        # Get current value (mean of window)
        fp1_current = np.mean(np.abs(fp1_raw))
        fp2_current = np.mean(np.abs(fp2_raw))

        # Add to history for baseline tracking
        self.fp1_history.append(fp1_current)
        self.fp2_history.append(fp2_current)

        # Need some history for baseline
        if len(self.fp1_history) < 10:
            return DetectionResult(
                command=None,
                confidence=0.0,
                features={
                    'fp1_current': float(fp1_current),
                    'fp2_current': float(fp2_current),
                    'status': 'warming_up'
                },
                timestamp=timestamp
            )

        # Calculate baseline (mean of history, excluding current)
        fp1_baseline = np.mean(list(self.fp1_history)[:-1])
        fp2_baseline = np.mean(list(self.fp2_history)[:-1])

        # Calculate percentage change from baseline
        fp1_pct_change = abs(fp1_current - fp1_baseline) / (fp1_baseline + 1e-6)
        fp2_pct_change = abs(fp2_current - fp2_baseline) / (fp2_baseline + 1e-6)

        # Check if each channel exceeds its threshold (separate for left/right tuning)
        # First check with normal thresholds
        fp1_above_threshold = fp1_pct_change >= self.ch1_threshold  # Ch1 -> LEFT
        fp2_above_threshold = fp2_pct_change >= self.ch3_threshold  # Ch3 -> RIGHT

        # If one side is triggered, reduce threshold for the other side (coupled activation)
        if fp1_above_threshold and not fp2_above_threshold:
            # Ch1 triggered, check Ch3 with reduced threshold
            reduced_ch3_threshold = self.ch3_threshold * (1 - self.coupled_reduction)
            fp2_above_threshold = fp2_pct_change >= reduced_ch3_threshold
        elif fp2_above_threshold and not fp1_above_threshold:
            # Ch3 triggered, check Ch1 with reduced threshold
            reduced_ch1_threshold = self.ch1_threshold * (1 - self.coupled_reduction)
            fp1_above_threshold = fp1_pct_change >= reduced_ch1_threshold

        # Track how long each channel has been above threshold
        if fp1_above_threshold:
            if self.fp1_change_start is None:
                self.fp1_change_start = timestamp
            fp1_duration = timestamp - self.fp1_change_start
        else:
            self.fp1_change_start = None
            fp1_duration = 0.0

        if fp2_above_threshold:
            if self.fp2_change_start is None:
                self.fp2_change_start = timestamp
            fp2_duration = timestamp - self.fp2_change_start
        else:
            self.fp2_change_start = None
            fp2_duration = 0.0

        # Trigger only if change has been sustained for required duration
        fp1_triggered = fp1_above_threshold and fp1_duration >= self.change_duration_sec
        fp2_triggered = fp2_above_threshold and fp2_duration >= self.change_duration_sec

        features = {
            'fp1_current': float(fp1_current),
            'fp2_current': float(fp2_current),
            'fp1_baseline': float(fp1_baseline),
            'fp2_baseline': float(fp2_baseline),
            'fp1_pct_change': float(fp1_pct_change * 100),  # Show as percentage
            'fp2_pct_change': float(fp2_pct_change * 100),
            'fp1_duration': float(fp1_duration),
            'fp2_duration': float(fp2_duration),
            'fp1_triggered': bool(fp1_triggered),  # Convert numpy bool to Python bool
            'fp2_triggered': bool(fp2_triggered),
            'ch1_threshold_pct': float(self.ch1_threshold * 100),  # RIGHT threshold
            'ch3_threshold_pct': float(self.ch3_threshold * 100),  # LEFT threshold
            'duration_required': float(self.change_duration_sec),
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

        # Detect commands - each channel independent
        raw_command = None
        confidence = 0.0

        if fp1_triggered and fp2_triggered:
            raw_command = "both"
            confidence = min(1.0, (fp1_pct_change + fp2_pct_change) / 2)
            # Reset change tracking
            self.fp1_change_start = None
            self.fp2_change_start = None
        elif fp1_triggered:
            raw_command = config.Command.FORWARD
            confidence = min(1.0, fp1_pct_change)
            self.fp1_change_start = None
        elif fp2_triggered:
            raw_command = config.Command.RIGHT
            confidence = min(1.0, fp2_pct_change)
            self.fp2_change_start = None

        # ============ DEDUPLICATION LOGIC ============
        # Only output a command if:
        #   1. It's DIFFERENT from the last output command, OR
        #   2. Enough silence has passed (signal dropped, user "released")

        output_command = None

        if raw_command:
            # A command was detected
            if raw_command != self.last_output_command:
                # Different command - allow it through
                output_command = raw_command
                self.last_output_command = raw_command
                self.last_command_time = timestamp
                print(f"[DETECT] ✓ {raw_command.upper()} (new command)")
            else:
                # Same command as before - suppress it (dedupe)
                features['status'] = 'dedupe_suppressed'
                # Suppressed commands - don't log to reduce noise
        else:
            # No command detected - signal dropped
            if self.last_output_command is not None:
                # Track when the command ended
                if self.command_end_time == 0.0:
                    self.command_end_time = timestamp
                    print(f"[DETECT] Signal dropped, waiting {self.dedupe_reset_sec}s to reset...")

                # After enough silence, reset so same command can trigger again
                if (timestamp - self.command_end_time) >= self.dedupe_reset_sec:
                    print(f"[DETECT] Reset complete - ready for new commands")
                    self.last_output_command = None
                    self.command_end_time = 0.0

        if output_command:
            self.command_end_time = 0.0  # Reset end timer since command is active
            return DetectionResult(
                command=output_command,
                confidence=confidence,
                features=features,
                timestamp=timestamp
            )

        features['status'] = features.get('status', 'no_change')
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
