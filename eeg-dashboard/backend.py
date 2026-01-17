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
from datetime import datetime
from pathlib import Path
from typing import Optional
import threading
from collections import deque

from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError
from brainflow.data_filter import DataFilter
import numpy as np

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

# Import wheelchair control modules
from eeg_wheelchair_control import (
    SignalProcessor,
    CommandDetector,
    CalibrationManager,
    quick_calibration,
    Command,
)
from eeg_wheelchair_control import config as wc_config

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

        # Calibration state
        self.is_calibrating = False
        self.calibration_phase = ""
        self.calibration_progress = 0.0
        self.calibration_result = None
        self.calibration_logs = []


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

            # Add to command detection buffer
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
    """Detect wheelchair command from current buffer."""
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

        # Update state
        if result.command:
            state.last_command = result.command
            state.last_command_time = result.timestamp
            state.last_command_confidence = result.confidence
            state.total_commands += 1

            # Add to history (keep last 50)
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
        }

    except Exception as e:
        print(f"Error in command detection: {e}")
        return None

# -----------------------------------------------------------------------------
# Calibration
# -----------------------------------------------------------------------------

def run_quick_calibration() -> dict:
    """Run quick baseline calibration (10 seconds)."""
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
    state.calibration_result = None  # Will store the result
    state.calibration_logs = []  # Store logs for frontend

    def add_log(msg, log_type="info"):
        """Add a calibration log entry."""
        entry = {"time": datetime.now().isoformat(), "message": msg, "type": log_type}
        state.calibration_logs.append(entry)
        print(f"[CALIBRATION] {msg}")

    add_log("Calibration started", "info")

    # Log channel configuration
    if state.eeg_channels:
        fp1_idx = state.eeg_channels[FP1_CHANNEL - 1] if len(state.eeg_channels) >= FP1_CHANNEL else -1
        fp2_idx = state.eeg_channels[FP2_CHANNEL - 1] if len(state.eeg_channels) >= FP2_CHANNEL else -1
        add_log(f"Channel config: FP1=Ch{FP1_CHANNEL}(idx:{fp1_idx}), FP2=Ch{FP2_CHANNEL}(idx:{fp2_idx})", "info")
        print(f"[CALIBRATION] EEG channel indices from BrainFlow: {state.eeg_channels}")

    try:
        # Collect baseline data for 10 seconds
        duration = 10.0
        amplitudes = []
        jaw_powers = []
        fp1_values = []
        fp2_values = []
        start_time = time.time()
        last_log_time = 0
        raw_logged = False

        while time.time() - start_time < duration:
            if len(state.eeg_buffer_fp1) >= wc_config.WINDOW_SIZE_SAMPLES:
                fp1 = np.array(list(state.eeg_buffer_fp1))
                fp2 = np.array(list(state.eeg_buffer_fp2))

                # Log raw data once for debugging
                if not raw_logged:
                    print(f"[CALIBRATION] RAW DATA DEBUG:")
                    print(f"[CALIBRATION]   FP1 buffer size: {len(fp1)}, min={fp1.min():.4f}, max={fp1.max():.4f}, mean={fp1.mean():.4f}, std={fp1.std():.4f}")
                    print(f"[CALIBRATION]   FP2 buffer size: {len(fp2)}, min={fp2.min():.4f}, max={fp2.max():.4f}, mean={fp2.mean():.4f}, std={fp2.std():.4f}")
                    print(f"[CALIBRATION]   FP1 first 10 samples: {fp1[:10]}")
                    print(f"[CALIBRATION]   FP2 first 10 samples: {fp2[:10]}")
                    add_log(f"Raw FP1: min={fp1.min():.2f}, max={fp1.max():.2f}, std={fp1.std():.2f}", "info")
                    add_log(f"Raw FP2: min={fp2.min():.2f}, max={fp2.max():.2f}, std={fp2.std():.2f}", "info")
                    raw_logged = True

                features = state.signal_processor.extract_features(fp1, fp2)
                amp = max(features.fp1_max, features.fp2_max)
                amplitudes.append(amp)
                jaw_powers.append(features.jaw_power)
                fp1_values.append(features.fp1_max)
                fp2_values.append(features.fp2_max)

                # Log every 2 seconds
                elapsed = time.time() - start_time
                if elapsed - last_log_time >= 2.0:
                    add_log(f"Progress {elapsed:.0f}s: FP1={features.fp1_max:.1f}uV, FP2={features.fp2_max:.1f}uV, Jaw={features.jaw_power:.0f}")
                    last_log_time = elapsed

            state.calibration_progress = min((time.time() - start_time) / duration, 0.99)
            time.sleep(0.2)

        # Calculate thresholds
        if amplitudes:
            amp_mean = np.mean(amplitudes)
            amp_std = np.std(amplitudes)
            jaw_mean = np.mean(jaw_powers)
            jaw_std = np.std(jaw_powers)
            fp1_mean = np.mean(fp1_values)
            fp2_mean = np.mean(fp2_values)

            add_log(f"Collected {len(amplitudes)} samples", "info")
            add_log(f"Baseline stats:", "info")
            add_log(f"  Amplitude: mean={amp_mean:.1f}uV, std={amp_std:.1f}uV", "info")
            add_log(f"  Jaw power: mean={jaw_mean:.0f}, std={jaw_std:.0f}", "info")
            add_log(f"  FP1 mean={fp1_mean:.1f}uV, FP2 mean={fp2_mean:.1f}uV", "info")

            thresholds = {
                'blink_threshold': float(amp_mean + 3.0 * amp_std),
                'jaw_threshold': float(jaw_mean + 2.5 * jaw_std),
                'eye_move_threshold': float(amp_mean + 1.5 * amp_std),
                'lateral_ratio': wc_config.LATERAL_RATIO,
                'baseline_amplitude': float(amp_mean),
                'baseline_jaw_power': float(jaw_mean),
            }

            add_log(f"Calculated thresholds:", "success")
            add_log(f"  Blink threshold: {thresholds['blink_threshold']:.1f}uV (3x std)", "success")
            add_log(f"  Jaw threshold: {thresholds['jaw_threshold']:.0f} (2.5x std)", "success")
            add_log(f"  Eye move threshold: {thresholds['eye_move_threshold']:.1f}uV (1.5x std)", "success")
            add_log(f"  Lateral ratio: {thresholds['lateral_ratio']}", "success")

            # Update detector
            state.command_detector.update_thresholds(thresholds)
            state.calibration_data = {'thresholds': thresholds}

            # Save to file
            import json
            with open(wc_config.CALIBRATION_FILE, 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'thresholds': thresholds,
                    'stats': {
                        'samples': len(amplitudes),
                        'amp_mean': float(amp_mean),
                        'amp_std': float(amp_std),
                        'jaw_mean': float(jaw_mean),
                        'jaw_std': float(jaw_std),
                    }
                }, f, indent=2)

            add_log(f"Calibration saved to {wc_config.CALIBRATION_FILE}", "success")

            # Mark complete - set progress to 100% and store result
            state.calibration_progress = 1.0
            state.calibration_phase = "complete"
            state.calibration_result = {
                "success": True,
                "thresholds": thresholds,
                "logs": state.calibration_logs
            }

            print("-" * 60)
            print("[CALIBRATION] Complete!")
            print("=" * 60 + "\n")

            time.sleep(0.3)  # Give time for WebSocket to send the 100% update
            state.is_calibrating = False
            return {"success": True, "thresholds": thresholds}

        # No data collected
        add_log("Error: No data collected during calibration", "error")
        state.calibration_progress = 1.0
        state.calibration_phase = "failed"
        state.calibration_result = {
            "success": False,
            "error": "No data collected",
            "logs": state.calibration_logs
        }
        print("[CALIBRATION] Failed: No data collected\n")
        time.sleep(0.3)
        state.is_calibrating = False
        return {"success": False, "error": "No data collected"}

    except Exception as e:
        add_log(f"Error: {str(e)}", "error")
        state.calibration_progress = 1.0
        state.calibration_phase = "failed"
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

                    await websocket.send_json(response)

            # Send calibration progress if calibrating
            if state.is_calibrating:
                msg = {
                    "type": "calibration_progress",
                    "phase": state.calibration_phase,
                    "progress": state.calibration_progress,
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
        })

# -----------------------------------------------------------------------------
# HTTP Routes
# -----------------------------------------------------------------------------

@app.get("/")
async def serve_frontend():
    return FileResponse("frontend.html")

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "board_connected": state.is_connected,
        "wheelchair_enabled": state.wheelchair_enabled,
    }

# -----------------------------------------------------------------------------
# Shutdown Handler
# -----------------------------------------------------------------------------

def shutdown_handler(signum, frame):
    """Graceful shutdown on Ctrl+C."""
    print("\nShutting down...")

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
