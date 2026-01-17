"""
Data Handler Module for EEG Wheelchair Control.

Provides flexible data input from multiple sources:
- BrainFlow board (live streaming)
- Function callback (custom data source)
- CSV file (recorded data)
- Numpy array (testing)

Implements a circular buffer for efficient memory usage.
"""

import time
import logging
from typing import Optional, Callable, Union, Tuple
from collections import deque
from pathlib import Path
import numpy as np

from . import config

logger = logging.getLogger(__name__)


class CircularBuffer:
    """
    Circular buffer for efficient real-time data storage.

    Maintains a fixed-size buffer that overwrites oldest data
    when full. Provides efficient window extraction.
    """

    def __init__(self, max_size: int = config.BUFFER_SIZE_SAMPLES):
        """
        Initialize the circular buffer.

        Args:
            max_size: Maximum number of samples to store
        """
        self.max_size = max_size
        self.buffer = deque(maxlen=max_size)
        self.timestamps = deque(maxlen=max_size)

    def add(self, sample: np.ndarray, timestamp: Optional[float] = None):
        """
        Add a sample to the buffer.

        Args:
            sample: Data sample (can be multi-channel)
            timestamp: Optional timestamp (defaults to current time)
        """
        if timestamp is None:
            timestamp = time.time()

        self.buffer.append(sample)
        self.timestamps.append(timestamp)

    def add_batch(self, samples: np.ndarray, timestamps: Optional[np.ndarray] = None):
        """
        Add multiple samples to the buffer.

        Args:
            samples: Array of samples (shape: [n_channels, n_samples] or [n_samples])
            timestamps: Optional array of timestamps
        """
        if samples.ndim == 1:
            samples = samples.reshape(1, -1)

        n_samples = samples.shape[1]

        if timestamps is None:
            base_time = time.time()
            timestamps = np.linspace(
                base_time - n_samples / config.SAMPLING_RATE,
                base_time,
                n_samples
            )

        for i in range(n_samples):
            self.buffer.append(samples[:, i])
            self.timestamps.append(timestamps[i])

    def get_window(self, window_size: int = config.WINDOW_SIZE_SAMPLES) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Get the most recent window of data.

        Args:
            window_size: Number of samples to retrieve

        Returns:
            Tuple of (data_array, timestamp_array) or (None, None) if insufficient data
        """
        if len(self.buffer) < window_size:
            return None, None

        # Get last window_size samples
        data = list(self.buffer)[-window_size:]
        timestamps = list(self.timestamps)[-window_size:]

        # Convert to numpy array
        data_array = np.array(data).T  # Shape: [n_channels, n_samples]
        timestamp_array = np.array(timestamps)

        return data_array, timestamp_array

    def get_all(self) -> Tuple[np.ndarray, np.ndarray]:
        """Get all data in buffer."""
        if len(self.buffer) == 0:
            return np.array([]), np.array([])

        data = np.array(list(self.buffer)).T
        timestamps = np.array(list(self.timestamps))

        return data, timestamps

    def clear(self):
        """Clear the buffer."""
        self.buffer.clear()
        self.timestamps.clear()

    def __len__(self):
        return len(self.buffer)

    @property
    def is_ready(self) -> bool:
        """Check if buffer has enough data for one window."""
        return len(self.buffer) >= config.WINDOW_SIZE_SAMPLES


class DataHandler:
    """
    Unified data handler for EEG input from multiple sources.

    Supports:
    - BrainFlow BoardShim object (live streaming)
    - Callable function returning data
    - CSV file path
    - Numpy array (for testing)
    """

    def __init__(self,
                 data_source: Union[object, Callable, str, np.ndarray, None] = None,
                 fp1_channel: int = config.FP1_CHANNEL,
                 fp2_channel: int = config.FP2_CHANNEL):
        """
        Initialize the data handler.

        Args:
            data_source: Data source (BrainFlow board, function, file path, or array)
            fp1_channel: Channel index for FP1 electrode
            fp2_channel: Channel index for FP2 electrode
        """
        self.fp1_channel = fp1_channel
        self.fp2_channel = fp2_channel

        # Initialize circular buffer
        self.buffer = CircularBuffer()

        # Source type detection
        self.source_type = None
        self.data_source = None
        self.file_data = None
        self.file_index = 0

        if data_source is not None:
            self.set_source(data_source)

        logger.info(f"DataHandler initialized (FP1={fp1_channel}, FP2={fp2_channel})")

    def set_source(self, data_source: Union[object, Callable, str, np.ndarray]):
        """
        Set or change the data source.

        Args:
            data_source: New data source
        """
        # Detect source type
        if callable(data_source):
            self.source_type = 'function'
            self.data_source = data_source
            logger.info("Data source: function callback")

        elif isinstance(data_source, (str, Path)):
            self.source_type = 'file'
            self._load_file(str(data_source))
            logger.info(f"Data source: file ({data_source})")

        elif isinstance(data_source, np.ndarray):
            self.source_type = 'array'
            self.file_data = data_source
            self.file_index = 0
            logger.info("Data source: numpy array")

        elif hasattr(data_source, 'get_board_data'):
            # BrainFlow BoardShim
            self.source_type = 'brainflow'
            self.data_source = data_source
            logger.info("Data source: BrainFlow board")

        else:
            raise ValueError(f"Unknown data source type: {type(data_source)}")

    def _load_file(self, filepath: str):
        """Load data from CSV file."""
        try:
            # Try to load as CSV
            self.file_data = np.loadtxt(filepath, delimiter=',', skiprows=1).T
            self.file_index = 0
            logger.info(f"Loaded file with shape {self.file_data.shape}")
        except Exception as e:
            logger.error(f"Failed to load file: {e}")
            raise

    def update(self) -> bool:
        """
        Update the buffer with new data from the source.

        Returns:
            True if new data was added
        """
        if self.source_type == 'brainflow':
            return self._update_from_brainflow()
        elif self.source_type == 'function':
            return self._update_from_function()
        elif self.source_type in ('file', 'array'):
            return self._update_from_array()
        else:
            logger.warning("No data source configured")
            return False

    def _update_from_brainflow(self) -> bool:
        """Get new data from BrainFlow board."""
        try:
            # Get all available data (non-blocking)
            data = self.data_source.get_board_data()

            if data.shape[1] > 0:
                self.buffer.add_batch(data)
                return True

        except Exception as e:
            logger.error(f"BrainFlow data error: {e}")

        return False

    def _update_from_function(self) -> bool:
        """Get new data from callback function."""
        try:
            result = self.data_source()

            if result is None:
                return False

            # Handle different return formats
            if isinstance(result, tuple) and len(result) == 2:
                fp1, fp2 = result
                # Combine into multi-channel array
                if fp1 is not None and fp2 is not None:
                    # Create full array with channels at correct indices
                    max_ch = max(self.fp1_channel, self.fp2_channel) + 1
                    data = np.zeros((max_ch, len(fp1)))
                    data[self.fp1_channel] = fp1
                    data[self.fp2_channel] = fp2
                    self.buffer.add_batch(data)
                    return True
            elif isinstance(result, np.ndarray):
                self.buffer.add_batch(result)
                return True

        except Exception as e:
            logger.error(f"Function callback error: {e}")

        return False

    def _update_from_array(self) -> bool:
        """Get next chunk from file/array data."""
        if self.file_data is None:
            return False

        # Get next window_step samples
        chunk_size = config.WINDOW_STEP_SAMPLES
        end_idx = self.file_index + chunk_size

        if end_idx > self.file_data.shape[1]:
            # Loop back to beginning (for continuous testing)
            self.file_index = 0
            end_idx = chunk_size

        chunk = self.file_data[:, self.file_index:end_idx]
        self.buffer.add_batch(chunk)
        self.file_index = end_idx

        return True

    def get_window(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Get the current window of FP1 and FP2 data.

        Returns:
            Tuple of (fp1_data, fp2_data) or (None, None) if not ready
        """
        # Update buffer with new data
        self.update()

        # Get window from buffer
        data, timestamps = self.buffer.get_window()

        if data is None:
            return None, None

        # Extract FP1 and FP2 channels
        try:
            fp1 = data[self.fp1_channel]
            fp2 = data[self.fp2_channel]
            return fp1, fp2
        except IndexError:
            logger.error(f"Channel index out of range. Data shape: {data.shape}")
            return None, None

    def get_window_with_timestamps(self) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Get the current window with timestamps.

        Returns:
            Tuple of (fp1_data, fp2_data, timestamps)
        """
        self.update()
        data, timestamps = self.buffer.get_window()

        if data is None:
            return None, None, None

        try:
            fp1 = data[self.fp1_channel]
            fp2 = data[self.fp2_channel]
            return fp1, fp2, timestamps
        except IndexError:
            return None, None, None

    def is_ready(self) -> bool:
        """Check if enough data is available for processing."""
        return self.buffer.is_ready

    def clear(self):
        """Clear the data buffer."""
        self.buffer.clear()

    def get_stats(self) -> dict:
        """Get buffer statistics."""
        return {
            'buffer_size': len(self.buffer),
            'max_size': self.buffer.max_size,
            'is_ready': self.is_ready(),
            'source_type': self.source_type,
        }


class BrainFlowDataHandler(DataHandler):
    """
    Specialized data handler for BrainFlow boards.

    Provides additional BrainFlow-specific functionality.
    """

    def __init__(self, board=None,
                 fp1_channel: int = config.FP1_CHANNEL,
                 fp2_channel: int = config.FP2_CHANNEL):
        """
        Initialize BrainFlow data handler.

        Args:
            board: BrainFlow BoardShim instance
            fp1_channel: FP1 channel index
            fp2_channel: FP2 channel index
        """
        super().__init__(None, fp1_channel, fp2_channel)

        self.board = board

        if board is not None:
            self.set_board(board)

    def set_board(self, board):
        """Set the BrainFlow board instance."""
        self.board = board
        self.source_type = 'brainflow'
        self.data_source = board
        logger.info("BrainFlow board connected to DataHandler")

    def _update_from_brainflow(self) -> bool:
        """Override to get data from BrainFlow board."""
        if self.board is None:
            return False

        try:
            # Get available data (clears the board's buffer)
            data = self.board.get_board_data()

            if data.shape[1] > 0:
                self.buffer.add_batch(data)
                logger.debug(f"Added {data.shape[1]} samples from BrainFlow")
                return True

        except Exception as e:
            logger.error(f"BrainFlow error: {e}")

        return False


# =============================================================================
# Factory function for easy creation
# =============================================================================

def create_data_handler(source_type: str = 'brainflow', **kwargs) -> DataHandler:
    """
    Factory function to create appropriate data handler.

    Args:
        source_type: 'brainflow', 'function', 'file', or 'array'
        **kwargs: Additional arguments for the handler

    Returns:
        Configured DataHandler instance
    """
    if source_type == 'brainflow':
        return BrainFlowDataHandler(**kwargs)
    else:
        return DataHandler(**kwargs)


# =============================================================================
# Testing
# =============================================================================

def test_circular_buffer():
    """Test circular buffer functionality."""
    print("\nTesting Circular Buffer")
    print("=" * 50)

    buffer = CircularBuffer(max_size=100)

    # Add samples
    for i in range(150):
        buffer.add(np.array([i, i * 2]))

    print(f"Buffer length: {len(buffer)} (max: {buffer.max_size})")
    assert len(buffer) == 100, "Buffer should be at max size"

    # Get window
    data, timestamps = buffer.get_window(50)
    print(f"Window shape: {data.shape}")
    assert data.shape == (2, 50), "Window shape incorrect"

    print("Circular buffer tests passed!")


def test_data_handler():
    """Test data handler with synthetic data."""
    from .signal_processor import generate_test_signal

    print("\nTesting Data Handler")
    print("=" * 50)

    # Test with function source
    def data_getter():
        return generate_test_signal(signal_type='noise')

    handler = DataHandler(data_getter)

    # Fill buffer
    for _ in range(10):
        handler.update()
        time.sleep(0.1)

    print(f"Handler stats: {handler.get_stats()}")

    # Get window
    fp1, fp2 = handler.get_window()
    if fp1 is not None:
        print(f"FP1 shape: {fp1.shape}, FP2 shape: {fp2.shape}")
    else:
        print("Window not ready yet")

    print("Data handler tests passed!")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    test_circular_buffer()
    test_data_handler()
