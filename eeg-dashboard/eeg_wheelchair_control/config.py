"""
Configuration parameters for EEG Wheelchair Control System.

All threshold values and timing parameters are documented with their rationale.
"""

# =============================================================================
# Channel Configuration
# =============================================================================
# OpenBCI Cyton+Daisy channel mapping (1-indexed, converted to 0-indexed in code)
# FP1 and FP2 are frontal polar electrodes - ideal for EOG and blink detection
#
# If using Daisy board (channels 9-16): FP1=9, FP2=10
# If Daisy not working, use Cyton (channels 1-8): FP1=1, FP2=2
#
FP1_CHANNEL = 9   # Left frontal electrode (Daisy board)
FP2_CHANNEL = 10  # Right frontal electrode (Daisy board)

# Sampling rate for OpenBCI Cyton+Daisy
SAMPLING_RATE = 125  # Hz

# =============================================================================
# Filter Parameters
# =============================================================================
# Notch filter to remove power line interference
# Use 60 Hz for US/Canada, 50 Hz for Europe/Asia
NOTCH_FREQ = 60  # Hz
NOTCH_Q = 30     # Quality factor (higher = narrower notch)

# Bandpass filter for EOG signals (eye movements and blinks)
# Eye movements and blinks are slow, typically 0.5-15 Hz
EOG_HIGHPASS = 0.5   # Hz - removes DC drift
EOG_LOWPASS = 15.0   # Hz - removes muscle noise

# Bandpass filter for EMG signals (jaw clench detection)
# Muscle activity produces high-frequency signals 20-100+ Hz
# NOTE: Nyquist = 62.5 Hz (for 125 Hz sampling), so max lowpass is ~60 Hz
EMG_HIGHPASS = 20.0   # Hz - removes eye artifacts
EMG_LOWPASS = 55.0    # Hz - captures muscle activity (must be < Nyquist)

# Butterworth filter order
# 4th order provides good rolloff without excessive ringing
FILTER_ORDER = 4

# =============================================================================
# Window Parameters
# =============================================================================
# Window size for feature extraction
# 1 second provides enough samples for reliable detection
WINDOW_SIZE_SEC = 1.0
WINDOW_SIZE_SAMPLES = int(WINDOW_SIZE_SEC * SAMPLING_RATE)  # 125 samples

# Window overlap for smoother detection
# 0.5 second overlap means we process every 0.5 seconds
WINDOW_OVERLAP_SEC = 0.5
WINDOW_STEP_SAMPLES = int(WINDOW_OVERLAP_SEC * SAMPLING_RATE)  # 62 samples

# Buffer size for circular buffer (store 30 seconds of data)
BUFFER_SIZE_SEC = 30
BUFFER_SIZE_SAMPLES = int(BUFFER_SIZE_SEC * SAMPLING_RATE)  # 3750 samples

# =============================================================================
# Detection Thresholds (Default - will be overwritten by calibration)
# =============================================================================
# These are multipliers applied to baseline measurements during calibration

# Blink detection: peaks must be this many times above baseline
# Blinks produce very large amplitude spikes (100-400 uV)
BLINK_MULTIPLIER = 3.0

# Jaw clench detection: high-freq power must be this many times above baseline
# Jaw clenching produces strong EMG in the 20-100 Hz band
JAW_MULTIPLIER = 2.5

# Eye movement detection: amplitude must exceed this multiplier of baseline
# Lateral eye movements produce 50-200 uV deflections
EYE_MOVE_MULTIPLIER = 1.5

# Lateral asymmetry ratio: one channel must be this much larger than the other
# Looking left/right creates asymmetric signals between FP1 and FP2
LATERAL_RATIO = 1.3

# Minimum confidence score to accept a command (0-1)
MIN_CONFIDENCE = 0.8

# =============================================================================
# Double Blink Timing Parameters
# =============================================================================
# Time window between two blinks for double-blink detection
# Too fast (<200ms) is hard for humans, too slow (>800ms) looks like two singles
BLINK_MIN_INTERVAL_SEC = 0.2  # 200ms minimum between blinks
BLINK_MAX_INTERVAL_SEC = 0.8  # 800ms maximum between blinks

# Convert to samples
BLINK_MIN_INTERVAL_SAMPLES = int(BLINK_MIN_INTERVAL_SEC * SAMPLING_RATE)  # 25 samples
BLINK_MAX_INTERVAL_SAMPLES = int(BLINK_MAX_INTERVAL_SEC * SAMPLING_RATE)  # 100 samples

# Minimum peak prominence for blink detection (relative to baseline)
BLINK_MIN_PROMINENCE = 2.0

# =============================================================================
# Timing Parameters
# =============================================================================
# Cooldown after detecting a command (prevents repeated triggers)
COMMAND_COOLDOWN_SEC = 1.0

# Safety timeout: stop wheelchair if no command for this long
SAFETY_TIMEOUT_SEC = 3.0

# Processing target: each window should be processed within this time
MAX_PROCESSING_TIME_MS = 50

# =============================================================================
# Calibration Parameters
# =============================================================================
# Duration for baseline recording (user sits still, relaxed)
BASELINE_DURATION_SEC = 30

# Number of trials per command during calibration
CALIBRATION_TRIALS_PER_COMMAND = 10

# Time between calibration trials
CALIBRATION_TRIAL_INTERVAL_SEC = 2.0

# Calibration file path
CALIBRATION_FILE = "calibration_profile.json"

# =============================================================================
# Command Definitions
# =============================================================================
class Command:
    """Enumeration of wheelchair commands."""
    NONE = None
    LEFT = "left"
    RIGHT = "right"
    FORWARD = "forward"
    BACKWARD = "backward"
    STOP = "stop"

# Priority order for command detection
# Jaw clench is most distinct, then double blink, then eye movements
DETECTION_PRIORITY = [
    Command.FORWARD,   # Jaw clench - very distinct EMG signal
    Command.BACKWARD,  # Double blink - time-based pattern
    Command.LEFT,      # Look left - requires asymmetry check
    Command.RIGHT,     # Look right - requires asymmetry check
]

# =============================================================================
# Logging Configuration
# =============================================================================
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_FILE = "eeg_wheelchair.log"

# =============================================================================
# Safety Thresholds
# =============================================================================
# If signal amplitude exceeds this, likely electrode issue
MAX_VALID_AMPLITUDE_UV = 500.0

# If noise level is this high, pause detection
MAX_NOISE_THRESHOLD_UV = 100.0

# Minimum signal quality (0-1) to proceed with detection
MIN_SIGNAL_QUALITY = 0.5
