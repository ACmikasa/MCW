/*
 * EEG Wheelchair Motor Control
 *
 * Receives commands via Serial from Python backend:
 * 'F' = Forward (both motors)
 * 'R' = Turn Right (left motor forward, right stopped)
 * 'S' = Stop (all motors off)
 *
 * Motor Configuration (your tested setup):
 * - Left motor: FWD=6, REV=5 (flipped)
 * - Right motor: FWD=9, REV=10 (normal)
 */

// ===================== Motor pins (based on your tests) =====================
// Left motor is flipped:
const int LEFT_REV  = 5;
const int LEFT_FWD  = 6;

// Right motor normal:
const int RIGHT_FWD = 9;
const int RIGHT_REV = 10;

// ===================== Speed tuning =====================
const int PWM_DRIVE = 150;      // Speed for forward movement (0-255)
const int PWM_TURN  = 120;      // Speed for turning

// Current command state
char currentCommand = 'S';  // Start stopped

void setup() {
  Serial.begin(9600);

  pinMode(LEFT_FWD, OUTPUT);
  pinMode(LEFT_REV, OUTPUT);
  pinMode(RIGHT_FWD, OUTPUT);
  pinMode(RIGHT_REV, OUTPUT);

  stopAll();

  Serial.println("EEG Wheelchair Ready - Listening for commands (F/R/S)");
}

void loop() {
  // Check for incoming serial commands
  if (Serial.available() > 0) {
    char cmd = Serial.read();

    // Only process valid commands
    if (cmd == 'F' || cmd == 'R' || cmd == 'S') {
      if (cmd != currentCommand) {
        currentCommand = cmd;
        executeCommand(cmd);
      }
    }
  }
}

void executeCommand(char cmd) {
  switch(cmd) {
    case 'F':
      Serial.println("-> FORWARD");
      driveForward();
      break;
    case 'R':
      Serial.println("-> RIGHT");
      turnRight();
      break;
    case 'S':
      Serial.println("-> STOP");
      stopAll();
      break;
  }
}

// ===================== Movement functions =====================

void driveForward() {
  // Both motors forward at same speed
  setLeft(PWM_DRIVE);
  setRight(PWM_DRIVE);
}

void turnRight() {
  // Left motor forward, right motor stopped (pivot turn)
  setLeft(PWM_TURN);
  setRight(0);
}

// ===================== Motor control (from your tested code) =====================

// signedPWM: + forward, - reverse, 0 stop
void setLeft(int signedPWM) {
  signedPWM = constrain(signedPWM, -255, 255);

  if (signedPWM > 0) {
    analogWrite(LEFT_FWD, signedPWM);
    analogWrite(LEFT_REV, 0);
  } else if (signedPWM < 0) {
    analogWrite(LEFT_FWD, 0);
    analogWrite(LEFT_REV, -signedPWM);
  } else {
    analogWrite(LEFT_FWD, 0);
    analogWrite(LEFT_REV, 0);
  }
}

void setRight(int signedPWM) {
  signedPWM = constrain(signedPWM, -255, 255);

  if (signedPWM > 0) {
    analogWrite(RIGHT_FWD, signedPWM);
    analogWrite(RIGHT_REV, 0);
  } else if (signedPWM < 0) {
    analogWrite(RIGHT_FWD, 0);
    analogWrite(RIGHT_REV, -signedPWM);
  } else {
    analogWrite(RIGHT_FWD, 0);
    analogWrite(RIGHT_REV, 0);
  }
}

void stopAll() {
  analogWrite(LEFT_FWD, 0);
  analogWrite(LEFT_REV, 0);
  analogWrite(RIGHT_FWD, 0);
  analogWrite(RIGHT_REV, 0);
}
