"""
calibrate.py — Hardware Calibration Tool
==========================================
Run this script BEFORE first playback to calibrate:
  1. Stepper steps/mm  (physical ruler measurement)
  2. Servo press angle (visual verification)

Usage:
    python -m Software.utils.calibrate

The script connects to the Arduino, runs each calibration step interactively,
and prints the exact values you should paste into Config/config.py.
"""

import sys
import logging
import time

# Allow running from project root
sys.path.insert(0, ".")

from Config.config import SERIAL_PORT, SERIAL_BAUD
from Software.core.serial_comm import SerialComm

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

DIVIDER = "─" * 60


def main():
    print(f"\n{'═'*60}")
    print("  PIANO ROBOT — HARDWARE CALIBRATION TOOL")
    print(f"{'═'*60}\n")
    print(f"Connecting to Arduino on {SERIAL_PORT} ...\n")

    comm = SerialComm(port=SERIAL_PORT, baud=SERIAL_BAUD)
    if not comm.connect():
        print(f"ERROR: Could not connect to {SERIAL_PORT}")
        print("Check: USB cable, Device Manager COM port, correct port in config.py")
        sys.exit(1)

    print("Connected.\n")

    # ── Step 1: Ping test ────────────────────────────────────────────────────
    print(DIVIDER)
    print("STEP 1 — Connection health check")
    print(DIVIDER)
    if comm.send_ping():
        print("✓ PING/PONG OK\n")
    else:
        print("✗ PING failed. Check firmware is uploaded and baud rate matches.")
        comm.disconnect()
        sys.exit(1)

    # ── Step 2: Homing ───────────────────────────────────────────────────────
    print(DIVIDER)
    print("STEP 2 — Homing both arms")
    print(DIVIDER)
    print("Both arms will move slowly toward the motor end and retract 10 mm.")
    print("Make sure the rails are clear of obstacles.\n")
    input("Press ENTER to begin homing...")

    if not comm.send_home_command():
        print("✗ Homing failed. Check wiring and try again.")
        comm.disconnect()
        sys.exit(1)
    print("✓ Homing complete\n")

    # ── Step 3: Stepper calibration ──────────────────────────────────────────
    print(DIVIDER)
    print("STEP 3 — Stepper steps/mm calibration")
    print(DIVIDER)
    print("Arm A will move EXACTLY 100 mm (according to current config).")
    print("Measure the actual physical movement with a ruler.\n")
    input("Place a ruler next to Arm A, mark zero position. Press ENTER...")

    comm.send_calibration_command()
    print("\nMeasure how far Arm A actually moved.")

    while True:
        try:
            measured = float(input("Actual measured distance (mm): "))
            break
        except ValueError:
            print("Please enter a number.")

    current_spm = 5.0  # default
    try:
        with open("Config/config.py") as f:
            for line in f:
                if "STEPS_PER_MM" in line and "=" in line and "#" not in line.split("=")[0]:
                    current_spm = float(line.split("=")[1].split("#")[0].strip())
    except Exception:
        pass

    if measured <= 0:
        print("Invalid measurement.")
    else:
        corrected = current_spm * 100.0 / measured
        print(f"\n  Current STEPS_PER_MM : {current_spm:.4f}")
        print(f"  Measured movement    : {measured:.1f} mm")
        print(f"  ✓ Corrected value    : {corrected:.4f}")
        print(f"\n  ➜ Set STEPS_PER_MM = {corrected:.4f} in Config/config.py\n")

    # ── Step 4: Servo angle calibration ─────────────────────────────────────
    print(DIVIDER)
    print("STEP 4 — Servo press angle calibration")
    print(DIVIDER)
    print("Testing finger 1 on Arm A at angles 50–90°.")
    print("Watch the finger and find the angle that just depresses a key.\n")
    input("Press ENTER to begin angle sweep...")

    best_angle = 75
    for angle in [50, 60, 70, 75, 80, 85, 90]:
        # Sends the real angle to the firmware via TESTANGLE so this step
        # actually tests the angle being printed (previously this always
        # pressed at the fixed SERVO_ANGLE_PRESS firmware constant regardless
        # of which angle the loop claimed to be testing).
        comm.send_test_angle(angle)
        print(f"  Testing angle: {angle}° — does the finger press the key? (y/n)")
        ans = input("  → ").strip().lower()
        time.sleep(0.3)
        if ans == "y":
            best_angle = angle
            print(f"  ✓ First working angle: {angle}°")
            break

    print(f"\n  ➜ Set SERVO_ANGLE_PRESS = {best_angle} in Config/config.py")
    print("    Then also set it in SERVO_ANGLE_PRESS in the Arduino firmware.\n")

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"{'═'*60}")
    print("  CALIBRATION COMPLETE — update Config/config.py with:")
    print(f"{'═'*60}")
    print(f"  STEPS_PER_MM      = <your corrected value>")
    print(f"  SERVO_ANGLE_PRESS = {best_angle}")
    print(f"{'═'*60}\n")

    comm.disconnect()


if __name__ == "__main__":
    main()
