"""
Command Detection Module for EEG Wheelchair Control.

Implements detection algorithms for:
- Look Left (FP2 > FP1)
- Look Right (FP1 > FP2)
- Jaw Clench (high-frequency EMG power)
- Double Eye Blink (two consecutive peaks)
"""

import time
import logging
from typing import Optional, Dict, Tuple
from dataclasses import dataclass
import numpy as np

from . import config
from .signal_processor import SignalProcessor, Features

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    """Result of command detection."""
    command: Optional[str]      # Detected command or None
    confidence: float           # Confidence score (0-1)
    features: Dict              # Extracted features for debugging
    timestamp: float            # Unix timestamp of detection


class CommandDetector:
    """
    Real-time command detector for EEG-based wheelchair control.

    Detects four commands:
    - FORWARD: Jaw clench (strong EMG in 20-100 Hz band)
    - BACKWARD: Double eye blink (two peaks within 200-800ms)
    - LEFT: Look left (FP2 amplitude > FP1 amplitude)
    - RIGHT: Look right (FP1 amplitude > FP2 amplitude)
    """

    def __init__(self, calibration_data: Optional[Dict] = None):
        """
        Initialize the command detector.

        Args:
            calibration_data: Optional calibration data from calibration.py
        """
        self.signal_processor = SignalProcessor()

        # Load calibration or use defaults
        self.thresholds = self._load_thresholds(calibration_data)

        # State tracking
        self.last_command_time = 0.0
        self.last_command = None
        self.cooldown_active = False

        # For signal-return-to-baseline check
        self.signal_was_below_threshold = True

        logger.info("CommandDetector initialized")
        logger.info(f"Thresholds: {self.thresholds}")

    def _load_thresholds(self, calibration_data: Optional[Dict]) -> Dict:
        """Load thresholds from calibration data or use defaults."""
        if calibration_data:
            return {
                'blink_threshold': calibration_data.get('blink_threshold', 50.0),
                'jaw_threshold': calibration_data.get('jaw_threshold', 500.0),
                'eye_move_threshold': calibration_data.get('eye_move_threshold', 30.0),
                'lateral_ratio': calibration_data.get('lateral_ratio', config.LATERAL_RATIO),
                'baseline_amplitude': calibration_data.get('baseline_amplitude', 20.0),
                'baseline_jaw_power': calibration_data.get('baseline_jaw_power', 200.0),
            }
        else:
            # Default thresholds (should be calibrated for each user)
            # These work with raw values from OpenBCI
            return {
                'blink_threshold': 50000.0,     # Raw amplitude for blink peaks
                'jaw_threshold': 500000.0,      # Raw power for jaw clench
                'eye_move_threshold': 30000.0,  # Raw amplitude for eye movements
                'lateral_ratio': config.LATERAL_RATIO,
                'baseline_amplitude': 20000.0,
                'baseline_jaw_power': 200000.0,
            }

    def update_thresholds(self, calibration_data: Dict):
        """Update thresholds with new calibration data."""
        self.thresholds = self._load_thresholds(calibration_data)
        logger.info(f"Thresholds updated: {self.thresholds}")

    def detect_jaw_clench(self, features: Features) -> Tuple[bool, float]:
        """
        Detect jaw clench from high-frequency EMG power.

        Jaw clenching activates the temporalis muscle, producing strong
        EMG signals in the 20-100 Hz band on frontal electrodes.

        Args:
            features: Extracted signal features

        Returns:
            Tuple of (detected: bool, confidence: float)
        """
        threshold = self.thresholds['jaw_threshold']
        baseline = self.thresholds['baseline_jaw_power']

        # Check if jaw power exceeds threshold
        if features.jaw_power > threshold:
            # Calculate confidence based on how far above threshold
            ratio = features.jaw_power / threshold
            confidence = min(1.0, 0.5 + (ratio - 1.0) * 0.5)

            logger.debug(f"Jaw clench detected: power={features.jaw_power:.2f}, "
                        f"threshold={threshold:.2f}, confidence={confidence:.2f}")
            return True, confidence

        return False, 0.0

    def detect_double_blink(self, features: Features, fp1_eog: np.ndarray) -> Tuple[bool, float]:
        """
        Detect double eye blink from two consecutive peaks.

        Double blinks are identified by:
        1. Exactly 2 high-amplitude peaks in the window
        2. Peaks are 200-800ms apart

        Args:
            features: Extracted signal features
            fp1_eog: EOG-filtered FP1 signal for precise peak timing

        Returns:
            Tuple of (detected: bool, confidence: float)
        """
        peaks = features.blink_peaks

        # Need exactly 2 peaks for double blink
        if len(peaks) != 2:
            return False, 0.0

        # Check timing between peaks
        peak_interval = peaks[1] - peaks[0]

        if (config.BLINK_MIN_INTERVAL_SAMPLES <= peak_interval <=
            config.BLINK_MAX_INTERVAL_SAMPLES):

            # Calculate confidence based on peak amplitudes
            threshold = self.thresholds['blink_threshold']

            # Get peak amplitudes from the EOG signal
            if len(fp1_eog) > max(peaks):
                amp1 = abs(fp1_eog[peaks[0]])
                amp2 = abs(fp1_eog[peaks[1]])
                avg_amp = (amp1 + amp2) / 2

                ratio = avg_amp / threshold
                confidence = min(1.0, 0.5 + (ratio - 1.0) * 0.3)

                interval_ms = peak_interval / config.SAMPLING_RATE * 1000
                logger.debug(f"Double blink detected: interval={interval_ms:.0f}ms, "
                            f"confidence={confidence:.2f}")
                return True, confidence

        return False, 0.0

    def detect_look_left(self, features: Features) -> Tuple[bool, float]:
        """
        Detect looking left from FP2 > FP1 amplitude asymmetry.

        When looking left, the eyes move toward FP2 (right electrode),
        creating a stronger signal on FP2 than FP1.

        Args:
            features: Extracted signal features

        Returns:
            Tuple of (detected: bool, confidence: float)
        """
        threshold = self.thresholds['eye_move_threshold']
        ratio_threshold = self.thresholds['lateral_ratio']

        # Check if FP2 exceeds threshold AND is significantly larger than FP1
        if (features.fp2_max > threshold and
            features.fp2_fp1_ratio > ratio_threshold):

            # Calculate confidence
            ratio_excess = features.fp2_fp1_ratio / ratio_threshold
            amp_excess = features.fp2_max / threshold
            confidence = min(1.0, 0.4 + ratio_excess * 0.3 + amp_excess * 0.2)

            logger.debug(f"Look LEFT detected: FP2={features.fp2_max:.2f}, "
                        f"ratio={features.fp2_fp1_ratio:.2f}, confidence={confidence:.2f}")
            return True, confidence

        return False, 0.0

    def detect_look_right(self, features: Features) -> Tuple[bool, float]:
        """
        Detect looking right from FP1 > FP2 amplitude asymmetry.

        When looking right, the eyes move toward FP1 (left electrode),
        creating a stronger signal on FP1 than FP2.

        Args:
            features: Extracted signal features

        Returns:
            Tuple of (detected: bool, confidence: float)
        """
        threshold = self.thresholds['eye_move_threshold']
        ratio_threshold = self.thresholds['lateral_ratio']

        # Check if FP1 exceeds threshold AND is significantly larger than FP2
        if (features.fp1_max > threshold and
            features.fp1_fp2_ratio > ratio_threshold):

            # Calculate confidence
            ratio_excess = features.fp1_fp2_ratio / ratio_threshold
            amp_excess = features.fp1_max / threshold
            confidence = min(1.0, 0.4 + ratio_excess * 0.3 + amp_excess * 0.2)

            logger.debug(f"Look RIGHT detected: FP1={features.fp1_max:.2f}, "
                        f"ratio={features.fp1_fp2_ratio:.2f}, confidence={confidence:.2f}")
            return True, confidence

        return False, 0.0

    def check_cooldown(self) -> bool:
        """Check if cooldown period has elapsed."""
        current_time = time.time()
        elapsed = current_time - self.last_command_time

        if elapsed < config.COMMAND_COOLDOWN_SEC:
            return True  # Still in cooldown

        return False

    def get_cooldown_remaining(self) -> float:
        """Get remaining cooldown time in seconds."""
        elapsed = time.time() - self.last_command_time
        remaining = config.COMMAND_COOLDOWN_SEC - elapsed
        return max(0.0, remaining)

    def get_command(self, fp1_raw: np.ndarray, fp2_raw: np.ndarray) -> DetectionResult:
        """
        Main detection function - analyzes signals and returns detected command.

        Detection priority (most distinct signals first):
        1. Jaw clench (FORWARD) - very distinct EMG pattern
        2. Double blink (BACKWARD) - time-based pattern
        3. Look left (LEFT) - amplitude asymmetry
        4. Look right (RIGHT) - amplitude asymmetry

        Args:
            fp1_raw: Raw FP1 data (1-second window)
            fp2_raw: Raw FP2 data (1-second window)

        Returns:
            DetectionResult with command, confidence, and features
        """
        timestamp = time.time()

        # Extract features
        features = self.signal_processor.extract_features(
            fp1_raw, fp2_raw,
            baseline_stats={'blink_threshold': self.thresholds['blink_threshold']}
        )

        # Check signal quality
        if features.signal_quality < config.MIN_SIGNAL_QUALITY:
            logger.warning(f"Poor signal quality: {features.signal_quality:.2f}")
            return DetectionResult(
                command=None,
                confidence=0.0,
                features=self._features_to_dict(features),
                timestamp=timestamp
            )

        # Check cooldown
        if self.check_cooldown():
            return DetectionResult(
                command=None,
                confidence=0.0,
                features=self._features_to_dict(features),
                timestamp=timestamp
            )

        # Get preprocessed signals for detailed analysis
        fp1_eog, fp2_eog, fp1_emg, fp2_emg = self.signal_processor.preprocess(
            fp1_raw, fp2_raw
        )

        # Detection in priority order
        detected_command = None
        confidence = 0.0

        # 1. Check jaw clench (most distinct)
        jaw_detected, jaw_conf = self.detect_jaw_clench(features)
        if jaw_detected and jaw_conf >= config.MIN_CONFIDENCE:
            detected_command = config.Command.FORWARD
            confidence = jaw_conf

        # 2. Check double blink
        if detected_command is None:
            blink_detected, blink_conf = self.detect_double_blink(features, fp1_eog)
            if blink_detected and blink_conf >= config.MIN_CONFIDENCE:
                detected_command = config.Command.BACKWARD
                confidence = blink_conf

        # 3. Check look left
        if detected_command is None:
            left_detected, left_conf = self.detect_look_left(features)
            if left_detected and left_conf >= config.MIN_CONFIDENCE:
                detected_command = config.Command.LEFT
                confidence = left_conf

        # 4. Check look right
        if detected_command is None:
            right_detected, right_conf = self.detect_look_right(features)
            if right_detected and right_conf >= config.MIN_CONFIDENCE:
                detected_command = config.Command.RIGHT
                confidence = right_conf

        # Update state if command detected
        if detected_command is not None:
            self.last_command_time = timestamp
            self.last_command = detected_command
            logger.info(f"Command detected: {detected_command} (confidence: {confidence:.2f})")

        return DetectionResult(
            command=detected_command,
            confidence=confidence,
            features=self._features_to_dict(features),
            timestamp=timestamp
        )

    def _features_to_dict(self, features: Features) -> Dict:
        """Convert Features dataclass to dictionary."""
        return {
            'fp1_max': features.fp1_max,
            'fp2_max': features.fp2_max,
            'fp1_mean': features.fp1_mean,
            'fp2_mean': features.fp2_mean,
            'jaw_power': features.jaw_power,
            'blink_count': features.blink_count,
            'fp1_fp2_ratio': features.fp1_fp2_ratio,
            'fp2_fp1_ratio': features.fp2_fp1_ratio,
            'signal_quality': features.signal_quality,
        }


# =============================================================================
# Testing Functions
# =============================================================================

def test_command_detection():
    """Test command detection with synthetic signals."""
    from .signal_processor import generate_test_signal

    print("\nTesting Command Detection")
    print("=" * 50)

    detector = CommandDetector()

    # Test each command type
    test_cases = [
        ('noise', None),
        ('jaw', config.Command.FORWARD),
        ('blink', config.Command.BACKWARD),
        ('left', config.Command.LEFT),
        ('right', config.Command.RIGHT),
    ]

    for signal_type, expected in test_cases:
        fp1, fp2 = generate_test_signal(signal_type=signal_type)
        result = detector.get_command(fp1, fp2)

        status = "PASS" if result.command == expected else "FAIL"
        print(f"{signal_type:8} -> {str(result.command):10} "
              f"(expected: {str(expected):10}) [{status}] "
              f"conf={result.confidence:.2f}")

        # Reset cooldown between tests
        detector.last_command_time = 0

    print("=" * 50)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_command_detection()
