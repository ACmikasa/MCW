# OpenBCI Cyton + Daisy EEG Dashboard

A real-time web dashboard for streaming and visualizing 16-channel EEG data from OpenBCI Cyton + Daisy boards.

## Docs-First Decision

### Library Choice: BrainFlow

**Why BrainFlow?**
The official [OpenBCI Python documentation](https://docs.openbci.com/Deprecated/Python/) is marked as **deprecated**. OpenBCI now recommends BrainFlow as the standard way to interface with their hardware.

**BrainFlow Documentation Sources:**
- [Supported Boards](https://brainflow.readthedocs.io/en/stable/SupportedBoards.html) - Board IDs and configuration
- [Python Code Samples](https://brainflow.readthedocs.io/en/stable/Examples.html) - API usage examples
- [User API Reference](https://brainflow.readthedocs.io/en/stable/UserAPI.html) - Complete API docs

### Cyton + Daisy Specifications (from BrainFlow docs)

| Parameter | Value |
|-----------|-------|
| Board ID | `BoardIds.CYTON_DAISY_BOARD` (value = 2) |
| EEG Channels | 16 |
| Accelerometer Channels | 3 |
| Sample Rate | **125 Hz** (Daisy halves the 250 Hz Cyton rate) |
| Communication | Serial (via USB dongle) |

### How to Enable 16 Channels / Daisy Mode

With BrainFlow, you simply specify `BoardIds.CYTON_DAISY_BOARD` as the board ID. The Daisy module is auto-detected when physically connected to the Cyton board. No additional "enable Daisy" command is needed.

```python
from brainflow.board_shim import BoardShim, BrainFlowInputParams, BoardIds

params = BrainFlowInputParams()
params.serial_port = "/dev/cu.usbserial-XXXXX"  # Your port
board = BoardShim(BoardIds.CYTON_DAISY_BOARD, params)  # Board ID = 2
```

---

## Quick Start

### 1. Install Dependencies

```bash
cd eeg-dashboard
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Find Your Serial Port

On macOS, the OpenBCI dongle appears as `/dev/cu.usbserial-*`:

```bash
ls /dev/cu.usbserial-*
```

**IMPORTANT (macOS):** You MUST use `/dev/cu.*` ports, NOT `/dev/tty.*`. This is a BrainFlow requirement.

### 3. Run the Dashboard

```bash
python backend.py
```

### 4. Open the Dashboard

Navigate to: **http://localhost:8080**

### 5. Connect and Stream

1. Select your serial port from the dropdown (or enter manually)
2. Click **Connect**
3. Verify you see "16" channels and "125 Hz" sample rate
4. Click **Start Streaming**
5. You should see live EEG waveforms on all 16 channels

---

## Setup Instructions for macOS

### Prerequisites

1. **Python 3.9+** (check with `python3 --version`)
2. **OpenBCI Cyton board** with Daisy module attached
3. **OpenBCI USB dongle** plugged in

### Serial Port Permissions

macOS typically allows serial port access without special permissions. However, if you encounter permission errors:

```bash
# Check your username
whoami

# Ensure you're in the 'dialout' or equivalent group (usually not needed on macOS)
sudo dseditgroup -o edit -a $(whoami) -t user dialout
```

### Finding the Correct Port

1. **With dongle unplugged**, run:
   ```bash
   ls /dev/cu.*
   ```

2. **Plug in the dongle**, then run again:
   ```bash
   ls /dev/cu.*
   ```

3. The new device that appears is your OpenBCI dongle (e.g., `/dev/cu.usbserial-DM00XXXX`)

### Verifying Hardware Setup

Before running the dashboard, ensure:

1. **Daisy module is properly seated** on the Cyton board
2. **Both boards powered on** (blue LED on Cyton, check Daisy connection)
3. **Dongle paired** (dongle LED should be solid, not blinking)

---

## Verifying Correct Operation

### Expected Behavior

When everything is working correctly:

| Indicator | Expected Value |
|-----------|----------------|
| Channels | **16** |
| Sample Rate | **125 Hz** |
| Effective Rate | ~125 Hz (±5%) |
| Dropped Packets | 0 or very low |

### Normal Waveforms

- **With electrodes attached:** You should see rhythmic brain activity (alpha waves ~8-12 Hz when eyes closed)
- **Without electrodes (dry run):** You'll see baseline noise, which is normal
- **Amplitude:** Typical EEG is 10-100 μV; adjust Y-scale slider as needed

### Test Without Electrodes

For initial testing, you can run without electrodes attached. You should see:
- Baseline noise on all 16 channels
- Consistent updates at ~125 Hz
- No dropped packets

---

## Troubleshooting

### Port Busy / Cannot Open Port

**Symptom:** `BrainFlow error: BOARD_NOT_READY_ERROR` or "port busy"

**Solutions:**
1. Close the OpenBCI GUI if it's running
2. Kill any other process using the port:
   ```bash
   lsof | grep usbserial
   kill -9 <PID>
   ```
3. Unplug and replug the dongle
4. Restart the backend

### Wrong Channel Count (8 instead of 16)

**Symptom:** Only 8 channels shown, or first 8 channels have data

**Causes:**
- Daisy module not properly connected
- Using `CYTON_BOARD` instead of `CYTON_DAISY_BOARD`

**Solutions:**
1. Power off the Cyton
2. Reseat the Daisy module firmly onto the Cyton headers
3. Power on and verify both boards' LEDs
4. Ensure `BOARD_ID = BoardIds.CYTON_DAISY_BOARD` in `backend.py`

### No Data / Flat Lines

**Symptom:** Connected successfully but no data streaming

**Solutions:**
1. Check dongle pairing (solid LED, not blinking)
2. Press reset button on Cyton board
3. Try a different USB port
4. Check battery level on Cyton board

### Dropped Packets

**Symptom:** High "Dropped" count in statistics

**Causes:**
- USB bandwidth issues
- System under heavy load
- Interference

**Solutions:**
1. Use a USB 2.0 port (sometimes more reliable than USB 3.0 hubs)
2. Close other applications
3. Reduce wireless interference near the dongle

### Permission Denied

**Symptom:** `PermissionError` when connecting

**Solutions:**
```bash
# macOS - rarely needed but try:
sudo chmod 666 /dev/cu.usbserial-*

# Or run with sudo (not recommended for production)
sudo python backend.py
```

### WebSocket Connection Failed

**Symptom:** Frontend shows "WebSocket disconnected"

**Solutions:**
1. Ensure backend is running
2. Check if port 8080 is available:
   ```bash
   lsof -i :8080
   ```
3. Try a different port:
   ```bash
   python backend.py --port 8081
   ```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Web Browser                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │  frontend.html                                     │  │
│  │  - Plotly.js for real-time charts                 │  │
│  │  - 16-channel stacked time series                 │  │
│  │  - WebSocket client                               │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────┬───────────────────────────────────┘
                      │ WebSocket (ws://localhost:8080/ws)
                      ▼
┌─────────────────────────────────────────────────────────┐
│                   backend.py                            │
│  ┌───────────────────────────────────────────────────┐  │
│  │  FastAPI + Uvicorn                                │  │
│  │  - WebSocket server                               │  │
│  │  - HTTP static file server                        │  │
│  └───────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────┐  │
│  │  BrainFlow                                        │  │
│  │  - Board connection (CYTON_DAISY_BOARD)           │  │
│  │  - Data acquisition (125 Hz, 16 channels)         │  │
│  │  - Ring buffer management                         │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────┬───────────────────────────────────┘
                      │ Serial (USB)
                      ▼
┌─────────────────────────────────────────────────────────┐
│                 OpenBCI Hardware                        │
│  ┌─────────────────┐    ┌─────────────────┐            │
│  │  Cyton Board    │◄───│  Daisy Module   │            │
│  │  (Ch 1-8)       │    │  (Ch 9-16)      │            │
│  └────────┬────────┘    └─────────────────┘            │
│           │                                             │
│  ┌────────▼────────┐                                   │
│  │   USB Dongle    │                                   │
│  └─────────────────┘                                   │
└─────────────────────────────────────────────────────────┘
```

---

## Features

- **Live 16-channel EEG visualization** with Plotly.js
- **Adjustable time window** (1-30 seconds)
- **Adjustable Y-scale** (25-500 μV)
- **Real-time statistics:** sample count, dropped packets, effective rate
- **CSV recording** with timestamped samples
- **Auto-detect serial ports** on macOS/Linux/Windows
- **Graceful shutdown** on Ctrl+C
- **WebSocket reconnection** if connection drops

---

## Files

```
eeg-dashboard/
├── backend.py        # Python server (BrainFlow + FastAPI)
├── frontend.html     # Web dashboard (Plotly.js)
├── requirements.txt  # Python dependencies
└── README.md         # This file
```

---

## References

- [BrainFlow Documentation](https://brainflow.readthedocs.io/)
- [BrainFlow Supported Boards](https://brainflow.readthedocs.io/en/stable/SupportedBoards.html)
- [OpenBCI Cyton Getting Started](https://docs.openbci.com/GettingStarted/Boards/CytonGS/)
- [OpenBCI Daisy Getting Started](https://docs.openbci.com/GettingStarted/Boards/DaisyGS/)
