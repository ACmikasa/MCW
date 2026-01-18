//this is an arduino file ran on the arduino component individually.
// ===================== Motor pins (based on your tests) =====================
const int LEFT_REV  = 5;
const int LEFT_FWD  = 6;
const int RIGHT_FWD = 9;
const int RIGHT_REV = 10;

// ===================== Tuning =====================
const int PWM_MAX = 170;
const int PWM_MIN = 45;

// Gentle ramp rates (fragile prints)
const float ACCEL_PWM_PER_S = 140.0;  // ramp up speed
const float DECEL_PWM_PER_S = 80.0;   // ramp down slower (gentler)

// "Hold-to-run" tolerance (ms)
const unsigned long HOLD_TOL_MS = 500;

// Control loop
const unsigned long LOOP_DT_MS = 10;

enum Mode { STOPPED, FORWARD, SPIN_RIGHT };
Mode mode = STOPPED;

unsigned long lastCmdTime = 0;

// Current PWM outputs (signed: +fwd, -rev)
int curL = 0;
int curR = 0;

void setup() {
  Serial.begin(115200);

  pinMode(LEFT_FWD, OUTPUT);
  pinMode(LEFT_REV, OUTPUT);
  pinMode(RIGHT_FWD, OUTPUT);
  pinMode(RIGHT_REV, OUTPUT);

  stopAll();
  lastCmdTime = millis();
  Serial.println("READY: send F, R (repeat to hold), S");
}

void loop() {
  // 1) Read ALL incoming bytes quickly
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r' || c == ' ') continue;

    if (c == 'F') {
      mode = FORWARD;
      lastCmdTime = millis();
    } else if (c == 'R') {
      mode = SPIN_RIGHT;
      lastCmdTime = millis();
    } else if (c == 'S') {
      mode = STOPPED;
      lastCmdTime = millis();
    }
  }

  // 2) If we haven't heard a command recently, stop (tolerance)
  if (mode != STOPPED && (millis() - lastCmdTime) > HOLD_TOL_MS) {
    mode = STOPPED;
  }

  // 3) Compute target PWMs for the mode
  int targetL = 0, targetR = 0;

  if (mode == FORWARD) {
    targetL = +PWM_MAX;
    targetR = +PWM_MAX;
  } else if (mode == SPIN_RIGHT) {
    targetL = +PWM_MAX;
    targetR = -PWM_MAX;
  } else {
    targetL = 0;
    targetR = 0;
  }

  // 4) Ramp current PWMs toward targets gently
  curL = rampTo(curL, targetL, LOOP_DT_MS / 1000.0f);
  curR = rampTo(curR, targetR, LOOP_DT_MS / 1000.0f);

  // 5) Apply motor outputs
  setLeft(curL);
  setRight(curR);

  delay(LOOP_DT_MS);
}

// ===================== Ramping =====================
int rampTo(int cur, int target, float dt) {
  float rate = (abs(target) < abs(cur)) ? DECEL_PWM_PER_S : ACCEL_PWM_PER_S;
  int step = (int)(rate * dt);
  if (step < 1) step = 1;

  if (cur < target) cur = min(cur + step, target);
  else if (cur > target) cur = max(cur - step, target);

  // ✅ If we're trying to stop, allow cur to reach 0 (do NOT apply PWM_MIN)
  if (target == 0) {
    if (abs(cur) < PWM_MIN) cur = 0;   // snap to zero near the end
    return cur;
  }

  // Enforce PWM_MIN only when we actually want motion
  if (cur != 0) {
    int a = abs(cur);
    if (a < PWM_MIN) a = PWM_MIN;
    if (a > PWM_MAX) a = PWM_MAX;
    cur = (cur > 0) ? a : -a;
  }

  return cur;
}


// ===================== Motor output (one PWM per motor at a time) =====================
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
