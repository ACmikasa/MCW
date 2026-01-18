#!/usr/bin/env python3
"""
OpenBCI Cyton + Daisy EEG Streaming Backend with Wheelchair Control
====================================================================
Uses BrainFlow for data acquisition and WebSockets for live streaming.
Integrates real-time command detection for wheelchair control.

Technical specs (from BrainFlow docs):
- Board ID: CYTON_DAISY_BOARD = 2
- Sample Rate: 125 Hz (Daisy halves the 250 Hz Cyton rate)
- EEG Channels: 16
- Accelerometer Channels: 3
- macOS: Must use /dev/cu.* ports (not /dev/tty.*)

Commands:
- Jaw Clench -> Forward
- Double Blink -> Backward
- Look Left -> Turn Left
- Look Right -> Turn Right
"""

import asyncio
import json
import signal
import sys
import time
import csv
import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import threading
from collections import deque

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, use os.environ directly

OVERSHOOT_API_KEY = os.environ.get("OVERSHOOT_API_KEY", "")

from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError
from brainflow.data_filter import DataFilter
import numpy as np

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
import uvicorn

# Camera and Vision imports
try:
    import cv2
    CAMERA_AVAILABLE = True
except ImportError:
    CAMERA_AVAILABLE = False
    print("[WARNING] OpenCV not installed. Camera features disabled. Run: pip install opencv-python")

try:
    import anthropic
    CLAUDE_AVAILABLE = True
except ImportError:
    CLAUDE_AVAILABLE = False
    print("[WARNING] Anthropic SDK not installed. Vision detection disabled. Run: pip install anthropic")

# Import wheelchair control modules
from eeg_wheelchair_control import (
    SignalProcessor,
    CommandDetector,
    CalibrationManager,
    quick_calibration,
    Command,
)
from eeg_wheelchair_control import config as wc_config

# Import Arduino control
from arduino_control import arduino, ArduinoController

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

BOARD_ID = BoardIds.CYTON_DAISY_BOARD  # Board ID = 2 for Cyton + Daisy (16 channels)
BATCH_SIZE = 25  # Samples per WebSocket message (~200ms at 125 Hz)
WS_UPDATE_INTERVAL = 0.1  # Seconds between WebSocket updates

# Wheelchair control channels (from config)
FP1_CHANNEL = wc_config.FP1_CHANNEL  # 9
FP2_CHANNEL = wc_config.FP2_CHANNEL  # 10

# -----------------------------------------------------------------------------
# Global State
# -----------------------------------------------------------------------------

class EEGState:
    """Global state for the EEG streaming system."""

    def __init__(self):
        self.board: Optional[BoardShim] = None
        self.is_streaming = False
        self.is_connected = False
        self.serial_port: Optional[str] = None
        self.websockets: list[WebSocket] = []
        self.lock = threading.Lock()

        # Statistics
        self.total_samples = 0
        self.dropped_packets = 0
        self.last_sample_time = 0
        self.start_time = 0

        # CSV recording
        self.csv_enabled = False
        self.csv_filename: Optional[str] = None
        self.csv_file = None
        self.csv_writer = None

        # Board info (populated on connect)
        self.sampling_rate = 0
        self.eeg_channels = []
        self.accel_channels = []
        self.timestamp_channel = 0
        self.num_channels = 0

        # ----- Wheelchair Control State -----
        self.wheelchair_enabled = False
        self.command_detector: Optional[CommandDetector] = None
        self.signal_processor: Optional[SignalProcessor] = None
        self.calibration_data: Optional[dict] = None

        # Data buffer for command detection (need 1 second = 125 samples)
        self.eeg_buffer_fp1 = deque(maxlen=wc_config.WINDOW_SIZE_SAMPLES)
        self.eeg_buffer_fp2 = deque(maxlen=wc_config.WINDOW_SIZE_SAMPLES)

        # Command statistics
        self.last_command = None
        self.last_command_time = 0
        self.last_command_confidence = 0
        self.command_history = []
        self.total_commands = 0
        
        # ----- Arduino hold-to-run / keepalive state -----
        self._active_action = "stop"           # latched: "forward" | "right" | "stop"
        self._last_seen_action_time = 0.0      # last time forward/right was detected
        self._last_arduino_cmd = "stop"        # last command sent to Arduino
        self._last_arduino_send_time = 0.0     # last time we wrote serial

        # Calibration state
        self.is_calibrating = False
        self.calibration_phase = ""
        self.calibration_progress = 0.0
        self.calibration_result = None
        self.calibration_logs = []
        self.calibration_instruction = ""
        self.calibration_trial = 0
        self.calibration_total_trials = 0

        # ----- Camera & Vision State -----
        self.camera = None
        self.camera_enabled = False
        self.vision_enabled = False
        self.last_frame = None
        self.last_detection = None
        self.last_detection_time = 0
        self.object_detected = False
        self.detection_message = ""
        self.vision_check_interval = 1.0  # Check every 1 second


state = EEGState()
app = FastAPI()

# -----------------------------------------------------------------------------
# Serial Port Detection
# -----------------------------------------------------------------------------

def detect_serial_ports() -> list[str]:
    """Detect available serial ports, prioritizing OpenBCI dongles on macOS."""
    import glob

    ports = []

    # macOS: Must use /dev/cu.* (not /dev/tty.*)
    if sys.platform == "darwin":
        patterns = [
            "/dev/cu.usbserial-*",
            "/dev/cu.usbmodem*",
        ]
        for pattern in patterns:
            ports.extend(glob.glob(pattern))

    # Linux
    elif sys.platform.startswith("linux"):
        patterns = [
            "/dev/ttyUSB*",
            "/dev/ttyACM*",
        ]
        for pattern in patterns:
            ports.extend(glob.glob(pattern))

    # Windows
    elif sys.platform == "win32":
        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]

    return sorted(ports)

# -----------------------------------------------------------------------------
# BrainFlow Board Management
# -----------------------------------------------------------------------------

def connect_board(serial_port: str) -> dict:
    """Connect to the OpenBCI Cyton + Daisy board."""
    global state

    if state.is_connected:
        return {"success": False, "error": "Already connected"}

    try:
        # Enable BrainFlow logging for debugging
        BoardShim.enable_dev_board_logger()

        # Configure connection parameters
        params = BrainFlowInputParams()
        params.serial_port = serial_port

        # Create board instance
        state.board = BoardShim(BOARD_ID, params)
        state.serial_port = serial_port

        # Prepare session (opens connection)
        state.board.prepare_session()

        # Configure PGA gain for all channels to reduce amplitude
        # OpenBCI gain codes: 0=24x(default), 1=1x, 2=2x, 3=4x, 4=6x, 5=8x, 6=12x
        # Setting to 1x gain to reduce signal amplitude (code = 1)
        # Command format: x{channel}{power}{gain}{input}{bias}{srb2}{srb1}X
        print("[BOARD] Configuring PGA gain to 1x for all channels...")
        try:
            # Cyton channels 1-8
            for ch in range(1, 9):
                # x{ch}010110X = channel on, gain=1x, normal input, bias on, srb2 on, srb1 off
                cmd = f"x{ch}010110X"
                state.board.config_board(cmd)
                time.sleep(0.05)  # Small delay between commands

            # Daisy channels 9-16 (use Q,W,E,R,T,Y,U,I)
            daisy_chars = ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I']
            for i, ch_char in enumerate(daisy_chars):
                cmd = f"x{ch_char}010110X"
                state.board.config_board(cmd)
                time.sleep(0.05)

            print("[BOARD] PGA gain set to 1x for all 16 channels")
        except Exception as e:
            print(f"[BOARD] Warning: Could not configure PGA gain: {e}")
            print("[BOARD] Continuing with default gain (24x)")

        # Get board specifications
        state.sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        state.eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
        state.accel_channels = BoardShim.get_accel_channels(BOARD_ID)
        state.timestamp_channel = BoardShim.get_timestamp_channel(BOARD_ID)
        state.num_channels = len(state.eeg_channels)

        # Initialize wheelchair control components
        state.signal_processor = SignalProcessor(state.sampling_rate)
        state.command_detector = CommandDetector()

        # Try to load existing calibration
        try:
            state.calibration_data = CalibrationManager.load_calibration()
            if state.calibration_data:
                state.command_detector.update_thresholds(
                    state.calibration_data.get('thresholds', {})
                )
        except:
            pass

        state.is_connected = True

        return {
            "success": True,
            "sampling_rate": state.sampling_rate,
            "num_channels": state.num_channels,
            "eeg_channels": state.eeg_channels,
            "accel_channels": state.accel_channels,
            "has_calibration": state.calibration_data is not None,
        }

    except BrainFlowError as e:
        state.board = None
        return {"success": False, "error": f"BrainFlow error: {str(e)}"}
    except Exception as e:
        state.board = None
        return {"success": False, "error": str(e)}

def disconnect_board() -> dict:
    """Disconnect from the board."""
    global state

    if not state.is_connected:
        return {"success": False, "error": "Not connected"}

    try:
        # Stop streaming first if active
        if state.is_streaming:
            stop_streaming()

        # Release the session
        if state.board:
            state.board.release_session()

        state.board = None
        state.is_connected = False
        state.serial_port = None

        return {"success": True}

    except Exception as e:
        return {"success": False, "error": str(e)}

def start_streaming() -> dict:
    """Start data acquisition."""
    global state

    if not state.is_connected:
        return {"success": False, "error": "Not connected"}

    if state.is_streaming:
        return {"success": False, "error": "Already streaming"}

    try:
        # Start the stream with a ring buffer
        state.board.start_stream(45000)

        state.is_streaming = True
        state.start_time = time.time()
        state.total_samples = 0
        state.dropped_packets = 0

        # Clear command buffers
        state.eeg_buffer_fp1.clear()
        state.eeg_buffer_fp2.clear()
        state.command_history = []
        state.total_commands = 0

        return {"success": True}

    except Exception as e:
        return {"success": False, "error": str(e)}

def stop_streaming() -> dict:
    """Stop data acquisition."""
    global state

    if not state.is_streaming:
        return {"success": False, "error": "Not streaming"}

    try:
        state.board.stop_stream()
        state.is_streaming = False

        # Close CSV if recording
        if state.csv_file:
            state.csv_file.close()
            state.csv_file = None
            state.csv_writer = None

        return {"success": True, "total_samples": state.total_samples}

    except Exception as e:
        return {"success": False, "error": str(e)}

def get_board_data() -> Optional[np.ndarray]:
    """Get available data from the board's ring buffer."""
    global state

    if not state.is_streaming or not state.board:
        return None

    try:
        # Get all available data (clears the buffer)
        data = state.board.get_board_data()

        if data.shape[1] > 0:
            state.total_samples += data.shape[1]
            state.last_sample_time = time.time()

            # Check for dropped packets
            sample_indices = data[0, :]
            if len(sample_indices) > 1:
                actual_diff = np.diff(sample_indices)
                actual_diff = np.where(actual_diff < 0, actual_diff + 256, actual_diff)
                dropped = np.sum(actual_diff > 1)
                state.dropped_packets += int(dropped)

            # Add to command detection buffer (raw values, no scaling)
            # FP1 is at index 9, FP2 is at index 10 in the EEG channels
            # But eeg_channels gives us the actual row indices
            fp1_idx = state.eeg_channels[FP1_CHANNEL - 1]  # Channel 9 -> index 8 in list
            fp2_idx = state.eeg_channels[FP2_CHANNEL - 1]  # Channel 10 -> index 9 in list

            for i in range(data.shape[1]):
                state.eeg_buffer_fp1.append(data[fp1_idx, i])
                state.eeg_buffer_fp2.append(data[fp2_idx, i])

        return data

    except Exception as e:
        print(f"Error getting board data: {e}")
        return None

# -----------------------------------------------------------------------------
# Wheelchair Command Detection
# -----------------------------------------------------------------------------

def detect_command() -> Optional[dict]:
    """Detect wheelchair command from current buffer and send to Arduino."""
    global state

    if not state.wheelchair_enabled or not state.command_detector:
        return None

    # Need full window (125 samples = 1 second)
    if len(state.eeg_buffer_fp1) < wc_config.WINDOW_SIZE_SAMPLES:
        return None

    try:
        # Get data from buffers
        fp1_data = np.array(list(state.eeg_buffer_fp1))
        fp2_data = np.array(list(state.eeg_buffer_fp2))

        # Run detection
        result = state.command_detector.get_command(fp1_data, fp2_data)

        # Print percentage changes every 0.5 seconds for tuning
        f = result.features
        current_time = time.time()
        if not hasattr(state, '_last_delta_print'):
            state._last_delta_print = 0
        if 'fp1_pct_change' in f and 'fp2_pct_change' in f:
            if current_time - state._last_delta_print >= 0.5 or result.command:
                cmd_str = f" --> {result.command}" if result.command else ""
                print(f"Ch1: {f['fp1_pct_change']:6.1f}% | Ch3: {f['fp2_pct_change']:6.1f}%{cmd_str}")
                state._last_delta_print = current_time

        # ---------------- Determine Arduino action ----------------
        arduino_action = None
        is_blocked = False

        if result.command == 'forward' or result.command == 'both':
            # Check if blocked by vision
            if state.vision_enabled and state.object_detected:
                is_blocked = True
                arduino_action = 'stop'
                print("[ARDUINO] Forward BLOCKED by obstacle!")
            else:
                arduino_action = 'forward'

        elif result.command == 'right':
            arduino_action = 'right'

        else:
            # No command - stop motors (but we will NOT spam stop repeatedly)
            arduino_action = 'stop'

        # ---------------- Send to Arduino (KEEPALIVE while active) ----------------
        # IMPORTANT: This supports "hold-to-run" Arduino logic.
        # While forward/right is active, resend command at ~12.5 Hz (every 80 ms).
        # ---------------- Hold-to-run latch + 0.5s tolerance + keepalive ----------------
        now = time.time()

        RELEASE_TOL = 0.5          # seconds (gesture dropout tolerance before stopping)
        KEEPALIVE_INTERVAL = 0.08  # seconds (resend rate while active)

        # 1) Latch the active action while we see a valid command
        if arduino_action in ("forward", "right"):
            state._active_action = arduino_action
            state._last_seen_action_time = now
        else:
            # Only stop if we haven't seen a command for RELEASE_TOL seconds
            if (now - state._last_seen_action_time) >= RELEASE_TOL:
                state._active_action = "stop"

        # 2) Send to Arduino
        if arduino.is_connected:
            if state._active_action in ("forward", "right"):
                should_send = (
                    (state._active_action != state._last_arduino_cmd) or
                    ((now - state._last_arduino_send_time) >= KEEPALIVE_INTERVAL)
                )
                if should_send:
                    state._last_arduino_cmd = state._active_action
                    state._last_arduino_send_time = now

                    if state._active_action == "forward":
                        arduino.forward()
                    elif state._active_action == "right":
                        arduino.right()

            else:
                # Stop ONCE when transitioning into stop
                if state._last_arduino_cmd != "stop":
                    state._last_arduino_cmd = "stop"
                    state._last_arduino_send_time = now
                    arduino.stop()

        # ---------------- Update stats/history ----------------
        if result.command:
            state.last_command = result.command
            state.last_command_time = result.timestamp
            state.last_command_confidence = result.confidence
            state.total_commands += 1

            state.command_history.append({
                'command': result.command,
                'confidence': result.confidence,
                'timestamp': result.timestamp,
            })
            if len(state.command_history) > 50:
                state.command_history = state.command_history[-50:]

        return {
            'command': result.command,
            'confidence': result.confidence,
            'features': result.features,
            'cooldown_remaining': state.command_detector.get_cooldown_remaining(),
            'arduino_action': arduino_action,
            'is_blocked': is_blocked,
        }

    except Exception as e:
        print(f"Error in command detection: {e}")
        return None


# -----------------------------------------------------------------------------
# Calibration
# -----------------------------------------------------------------------------

def load_multipliers() -> dict:
    """Load multipliers from the tuning config file."""
    multipliers_file = Path("detection_multipliers.json")
    default_multipliers = {
        "blink_multiplier": 1.5,
        "jaw_multiplier": 1.5,
        "eye_move_multiplier": 1.2,
        "lateral_ratio": 1.3,
        "_comment": "Edit these values to tune detection sensitivity. Lower = more sensitive."
    }

    if multipliers_file.exists():
        try:
            with open(multipliers_file, 'r') as f:
                loaded = json.load(f)
                # Merge with defaults in case new keys are added
                for key in default_multipliers:
                    if key not in loaded:
                        loaded[key] = default_multipliers[key]
                return loaded
        except Exception as e:
            print(f"[CONFIG] Error loading multipliers: {e}, using defaults")
            return default_multipliers
    else:
        # Create the file with defaults
        with open(multipliers_file, 'w') as f:
            json.dump(default_multipliers, f, indent=2)
        print(f"[CONFIG] Created {multipliers_file} with default values")
        return default_multipliers


def run_quick_calibration() -> dict:
    """
    Run simple 10-second baseline calibration.

    Records baseline values while user sits still, then applies
    multipliers from detection_multipliers.json to calculate thresholds.
    """
    global state

    if not state.is_streaming:
        print("[CALIBRATION] Error: Not streaming")
        return {"success": False, "error": "Not streaming"}

    print("\n" + "=" * 60)
    print("[CALIBRATION] Starting 10-second baseline calibration")
    print("=" * 60)
    print("[CALIBRATION] Instructions: Sit still, look straight ahead, relax")
    print("[CALIBRATION] Avoid blinking and jaw clenching during calibration")
    print("-" * 60)

    state.is_calibrating = True
    state.calibration_phase = "baseline"
    state.calibration_progress = 0.0
    state.calibration_result = None
    state.calibration_logs = []
    state.calibration_instruction = "Sit still, look straight ahead, relax. Avoid blinking."
    state.calibration_trial = 0
    state.calibration_total_trials = 0

    def add_log(msg, log_type="info"):
        """Add a calibration log entry."""
        entry = {"time": datetime.now().isoformat(), "message": msg, "type": log_type}
        state.calibration_logs.append(entry)
        print(f"[CALIBRATION] {msg}")

    add_log("Calibration started", "info")
    add_log("Recording baseline for 10 seconds...", "info")

    try:
        # Collect baseline data for 10 seconds
        duration = 10.0
        amplitudes = []
        jaw_powers = []
        fp1_values = []
        fp2_values = []
        start_time = time.time()
        last_log_time = 0

        while time.time() - start_time < duration:
            if len(state.eeg_buffer_fp1) >= wc_config.WINDOW_SIZE_SAMPLES:
                fp1 = np.array(list(state.eeg_buffer_fp1))
                fp2 = np.array(list(state.eeg_buffer_fp2))

                features = state.signal_processor.extract_features(fp1, fp2)
                amp = max(features.fp1_max, features.fp2_max)
                amplitudes.append(amp)
                jaw_powers.append(features.jaw_power)
                fp1_values.append(features.fp1_max)
                fp2_values.append(features.fp2_max)

                # Log every 2 seconds
                elapsed = time.time() - start_time
                if elapsed - last_log_time >= 2.0:
                    add_log(f"{elapsed:.0f}s: FP1={features.fp1_max:.1f}, FP2={features.fp2_max:.1f}, Jaw={features.jaw_power:.0f}")
                    last_log_time = elapsed

            state.calibration_progress = min((time.time() - start_time) / duration, 0.99)
            time.sleep(0.2)

        # Calculate baseline stats
        if amplitudes:
            amp_mean = np.mean(amplitudes)
            amp_std = np.std(amplitudes)
            jaw_mean = np.mean(jaw_powers)
            jaw_std = np.std(jaw_powers)

            add_log(f"Collected {len(amplitudes)} samples", "success")
            add_log(f"Baseline amplitude: mean={amp_mean:.1f}, std={amp_std:.1f}", "info")
            add_log(f"Baseline jaw power: mean={jaw_mean:.0f}, std={jaw_std:.0f}", "info")

            # Load multipliers from config file
            multipliers = load_multipliers()
            add_log(f"Loaded multipliers from detection_multipliers.json", "info")

            # Calculate thresholds using multipliers
            thresholds = {
                'blink_threshold': float(amp_mean + multipliers['blink_multiplier'] * amp_std),
                'jaw_threshold': float(jaw_mean + multipliers['jaw_multiplier'] * jaw_std),
                'eye_move_threshold': float(amp_mean + multipliers['eye_move_multiplier'] * amp_std),
                'lateral_ratio': float(multipliers['lateral_ratio']),
                'baseline_amplitude': float(amp_mean),
                'baseline_jaw_power': float(jaw_mean),
            }

            add_log(f"Calculated thresholds:", "success")
            add_log(f"  Blink: {thresholds['blink_threshold']:.1f} (baseline + {multipliers['blink_multiplier']}x std)", "success")
            add_log(f"  Jaw: {thresholds['jaw_threshold']:.0f} (baseline + {multipliers['jaw_multiplier']}x std)", "success")
            add_log(f"  Eye move: {thresholds['eye_move_threshold']:.1f} (baseline + {multipliers['eye_move_multiplier']}x std)", "success")
            add_log(f"  Lateral ratio: {thresholds['lateral_ratio']}", "success")

            # Update detector
            state.command_detector.update_thresholds(thresholds)
            state.calibration_data = {'thresholds': thresholds}

            # Save calibration data
            with open(wc_config.CALIBRATION_FILE, 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'thresholds': thresholds,
                    'baseline': {
                        'samples': len(amplitudes),
                        'amp_mean': float(amp_mean),
                        'amp_std': float(amp_std),
                        'jaw_mean': float(jaw_mean),
                        'jaw_std': float(jaw_std),
                    },
                    'multipliers_used': multipliers
                }, f, indent=2)

            add_log(f"Saved to {wc_config.CALIBRATION_FILE}", "success")
            add_log("Edit detection_multipliers.json to tune sensitivity", "info")

            # Mark complete
            state.calibration_progress = 1.0
            state.calibration_phase = "complete"
            state.calibration_instruction = "Calibration complete!"
            state.calibration_result = {
                "success": True,
                "thresholds": thresholds,
                "logs": state.calibration_logs
            }

            time.sleep(0.3)
            state.is_calibrating = False
            return {"success": True, "thresholds": thresholds}

        # No data collected
        add_log("Error: No data collected during calibration", "error")
        state.calibration_progress = 1.0
        state.calibration_phase = "failed"
        state.calibration_instruction = "Calibration failed - no data"
        state.calibration_result = {
            "success": False,
            "error": "No data collected",
            "logs": state.calibration_logs
        }
        time.sleep(0.3)
        state.is_calibrating = False
        return {"success": False, "error": "No data collected"}

    except Exception as e:
        add_log(f"Error: {str(e)}", "error")
        state.calibration_progress = 1.0
        state.calibration_phase = "failed"
        state.calibration_instruction = f"Error: {str(e)}"
        state.calibration_result = {
            "success": False,
            "error": str(e),
            "logs": state.calibration_logs
        }
        print(f"[CALIBRATION] Failed: {str(e)}\n")
        time.sleep(0.3)
        state.is_calibrating = False
        return {"success": False, "error": str(e)}

# -----------------------------------------------------------------------------
# Camera & Vision Detection
# -----------------------------------------------------------------------------

def start_camera() -> dict:
    """Start the camera capture."""
    global state

    if not CAMERA_AVAILABLE:
        return {"success": False, "error": "OpenCV not installed"}

    if state.camera is not None:
        return {"success": False, "error": "Camera already running"}

    try:
        state.camera = cv2.VideoCapture(0)
        if not state.camera.isOpened():
            state.camera = None
            return {"success": False, "error": "Could not open camera"}

        state.camera_enabled = True
        print("[CAMERA] Camera started")
        return {"success": True}

    except Exception as e:
        state.camera = None
        return {"success": False, "error": str(e)}


def stop_camera() -> dict:
    """Stop the camera capture."""
    global state

    if state.camera is None:
        return {"success": False, "error": "Camera not running"}

    try:
        state.camera.release()
        state.camera = None
        state.camera_enabled = False
        state.last_frame = None
        print("[CAMERA] Camera stopped")
        return {"success": True}

    except Exception as e:
        return {"success": False, "error": str(e)}


def get_camera_frame() -> Optional[bytes]:
    """Capture a frame from the camera and return as JPEG bytes."""
    global state

    if state.camera is None or not state.camera_enabled:
        return None

    try:
        ret, frame = state.camera.read()
        if not ret:
            return None

        # Store the frame for vision detection
        state.last_frame = frame

        # Encode as JPEG
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return jpeg.tobytes()

    except Exception as e:
        print(f"[CAMERA] Error capturing frame: {e}")
        return None


def analyze_frame_with_claude() -> Optional[dict]:
    """Send the current frame to Claude Vision API to detect obstacles."""
    global state

    if not CLAUDE_AVAILABLE:
        return {"error": "Anthropic SDK not installed"}

    if state.last_frame is None:
        return {"error": "No frame available"}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not set"}

    try:
        # Encode frame as base64 JPEG
        _, jpeg = cv2.imencode('.jpg', state.last_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        image_base64 = base64.standard_b64encode(jpeg.tobytes()).decode('utf-8')

        # Call Claude Vision API
        client = anthropic.Anthropic(api_key=api_key)

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_base64,
                            },
                        },
                        {
                            "type": "text",
                            "text": "You are an obstacle detection system for a wheelchair. Look at this image and determine if there is an obstacle directly in front that would block forward movement (within ~2 meters). Respond with ONLY valid JSON in this exact format: {\"object_detected\": true/false, \"message\": \"brief description\"}. If there's a wall, person, furniture, or any obstacle blocking the path ahead, set object_detected to true."
                        }
                    ],
                }
            ],
        )

        # Parse the response
        response_text = message.content[0].text.strip()

        # Try to extract JSON from response
        try:
            # Handle case where response might have markdown code blocks
            if "```" in response_text:
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]
                response_text = response_text.strip()

            result = json.loads(response_text)
            state.object_detected = result.get("object_detected", False)
            state.detection_message = result.get("message", "")
            state.last_detection = result
            state.last_detection_time = time.time()

            status = "BLOCKED" if state.object_detected else "CLEAR"
            print(f"[VISION] {status}: {state.detection_message}")

            return result

        except json.JSONDecodeError:
            # If JSON parsing fails, try to extract info from text
            is_blocked = "true" in response_text.lower() or "yes" in response_text.lower() or "obstacle" in response_text.lower()
            result = {
                "object_detected": is_blocked,
                "message": response_text[:100]
            }
            state.object_detected = is_blocked
            state.detection_message = response_text[:100]
            state.last_detection = result
            state.last_detection_time = time.time()
            return result

    except Exception as e:
        print(f"[VISION] Error calling Claude API: {e}")
        return {"error": str(e)}


def vision_detection_loop():
    """Background thread for periodic vision detection."""
    global state

    while state.vision_enabled:
        if state.camera_enabled and state.last_frame is not None:
            analyze_frame_with_claude()
        time.sleep(state.vision_check_interval)

    print("[VISION] Detection loop stopped")


# -----------------------------------------------------------------------------
# CSV Recording
# -----------------------------------------------------------------------------

def start_csv_recording(filename: str) -> dict:
    """Start recording data to CSV."""
    global state

    if not state.is_connected:
        return {"success": False, "error": "Not connected"}

    if state.csv_file:
        return {"success": False, "error": "Already recording"}

    try:
        if not filename.endswith(".csv"):
            filename += ".csv"

        state.csv_filename = filename
        state.csv_file = open(filename, "w", newline="")
        state.csv_writer = csv.writer(state.csv_file)

        # Write header
        header = ["timestamp", "sample_index"]
        header.extend([f"ch{i+1}" for i in range(state.num_channels)])
        header.extend([f"accel_{axis}" for axis in ["x", "y", "z"]])
        header.append("detected_command")
        state.csv_writer.writerow(header)

        state.csv_enabled = True
        return {"success": True, "filename": filename}

    except Exception as e:
        return {"success": False, "error": str(e)}

def stop_csv_recording() -> dict:
    """Stop CSV recording."""
    global state

    if not state.csv_file:
        return {"success": False, "error": "Not recording"}

    try:
        state.csv_file.close()
        state.csv_file = None
        state.csv_writer = None
        state.csv_enabled = False

        return {"success": True, "filename": state.csv_filename}

    except Exception as e:
        return {"success": False, "error": str(e)}

def write_csv_data(data: np.ndarray, command: Optional[str] = None):
    """Write data samples to CSV."""
    global state

    if not state.csv_writer or data is None or data.shape[1] == 0:
        return

    try:
        for i in range(data.shape[1]):
            row = [
                data[state.timestamp_channel, i],
                int(data[0, i]),
            ]
            for ch in state.eeg_channels:
                row.append(data[ch, i])
            for ch in state.accel_channels:
                row.append(data[ch, i])
            row.append(command if command and i == data.shape[1] - 1 else "")

            state.csv_writer.writerow(row)

        state.csv_file.flush()

    except Exception as e:
        print(f"Error writing CSV: {e}")

# -----------------------------------------------------------------------------
# WebSocket API
# -----------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time data streaming."""
    await websocket.accept()
    state.websockets.append(websocket)

    try:
        # Send initial state
        await websocket.send_json({
            "type": "state",
            "is_connected": state.is_connected,
            "is_streaming": state.is_streaming,
            "serial_port": state.serial_port,
            "sampling_rate": state.sampling_rate,
            "num_channels": state.num_channels,
            "ports": detect_serial_ports(),
            "wheelchair_enabled": state.wheelchair_enabled,
            "has_calibration": state.calibration_data is not None,
        })

        # Data streaming loop
        while True:
            # Check for incoming commands
            try:
                message = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=WS_UPDATE_INTERVAL
                )
                await handle_command(websocket, message)
            except asyncio.TimeoutError:
                pass

            # Send data if streaming
            if state.is_streaming:
                data = get_board_data()
                if data is not None and data.shape[1] > 0:
                    # Detect wheelchair command
                    command_result = detect_command()

                    # Write to CSV if enabled
                    if state.csv_enabled:
                        cmd = command_result['command'] if command_result else None
                        write_csv_data(data, cmd)

                    # Send EEG data to frontend
                    eeg_data = []
                    for ch_idx in state.eeg_channels:
                        eeg_data.append(data[ch_idx, :].tolist())

                    # Send accelerometer data
                    accel_data = []
                    for ch_idx in state.accel_channels:
                        accel_data.append(data[ch_idx, :].tolist())

                    # Calculate elapsed time
                    elapsed = time.time() - state.start_time if state.start_time else 0

                    # Build response
                    response = {
                        "type": "data",
                        "eeg": eeg_data,
                        "accel": accel_data,
                        "timestamps": data[state.timestamp_channel, :].tolist(),
                        "sample_indices": data[0, :].tolist(),
                        "stats": {
                            "total_samples": state.total_samples,
                            "dropped_packets": state.dropped_packets,
                            "elapsed_seconds": elapsed,
                            "effective_rate": state.total_samples / elapsed if elapsed > 0 else 0,
                        },
                    }

                    # Add wheelchair control data
                    if command_result:
                        response["wheelchair"] = {
                            "enabled": state.wheelchair_enabled,
                            "command": command_result['command'],
                            "confidence": command_result['confidence'],
                            "features": command_result['features'],
                            "cooldown": command_result['cooldown_remaining'],
                            "total_commands": state.total_commands,
                            "last_command": state.last_command,
                            "last_command_time": state.last_command_time,
                        }

                    # Add vision detection data
                    if state.vision_enabled:
                        response["vision"] = {
                            "enabled": True,
                            "object_detected": state.object_detected,
                            "message": state.detection_message,
                            "last_check": state.last_detection_time,
                        }

                    await websocket.send_json(response)

            # Send calibration progress if calibrating
            if state.is_calibrating:
                msg = {
                    "type": "calibration_progress",
                    "phase": state.calibration_phase,
                    "progress": state.calibration_progress,
                    "instruction": state.calibration_instruction,
                    "trial": state.calibration_trial,
                    "total_trials": state.calibration_total_trials,
                    "logs": state.calibration_logs[-10:] if state.calibration_logs else [],  # Last 10 logs
                }
                if state.calibration_result:
                    msg["result"] = state.calibration_result
                await websocket.send_json(msg)

            await asyncio.sleep(0.01)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        if websocket in state.websockets:
            state.websockets.remove(websocket)

async def handle_command(websocket: WebSocket, message: dict):
    """Handle incoming WebSocket commands."""
    cmd = message.get("command")

    if cmd == "connect":
        port = message.get("port")
        if not port:
            await websocket.send_json({"type": "error", "error": "No port specified"})
            return
        result = connect_board(port)
        await websocket.send_json({"type": "connect_result", **result})

    elif cmd == "disconnect":
        result = disconnect_board()
        await websocket.send_json({"type": "disconnect_result", **result})

    elif cmd == "start":
        result = start_streaming()
        await websocket.send_json({"type": "start_result", **result})

    elif cmd == "stop":
        result = stop_streaming()
        await websocket.send_json({"type": "stop_result", **result})

    elif cmd == "detect_ports":
        ports = detect_serial_ports()
        await websocket.send_json({"type": "ports", "ports": ports})

    elif cmd == "start_csv":
        filename = message.get("filename", f"eeg_recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        result = start_csv_recording(filename)
        await websocket.send_json({"type": "csv_result", **result})

    elif cmd == "stop_csv":
        result = stop_csv_recording()
        await websocket.send_json({"type": "csv_result", **result})

    # ----- Wheelchair Control Commands -----

    elif cmd == "wheelchair_enable":
        state.wheelchair_enabled = True
        await websocket.send_json({
            "type": "wheelchair_status",
            "enabled": True,
            "has_calibration": state.calibration_data is not None,
        })

    elif cmd == "wheelchair_disable":
        state.wheelchair_enabled = False
        await websocket.send_json({
            "type": "wheelchair_status",
            "enabled": False,
        })

    elif cmd == "calibrate":
        # Run calibration in background
        import threading
        def run_cal():
            result = run_quick_calibration()
            # Result will be sent via calibration_progress messages
        threading.Thread(target=run_cal, daemon=True).start()
        await websocket.send_json({"type": "calibration_started"})

    # ----- Camera & Vision Commands -----

    elif cmd == "camera_start":
        result = start_camera()
        await websocket.send_json({"type": "camera_status", **result, "enabled": result.get("success", False)})

    elif cmd == "camera_stop":
        result = stop_camera()
        await websocket.send_json({"type": "camera_status", **result, "enabled": False})

    elif cmd == "vision_enable":
        if not state.camera_enabled:
            await websocket.send_json({"type": "vision_status", "success": False, "error": "Camera not running"})
        else:
            state.vision_enabled = True
            # Start vision detection loop in background
            threading.Thread(target=vision_detection_loop, daemon=True).start()
            await websocket.send_json({"type": "vision_status", "success": True, "enabled": True})

    elif cmd == "vision_disable":
        state.vision_enabled = False
        await websocket.send_json({"type": "vision_status", "success": True, "enabled": False})

    # ----- Arduino Commands -----

    elif cmd == "arduino_list_ports":
        ports = arduino.list_ports()
        await websocket.send_json({"type": "arduino_ports", "ports": ports})

    elif cmd == "arduino_connect":
        port = message.get("port")
        if not port:
            await websocket.send_json({"type": "arduino_status", "success": False, "error": "No port specified"})
        else:
            result = arduino.connect(port)
            await websocket.send_json({"type": "arduino_status", **result, "connected": result.get("success", False)})

    elif cmd == "arduino_disconnect":
        result = arduino.disconnect()
        await websocket.send_json({"type": "arduino_status", **result, "connected": False})

    elif cmd == "arduino_test":
        # Test command - cycle through movements
        test_cmd = message.get("test_command", "stop")
        if test_cmd == "forward":
            arduino.forward()
        elif test_cmd == "right":
            arduino.right()
        elif test_cmd == "left":
            arduino.left()
        elif test_cmd == "backward":
            arduino.backward()
        else:
            arduino.stop()
        await websocket.send_json({"type": "arduino_test", "command": test_cmd})

    elif cmd == "get_state":
        await websocket.send_json({
            "type": "state",
            "is_connected": state.is_connected,
            "is_streaming": state.is_streaming,
            "serial_port": state.serial_port,
            "sampling_rate": state.sampling_rate,
            "num_channels": state.num_channels,
            "csv_enabled": state.csv_enabled,
            "csv_filename": state.csv_filename,
            "ports": detect_serial_ports(),
            "wheelchair_enabled": state.wheelchair_enabled,
            "has_calibration": state.calibration_data is not None,
            "calibration_data": state.calibration_data,
            "camera_enabled": state.camera_enabled,
            "vision_enabled": state.vision_enabled,
            "arduino": arduino.get_status(),
        })

# -----------------------------------------------------------------------------
# HTTP Routes
# -----------------------------------------------------------------------------

@app.get("/")
async def serve_frontend():
    # Resolve absolute path to frontend.html
    frontend_path = Path(__file__).parent / "frontend.html"
    if not frontend_path.exists():
        raise RuntimeError(f"frontend.html not found at {frontend_path}")
    return FileResponse(frontend_path)

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "board_connected": state.is_connected,
        "wheelchair_enabled": state.wheelchair_enabled,
        "camera_enabled": state.camera_enabled,
        "vision_enabled": state.vision_enabled,
    }


@app.get("/video_feed")
async def video_feed():
    """Stream video frames as multipart JPEG."""
    if not CAMERA_AVAILABLE:
        return {"error": "OpenCV not installed"}

    if not state.camera_enabled:
        return {"error": "Camera not running"}

    def generate_frames():
        while state.camera_enabled:
            frame_bytes = get_camera_frame()
            if frame_bytes:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + frame_bytes + b'\r\n')
            time.sleep(0.033)  # ~30 fps

    return StreamingResponse(
        generate_frames(),
        media_type='multipart/x-mixed-replace; boundary=frame'
    )


@app.get("/vision_status")
async def get_vision_status():
    """Get current vision detection status."""
    return {
        "camera_enabled": state.camera_enabled,
        "vision_enabled": state.vision_enabled,
        "object_detected": state.object_detected,
        "message": state.detection_message,
        "last_check": state.last_detection_time,
    }


@app.get("/overshoot_key")
async def get_overshoot_key():
    """Get Overshoot API key from environment."""
    return {"key": OVERSHOOT_API_KEY}

# -----------------------------------------------------------------------------
# Shutdown Handler
# -----------------------------------------------------------------------------

def shutdown_handler(signum, frame):
    """Graceful shutdown on Ctrl+C."""
    print("\nShutting down...")

    # Stop Arduino motors and disconnect
    if arduino.is_connected:
        print("[ARDUINO] Stopping motors...")
        arduino.stop()
        arduino.disconnect()

    # Stop vision detection
    state.vision_enabled = False

    # Stop camera
    if state.camera_enabled:
        stop_camera()

    if state.is_streaming:
        stop_streaming()

    if state.is_connected:
        disconnect_board()

    sys.exit(0)

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OpenBCI EEG Streaming Server with Wheelchair Control")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║     OpenBCI Cyton + Daisy EEG Dashboard + Wheelchair Control     ║
╠══════════════════════════════════════════════════════════════════╣
║  Board: Cyton + Daisy (16 channels)                              ║
║  Sample Rate: 125 Hz                                             ║
║  FP1 Channel: {FP1_CHANNEL} | FP2 Channel: {FP2_CHANNEL}                                   ║
╠══════════════════════════════════════════════════════════════════╣
║  Commands:                                                       ║
║    Jaw Clench    -> FORWARD                                      ║
║    Double Blink  -> BACKWARD                                     ║
║    Look Left     -> LEFT                                         ║
║    Look Right    -> RIGHT                                        ║
╠══════════════════════════════════════════════════════════════════╣
║  Open in browser: http://{args.host}:{args.port}                          ║
║  Press Ctrl+C to stop                                            ║
╚══════════════════════════════════════════════════════════════════╝
""")

    ports = detect_serial_ports()
    if ports:
        print(f"Detected serial ports: {', '.join(ports)}")
    else:
        print("No serial ports detected. Make sure the OpenBCI dongle is connected.")

    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
