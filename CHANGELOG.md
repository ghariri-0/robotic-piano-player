# Changelog

## Motion planner and connection fixes

- **Motion planner rewritten.** Finger 1 sits at the carriage position (plus a hand offset), so finger *f* is over key *base + f − 1*. The old planner moved finger *f* to the note and pressed the wrong key (the new planner puts all 19 notes of *Ode to Joy* on the right key — covered by tests). The new planner chooses a hand window with look-ahead, never moves the carriage with a finger down, and releases with the same finger it pressed with. New config: `FINGERS_PER_HAND`, `FINGER_SPACING_MM`, `HAND_OFFSET_A_MM`, `HAND_OFFSET_B_MM` (measure on the robot), `PLANNER_LOOKAHEAD`.
- **Serial connect** uses `PING`/`PONG` instead of waiting for a boot `READY` (the UNO R4 does not reset when the port opens). `ERROR:AUTO_RELEASE_*` is no longer fatal.
- **Firmware:** homing travel is the rail length + `HOMING_OVERTRAVEL_MM` (rail B is 400 mm; the old fixed travel was too short). `mmToSteps` rounds instead of truncating.
- **Tools:** `tools/firmware_sim.py` (serial-compatible firmware simulator), `tools/visualize.py` (simulation GIF from a real plan), `tools/songs.py`.
- **Tests:** 18 tests (planner + end-to-end through scheduler, serial and simulator).
- **Docs:** README project status, annotated hardware photos, servo test clip, simulation animation.

## Bug-fix pass

This is a **bug-fix pass on top of the power-stress fixes below**. No hardware, wiring, controller, sensors, actuators, or the core architecture changed. Three real defects were found and fixed:

1. **Emergency Stop could become unresponsive during real playback.** Real playback used to start a background thread and then immediately block the whole app on it (`thread.join()`), with progress callbacks calling Streamlit display functions from that background thread — both unsafe/unreliable patterns in Streamlit. Playback now runs non-blocking: the worker thread only updates a small lock-protected progress dict, and the main app polls and renders it on quick reruns. Emergency Stop is reached on one of those quick reruns and calls `scheduler.stop()` immediately — it no longer depends on Streamlit's internal scheduling to "happen to" get through. Dry-run now goes through the same Start/Stop machinery as real playback (it previously ignored Emergency Stop entirely).
2. **The `SERVO_MAX_HOLD_MS` auto-release watchdog could be starved during a long carriage move.** It only ran once per `loop()` iteration in firmware, but a move blocks inside a `while (distanceToGo() != 0)` loop that can run for well over a second. `checkServoSafety()` is now called inside every such blocking loop (playback moves, homing, calibration), not just once per `loop()`.
3. **The servo angle calibration sweep in `utils/calibrate.py` never actually tested different angles** — it always pressed at the fixed `SERVO_ANGLE_PRESS` firmware constant regardless of which angle the prompt claimed to test. Added a narrow `TESTANGLE,<deg>` debug command (calibration-only, not used during playback) so the sweep tests real angles.

None of this changes the serial protocol used during playback, the firmware's homing/safety behavior beyond the watchdog timing fix, or anything in `docs/power_notes.md` — the hardware-level overvoltage risk is unchanged and would still call for the regulator described there if you run the servo rail at 12 V.

## Power-stress fixes

- Staggered servo actuation, 3 s servo auto-release watchdog, gentler stepper profile, forced `RESTALL` on every stop, bounded serial retries.
