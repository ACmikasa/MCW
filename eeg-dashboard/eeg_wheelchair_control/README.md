# EEG Wheelchair Control System

Real-time EEG-based wheelchair control using OpenBCI Cyton+Daisy (16 channels).

## Commands

| EEG Action | Wheelchair Command |
|------------|-------------------|
| Jaw Clench | Forward |
| Double Eye Blink | Backward |
| Look Left | Turn Left |
| Look Right | Turn Right |

## Quick Start

### 1. Install Dependencies

```bash
cd eeg-dashboard
source venv/bin/activate
pip install scipy  # Additional dependency for filters
```

### 2. Find Your Serial Port

```bash
ls /dev/cu.usbserial-*
```

### 3. Run the System

```bash
# Interactive mode
python -m eeg_wheelchair_control.main

# Direct detection (after calibration)
python -m eeg_wheelchair_control.main --port /dev/cu.usbserial-XXXX --run

# Test mode (no hardware needed)
python -m eeg_wheelchair_control.main --test
```

## Calibration

Calibration is essential for reliable command detection. The system calibrates:

1. **Baseline** (30 seconds): Establishes your normal EEG patterns
2. **Jaw Clench** (10 trials): Calibrates forward command
3. **Double Blink** (10 trials): Calibrates backward command
4. **Look Left** (10 trials): Calibrates left turn
5. **Look Right** (10 trials): Calibrates right turn

### Quick Calibration

For testing, use quick calibration (10 seconds baseline only):

```bash
python -m eeg_wheelchair_control.main --port /dev/cu.usbserial-XXXX
# Select option 2: Quick Calibration
```

## Detection Algorithms

### Jaw Clench Detection (Forward)

```
Method: High-frequency EMG power in 20-100 Hz band
Trigger: jaw_power > baseline_jaw_power * 2.5
Rationale: Clenching activates temporalis muscle, producing strong EMG
```

### Double Blink Detection (Backward)

```
Method: Two consecutive amplitude peaks in EOG band (0.5-15 Hz)
Trigger: 2 peaks within 200-800ms, each > baseline * 3
Rationale: Blinks create sharp positive spikes on frontal electrodes
```

### Look Left Detection

```
Method: Amplitude asymmetry between FP1 and FP2
Trigger: FP2_max > threshold AND FP2_max > 1.3 * FP1_max
Rationale: Looking left moves eyes toward right electrode (FP2)
```

### Look Right Detection

```
Method: Amplitude asymmetry between FP1 and FP2
Trigger: FP1_max > threshold AND FP1_max > 1.3 * FP2_max
Rationale: Looking right moves eyes toward left electrode (FP1)
```

## Channel Configuration

The system uses FP1 and FP2 frontal polar electrodes:

- **FP1** (Channel 9): Left forehead
- **FP2** (Channel 10): Right forehead

These electrodes are optimal for:
- EOG (eye movements and blinks)
- EMG (jaw clench from temporalis muscle)

## Signal Processing Pipeline

```
Raw EEG (125 Hz)
    │
    ▼
Notch Filter (60 Hz removal)
    │
    ├──► EOG Bandpass (0.5-15 Hz) ──► Blink & Eye Movement Detection
    │
    └──► EMG Bandpass (20-100 Hz) ──► Jaw Clench Detection
```

## File Structure

```
eeg_wheelchair_control/
├── __init__.py           # Package exports
├── config.py             # All configuration parameters
├── signal_processor.py   # Filtering and feature extraction
├── command_detector.py   # Detection algorithms
├── calibration.py        # User calibration system
├── data_handler.py       # Data buffering and input
├── wheelchair_interface.py # Wheelchair control (stub)
├── main.py               # Main entry point
└── README.md             # This file
```

## Configuration

Key parameters in `config.py`:

```python
# Channels
FP1_CHANNEL = 9
FP2_CHANNEL = 10

# Thresholds (multipliers applied to baseline)
BLINK_MULTIPLIER = 3.0      # Peaks must be 3x baseline
JAW_MULTIPLIER = 2.5        # EMG power must be 2.5x baseline
EYE_MOVE_MULTIPLIER = 1.5   # Eye movements must be 1.5x baseline
LATERAL_RATIO = 1.3         # Asymmetry ratio for left/right

# Timing
COMMAND_COOLDOWN_SEC = 1.0  # Prevent repeated triggers
SAFETY_TIMEOUT_SEC = 3.0    # Auto-stop after 3s of no commands
```

## Python API Usage

```python
from eeg_wheelchair_control import (
    EEGWheelchairController,
    CommandDetector,
    MockWheelchairInterface
)

# Create controller
controller = EEGWheelchairController(
    serial_port='/dev/cu.usbserial-XXXX'
)

# Connect to board
controller.connect()

# Load or run calibration
controller.load_calibration()
# or: controller.run_calibration()

# Start detection loop
controller.run_detection_loop()

# Cleanup
controller.disconnect()
```

### Custom Wheelchair Interface

```python
from eeg_wheelchair_control import (
    CallbackWheelchairInterface,
    EEGWheelchairController
)

def my_wheelchair_handler(command, confidence):
    print(f"Sending to wheelchair: {command}")
    # Add your hardware control code here
    return True

wheelchair = CallbackWheelchairInterface(my_wheelchair_handler)
controller = EEGWheelchairController(
    serial_port='/dev/cu.usbserial-XXXX',
    wheelchair_interface=wheelchair
)
```

### Process Single Window

```python
from eeg_wheelchair_control import CommandDetector, SignalProcessor
import numpy as np

# Initialize
detector = CommandDetector()

# Get your EEG data (125 samples = 1 second at 125 Hz)
fp1_data = np.array([...])  # Channel 9 data
fp2_data = np.array([...])  # Channel 10 data

# Detect command
result = detector.get_command(fp1_data, fp2_data)

if result.command:
    print(f"Detected: {result.command} (confidence: {result.confidence})")
```

## Troubleshooting

### No Commands Detected

1. **Check electrode placement**: FP1 and FP2 must be on forehead
2. **Check signal quality**: Run the EEG dashboard to visualize signals
3. **Recalibrate**: Thresholds may be too high
4. **Check cooldown**: 1 second cooldown between commands

### False Positives

1. **Recalibrate**: Thresholds may be too low
2. **Reduce noise**: Check electrode impedance
3. **Adjust multipliers**: Increase in `config.py`

### Connection Issues

1. **Check serial port**: `ls /dev/cu.usbserial-*`
2. **Check permissions**: May need `sudo` or add user to dialout group
3. **Check dongle pairing**: LED should be solid, not blinking

## Safety Features

1. **Command Cooldown**: 1 second between commands to prevent accidental triggers
2. **Safety Timeout**: Auto-stop after 3 seconds of no commands
3. **Confidence Threshold**: Commands only sent if confidence > 80%
4. **Signal Quality Check**: Detection paused if signal quality is poor

## Performance

- **Processing Time**: < 50ms per window (real-time capable)
- **Memory**: Fixed circular buffer (30 seconds of data)
- **Detection Rate**: Every 0.5 seconds (2 Hz)

## Testing Without Hardware

Use test mode to verify the system works:

```bash
python -m eeg_wheelchair_control.main --test
```

This generates synthetic EEG signals with random commands.
