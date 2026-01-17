#!/usr/bin/env python3
"""
OpenBCI Cyton + Daisy EEG Streaming Backend
============================================
Uses BrainFlow for data acquisition and WebSockets for live streaming.

Technical specs (from BrainFlow docs):
- Board ID: CYTON_DAISY_BOARD = 2
- Sample Rate: 125 Hz (Daisy halves the 250 Hz Cyton rate)
- EEG Channels: 16
- Accelerometer Channels: 3
- macOS: Must use /dev/cu.* ports (not /dev/tty.*)
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

from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds, BrainFlowError
from brainflow.data_filter import DataFilter
import numpy as np

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

BOARD_ID = BoardIds.CYTON_DAISY_BOARD  # Board ID = 2 for Cyton + Daisy (16 channels)
BATCH_SIZE = 25  # Samples per WebSocket message (~200ms at 125 Hz)
WS_UPDATE_INTERVAL = 0.1  # Seconds between WebSocket updates

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
    # OpenBCI dongles typically show as /dev/cu.usbserial-* or /dev/cu.usbmodem*
    if sys.platform == "darwin":
        patterns = [
            "/dev/cu.usbserial-*",  # FTDI-based dongles
            "/dev/cu.usbmodem*",    # CDC-ACM devices
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

        # Get board specifications
        state.sampling_rate = BoardShim.get_sampling_rate(BOARD_ID)
        state.eeg_channels = BoardShim.get_eeg_channels(BOARD_ID)
        state.accel_channels = BoardShim.get_accel_channels(BOARD_ID)
        state.timestamp_channel = BoardShim.get_timestamp_channel(BOARD_ID)
        state.num_channels = len(state.eeg_channels)

        state.is_connected = True

        return {
            "success": True,
            "sampling_rate": state.sampling_rate,
            "num_channels": state.num_channels,
            "eeg_channels": state.eeg_channels,
            "accel_channels": state.accel_channels,
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
        # Start the stream with a ring buffer (45000 = 6 minutes at 125 Hz * 16 channels)
        state.board.start_stream(45000)

        state.is_streaming = True
        state.start_time = time.time()
        state.total_samples = 0
        state.dropped_packets = 0

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

            # Check for dropped packets (gaps in sample counter)
            # The first row is typically the sample counter
            sample_indices = data[0, :]
            if len(sample_indices) > 1:
                expected_diff = np.ones(len(sample_indices) - 1)
                actual_diff = np.diff(sample_indices)
                # Handle counter wrap-around (0-255 for Cyton)
                actual_diff = np.where(actual_diff < 0, actual_diff + 256, actual_diff)
                dropped = np.sum(actual_diff > 1)
                state.dropped_packets += int(dropped)

        return data

    except Exception as e:
        print(f"Error getting board data: {e}")
        return None

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
        # Ensure filename has .csv extension
        if not filename.endswith(".csv"):
            filename += ".csv"

        state.csv_filename = filename
        state.csv_file = open(filename, "w", newline="")
        state.csv_writer = csv.writer(state.csv_file)

        # Write header
        header = ["timestamp", "sample_index"]
        header.extend([f"ch{i+1}" for i in range(state.num_channels)])
        header.extend([f"accel_{axis}" for axis in ["x", "y", "z"]])
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

def write_csv_data(data: np.ndarray):
    """Write data samples to CSV."""
    global state

    if not state.csv_writer or data is None or data.shape[1] == 0:
        return

    try:
        for i in range(data.shape[1]):
            row = [
                data[state.timestamp_channel, i],  # Timestamp
                int(data[0, i]),  # Sample index
            ]
            # EEG channels
            for ch in state.eeg_channels:
                row.append(data[ch, i])
            # Accelerometer channels
            for ch in state.accel_channels:
                row.append(data[ch, i])

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
                    # Write to CSV if enabled
                    if state.csv_enabled:
                        write_csv_data(data)

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

                    await websocket.send_json({
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
                        }
                    })

            await asyncio.sleep(0.01)  # Small sleep to prevent CPU spinning

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
        })

# -----------------------------------------------------------------------------
# HTTP Routes
# -----------------------------------------------------------------------------

# Serve the frontend
@app.get("/")
async def serve_frontend():
    return FileResponse("frontend.html")

@app.get("/health")
async def health_check():
    return {"status": "ok", "board_connected": state.is_connected}

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

# Register signal handlers
signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)

# -----------------------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OpenBCI EEG Streaming Server")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind to")
    args = parser.parse_args()

    print(f"""
╔══════════════════════════════════════════════════════════════════╗
║           OpenBCI Cyton + Daisy EEG Dashboard                   ║
╠══════════════════════════════════════════════════════════════════╣
║  Board: Cyton + Daisy (16 channels)                             ║
║  Board ID: {BOARD_ID} (CYTON_DAISY_BOARD)                                    ║
║  Sample Rate: 125 Hz                                            ║
║  Library: BrainFlow                                             ║
╠══════════════════════════════════════════════════════════════════╣
║  Open in browser: http://{args.host}:{args.port}                         ║
║  Press Ctrl+C to stop                                           ║
╚══════════════════════════════════════════════════════════════════╝
""")

    # Detect available ports on startup
    ports = detect_serial_ports()
    if ports:
        print(f"Detected serial ports: {', '.join(ports)}")
    else:
        print("No serial ports detected. Make sure the OpenBCI dongle is connected.")

    print()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
