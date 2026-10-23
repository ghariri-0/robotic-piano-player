// =============================================================================
// PianoRobot_Firmware.ino
// =============================================================================
// Arduino UNO R4 Minima — Piano Robot Controller
//
// Hardware:
//   • 2 × NEMA 17 stepper motors via A4988 drivers
//   • 10 × MG90S servo motors via PCA9685 I2C driver (address 0x40)
//   • No limit switches — software homing via slow crawl
//
// Serial Protocol (115200 baud):
//   Receive:  "<ARM>,<X_MM>,<SERVO>,<ACTION>\n"
//             ARM    = A or B
//             X_MM   = float, carriage position in mm from home (where finger 1 sits)
//             SERVO  = 0–5  (0 = move-only, 1–5 = finger number)
//             ACTION = P (press) | R (release) | M (move only) | H (home)
//             "RESTALL\n" — force every finger to rest position immediately
//             (safety command, valid at any time, homed or not)
//             "TESTANGLE,<deg>\n" — CALIBRATION ONLY: presses finger 1 of
//             Arm A at the given angle (0-180), holds briefly, then releases.
//             Lets utils/calibrate.py actually sweep real angles instead of
//             always pressing at the fixed SERVO_ANGLE_PRESS constant. Not
//             used during normal playback.
//   Send:     "READY\n"  — after completing every command
//             "ERROR:<msg>\n" — on fault
//             "HOMED\n"  — after homing completes
//             "RESTALL_OK\n" — after a RESTALL command completes
//             "TESTANGLE_OK\n" — after a TESTANGLE command completes
//             "ERROR:AUTO_RELEASE_CH<n>\n" — a servo was force-released after
//             exceeding SERVO_MAX_HOLD_MS without a matching RELEASE command
//
// Dependencies (install via Arduino Library Manager):
//   • AccelStepper by Mike McCauley
//   • Adafruit PWM Servo Driver Library
//   • Wire (built-in)
// =============================================================================

#include <Wire.h>
#include <AccelStepper.h>
#include <Adafruit_PWMServoDriver.h>

// ── Pin Definitions ──────────────────────────────────────────────────────────
// Stepper A (Left rail) — Step pin 2, Dir pin 3
// Stepper B (Right rail) — Step pin 5, Dir pin 4  (swapped to fix direction)
#define STEP_A   2
#define DIR_A    3
#define STEP_B   5
#define DIR_B    4

// ── Calibration Constants ────────────────────────────────────────────────────
// ⚠ TUNE THESE to match your physical hardware
// Run the calibration routine (send "CAL\n") to verify steps/mm
const float STEPS_PER_MM     = 5.0;    // Recalibrate with CAL command
const float RAIL_A_MAX_MM    = 280.0;
const float RAIL_B_MAX_MM    = 400.0;
const float SAFE_MARGIN_MM   = 5.0;    // Never go closer than this to 0 or MAX

// Stepper motion profile
// NOTE: reduced from 2000/800 — lower speed/accel pulls less peak current per
// move. Keep these in sync with Config/config.py (the two are NOT
// auto-synced; this is a manual mirror, same as before).
const float MAX_SPEED         = 1200.0; // steps/sec
const float ACCELERATION      = 400.0;  // steps/sec²
const float HOMING_SPEED      = 200.0;  // steps/sec (slow + safe)
const float HOMING_ACCEL      = 100.0;
// Crawl a little further than each rail is long, so homing reaches the frame
// from ANY start position (Rail B is 400 mm, so a fixed 310 mm crawl is not enough).
const float HOMING_OVERTRAVEL_MM = 20.0;
const float HOMING_RETRACT_MM = 10.0;

// Servo constants
const int   SERVO_FREQ_HZ    = 50;
const int   SERVO_ANGLE_REST  = 0;    // degrees — finger raised
const int   SERVO_ANGLE_PRESS = 75;   // degrees — key depressed  ⚠ TUNE THIS
const int   SERVO_SETTLE_MS   = 60;   // mechanical settle time

// ── Power-Stress Protection Constants ────────────────────────────────────────
// ⚠ These reduce electrical/thermal stress. They CANNOT lower the voltage a
// servo motor sees — that is set entirely by the rail feeding PCA9685 V+.
// If that rail is 12V while these are 6V-rated MG90S servos, the servos are
// still being overvoltted every time they move. See README "Power
// Requirements" for the real fix (separate 5-6V servo rail regulator).
const unsigned long SERVO_STAGGER_MS   = 15;    // spacing between multi-servo writes
const unsigned long SERVO_MAX_HOLD_MS  = 3000;  // force-release ceiling per channel

// PCA9685 channel mapping
// Channels 0–4 = Left hand (Arm A), fingers 1–5
// Channels 5–9 = Right hand (Arm B), fingers 1–5
const int CHANNELS_A[5] = {0, 1, 2, 3, 4};
const int CHANNELS_B[5] = {5, 6, 7, 8, 9};

// ── Objects ──────────────────────────────────────────────────────────────────
AccelStepper stepperA(AccelStepper::DRIVER, STEP_A, DIR_A);
AccelStepper stepperB(AccelStepper::DRIVER, STEP_B, DIR_B);
Adafruit_PWMServoDriver pca = Adafruit_PWMServoDriver(0x40);

// ── State ────────────────────────────────────────────────────────────────────
bool isHomed = false;

// Per-channel press tracking for SERVO_MAX_HOLD_MS safety timeout.
// Index 0-9 maps directly to PCA9685 channels 0-9 (CHANNELS_A + CHANNELS_B).
bool          channelPressed[10]   = {false};
unsigned long channelPressedAt[10] = {0};

// ── Setup ────────────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  while (!Serial) { ; }   // Wait for USB serial on R4

  // Stepper configuration
  stepperA.setMaxSpeed(MAX_SPEED);
  stepperA.setAcceleration(ACCELERATION);
  stepperB.setMaxSpeed(MAX_SPEED);
  stepperB.setAcceleration(ACCELERATION);

  // PCA9685 initialisation
  Wire.begin();
  pca.begin();
  pca.setPWMFreq(SERVO_FREQ_HZ);
  delay(10);

  // Park all fingers in rest position
  // Settle delay: gives a marginal supply time to stabilize after the Arduino
  // reset / USB enumeration before the first multi-servo current draw.
  delay(50);
  raiseAllFingers();
  delay(800);

  Serial.println("BOOT_OK");
  Serial.println("ERROR:NOT_HOMED — send H command before playback");
}

// ── Main Loop ────────────────────────────────────────────────────────────────
void loop() {
  // Safety check runs every loop iteration regardless of serial traffic —
  // a servo must never stay energized past SERVO_MAX_HOLD_MS, even if the
  // PC never sends the matching RELEASE (e.g. comms drop mid-song).
  checkServoSafety();

  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) return;

    // Special single-character commands
    if (cmd == "H" || cmd == "HOME") {
      performHoming();
      return;
    }
    if (cmd == "CAL") {
      performCalibrationMove();
      return;
    }
    if (cmd == "PING") {
      Serial.println("PONG");
      return;
    }
    if (cmd == "STATUS") {
      printStatus();
      return;
    }
    if (cmd == "RESTALL") {
      // Protective command: force every finger to rest immediately.
      // Safe to call at any time, homed or not.
      raiseAllFingers();
      Serial.println("RESTALL_OK");
      Serial.println("READY");
      return;
    }
    if (cmd.startsWith("TESTANGLE")) {
      // Calibration-only: press finger 1 / Arm A at an arbitrary angle so
      // utils/calibrate.py can sweep real angles instead of always testing
      // the fixed SERVO_ANGLE_PRESS constant. Not used during playback.
      int comma = cmd.indexOf(',');
      int deg = (comma >= 0) ? cmd.substring(comma + 1).toInt() : SERVO_ANGLE_PRESS;
      deg = constrain(deg, 0, 180);
      setServoAngle(CHANNELS_A[0], deg, true);
      delay(500);
      setServoAngle(CHANNELS_A[0], SERVO_ANGLE_REST, false);
      Serial.println("TESTANGLE_OK");
      Serial.println("READY");
      return;
    }

    // Guard: refuse playback commands until homed
    if (!isHomed) {
      Serial.println("ERROR:NOT_HOMED");
      return;
    }

    parseAndExecute(cmd);
  }
}

// ── Command Parser ───────────────────────────────────────────────────────────
// Format: "A,150.0,3,P"
void parseAndExecute(String cmd) {
  // Validate minimum length and arm character
  if (cmd.length() < 7) { Serial.println("ERROR:CMD_TOO_SHORT"); return; }

  char arm = cmd.charAt(0);
  if (arm != 'A' && arm != 'B') { Serial.println("ERROR:BAD_ARM"); return; }

  int c1 = cmd.indexOf(',');
  int c2 = cmd.indexOf(',', c1 + 1);
  int c3 = cmd.indexOf(',', c2 + 1);
  if (c1 < 0 || c2 < 0 || c3 < 0) { Serial.println("ERROR:BAD_FORMAT"); return; }

  float  x_mm      = cmd.substring(c1 + 1, c2).toFloat();
  int    servo_num  = cmd.substring(c2 + 1, c3).toInt();
  char   action     = cmd.charAt(c3 + 1);

  // Clamp position to safe rail limits
  float rail_max = (arm == 'A') ? RAIL_A_MAX_MM : RAIL_B_MAX_MM;
  x_mm = constrain(x_mm, SAFE_MARGIN_MM, rail_max - SAFE_MARGIN_MM);

  // ── Step 1: Move carriage to target position ──────────────────────────────
  long target_steps = mmToSteps(x_mm);

  // NOTE: checkServoSafety() is called inside these loops (not just once per
  // loop() iteration) because a long carriage move can otherwise consume the
  // entire SERVO_MAX_HOLD_MS budget for a finger pressed just before the
  // move, with the watchdog never getting a chance to run during that time.
  if (arm == 'A') {
    stepperA.moveTo(target_steps);
    while (stepperA.distanceToGo() != 0) { stepperA.run(); checkServoSafety(); }
  } else {
    stepperB.moveTo(target_steps);
    while (stepperB.distanceToGo() != 0) { stepperB.run(); checkServoSafety(); }
  }

  // ── Step 2: Actuate finger (skip if move-only) ───────────────────────────
  if (servo_num >= 1 && servo_num <= 5 && action != 'M') {
    int idx     = servo_num - 1;
    int channel = (arm == 'A') ? CHANNELS_A[idx] : CHANNELS_B[idx];
    bool isPress = (action == 'P');
    int angle   = isPress ? SERVO_ANGLE_PRESS : SERVO_ANGLE_REST;
    setServoAngle(channel, angle, isPress);
    delay(SERVO_SETTLE_MS);
  }

  // ── Step 3: Handshake ────────────────────────────────────────────────────
  Serial.println("READY");
}

// ── Homing Routine (Software Only — No Limit Switches) ───────────────────────
// Strategy: move each arm slowly toward the motor end (negative direction)
// for a fixed large distance. The carriage hits the frame gently at low speed.
// Then retract by HOMING_RETRACT_MM and define that as position 0.
void performHoming() {
  Serial.println("HOMING_START");
  raiseAllFingers();
  delay(200);

  // Home Arm A
  stepperA.setMaxSpeed(HOMING_SPEED);
  stepperA.setAcceleration(HOMING_ACCEL);
  stepperA.move(-mmToSteps(RAIL_A_MAX_MM + HOMING_OVERTRAVEL_MM));
  while (stepperA.distanceToGo() != 0) { stepperA.run(); checkServoSafety(); }
  stepperA.setCurrentPosition(0);
  delay(100);

  // Retract A
  long retract = mmToSteps(HOMING_RETRACT_MM);
  stepperA.moveTo(retract);
  while (stepperA.distanceToGo() != 0) { stepperA.run(); checkServoSafety(); }
  stepperA.setCurrentPosition(0);

  // Home Arm B
  stepperB.setMaxSpeed(HOMING_SPEED);
  stepperB.setAcceleration(HOMING_ACCEL);
  stepperB.move(-mmToSteps(RAIL_B_MAX_MM + HOMING_OVERTRAVEL_MM));
  while (stepperB.distanceToGo() != 0) { stepperB.run(); checkServoSafety(); }
  stepperB.setCurrentPosition(0);
  delay(100);

  // Retract B
  stepperB.moveTo(retract);
  while (stepperB.distanceToGo() != 0) { stepperB.run(); checkServoSafety(); }
  stepperB.setCurrentPosition(0);

  // Restore normal motion profile
  stepperA.setMaxSpeed(MAX_SPEED);
  stepperA.setAcceleration(ACCELERATION);
  stepperB.setMaxSpeed(MAX_SPEED);
  stepperB.setAcceleration(ACCELERATION);

  isHomed = true;
  Serial.println("HOMED");
}

// ── Calibration Move (100 mm reference move) ─────────────────────────────────
// Send "CAL" to move Arm A exactly 100 mm forward.
// Measure the physical movement with a ruler.
// If the arm moves X mm instead of 100 mm, the true STEPS_PER_MM = (STEPS_PER_MM × 100) / X
void performCalibrationMove() {
  if (!isHomed) { Serial.println("ERROR:NOT_HOMED"); return; }
  Serial.println("CAL_MOVING_100MM");
  long steps = (long)(100.0 * STEPS_PER_MM);
  stepperA.moveTo(steps);
  while (stepperA.distanceToGo() != 0) { stepperA.run(); checkServoSafety(); }
  Serial.println("CAL_DONE — measure arm A physical displacement then adjust STEPS_PER_MM");
  Serial.println("READY");
}

// ── Status Report ────────────────────────────────────────────────────────────
void printStatus() {
  Serial.print("STATUS:homed="); Serial.print(isHomed ? "YES" : "NO");
  Serial.print(",posA_mm=");     Serial.print(stepsToMm(stepperA.currentPosition()), 2);
  Serial.print(",posB_mm=");     Serial.print(stepsToMm(stepperB.currentPosition()), 2);
  Serial.print(",steps_per_mm=");Serial.println(STEPS_PER_MM, 2);
}

// ── Servo Helpers ─────────────────────────────────────────────────────────────
// Convert degrees (0–180) to PCA9685 PWM pulse count
// Typical hobby servo: 1 ms (min) to 2 ms (max) pulse width at 50 Hz
// At 50 Hz, period = 20 ms = 4096 counts
// 1 ms = 4096/20 = 204 counts | 2 ms = 409 counts
//
// channel is 0–9 and doubles directly as the index into channelPressed[]/
// channelPressedAt[] (CHANNELS_A = 0-4, CHANNELS_B = 5-9, both contiguous).
void setServoAngle(int channel, int degrees, bool isPress) {
  degrees = constrain(degrees, 0, 180);
  // Map 0–180° to pulse counts 102–512 (0.5 ms to 2.5 ms — wider range for MG90S)
  int pulse = map(degrees, 0, 180, 102, 512);
  pca.setPWM(channel, 0, pulse);

  if (channel >= 0 && channel < 10) {
    if (isPress) {
      channelPressed[channel]   = true;
      channelPressedAt[channel] = millis();
    } else {
      channelPressed[channel] = false;
    }
  }
}

// Park every finger at rest. Staggered with SERVO_STAGGER_MS between writes
// instead of firing all 10 simultaneously — reduces the peak current spike
// at boot/homing/RESTALL, which matters most on a marginal or overvoltage
// supply where simultaneous inrush is the likeliest trigger for a brownout
// or thermal event.
void raiseAllFingers() {
  for (int i = 0; i < 5; i++) {
    setServoAngle(CHANNELS_A[i], SERVO_ANGLE_REST, false);
    delay(SERVO_STAGGER_MS);
    setServoAngle(CHANNELS_B[i], SERVO_ANGLE_REST, false);
    delay(SERVO_STAGGER_MS);
  }
}

// Force-release any servo that has been held in the PRESS state longer than
// SERVO_MAX_HOLD_MS. Called every loop() iteration. This is a pure software
// safety net against a stuck press (e.g. comms drop before the matching
// RELEASE arrives) — it does NOT compensate for overvoltage, it only
// guarantees nothing stays energized/loaded indefinitely.
void checkServoSafety() {
  unsigned long now = millis();
  for (int ch = 0; ch < 10; ch++) {
    if (channelPressed[ch] && (now - channelPressedAt[ch] > SERVO_MAX_HOLD_MS)) {
      int pulse = map(SERVO_ANGLE_REST, 0, 180, 102, 512);
      pca.setPWM(ch, 0, pulse);
      channelPressed[ch] = false;
      Serial.print("ERROR:AUTO_RELEASE_CH");
      Serial.println(ch);
    }
  }
}

// ── Unit Conversion ──────────────────────────────────────────────────────────
long mmToSteps(float mm) {
  return lround(mm * STEPS_PER_MM);   // round, not truncate (truncation adds up to 1 step of error per move)
}

float stepsToMm(long steps) {
  return (float)steps / STEPS_PER_MM;
}
