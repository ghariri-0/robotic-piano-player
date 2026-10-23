# =============================================================================
# config.py — PianoRobot Master Configuration
# =============================================================================
# ALL hardware constants, tuning values, and software parameters live here.
# Never hardcode these values anywhere else. Change them once, here.
# =============================================================================
#
# ⚠ POWER RAIL WARNING — READ BEFORE CHANGING ANY VALUE BELOW ⚠
# -----------------------------------------------------------------------------
# This project's hardware design assumes a 6 V rail feeding BOTH the A4988
# stepper drivers (VMOT) and the PCA9685 servo rail (V+). The 10x MG90S
# servos are rated 4.8-6 V.
#
# If your supply is actually set to 12 V, the servos are being run at roughly
# 2x their rated voltage. The constants below (SERVO_STAGGER_MS,
# SERVO_MAX_HOLD_MS, reduced stepper speed/accel) reduce electrical and
# thermal STRESS on the system — they do NOT and CANNOT reduce the voltage a
# servo motor actually sees, because the PCA9685 PWM signal only sets target
# angle (pulse width), not supply voltage. No software change can fix an
# overvoltage condition. See docs/power_notes.md for the explanation and a
# suggested hardware fix (separate 5-6 V regulator for the servo rail). Treat everything below as damage mitigation, not a cure.
# =============================================================================

# ── Serial Communication ──────────────────────────────────────────────────────
SERIAL_PORT         = "COM4"        # Windows COM port — change to match your Device Manager
SERIAL_BAUD         = 115200        # Must match Arduino firmware
SERIAL_TIMEOUT_S    = 10.0          # Max seconds to wait for READY before declaring fault
SERIAL_CONNECT_WAIT = 2.5           # Seconds to wait after opening port (Arduino reset time)

# ── Stepper Motor Calibration ────────────────────────────────────────────────
# Formula:  steps_per_mm = (motor_steps × microstepping) / (pulley_teeth × belt_pitch_mm)
# NEMA 17 = 200 steps/rev | A4988 default = no microstepping (x1) | GT2 pulley 20T | pitch 2mm
# Full-step: 200 / (20 × 2) = 5.0 steps/mm
# Use the calibration tool (python -m Software.utils.calibrate) to find YOUR actual value.
STEPS_PER_MM        = 5.0           # ⚠ UNVERIFIED — run calibration tool first

STEPPER_MAX_SPEED   = 1200          # steps/sec — reduced from 2000. Lower speed/accel
                                     # pulls less peak current per move, which matters
                                     # more on a 12V rail where the A4988 is dissipating
                                     # roughly 4x the power it would at 6V for the same
                                     # current setting. Re-raise only after confirming
                                     # stable operation (see README troubleshooting).
STEPPER_ACCEL       = 400           # steps/sec² — reduced from 800. Gentler accel
                                     # reduces the current spike at each direction
                                     # change/start, which is when steppers draw the most.

# Rail physical limits (measured in mm from home/zero position)
RAIL_A_MAX_MM       = 280.0         # Left arm rail length
RAIL_B_MAX_MM       = 400.0         # Right arm rail length
RAIL_SAFE_MARGIN_MM = 5.0           # Buffer before each hard end — never command to 0 or MAX

# ── Servo Calibration ────────────────────────────────────────────────────────
# PCA9685 I2C address (default 0x40 — check solder pads if using multiple boards)
PCA9685_ADDRESS     = 0x40
PCA9685_FREQ_HZ     = 50            # Standard 50 Hz PWM for hobby servos

# Angles — tune these with the calibration tool before full playback
SERVO_ANGLE_REST    = 0             # Finger fully raised (safe position)
SERVO_ANGLE_PRESS   = 75            # Finger depressed onto key — TUNE THIS
SERVO_SETTLE_MS     = 60            # Milliseconds to wait after servo command (mechanical settle)

# ── Power-Stress Protection (software mitigation only — see warning above) ──
# These do NOT fix overvoltage. They reduce how much current/heat the system
# generates per unit of operation, and they fail safe instead of fail stuck.
SERVO_STAGGER_MS      = 15          # Min. spacing between individual servo PWM writes
                                     # when multiple servos move together (e.g. boot
                                     # park, rest-all). Prevents 10 servos from drawing
                                     # their startup current spike simultaneously.
SERVO_MAX_HOLD_MS      = 3000        # Hard ceiling on how long any single servo may
                                     # stay in the PRESS (energized/loaded) state.
                                     # Firmware force-releases past this — protects
                                     # against a stuck/lost-release command holding a
                                     # servo loaded indefinitely at overvoltage.
MAX_CONSECUTIVE_SERIAL_ERRORS = 3   # Abort playback and force all-rest after this
                                     # many consecutive command failures — repeated
                                     # failures are a strong indicator of brownout or
                                     # thermal shutdown, not a one-off glitch worth
                                     # retrying through.

# ── PCA9685 Channel Mapping ───────────────────────────────────────────────────
# Left hand (Arm A): channels 0–4 → servo fingers 1–5
# Right hand (Arm B): channels 5–9 → servo fingers 1–5
SERVO_CHANNELS_A    = [0, 1, 2, 3, 4]   # PCA9685 channels for left hand
SERVO_CHANNELS_B    = [5, 6, 7, 8, 9]   # PCA9685 channels for right hand

# ── Hand Geometry ────────────────────────────────────────────────────────────
# Each hand has 5 fingers side by side, one white key apart. The carriage
# position x_mm is where FINGER 1 sits; finger n sits (n-1) × spacing further.
# HAND_OFFSET_*_MM = carriage position (after homing) at which finger 1 is
# exactly above the FIRST key of that arm's key list. ⚠ MEASURE THIS.
FINGERS_PER_HAND    = 5
FINGER_SPACING_MM   = 23.5          # = KEY_WIDTH_MM for one finger per white key
HAND_OFFSET_A_MM    = 5.0           # ⚠ UNVERIFIED — measure on the robot
HAND_OFFSET_B_MM    = 5.0           # ⚠ UNVERIFIED — measure on the robot
PLANNER_LOOKAHEAD   = 6             # upcoming notes considered when choosing a hand position

# ── MIDI / Piano Key Mapping ─────────────────────────────────────────────────
KEY_WIDTH_MM        = 23.5          # Standard piano white key width in mm

# MIDI note IDs assigned to left arm (Rail A) — white keys only
LEFT_WHITE_KEYS     = [48, 50, 52, 53, 55, 57, 59, 60, 62, 64]

# MIDI note IDs assigned to right arm (Rail B) — white keys only
RIGHT_WHITE_KEYS    = [65, 67, 69, 71, 72, 74, 76, 77, 79, 81, 83]

# Combined reference set (used for fast 'in' checks)
ALL_PLAYABLE_KEYS   = set(LEFT_WHITE_KEYS + RIGHT_WHITE_KEYS)

# ── Homing (Software-Only, No Limit Switches) ────────────────────────────────
# On startup, both arms move slowly toward their physical stop until current
# spikes (detected by timeout or fixed slow-move distance), then back off.
# Since we have NO limit switches, we use a timed slow crawl + fixed retract.
HOMING_SPEED        = 200           # steps/sec — slow and safe
HOMING_ACCEL        = 100           # steps/sec²
HOMING_OVERTRAVEL_MM = 20           # Crawl rail length + this, so homing works from any start position
HOMING_RETRACT_MM   = 10.0          # Pull back from frame after homing
HOMING_TIMEOUT_S    = 30            # Abort homing if it takes longer than this

# ── Safety ───────────────────────────────────────────────────────────────────
MIN_ARM_SEPARATION_MM = 0.0         # Independent rails — no separation needed (set >0 for caution)
EMERGENCY_STOP_KEY    = "q"         # Keyboard key to abort playback in terminal
