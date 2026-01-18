"""
Arduino Motor Control for EEG Wheelchair

Sends commands to Arduino via serial to control two motors (left/right wheels).

Commands sent to Arduino:
- 'F' = Forward (both motors)
- 'R' = Turn Right (left motor only)
- 'S' = Stop (both motors off)
- 'B' = Backward (both motors reverse) [optional]
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
        self.current_command = 'S'  # Start stopped
        self.lock = threading.Lock()

    def list_ports(self) -> list:
        """List available serial ports."""
        ports = []
        for port in serial.tools.list_ports.comports():
            ports.append({
                'port': port.device,
                'description': port.description,
                'hwid': port.hwid
            })
        return ports

    def connect(self, port: str, baudrate: int = 9600) -> dict:
        """Connect to Arduino on specified port."""
        if self.is_connected:
            return {"success": False, "error": "Already connected"}

        try:
            self.serial = serial.Serial(port, baudrate, timeout=1)
            time.sleep(2)  # Wait for Arduino to reset
            self.port = port
            self.is_connected = True
            print(f"[ARDUINO] Connected to {port}")

            # Send stop command initially
            self.stop()

            return {"success": True, "port": port}
        except Exception as e:
            print(f"[ARDUINO] Connection failed: {e}")
            return {"success": False, "error": str(e)}

    def disconnect(self) -> dict:
        """Disconnect from Arduino."""
        if not self.is_connected:
            return {"success": False, "error": "Not connected"}

        try:
            # Stop motors before disconnecting
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

    def send_command(self, cmd: str) -> bool:
        """Send a single character command to Arduino."""
        if not self.is_connected or not self.serial:
            return False

        with self.lock:
            try:
                self.serial.write(cmd.encode())
                self.current_command = cmd
                return True
            except Exception as e:
                print(f"[ARDUINO] Send error: {e}")
                return False

    def forward(self) -> bool:
        """Move forward (both motors)."""
        print("[ARDUINO] FORWARD")
        return self.send_command('F')

    def right(self) -> bool:
        """Turn right (left motor forward, right motor slower/off)."""
        print("[ARDUINO] RIGHT")
        return self.send_command('R')

    def left(self) -> bool:
        """Turn left (right motor forward, left motor slower/off)."""
        print("[ARDUINO] LEFT")
        return self.send_command('L')

    def backward(self) -> bool:
        """Move backward (both motors reverse)."""
        print("[ARDUINO] BACKWARD")
        return self.send_command('B')

    def stop(self) -> bool:
        """Stop all motors."""
        print("[ARDUINO] STOP")
        return self.send_command('S')

    def execute_eeg_command(self, command: str, is_blocked: bool = False) -> dict:
        """
        Execute an EEG command, checking for obstacles.

        Args:
            command: EEG command ('forward', 'right', 'both', etc.)
            is_blocked: Whether obstacle is detected (blocks forward movement)

        Returns:
            dict with executed action and status
        """
        if not self.is_connected:
            return {"success": False, "error": "Arduino not connected", "action": None}

        action = None

        if command == 'forward' or command == 'both':
            if is_blocked:
                # Obstacle detected - stop instead of moving forward
                self.stop()
                action = 'STOPPED (obstacle)'
                print("[ARDUINO] Forward BLOCKED by obstacle!")
            else:
                self.forward()
                action = 'FORWARD'

        elif command == 'right':
            self.right()
            action = 'RIGHT'

        elif command == 'left':
            self.left()
            action = 'LEFT'

        elif command == 'backward':
            self.backward()
            action = 'BACKWARD'

        elif command == 'stop' or command is None:
            self.stop()
            action = 'STOP'

        return {"success": True, "action": action, "command": command}

    def get_status(self) -> dict:
        """Get current Arduino connection status."""
        return {
            "connected": self.is_connected,
            "port": self.port,
            "current_command": self.current_command
        }


# Global instance
arduino = ArduinoController()


# Arduino sketch code for reference
ARDUINO_SKETCH = """
/*
 * EEG Wheelchair Motor Control
 *
 * Receives single character commands via Serial:
 * 'F' = Forward (both motors)
 * 'R' = Turn Right
 * 'L' = Turn Left
 * 'B' = Backward
 * 'S' = Stop
 *
 * Wiring:
 * - Left Motor: IN1=2, IN2=3, ENA=5 (PWM)
 * - Right Motor: IN3=4, IN4=7, ENB=6 (PWM)
 */

// Motor A (Left)
const int ENA = 5;   // PWM speed control
const int IN1 = 2;   // Direction
const int IN2 = 3;   // Direction

// Motor B (Right)
const int ENB = 6;   // PWM speed control
const int IN3 = 4;   // Direction
const int IN4 = 7;   // Direction

const int SPEED = 200;  // Motor speed (0-255)
const int TURN_SPEED = 150;  // Speed for turning

void setup() {
  Serial.begin(9600);

  // Set motor pins as outputs
  pinMode(ENA, OUTPUT);
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  pinMode(ENB, OUTPUT);
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);

  // Start with motors stopped
  stopMotors();

  Serial.println("EEG Wheelchair Ready");
}

void loop() {
  if (Serial.available() > 0) {
    char cmd = Serial.read();

    switch(cmd) {
      case 'F':  // Forward
        forward();
        break;
      case 'R':  // Right
        turnRight();
        break;
      case 'L':  // Left
        turnLeft();
        break;
      case 'B':  // Backward
        backward();
        break;
      case 'S':  // Stop
        stopMotors();
        break;
    }
  }
}

void forward() {
  // Both motors forward
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, LOW);
  analogWrite(ENA, SPEED);
  analogWrite(ENB, SPEED);
}

void backward() {
  // Both motors backward
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, HIGH);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, HIGH);
  analogWrite(ENA, SPEED);
  analogWrite(ENB, SPEED);
}

void turnRight() {
  // Left motor forward, right motor slower/stopped
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  analogWrite(ENA, TURN_SPEED);
  analogWrite(ENB, 0);
}

void turnLeft() {
  // Right motor forward, left motor slower/stopped
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, HIGH);
  digitalWrite(IN4, LOW);
  analogWrite(ENA, 0);
  analogWrite(ENB, TURN_SPEED);
}

void stopMotors() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  analogWrite(ENA, 0);
  analogWrite(ENB, 0);
}
"""

if __name__ == "__main__":
    # Print available ports
    print("Available ports:")
    for p in arduino.list_ports():
        print(f"  {p['port']}: {p['description']}")

    # Print Arduino sketch
    print("\n" + "="*50)
    print("Upload this sketch to your Arduino:")
    print("="*50)
    print(ARDUINO_SKETCH)
