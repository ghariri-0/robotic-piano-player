"""
firmware_sim.py — Software model of PianoRobot_Firmware.ino
=============================================================
Speaks the same serial protocol as the Arduino firmware, so SerialComm and
the Scheduler can run end-to-end without hardware:

    from tools.firmware_sim import FirmwareSim
    comm = SerialComm("SIM", serial_factory=lambda *a: FirmwareSim(time_scale=0))

It also keeps a timeline of carriage moves and finger presses (in robot
time), which `tools/visualize.py` turns into an animation.

time_scale = 1.0 runs in real time, 0 runs instantly (for tests).
"""
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Config.config import (  # noqa: E402
    STEPS_PER_MM, STEPPER_MAX_SPEED, STEPPER_ACCEL, RAIL_A_MAX_MM, RAIL_B_MAX_MM,
    RAIL_SAFE_MARGIN_MM, SERVO_SETTLE_MS, HOMING_RETRACT_MM,
)


def move_time_s(steps: float, vmax: float = STEPPER_MAX_SPEED, accel: float = STEPPER_ACCEL) -> float:
    """Duration of an AccelStepper trapezoidal (or triangular) move."""
    d = abs(steps)
    if d == 0:
        return 0.0
    d_ramp = vmax * vmax / accel           # distance to accelerate + decelerate
    if d < d_ramp:                         # triangular profile
        return 2.0 * math.sqrt(d / accel)
    return d / vmax + vmax / accel


class FirmwareSim:
    def __init__(self, time_scale: float = 0.0):
        self.time_scale = time_scale
        self.timeout = 1.0
        self.is_open = True
        self.homed = False
        self.pos_steps = {'A': 0, 'B': 0}
        self.pressed = set()               # (arm, finger)
        self.clock = 0.0                   # simulated robot time (s)
        self.timeline = []                 # (t_start, t_end, arm, x_from, x_to)
        self.presses = []                  # (t, arm, x_mm, finger, action)
        self._out = []
        self._out.append("BOOT_OK")
        self._out.append("ERROR:NOT_HOMED — send H command before playback")

    # ── serial-like interface ────────────────────────────────────────────────
    def reset_input_buffer(self):
        self._out.clear()

    def close(self):
        self.is_open = False

    def readline(self) -> bytes:
        return (self._out.pop(0) + "\n").encode() if self._out else b""

    def write(self, data: bytes):
        for line in data.decode().splitlines():
            self._handle(line.strip())

    # ── protocol ─────────────────────────────────────────────────────────────
    def _elapse(self, seconds: float):
        self.clock += seconds
        if self.time_scale > 0:
            time.sleep(seconds * self.time_scale)

    def _handle(self, cmd: str):
        if not cmd:
            return
        if cmd in ("H", "HOME"):
            self._out.append("HOMING_START")
            self.pressed.clear()
            for arm in "AB":
                self._elapse(2.0)
                self.pos_steps[arm] = 0
            self.homed = True
            self._out.append("HOMED")
            return
        if cmd == "PING":
            self._out.append("PONG"); return
        if cmd == "RESTALL":
            self.pressed.clear()
            self._out += ["RESTALL_OK", "READY"]; return
        if cmd == "STATUS":
            self._out.append(f"STATUS:homed={'YES' if self.homed else 'NO'},"
                             f"posA_mm={self.pos_steps['A']/STEPS_PER_MM:.2f},"
                             f"posB_mm={self.pos_steps['B']/STEPS_PER_MM:.2f},"
                             f"steps_per_mm={STEPS_PER_MM:.2f}")
            return
        if not self.homed:
            self._out.append("ERROR:NOT_HOMED"); return
        try:
            arm, x, servo, action = cmd.split(",")
            x = float(x); servo = int(servo)
        except ValueError:
            self._out.append("ERROR:BAD_FORMAT"); return
        if arm not in "AB" or action not in "PRM":
            self._out.append("ERROR:BAD_ARM"); return
        rail_max = RAIL_A_MAX_MM if arm == 'A' else RAIL_B_MAX_MM
        x = min(max(x, RAIL_SAFE_MARGIN_MM), rail_max - RAIL_SAFE_MARGIN_MM)
        target = round(x * STEPS_PER_MM)
        d = target - self.pos_steps[arm]
        if d:
            if any(a == arm for a, _ in self.pressed):
                self._out.append(f"ERROR:MOVED_WITH_FINGER_DOWN_{arm}")   # sim-only check
            t0 = self.clock
            self._elapse(move_time_s(d))
            self.timeline.append((t0, self.clock, arm,
                                  self.pos_steps[arm] / STEPS_PER_MM, target / STEPS_PER_MM))
            self.pos_steps[arm] = target
        if 1 <= servo <= 5 and action != 'M':
            if action == 'P':
                self.pressed.add((arm, servo))
            else:
                self.pressed.discard((arm, servo))
            self.presses.append((self.clock, arm, target / STEPS_PER_MM, servo, action))
            self._elapse(SERVO_SETTLE_MS / 1000.0)
        self._out.append("READY")
