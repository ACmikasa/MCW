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
