"""
Calibration Module for EEG Wheelchair Control.

Implements user-specific calibration for optimal command detection:
1. Baseline calibration (30 seconds of relaxed state)
2. Command calibration (10 trials per command)
3. Threshold calculation
4. Profile saving/loading
"""

import json
import time
import os
import logging
from datetime import datetime
from typing import Dict, Optional, Callable
import numpy as np

from . import config
from .signal_processor import SignalProcessor

logger = logging.getLogger(__name__)


class CalibrationManager:
    """
    Manages user calibration for EEG-based command detection.

    Calibration process:
    1. Baseline collection (30s): User sits still with eyes forward
    2. Command trials (10 per command): User performs each action
    3. Threshold calculation: Determines optimal thresholds
    4. Profile saving: Stores calibration for future sessions
    """

    def __init__(self, data_getter: Callable[[], tuple]):
        """
        Initialize the calibration manager.

        Args:
            data_getter: Function that returns (fp1_data, fp2_data) for a 1-second window
        """
        self.data_getter = data_getter
        self.signal_processor = SignalProcessor()

        self.baseline_data = {
            'fp1_amplitudes': [],
            'fp2_amplitudes': [],
            'jaw_powers': [],
        }

        self.command_data = {
            config.Command.FORWARD: [],   # Jaw clench
            config.Command.BACKWARD: [],  # Double blink
            config.Command.LEFT: [],      # Look left
            config.Command.RIGHT: [],     # Look right
        }

        self.calibration_result = None

        logger.info("CalibrationManager initialized")

    def run_baseline_calibration(self, duration_sec: float = config.BASELINE_DURATION_SEC,
                                  progress_callback: Optional[Callable] = None) -> Dict:
        """
        Collect baseline EEG data while user is relaxed.

        Instructions for user:
        - Sit comfortably with eyes open
        - Look straight ahead
        - Try not to move, blink excessively, or clench jaw
        - Relax facial muscles

        Args:
            duration_sec: Duration of baseline recording
            progress_callback: Optional callback(progress: float, message: str)

        Returns:
            Dictionary with baseline statistics
        """
        logger.info(f"Starting baseline calibration ({duration_sec}s)")

        if progress_callback:
            progress_callback(0.0, "Starting baseline calibration...")

        self.baseline_data = {
            'fp1_amplitudes': [],
            'fp2_amplitudes': [],
            'jaw_powers': [],
        }

        start_time = time.time()
        sample_count = 0

        while time.time() - start_time < duration_sec:
            # Get data window
            try:
                fp1, fp2 = self.data_getter()

                if fp1 is None or fp2 is None:
                    time.sleep(0.1)
                    continue

                # Process and extract features
                fp1_eog, fp2_eog, fp1_emg, fp2_emg = self.signal_processor.preprocess(fp1, fp2)

                # Store amplitudes
                self.baseline_data['fp1_amplitudes'].append(np.max(np.abs(fp1_eog)))
                self.baseline_data['fp2_amplitudes'].append(np.max(np.abs(fp2_eog)))
                self.baseline_data['jaw_powers'].append(self.signal_processor.calculate_power(fp1_emg))

                sample_count += 1

                # Progress update
                elapsed = time.time() - start_time
                progress = elapsed / duration_sec
                if progress_callback:
                    progress_callback(progress, f"Baseline: {elapsed:.1f}s / {duration_sec}s")

            except Exception as e:
                logger.error(f"Error during baseline: {e}")

            time.sleep(0.5)  # Sample every 0.5 seconds

        # Calculate baseline statistics
        baseline_stats = self._calculate_baseline_stats()

        if progress_callback:
            progress_callback(1.0, "Baseline calibration complete!")

        logger.info(f"Baseline complete: {sample_count} samples collected")
        logger.info(f"Baseline stats: {baseline_stats}")

        return baseline_stats

    def _calculate_baseline_stats(self) -> Dict:
        """Calculate statistics from baseline data."""
        fp1_amps = np.array(self.baseline_data['fp1_amplitudes'])
        fp2_amps = np.array(self.baseline_data['fp2_amplitudes'])
        jaw_powers = np.array(self.baseline_data['jaw_powers'])

        if len(fp1_amps) == 0:
            logger.warning("No baseline data collected!")
            return {}

        return {
            'fp1_mean': float(np.mean(fp1_amps)),
            'fp1_std': float(np.std(fp1_amps)),
            'fp2_mean': float(np.mean(fp2_amps)),
            'fp2_std': float(np.std(fp2_amps)),
            'amplitude_mean': float(np.mean([np.mean(fp1_amps), np.mean(fp2_amps)])),
            'amplitude_std': float(np.mean([np.std(fp1_amps), np.std(fp2_amps)])),
            'jaw_power_mean': float(np.mean(jaw_powers)),
            'jaw_power_std': float(np.std(jaw_powers)),
        }

    def run_command_calibration(self, command: str,
                                 num_trials: int = config.CALIBRATION_TRIALS_PER_COMMAND,
                                 progress_callback: Optional[Callable] = None) -> Dict:
        """
        Calibrate a specific command with multiple trials.

        Args:
            command: Command to calibrate (FORWARD, BACKWARD, LEFT, RIGHT)
            num_trials: Number of trials per command
            progress_callback: Optional callback(progress: float, message: str)

        Returns:
            Dictionary with command-specific statistics
        """
        logger.info(f"Starting calibration for {command} ({num_trials} trials)")

        command_instructions = {
            config.Command.FORWARD: "CLENCH YOUR JAW firmly for 1 second",
            config.Command.BACKWARD: "BLINK TWICE quickly (double blink)",
            config.Command.LEFT: "LOOK LEFT with your eyes (not head)",
            config.Command.RIGHT: "LOOK RIGHT with your eyes (not head)",
        }

        instruction = command_instructions.get(command, "Perform the action")
        self.command_data[command] = []

        for trial in range(num_trials):
            if progress_callback:
                progress = trial / num_trials
                progress_callback(progress, f"Trial {trial + 1}/{num_trials}: {instruction}")

            # Wait for user to prepare
            time.sleep(config.CALIBRATION_TRIAL_INTERVAL_SEC)

            # Collect data during action (2 seconds)
            trial_data = self._collect_trial_data(command, duration=2.0)
            self.command_data[command].append(trial_data)

            logger.debug(f"Trial {trial + 1}: {trial_data}")

        # Calculate command statistics
        command_stats = self._calculate_command_stats(command)

        if progress_callback:
            progress_callback(1.0, f"{command} calibration complete!")

        logger.info(f"{command} calibration complete: {command_stats}")

        return command_stats

    def _collect_trial_data(self, command: str, duration: float = 2.0) -> Dict:
        """Collect data during a command trial."""
        trial_features = {
            'fp1_max': [],
            'fp2_max': [],
            'jaw_power': [],
            'blink_count': [],
            'fp1_fp2_ratio': [],
            'fp2_fp1_ratio': [],
        }

        start_time = time.time()

        while time.time() - start_time < duration:
            try:
                fp1, fp2 = self.data_getter()

                if fp1 is None or fp2 is None:
                    time.sleep(0.1)
                    continue

                features = self.signal_processor.extract_features(fp1, fp2)

                trial_features['fp1_max'].append(features.fp1_max)
                trial_features['fp2_max'].append(features.fp2_max)
                trial_features['jaw_power'].append(features.jaw_power)
                trial_features['blink_count'].append(features.blink_count)
                trial_features['fp1_fp2_ratio'].append(features.fp1_fp2_ratio)
                trial_features['fp2_fp1_ratio'].append(features.fp2_fp1_ratio)

            except Exception as e:
                logger.error(f"Error during trial: {e}")

            time.sleep(0.2)

        # Return max/peak values from trial
        return {
            'fp1_max': max(trial_features['fp1_max']) if trial_features['fp1_max'] else 0,
            'fp2_max': max(trial_features['fp2_max']) if trial_features['fp2_max'] else 0,
            'jaw_power': max(trial_features['jaw_power']) if trial_features['jaw_power'] else 0,
            'blink_count': max(trial_features['blink_count']) if trial_features['blink_count'] else 0,
            'fp1_fp2_ratio': max(trial_features['fp1_fp2_ratio']) if trial_features['fp1_fp2_ratio'] else 0,
            'fp2_fp1_ratio': max(trial_features['fp2_fp1_ratio']) if trial_features['fp2_fp1_ratio'] else 0,
        }

    def _calculate_command_stats(self, command: str) -> Dict:
        """Calculate statistics for a command from trial data."""
        trials = self.command_data[command]

        if not trials:
            return {}

        # Extract relevant metric for each command
        if command == config.Command.FORWARD:
            values = [t['jaw_power'] for t in trials]
        elif command == config.Command.BACKWARD:
            values = [t['blink_count'] for t in trials]
        elif command == config.Command.LEFT:
            values = [t['fp2_fp1_ratio'] for t in trials]
        elif command == config.Command.RIGHT:
            values = [t['fp1_fp2_ratio'] for t in trials]
        else:
            values = []

        return {
            'mean': float(np.mean(values)) if values else 0,
            'std': float(np.std(values)) if values else 0,
            'min': float(np.min(values)) if values else 0,
            'max': float(np.max(values)) if values else 0,
            'trials': trials,
        }

    def calculate_thresholds(self, baseline_stats: Dict) -> Dict:
        """
        Calculate optimal thresholds based on calibration data.

        Threshold formula: baseline_mean + multiplier * baseline_std

        Args:
            baseline_stats: Statistics from baseline calibration

        Returns:
            Dictionary with calculated thresholds
        """
        if not baseline_stats:
            logger.warning("No baseline stats available, using defaults")
            return {
                'blink_threshold': 50.0,
                'jaw_threshold': 500.0,
                'eye_move_threshold': 30.0,
                'lateral_ratio': config.LATERAL_RATIO,
                'baseline_amplitude': 20.0,
                'baseline_jaw_power': 200.0,
            }

        # Blink threshold: 3x baseline amplitude
        blink_threshold = (
            baseline_stats['amplitude_mean'] +
            config.BLINK_MULTIPLIER * baseline_stats['amplitude_std']
        )
        blink_threshold = max(blink_threshold, 30.0)  # Minimum threshold

        # Jaw threshold: 2.5x baseline jaw power
        jaw_threshold = (
            baseline_stats['jaw_power_mean'] +
            config.JAW_MULTIPLIER * baseline_stats['jaw_power_std']
        )
        jaw_threshold = max(jaw_threshold, 100.0)  # Minimum threshold

        # Eye movement threshold: 1.5x baseline
        eye_move_threshold = (
            baseline_stats['amplitude_mean'] +
            config.EYE_MOVE_MULTIPLIER * baseline_stats['amplitude_std']
        )
        eye_move_threshold = max(eye_move_threshold, 20.0)  # Minimum threshold

        # Refine thresholds based on command calibration if available
        if self.command_data[config.Command.FORWARD]:
            jaw_values = [t['jaw_power'] for t in self.command_data[config.Command.FORWARD]]
            if jaw_values:
                # Set threshold at midpoint between baseline and command
                command_mean = np.mean(jaw_values)
                jaw_threshold = (baseline_stats['jaw_power_mean'] + command_mean) / 2

        thresholds = {
            'blink_threshold': float(blink_threshold),
            'jaw_threshold': float(jaw_threshold),
            'eye_move_threshold': float(eye_move_threshold),
            'lateral_ratio': config.LATERAL_RATIO,
            'baseline_amplitude': float(baseline_stats['amplitude_mean']),
            'baseline_jaw_power': float(baseline_stats['jaw_power_mean']),
        }

        logger.info(f"Calculated thresholds: {thresholds}")
        return thresholds

    def run_full_calibration(self, progress_callback: Optional[Callable] = None) -> Dict:
        """
        Run complete calibration sequence.

        1. Baseline (30 seconds)
        2. Jaw clench trials (10)
        3. Double blink trials (10)
        4. Look left trials (10)
        5. Look right trials (10)

        Args:
            progress_callback: Optional callback(progress: float, message: str)

        Returns:
            Complete calibration result
        """
        logger.info("Starting full calibration sequence")

        # Phase 1: Baseline
        if progress_callback:
            progress_callback(0.0, "Phase 1/5: Baseline calibration")

        baseline_stats = self.run_baseline_calibration(
            progress_callback=lambda p, m: progress_callback(p * 0.2, m) if progress_callback else None
        )

        # Phase 2-5: Command calibration
        commands = [
            (config.Command.FORWARD, "Phase 2/5: Jaw Clench (FORWARD)"),
            (config.Command.BACKWARD, "Phase 3/5: Double Blink (BACKWARD)"),
            (config.Command.LEFT, "Phase 4/5: Look Left (LEFT)"),
            (config.Command.RIGHT, "Phase 5/5: Look Right (RIGHT)"),
        ]

        for i, (command, phase_name) in enumerate(commands):
            if progress_callback:
                progress_callback(0.2 + i * 0.2, phase_name)

            self.run_command_calibration(
                command,
                progress_callback=lambda p, m: progress_callback(0.2 + i * 0.2 + p * 0.2, m) if progress_callback else None
            )

        # Calculate final thresholds
        thresholds = self.calculate_thresholds(baseline_stats)

        # Build calibration result
        self.calibration_result = {
            'timestamp': datetime.now().isoformat(),
            'baseline_stats': baseline_stats,
            'thresholds': thresholds,
            'command_stats': {
                cmd: self._calculate_command_stats(cmd)
                for cmd in self.command_data
            },
        }

        if progress_callback:
            progress_callback(1.0, "Calibration complete!")

        return self.calibration_result

    def save_calibration(self, filepath: str = config.CALIBRATION_FILE,
                         user_id: str = "default") -> bool:
        """
        Save calibration profile to JSON file.

        Args:
            filepath: Path to save calibration file
            user_id: User identifier

        Returns:
            True if saved successfully
        """
        if not self.calibration_result:
            logger.error("No calibration data to save")
            return False

        try:
            save_data = {
                'user_id': user_id,
                'version': '1.0',
                **self.calibration_result
            }

            with open(filepath, 'w') as f:
                json.dump(save_data, f, indent=2)

            logger.info(f"Calibration saved to {filepath}")
            return True

        except Exception as e:
            logger.error(f"Failed to save calibration: {e}")
            return False

    @staticmethod
    def load_calibration(filepath: str = config.CALIBRATION_FILE) -> Optional[Dict]:
        """
        Load calibration profile from JSON file.

        Args:
            filepath: Path to calibration file

        Returns:
            Calibration data dictionary or None if not found
        """
        if not os.path.exists(filepath):
            logger.warning(f"Calibration file not found: {filepath}")
            return None

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)

            logger.info(f"Calibration loaded from {filepath}")
            logger.info(f"User: {data.get('user_id')}, "
                       f"Timestamp: {data.get('timestamp')}")

            return data

        except Exception as e:
            logger.error(f"Failed to load calibration: {e}")
            return None

    @staticmethod
    def get_thresholds_from_file(filepath: str = config.CALIBRATION_FILE) -> Optional[Dict]:
        """
        Load just the thresholds from a calibration file.

        Args:
            filepath: Path to calibration file

        Returns:
            Thresholds dictionary or None
        """
        data = CalibrationManager.load_calibration(filepath)

        if data and 'thresholds' in data:
            return data['thresholds']

        return None


# =============================================================================
# Quick Calibration (Simplified)
# =============================================================================

def quick_calibration(data_getter: Callable, duration_sec: float = 10.0) -> Dict:
    """
    Quick baseline-only calibration for testing.

    Args:
        data_getter: Function returning (fp1, fp2) data
        duration_sec: Duration in seconds

    Returns:
        Basic thresholds dictionary
    """
    logger.info(f"Running quick calibration ({duration_sec}s)")

    processor = SignalProcessor()
    amplitudes = []
    jaw_powers = []

    start_time = time.time()

    while time.time() - start_time < duration_sec:
        try:
            fp1, fp2 = data_getter()

            if fp1 is not None and fp2 is not None:
                features = processor.extract_features(fp1, fp2)
                amplitudes.append(max(features.fp1_max, features.fp2_max))
                jaw_powers.append(features.jaw_power)

        except Exception as e:
            logger.error(f"Quick calibration error: {e}")

        time.sleep(0.2)

    if not amplitudes:
        return {
            'blink_threshold': 50.0,
            'jaw_threshold': 500.0,
            'eye_move_threshold': 30.0,
            'lateral_ratio': config.LATERAL_RATIO,
            'baseline_amplitude': 20.0,
            'baseline_jaw_power': 200.0,
        }

    amp_mean = np.mean(amplitudes)
    amp_std = np.std(amplitudes)
    jaw_mean = np.mean(jaw_powers)
    jaw_std = np.std(jaw_powers)

    return {
        'blink_threshold': float(amp_mean + 3.0 * amp_std),
        'jaw_threshold': float(jaw_mean + 2.5 * jaw_std),
        'eye_move_threshold': float(amp_mean + 1.5 * amp_std),
        'lateral_ratio': config.LATERAL_RATIO,
        'baseline_amplitude': float(amp_mean),
        'baseline_jaw_power': float(jaw_mean),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    # Test with synthetic data
    from .signal_processor import generate_test_signal

    def fake_data_getter():
        return generate_test_signal(signal_type='noise')

    thresholds = quick_calibration(fake_data_getter, duration_sec=3.0)
    print(f"Quick calibration thresholds: {thresholds}")
