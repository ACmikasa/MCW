#!/usr/bin/env python3
"""
EEG Wheelchair Control System - Main Entry Point

Real-time EEG-based wheelchair control using OpenBCI Cyton+Daisy.

Commands:
- Jaw Clench -> Forward
- Double Blink -> Backward
- Look Left -> Turn Left
- Look Right -> Turn Right

Usage:
    python -m eeg_wheelchair_control.main --port /dev/cu.usbserial-XXXXX
    python -m eeg_wheelchair_control.main --test  # Test mode with synthetic data
"""

import sys
import os
import time
import signal
import argparse
import logging
from datetime import datetime
from typing import Optional

import numpy as np

from . import config
from .signal_processor import SignalProcessor
from .command_detector import CommandDetector, DetectionResult
from .calibration import CalibrationManager, quick_calibration
from .data_handler import DataHandler, BrainFlowDataHandler
from .wheelchair_interface import (
    MockWheelchairInterface,
    create_wheelchair_interface,
    WheelchairInterface
)

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format=config.LOG_FORMAT,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(config.LOG_FILE),
    ]
)
logger = logging.getLogger(__name__)


class EEGWheelchairController:
    """
    Main controller for EEG-based wheelchair control.

    Integrates:
    - BrainFlow for EEG data acquisition
    - Signal processing and feature extraction
    - Command detection
    - Wheelchair control interface
    """

    def __init__(self,
                 serial_port: Optional[str] = None,
                 test_mode: bool = False,
                 wheelchair_interface: Optional[WheelchairInterface] = None):
        """
        Initialize the controller.

        Args:
            serial_port: Serial port for OpenBCI dongle
            test_mode: If True, use synthetic data instead of real EEG
            wheelchair_interface: Optional custom wheelchair interface
        """
        self.serial_port = serial_port
        self.test_mode = test_mode
        self.running = False
        self.board = None

        # Components
        self.data_handler: Optional[DataHandler] = None
        self.detector: Optional[CommandDetector] = None
        self.wheelchair = wheelchair_interface or MockWheelchairInterface()
        self.calibration_data: Optional[dict] = None

        # Statistics
        self.stats = {
            'total_windows': 0,
            'total_commands': 0,
            'commands_by_type': {cmd: 0 for cmd in [
                config.Command.FORWARD,
                config.Command.BACKWARD,
                config.Command.LEFT,
                config.Command.RIGHT,
            ]},
            'start_time': None,
            'last_command': None,
            'last_command_time': None,
        }

        # Safety
        self.last_command_time = 0
        self.safety_stop_sent = False

        logger.info(f"EEGWheelchairController initialized (test_mode={test_mode})")

    def connect(self) -> bool:
        """
        Connect to the OpenBCI board.

        Returns:
            True if connection successful
        """
        if self.test_mode:
            logger.info("Test mode: using synthetic data")
            self._setup_test_mode()
            return True

        try:
            from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds

            # Enable BrainFlow logging
            BoardShim.enable_dev_board_logger()

            # Configure board
            params = BrainFlowInputParams()
            params.serial_port = self.serial_port

            # Create board instance (Cyton + Daisy = 16 channels)
            self.board = BoardShim(BoardIds.CYTON_DAISY_BOARD, params)

            # Connect
            logger.info(f"Connecting to OpenBCI on {self.serial_port}...")
            self.board.prepare_session()

            # Setup data handler
            self.data_handler = BrainFlowDataHandler(
                board=self.board,
                fp1_channel=config.FP1_CHANNEL,
                fp2_channel=config.FP2_CHANNEL
            )

            logger.info("Connected to OpenBCI Cyton+Daisy")
            return True

        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    def _setup_test_mode(self):
        """Setup test mode with synthetic data."""
        from .signal_processor import generate_test_signal

        # Create data handler with function callback
        self.test_signal_type = 'noise'

        def test_data_getter():
            # Randomly generate different signal types for testing
            if np.random.random() < 0.1:  # 10% chance of command signal
                self.test_signal_type = np.random.choice(['jaw', 'blink', 'left', 'right'])
            else:
                self.test_signal_type = 'noise'

            return generate_test_signal(signal_type=self.test_signal_type)

        self.data_handler = DataHandler(
            data_source=test_data_getter,
            fp1_channel=0,  # Test signals only have 2 channels
            fp2_channel=1
        )

    def disconnect(self):
        """Disconnect from the board."""
        if self.board:
            try:
                self.board.stop_stream()
                self.board.release_session()
                logger.info("Disconnected from OpenBCI")
            except Exception as e:
                logger.error(f"Error disconnecting: {e}")

        self.board = None

    def start_streaming(self) -> bool:
        """Start EEG data streaming."""
        if self.test_mode:
            logger.info("Test mode: streaming started")
            return True

        if not self.board:
            logger.error("Board not connected")
            return False

        try:
            # Start stream with large ring buffer
            self.board.start_stream(45000)
            logger.info("Streaming started")
            return True
        except Exception as e:
            logger.error(f"Failed to start streaming: {e}")
            return False

    def stop_streaming(self):
        """Stop EEG data streaming."""
        if self.board:
            try:
                self.board.stop_stream()
                logger.info("Streaming stopped")
            except Exception as e:
                logger.error(f"Error stopping stream: {e}")

    def load_calibration(self, filepath: str = config.CALIBRATION_FILE) -> bool:
        """
        Load calibration from file.

        Args:
            filepath: Path to calibration file

        Returns:
            True if loaded successfully
        """
        self.calibration_data = CalibrationManager.load_calibration(filepath)

        if self.calibration_data:
            thresholds = self.calibration_data.get('thresholds', {})
            self.detector = CommandDetector(thresholds)
            logger.info("Calibration loaded")
            return True

        logger.warning("No calibration found, using defaults")
        self.detector = CommandDetector()
        return False

    def run_calibration(self):
        """Run interactive calibration."""
        if not self.data_handler:
            logger.error("Data handler not initialized")
            return

        print("\n" + "=" * 60)
        print("       EEG WHEELCHAIR CALIBRATION")
        print("=" * 60)

        def data_getter():
            return self.data_handler.get_window()

        manager = CalibrationManager(data_getter)

        def progress_callback(progress: float, message: str):
            bar_length = 30
            filled = int(bar_length * progress)
            bar = "█" * filled + "░" * (bar_length - filled)
            print(f"\r[{bar}] {progress:.0%} - {message}", end="", flush=True)
            if progress >= 1.0:
                print()

        # Start streaming for calibration
        self.start_streaming()
        time.sleep(1)  # Let buffer fill

        # Run full calibration
        try:
            result = manager.run_full_calibration(progress_callback)

            # Save calibration
            manager.save_calibration(config.CALIBRATION_FILE)

            # Update detector with new thresholds
            self.calibration_data = result
            self.detector = CommandDetector(result.get('thresholds', {}))

            print("\nCalibration complete and saved!")

        except KeyboardInterrupt:
            print("\nCalibration cancelled")

    def run_quick_calibration(self, duration: float = 10.0):
        """
        Run quick baseline-only calibration.

        Args:
            duration: Duration in seconds
        """
        print(f"\nQuick calibration ({duration}s)...")
        print("Sit still, look forward, relax...")

        self.start_streaming()
        time.sleep(1)  # Let buffer fill

        def data_getter():
            return self.data_handler.get_window()

        thresholds = quick_calibration(data_getter, duration)
        self.detector = CommandDetector(thresholds)

        print("Quick calibration complete!")
        print(f"Thresholds: {thresholds}")

    def process_window(self) -> Optional[DetectionResult]:
        """
        Process one window of EEG data.
        
        DEBUG MODE: Prints raw signal statistics.
        """
        if not self.data_handler:
            return None

        # Get current window
        fp1, fp2 = self.data_handler.get_window()

        if fp1 is None or fp2 is None:
            return None

        # --- DEBUG: Print Raw Values ---
        # Calculate stats for raw signal
        fp1_mean = np.mean(fp1)
        fp1_std = np.std(fp1)
        fp1_p2p = np.max(fp1) - np.min(fp1)
        
        fp2_mean = np.mean(fp2)
        fp2_std = np.std(fp2)
        fp2_p2p = np.max(fp2) - np.min(fp2)

        # Print raw stats on one line (CR will overwrite)
        print(f"\rRAW | FP1: mean={fp1_mean:8.1f} std={fp1_std:6.1f} p2p={fp1_p2p:6.1f} | "
              f"FP2: mean={fp2_mean:8.1f} std={fp2_std:6.1f} p2p={fp2_p2p:6.1f}", end="")

        return None  # Skip normal processing
        
        # Original processing logic disabled
        # result = self.detector.get_command(fp1, fp2)
        # 
        # # Update statistics
        # self.stats['total_windows'] += 1
        # 
        # if result.command:
        #     self.stats['total_commands'] += 1
        #     self.stats['commands_by_type'][result.command] += 1
        #     self.stats['last_command'] = result.command
        #     self.stats['last_command_time'] = result.timestamp
        #     self.last_command_time = result.timestamp
        # 
        #     # Send command to wheelchair
        #     self.wheelchair.send_command(result.command, result.confidence)
        #     self.safety_stop_sent = False
        # 
        # return result

    def check_safety_timeout(self):
        """Check if safety stop should be triggered."""
        if self.safety_stop_sent:
            return

        elapsed = time.time() - self.last_command_time

        if elapsed > config.SAFETY_TIMEOUT_SEC:
            logger.info("Safety timeout - sending STOP")
            self.wheelchair.send_command(config.Command.STOP, 1.0)
            self.safety_stop_sent = True

    def run_detection_loop(self):
        """Main detection loop."""
        print("\n" + "=" * 60)
        print("       REAL-TIME COMMAND DETECTION")
        print("=" * 60)
        print("Press Ctrl+C to stop\n")

        self.running = True
        self.stats['start_time'] = time.time()
        self.last_command_time = time.time()

        # Initialize detector if needed
        if not self.detector:
            self.detector = CommandDetector()

        # Start streaming
        if not self.start_streaming():
            print("Failed to start streaming!")
            return

        # Wait for buffer to fill
        print("Waiting for data...")
        while not self.data_handler.is_ready():
            self.data_handler.update()
            time.sleep(0.1)

        print("Detection active!\n")

        try:
            while self.running:
                start_time = time.time()

                # Process window
                result = self.process_window()

                # Display status
                if result:
                    self._display_status(result)

                # Check safety timeout
                self.check_safety_timeout()

                # Control loop timing
                processing_time = (time.time() - start_time) * 1000
                if processing_time > config.MAX_PROCESSING_TIME_MS:
                    logger.warning(f"Processing time exceeded: {processing_time:.0f}ms")

                # Sleep until next window
                sleep_time = config.WINDOW_OVERLAP_SEC - (time.time() - start_time)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except KeyboardInterrupt:
            print("\n\nStopping detection...")

        finally:
            self.running = False
            self.stop_streaming()
            self.wheelchair.stop()
            self._display_final_stats()

    def _display_status(self, result: DetectionResult):
        """Display current status."""
        cooldown = self.detector.get_cooldown_remaining()

        # Build status line
        status = "ACTIVE" if cooldown == 0 else f"COOLDOWN ({cooldown:.1f}s)"

        features = result.features
        fp1 = features['fp1_max']
        fp2 = features['fp2_max']
        jaw = features['jaw_power']
        quality = features['signal_quality']

        # Clear line and print status
        print(f"\rStatus: {status:15} | "
              f"FP1: {fp1:6.1f} uV | "
              f"FP2: {fp2:6.1f} uV | "
              f"Jaw: {jaw:7.1f} | "
              f"Quality: {quality:.0%}", end="")

        # Print command if detected
        if result.command:
            print(f"\n>>> COMMAND: {result.command.upper()} "
                  f"(confidence: {result.confidence:.0%})")

    def _display_final_stats(self):
        """Display final statistics."""
        if not self.stats['start_time']:
            return

        duration = time.time() - self.stats['start_time']

        print("\n" + "=" * 60)
        print("       SESSION STATISTICS")
        print("=" * 60)
        print(f"Duration: {duration:.1f} seconds")
        print(f"Total windows processed: {self.stats['total_windows']}")
        print(f"Total commands detected: {self.stats['total_commands']}")
        print("\nCommands by type:")
        for cmd, count in self.stats['commands_by_type'].items():
            if cmd:
                print(f"  {cmd}: {count}")
        print("=" * 60)


def print_banner():
    """Print application banner."""
    banner = """
╔══════════════════════════════════════════════════════════════════╗
║         EEG WHEELCHAIR CONTROL SYSTEM                            ║
║         OpenBCI Cyton + Daisy (16 channels)                      ║
╠══════════════════════════════════════════════════════════════════╣
║  Commands:                                                       ║
║    Jaw Clench    → FORWARD                                       ║
║    Double Blink  → BACKWARD                                      ║
║    Look Left     → TURN LEFT                                     ║
║    Look Right    → TURN RIGHT                                    ║
╚══════════════════════════════════════════════════════════════════╝
"""
    print(banner)


def print_menu():
    """Print main menu."""
    print("\n=== MAIN MENU ===")
    print("1. Run Calibration")
    print("2. Quick Calibration (10 seconds)")
    print("3. Load Previous Calibration")
    print("4. Start Real-Time Detection")
    print("5. Test Mode (synthetic data)")
    print("6. Exit")
    print()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="EEG Wheelchair Control System")
    parser.add_argument('--port', type=str, help='Serial port for OpenBCI dongle')
    parser.add_argument('--test', action='store_true', help='Run in test mode with synthetic data')
    parser.add_argument('--calibrate', action='store_true', help='Run calibration')
    parser.add_argument('--run', action='store_true', help='Start detection immediately')
    args = parser.parse_args()

    print_banner()

    # Create controller
    controller = EEGWheelchairController(
        serial_port=args.port,
        test_mode=args.test
    )

    # Handle direct commands
    if args.calibrate:
        if controller.connect():
            controller.run_calibration()
            controller.disconnect()
        return

    if args.run:
        if controller.connect():
            controller.load_calibration()
            controller.run_detection_loop()
            controller.disconnect()
        return

    # Interactive menu
    while True:
        print_menu()

        try:
            choice = input("Select option: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting...")
            break

        if choice == '1':
            # Full calibration
            port = args.port or input("Enter serial port (e.g., /dev/cu.usbserial-XXX): ").strip()
            controller.serial_port = port
            if controller.connect():
                controller.run_calibration()
                controller.disconnect()
            else:
                print("Failed to connect. Check port and try again.")

        elif choice == '2':
            # Quick calibration
            port = args.port or input("Enter serial port: ").strip()
            controller.serial_port = port
            if controller.connect():
                controller.run_quick_calibration(10.0)
                controller.disconnect()

        elif choice == '3':
            # Load calibration
            if controller.load_calibration():
                print("Calibration loaded successfully!")
            else:
                print("No calibration file found.")

        elif choice == '4':
            # Real-time detection
            port = args.port or input("Enter serial port: ").strip()
            controller.serial_port = port
            if controller.connect():
                controller.load_calibration()
                controller.run_detection_loop()
                controller.disconnect()

        elif choice == '5':
            # Test mode
            controller.test_mode = True
            if controller.connect():
                controller.run_quick_calibration(5.0)
                controller.run_detection_loop()

        elif choice == '6':
            print("Exiting...")
            break

        else:
            print("Invalid option. Try again.")

    # Cleanup
    controller.disconnect()


if __name__ == "__main__":
    main()
