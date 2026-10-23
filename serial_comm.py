"""
serial_comm.py — Arduino Serial Communication Module
=====================================================
Handles all low-level serial communication with the Arduino firmware.
Encapsulates connection management, command sending, handshake waiting,
and error recovery. All higher-level modules import this and never touch
pyserial directly.
"""

import serial
import time
import logging
from typing import Optional
from Config.config import (
    SERIAL_PORT, SERIAL_BAUD, SERIAL_TIMEOUT_S, SERIAL_CONNECT_WAIT,
    MAX_CONSECUTIVE_SERIAL_ERRORS,
)

log = logging.getLogger(__name__)


class SerialComm:
    """Thread-safe serial link to the Arduino firmware."""

    def __init__(self, port: str = SERIAL_PORT, baud: int = SERIAL_BAUD, serial_factory=None):
        """
        serial_factory: optional callable(port, baud, timeout) returning a
        serial-like object. Used by tests and the simulator; defaults to
        pyserial's serial.Serial.
        """
        self.port    = port
        self.baud    = baud
        self._serial_factory = serial_factory
        self._serial: Optional[serial.Serial] = None
        self._connected = False

    # ── Connection Management ────────────────────────────────────────────────

    def connect(self) -> bool:
        """
        Open the serial port and wait for the Arduino to boot.
        Returns True on success, False on failure.
        """
        try:
            if self._serial_factory:
                self._serial = self._serial_factory(self.port, self.baud, SERIAL_TIMEOUT_S)
            else:
                self._serial = serial.Serial(
                    port     = self.port,
                    baudrate = self.baud,
                    timeout  = SERIAL_TIMEOUT_S,
                )
            log.info(f"Opened {self.port} at {self.baud} baud")
            # Classic boards reset when the port opens; the UNO R4's native USB
            # does not, so BOOT_OK may have been printed long ago. Instead of
            # waiting for BOOT_OK, wait for any boot output, drop it, and
            # confirm the firmware with PING/PONG.
            time.sleep(SERIAL_CONNECT_WAIT if not self._serial_factory else 0)
            self._serial.reset_input_buffer()

            self._connected = True
            for attempt in range(3):
                if self.send_ping():
                    log.info("Arduino firmware confirmed (PONG)")
                    return True
                time.sleep(0.5)
            log.error("Port opened but the firmware did not answer PING")
            self.disconnect()
            return False

        except serial.SerialException as e:
            log.error(f"Failed to connect to {self.port}: {e}")
            self._connected = False
            return False

    def disconnect(self):
        """Close the serial port cleanly."""
        if self._serial and self._serial.is_open:
            self._serial.close()
            log.info("Serial port closed")
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected and self._serial is not None and self._serial.is_open

    # ── Command Interface ────────────────────────────────────────────────────

    def send_command(self, arm: str, x_mm: float, servo: int, action: str) -> bool:
        """
        Send a motion command and wait for READY handshake.

        Retries the SAME command up to MAX_CONSECUTIVE_SERIAL_ERRORS times
        if the Arduino fails to confirm READY (timeout). This tolerates a
        one-off transient glitch (e.g. a brief brownout blip on an unstable
        rail) without aborting an entire performance over a single missed
        handshake — but a *persistent* fault still returns False after the
        retry budget is exhausted, so a real ongoing problem is not masked
        or retried into worse stress on the hardware.

        Args:
            arm:    'A' (left) or 'B' (right)
            x_mm:   Target rail position in millimetres
            servo:  Finger number 1–5, or 0 for move-only
            action: 'P' press | 'R' release | 'M' move-only

        Returns:
            True if Arduino confirmed READY, False on timeout or error.
        """
        if not self.is_connected:
            log.error("send_command called but not connected")
            return False

        cmd = f"{arm},{x_mm:.2f},{servo},{action}\n"

        for attempt in range(1, MAX_CONSECUTIVE_SERIAL_ERRORS + 1):
            try:
                self._serial.write(cmd.encode("utf-8"))
                log.debug(f"TX (attempt {attempt}): {cmd.strip()}")
            except serial.SerialException as e:
                log.error(f"Write failed: {e}")
                return False

            outcome = self._wait_for_ready()
            if outcome == "ready":
                return True
            if outcome == "error":
                # Firmware actively rejected the command — retrying the
                # exact same bytes won't fix that and could compound an
                # existing fault. Fail immediately.
                return False

            # outcome == "timeout": possibly a transient glitch — retry.
            if attempt < MAX_CONSECUTIVE_SERIAL_ERRORS:
                log.warning(
                    f"No READY for {cmd.strip()} (attempt {attempt}/"
                    f"{MAX_CONSECUTIVE_SERIAL_ERRORS}) — short cooldown then retry"
                )
                time.sleep(0.2)  # brief cooldown — avoids hammering a struggling rail

        log.error(
            f"Command failed after {MAX_CONSECUTIVE_SERIAL_ERRORS} attempts: {cmd.strip()}"
        )
        return False

    def send_home_command(self) -> bool:
        """
        Trigger homing routine. Waits for HOMED response.
        Homing can take up to 30 seconds.
        """
        if not self.is_connected:
            return False
        self._serial.write(b"H\n")
        log.info("Homing started — waiting up to 35 s...")

        deadline = time.time() + 35.0
        while time.time() < deadline:
            line = self._read_line(timeout=35.0)
            if line is None:
                break
            log.info(f"HOMING: {line}")
            if "HOMED" in line:
                log.info("Homing complete")
                return True
            if "ERROR" in line:
                log.error(f"Homing error: {line}")
                return False

        log.error("Homing timed out")
        return False

    def send_ping(self) -> bool:
        """Send PING and verify PONG response — connection health check."""
        if not self.is_connected:
            return False
        self._serial.write(b"PING\n")
        ok, response = False, None
        deadline = time.time() + 3.0
        while time.time() < deadline:                 # skip boot / notice lines
            response = self._read_line(timeout=3.0)
            if response is None:
                break
            if "PONG" in response:
                ok = True
                break
        if not ok:
            log.warning(f"Ping failed — response: {response!r}")
        return ok

    def request_status(self) -> Optional[str]:
        """Request STATUS string from firmware."""
        if not self.is_connected:
            return None
        self._serial.write(b"STATUS\n")
        return self._read_line(timeout=3.0)

    def send_calibration_command(self) -> bool:
        """Trigger 100 mm calibration move on Arm A."""
        if not self.is_connected:
            return False
        self._serial.write(b"CAL\n")
        # Drain responses until READY
        deadline = time.time() + 20.0
        while time.time() < deadline:
            line = self._read_line(timeout=20.0)
            if line is None:
                break
            log.info(f"CAL: {line}")
            if "READY" in line:
                return True
        return False

    def send_test_angle(self, degrees: int) -> bool:
        """
        Calibration-only: press finger 1 / Arm A at an arbitrary angle so the
        angle-sweep step actually tests different physical angles, instead of
        always pressing at the fixed SERVO_ANGLE_PRESS firmware constant.
        Not used during normal playback.
        """
        if not self.is_connected:
            return False
        self._serial.write(f"TESTANGLE,{int(degrees)}\n".encode("utf-8"))
        deadline = time.time() + 5.0
        while time.time() < deadline:
            line = self._read_line(timeout=5.0)
            if line is None:
                break
            log.info(f"TESTANGLE: {line}")
            if "READY" in line:
                return True
        return False

    def send_rest_all(self) -> bool:
        """
        Force every finger to rest position immediately.

        Safety command: valid whether or not the robot is homed. Used as a
        protective measure whenever playback stops (normal completion,
        error, or Emergency Stop) so nothing is ever left energized/loaded
        after a fault. This does NOT compensate for an overvoltage supply —
        it only guarantees servos aren't held under load any longer than
        necessary.
        """
        if not self.is_connected:
            log.warning("send_rest_all called but not connected — cannot guarantee fingers are at rest")
            return False
        try:
            self._serial.write(b"RESTALL\n")
        except serial.SerialException as e:
            log.error(f"RESTALL write failed: {e}")
            return False

        deadline = time.time() + 5.0
        while time.time() < deadline:
            line = self._read_line(timeout=5.0)
            if line is None:
                break
            log.info(f"RESTALL: {line}")
            if "READY" in line:
                return True
        log.warning("RESTALL did not confirm READY within timeout")
        return False

    # ── Internal Helpers ─────────────────────────────────────────────────────

    def _wait_for_ready(self) -> str:
        """
        Block until Arduino sends READY or an error/timeout occurs.
        Logs all intermediate lines from the firmware for debugging.

        Returns one of: "ready", "error", "timeout" — callers use this to
        decide whether a retry is sensible (only for "timeout"; retrying a
        command the firmware actively rejected with ERROR serves no purpose
        and can compound an existing fault).
        """
        deadline = time.time() + SERIAL_TIMEOUT_S
        while time.time() < deadline:
            line = self._read_line(timeout=SERIAL_TIMEOUT_S)
            if line is None:
                log.error("Timeout waiting for READY")
                return "timeout"
            log.debug(f"RX: {line}")
            if line == "READY":
                return "ready"
            if line.startswith("ERROR:AUTO_RELEASE"):
                # Asynchronous safety notice from the servo watchdog, not a
                # reply to this command — log it and keep waiting for READY.
                log.warning(f"Arduino safety notice: {line}")
                continue
            if line.startswith("ERROR:"):
                log.error(f"Arduino error: {line}")
                return "error"
        log.error("READY wait loop exceeded deadline")
        return "timeout"

    def _read_line(self, timeout: float = 5.0) -> Optional[str]:
        """
        Read one newline-terminated line from serial within timeout seconds.
        Returns None on timeout or read error.
        """
        if not self._serial:
            return None
        self._serial.timeout = timeout
        try:
            raw = self._serial.readline()
            if not raw:
                return None
            return raw.decode("utf-8", errors="replace").strip()
        except serial.SerialException as e:
            log.error(f"Read error: {e}")
            return None

    # ── Context Manager Support ───────────────────────────────────────────────

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
