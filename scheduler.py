"""
scheduler.py — Playback Scheduler
====================================
Executes a RobotCommand list in real time, honouring MIDI timestamps.

Design decisions:
  • Uses a callback-based progress reporting so both CLI and Streamlit can
    display status without the scheduler knowing about either.
  • Supports emergency stop via threading.Event.
  • Timing: waits for MIDI timestamp BEFORE sending; Arduino handshake
    provides mechanical completion confirmation after each command.
  • If the Arduino is slower than the MIDI tempo, the scheduler falls
    slightly behind (notes play late) rather than dropping notes.
    A warning is logged when lag exceeds 100 ms.
"""

import time
import logging
import threading
from typing import List, Callable, Optional

from Software.core.serial_comm import SerialComm
from Software.core.motion_planner import RobotCommand

log = logging.getLogger(__name__)

LAG_WARN_THRESHOLD_S = 0.10   # Warn if falling more than 100 ms behind tempo


class Scheduler:
    """
    Sends RobotCommand objects to the Arduino at the right time.

    Usage:
        sched = Scheduler(serial_comm)
        sched.play(commands, on_progress=my_callback)
    """

    def __init__(self, comm: SerialComm):
        self._comm    = comm
        self._stop    = threading.Event()
        self._running = False

    # ── Public API ────────────────────────────────────────────────────────────

    def play(
        self,
        commands: List[RobotCommand],
        on_progress: Optional[Callable[[int, int, RobotCommand], None]] = None,
        on_error:    Optional[Callable[[str], None]] = None,
    ) -> bool:
        """
        Execute the full command sequence.

        Args:
            commands:    Ordered list of RobotCommand from MotionPlanner.
            on_progress: Called after each command with (index, total, command).
            on_error:    Called with an error message string on fault.

        Returns:
            True if playback completed without errors, False otherwise.
        """
        if not commands:
            log.warning("play() called with empty command list")
            return True

        if not self._comm.is_connected:
            msg = "Not connected to Arduino"
            log.error(msg)
            if on_error:
                on_error(msg)
            return False

        self._stop.clear()
        self._running = True
        total = len(commands)
        start_wall = time.monotonic()
        success = True

        log.info(f"Playback started — {total} commands")

        try:
            for idx, cmd in enumerate(commands):
                if self._stop.is_set():
                    log.info("Playback stopped by user")
                    break

                # ── Timing: wait until this note's MIDI timestamp ─────────
                target_wall = start_wall + cmd.timestamp_s
                now         = time.monotonic()
                wait_s      = target_wall - now

                if wait_s > 0:
                    # Sleep in small increments so stop signal is responsive
                    sleep_deadline = time.monotonic() + wait_s
                    while time.monotonic() < sleep_deadline:
                        if self._stop.is_set():
                            break
                        time.sleep(min(0.01, sleep_deadline - time.monotonic()))

                if self._stop.is_set():
                    break

                # ── Check for timing lag ──────────────────────────────────
                actual_lag = time.monotonic() - target_wall
                if actual_lag > LAG_WARN_THRESHOLD_S:
                    log.warning(
                        f"Timing lag {actual_lag*1000:.0f} ms at command {idx} "
                        f"(note {cmd.note_id})"
                    )

                # ── Send to Arduino ───────────────────────────────────────
                log.debug(f"[{idx+1}/{total}] {cmd}")
                ok = self._comm.send_command(
                    arm    = cmd.arm,
                    x_mm   = cmd.x_mm,
                    servo  = cmd.servo,
                    action = cmd.action,
                )

                if not ok:
                    msg = f"Arduino failed on command {idx+1}: {cmd.to_serial()}"
                    log.error(msg)
                    if on_error:
                        on_error(msg)
                    success = False
                    break

                if on_progress:
                    on_progress(idx + 1, total, cmd)

        finally:
            self._running = False
            # Power-stress protection: regardless of why playback stopped
            # (completed normally, errored out, or Emergency Stop), force
            # every finger to rest. This guarantees nothing stays
            # energized/loaded after this point — important on a marginal
            # or overvoltage supply where a finger left pressed continues
            # dissipating heat for no reason.
            try:
                self._comm.send_rest_all()
            except Exception as e:
                log.error(f"send_rest_all failed during shutdown: {e}")
            log.info("Playback finished")

        return success

    def stop(self):
        """Signal the scheduler to stop after the current command completes."""
        log.info("Stop requested")
        self._stop.set()

    @property
    def is_running(self) -> bool:
        return self._running
