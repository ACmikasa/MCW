"""
Arduino Serial Control for EEG Wheelchair

Sends single-character commands to Arduino via serial:
- 'F' = Forward
- 'R' = Right
- 'S' = Stop
"""

import serial
import serial.tools.list_ports
import time
import threading
from typing import Optional


class ArduinoController:
    def __init__(self):
        self.serial: Optional[serial.Serial] = None
        self.port: Optional[str] = None
        self.is_connected = False
        self.lock = threading.Lock()

    def list_ports(self) -> list:
        """List available serial ports."""
        ports = []
        for port in serial.tools.list_ports.comports():
            ports.append({
                "port": port.device,
                "description": port.description,
            })
        return ports

    def connect(self, port: str, baudrate: int = 115200) -> dict:
        """Connect to Arduino on specified port."""
        if self.is_connected:
            return {"success": False, "error": "Already connected"}

        try:
            self.serial = serial.Serial(port, baudrate, timeout=1)
            time.sleep(2)  # Arduino auto-reset delay
            self.port = port
            self.is_connected = True

            print(f"[ARDUINO] Connected to {port} @ {baudrate} baud")

            # Ensure motors are stopped on connect
            self.stop()

            return {"success": True, "port": port}

        except Exception as e:
            print(f"[ARDUINO] Connection failed: {e}")
            self.serial = None
            self.is_connected = False
            return {"success": False, "error": str(e)}

    def disconnect(self) -> dict:
        """Disconnect from Arduino."""
        if not self.is_connected:
            return {"success": False, "error": "Not connected"}

        try:
            self.stop()
            time.sleep(0.1)

            self.serial.close()
            self.serial = None
            self.port = None
            self.is_connected = False

            print("[ARDUINO] Disconnected")
            return {"success": True}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def _send(self, cmd: str) -> bool:
        """Send a single-character command to Arduino."""
        if not self.is_connected or not self.serial:
            return False

        with self.lock:
            try:
                self.serial.write((cmd + "\n").encode("utf-8"))
                self.serial.flush()
                return True
            except Exception as e:
                print(f"[ARDUINO] Send error: {e}")
                return False

    # ===================== High-level commands =====================

    def forward(self) -> bool:
        print("[ARDUINO] -> FORWARD")
        return self._send("F")

    def right(self) -> bool:
        print("[ARDUINO] -> RIGHT")
        return self._send("R")

    def stop(self) -> bool:
        print("[ARDUINO] -> STOP")
        return self._send("S")

    def get_status(self) -> dict:
        return {
            "connected": self.is_connected,
            "port": self.port,
        }


# Global instance (used by backend)
arduino = ArduinoController()
