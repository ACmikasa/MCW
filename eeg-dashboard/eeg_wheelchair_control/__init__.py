"""
EEG Wheelchair Control System

Real-time EEG-based wheelchair control using OpenBCI Cyton+Daisy.

Modules:
    - config: Configuration parameters
    - signal_processor: Signal filtering and feature extraction
    - command_detector: Command detection algorithms
    - calibration: User calibration system
    - data_handler: Data input and buffering
    - wheelchair_interface: Wheelchair control interface
    - main: Main application entry point

Usage:
    # Command line
    python -m eeg_wheelchair_control.main --port /dev/cu.usbserial-XXX

    # Python API
    from eeg_wheelchair_control import EEGWheelchairController

    controller = EEGWheelchairController(serial_port='/dev/cu.usbserial-XXX')
    controller.connect()
    controller.run_detection_loop()
"""

from .config import Command, SAMPLING_RATE, FP1_CHANNEL, FP2_CHANNEL
from .signal_processor import SignalProcessor, Features
from .command_detector import CommandDetector, DetectionResult
from .calibration import CalibrationManager, quick_calibration
from .data_handler import DataHandler, BrainFlowDataHandler, CircularBuffer
from .wheelchair_interface import (
    WheelchairInterface,
    MockWheelchairInterface,
    SerialWheelchairInterface,
    CallbackWheelchairInterface,
    create_wheelchair_interface
)
from .main import EEGWheelchairController

__version__ = "1.0.0"
__author__ = "EEG Wheelchair Control Team"

__all__ = [
    # Config
    'Command',
    'SAMPLING_RATE',
    'FP1_CHANNEL',
    'FP2_CHANNEL',

    # Signal Processing
    'SignalProcessor',
    'Features',

    # Command Detection
    'CommandDetector',
    'DetectionResult',

    # Calibration
    'CalibrationManager',
    'quick_calibration',

    # Data Handling
    'DataHandler',
    'BrainFlowDataHandler',
    'CircularBuffer',

    # Wheelchair Interface
    'WheelchairInterface',
    'MockWheelchairInterface',
    'SerialWheelchairInterface',
    'CallbackWheelchairInterface',
    'create_wheelchair_interface',

    # Main Controller
    'EEGWheelchairController',
]
