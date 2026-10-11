"""
Wheelchair Interface Module for EEG Wheelchair Control.

Stub implementation for wheelchair control commands.
Replace the send_command method with actual hardware interface.

Supported commands:
- FORWARD: Move wheelchair forward
- BACKWARD: Move wheelchair backward
- LEFT: Turn wheelchair left
- RIGHT: Turn wheelchair right
- STOP: Stop all movement
"""

import time
import logging
from typing import Optional, Callable
from abc import ABC, abstractmethod

from . import config

logger = logging.getLogger(__name__)


class WheelchairInterface(ABC):
    """Abstract base class for wheelchair interfaces."""

    @abstractmethod
    def send_command(self, command: str, confidence: float = 1.0) -> bool:
        """Send a command to the wheelchair."""
        pass

    @abstractmethod
    def stop(self) -> bool:
        """Emergency stop."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if wheelchair is connected."""
        pass


class MockWheelchairInterface(WheelchairInterface):
    """
    Mock wheelchair interface for testing and development.

    Logs commands to console instead of controlling actual hardware.
    """

    def __init__(self, verbose: bool = True):
        """
        Initialize mock wheelchair interface.

        Args:
            verbose: If True, print commands to console
        """
        self.verbose = verbose
        self.connected = True
        self.last_command = None
        self.last_command_time = 0
        self.command_history = []

        logger.info("MockWheelchairInterface initialized")

    def send_command(self, command: str, confidence: float = 1.0) -> bool:
        """
        Send a command to the wheelchair (mock).

        Args:
            command: Command to send (FORWARD, BACKWARD, LEFT, RIGHT, STOP)
            confidence: Confidence score of the command

        Returns:
            True if command was sent successfully
        """
        if not self.connected:
            logger.warning("Wheelchair not connected")
            return False

        self.last_command = command
        self.last_command_time = time.time()

        # Log to history
        self.command_history.append({
            'command': command,
            'confidence': confidence,
            'timestamp': self.last_command_time,
        })

        # Keep only last 100 commands
        if len(self.command_history) > 100:
            self.command_history = self.command_history[-100:]

        # Visual feedback
        command_symbols = {
            config.Command.FORWARD: "  [FORWARD]  ",
            config.Command.BACKWARD: " [BACKWARD] ",
            config.Command.LEFT: "   [LEFT]    <--",
            config.Command.RIGHT: "-->  [RIGHT]   ",
            config.Command.STOP: "   [STOP]    ",
        }

        symbol = command_symbols.get(command, f"[{command}]")

        if self.verbose:
            print(f"\n{'=' * 40}")
            print(f"   WHEELCHAIR COMMAND: {symbol}")
            print(f"   Confidence: {confidence:.0%}")
            print(f"{'=' * 40}\n")

        logger.info(f"Command sent: {command} (confidence: {confidence:.2f})")

        return True

    def stop(self) -> bool:
        """Emergency stop."""
        return self.send_command(config.Command.STOP, confidence=1.0)

    def is_connected(self) -> bool:
        """Check if wheelchair is connected."""
        return self.connected

    def disconnect(self):
        """Disconnect from wheelchair."""
        self.connected = False
        logger.info("MockWheelchair disconnected")

    def connect(self):
        """Connect to wheelchair."""
        self.connected = True
        logger.info("MockWheelchair connected")

    def get_status(self) -> dict:
        """Get wheelchair status."""
        return {
            'connected': self.connected,
            'last_command': self.last_command,
            'last_command_time': self.last_command_time,
            'total_commands': len(self.command_history),
        }


class SerialWheelchairInterface(WheelchairInterface):
    """
    Serial port interface for wheelchair control.

    Sends commands over serial port to wheelchair controller.
    Implement the protocol specific to your wheelchair hardware.
    """

    def __init__(self, port: str, baudrate: int = 9600):
        """
        Initialize serial wheelchair interface.

        Args:
            port: Serial port (e.g., '/dev/ttyUSB0' or 'COM3')
            baudrate: Serial baudrate
        """
        self.port = port
        self.baudrate = baudrate
        self.serial = None

        # Command to byte mapping (customize for your hardware)
        self.command_bytes = {
            config.Command.FORWARD: b'F',
            config.Command.BACKWARD: b'B',
            config.Command.LEFT: b'L',
            config.Command.RIGHT: b'R',
            config.Command.STOP: b'S',
        }

        logger.info(f"SerialWheelchairInterface initialized (port={port})")

    def connect(self) -> bool:
        """Connect to wheelchair via serial port."""
        try:
            import serial
            self.serial = serial.Serial(self.port, self.baudrate, timeout=1)
            logger.info(f"Connected to wheelchair on {self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    def disconnect(self):
        """Disconnect from wheelchair."""
        if self.serial:
            self.serial.close()
            self.serial = None
            logger.info("Disconnected from wheelchair")

    def send_command(self, command: str, confidence: float = 1.0) -> bool:
        """
        Send a command to the wheelchair via serial.

        Args:
            command: Command to send
            confidence: Confidence score (unused in basic protocol)

        Returns:
            True if command was sent successfully
        """
        if not self.is_connected():
            logger.warning("Wheelchair not connected")
            return False

        try:
            cmd_byte = self.command_bytes.get(command, b'S')  # Default to STOP
            self.serial.write(cmd_byte)
            logger.info(f"Sent command: {command} ({cmd_byte})")
            return True
        except Exception as e:
            logger.error(f"Failed to send command: {e}")
            return False

    def stop(self) -> bool:
        """Emergency stop."""
        return self.send_command(config.Command.STOP)

    def is_connected(self) -> bool:
        """Check if wheelchair is connected."""
        return self.serial is not None and self.serial.is_open


class CallbackWheelchairInterface(WheelchairInterface):
    """
    Callback-based wheelchair interface.

    Allows custom handling of wheelchair commands through callbacks.
    Useful for integrating with other systems or custom hardware.
    """

    def __init__(self, command_callback: Callable[[str, float], bool]):
        """
        Initialize callback wheelchair interface.

        Args:
            command_callback: Function to call with (command, confidence)
        """
        self.command_callback = command_callback
        self._connected = True

        logger.info("CallbackWheelchairInterface initialized")

    def send_command(self, command: str, confidence: float = 1.0) -> bool:
        """Send command via callback."""
        try:
            return self.command_callback(command, confidence)
        except Exception as e:
            logger.error(f"Callback error: {e}")
            return False

    def stop(self) -> bool:
        """Emergency stop."""
        return self.send_command(config.Command.STOP)

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    def disconnect(self):
        """Disconnect."""
        self._connected = False

    def connect(self):
        """Connect."""
        self._connected = True


# =============================================================================
# Factory function
# =============================================================================

def create_wheelchair_interface(interface_type: str = 'mock', **kwargs) -> WheelchairInterface:
    """
    Factory function to create wheelchair interface.

    Args:
        interface_type: 'mock', 'serial', or 'callback'
        **kwargs: Additional arguments for the interface

    Returns:
        WheelchairInterface instance
    """
    if interface_type == 'mock':
        return MockWheelchairInterface(**kwargs)
    elif interface_type == 'serial':
        return SerialWheelchairInterface(**kwargs)
    elif interface_type == 'callback':
        return CallbackWheelchairInterface(**kwargs)
    else:
        raise ValueError(f"Unknown interface type: {interface_type}")


# =============================================================================
# Testing
# =============================================================================

def test_mock_interface():
    """Test mock wheelchair interface."""
    print("\nTesting Mock Wheelchair Interface")
    print("=" * 50)

    wheelchair = MockWheelchairInterface(verbose=True)

    # Test all commands
    commands = [
        (config.Command.FORWARD, 0.95),
        (config.Command.LEFT, 0.88),
        (config.Command.RIGHT, 0.92),
        (config.Command.BACKWARD, 0.85),
        (config.Command.STOP, 1.0),
    ]

    for cmd, conf in commands:
        result = wheelchair.send_command(cmd, conf)
        assert result, f"Command {cmd} failed"
        time.sleep(0.5)

    # Check status
    status = wheelchair.get_status()
    print(f"\nFinal status: {status}")

    print("\nMock interface tests passed!")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_mock_interface()
