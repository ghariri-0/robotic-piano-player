# Power Notes — Servo Rail

Read this before powering the robot.

### What the design assumes
The wiring table in the README assumes **one regulated 6 V rail** feeding both:
- `VMOT` on both A4988 stepper drivers, and
- `V+` on the PCA9685 servo driver (which feeds all 10 MG90S servos).

MG90S servos are rated **4.8–6 V**. A4988 drivers tolerate up to 35 V on VMOT, so they're not the constraint — the **servos are**.

### If you are running this at 12 V
If your supply is set to 12 V instead of 6 V, you are running 10 servos at roughly **double their rated voltage**, continuously, every time a finger moves. This is likely an electrical/thermal stress condition for the servos, and not something software can check or correct.

**What this could look like, and possible explanations (not confirmed):**

| Possible observation | Possible explanation |
|---|---|
| 9 V "worked," output read ~8 V | The supply may be sagging under the combined stepper + servo load, which would suggest the rail is not comfortably sized for it |
| Needed 10 V, then 11 V, then 12 V to get the same result | The servos may already have been running hot from overvoltage. As copper windings heat, their resistance rises and torque per volt can drop, so the same job may seem to need more volts. Raising the voltage to compensate would add more heat, which could create a feedback loop. This is one possible explanation and has not been confirmed by measurement. |
| System "stopped working normally" after that | One possible endpoint: a servo winding opened, an onboard servo driver IC failed, or a gearbox deformed from heat. If that happened, it would be component damage rather than a software fault, and configuration changes would not fix it. It could also have another cause (for example a wiring or supply problem), so check the servos and the supply before concluding. |

### The real fix (recommended, even though it's a hardware change)
Add a small 5–6 V buck regulator/converter feeding **only** the PCA9685 servo rail, separate from the 12 V stepper rail. This is a low-cost, low-risk addition and is the only way to actually stop overvoltage stress on the servos — **no firmware or software change can do this**, because the PCA9685 PWM signal only sets target angle (pulse width); it does not regulate the voltage delivered to the servo's internal motor. That voltage is set entirely by whatever is on `V+`.

### What the software safety features do (and don't do)
The firmware/software features below reduce *how much* electrical and thermal stress accumulates per unit of operation, and make the system fail safe instead of fail stuck. They do **not** lower the voltage the servos see and will **not** prevent eventual servo damage if you continue running them at 12 V long-term:

- **Staggered multi-servo actuation** (`SERVO_STAGGER_MS`) — boot park, homing, and the new `RESTALL` safety command move the 10 servos one at a time with a small delay instead of firing all 10 simultaneously, avoiding a simultaneous current-inrush spike.
- **Auto-release timeout** (`SERVO_MAX_HOLD_MS`) — any servo left in the pressed/energized state for more than 3 seconds (e.g. a dropped RELEASE command) is force-released by the firmware automatically. Nothing stays loaded indefinitely.
- **Gentler stepper motion profile** — `STEPPER_MAX_SPEED` and `STEPPER_ACCEL` were both reduced, which lowers peak current draw and driver heating.
- **Forced rest-all on every playback stop** — whether playback finishes normally, hits an error, or you hit Emergency Stop, the PC now automatically sends `RESTALL` so no finger is left pressed/energized after the fact.
- **Bounded retry on serial timeout** — a single transient comms glitch (plausible on a stressed/sagging rail) gets retried up to `MAX_CONSECUTIVE_SERIAL_ERRORS` times before the system gives up on that command, rather than either masking a real fault by retrying forever or aborting an entire performance over one blip.

If you still see degraded behavior with these features in place, it may be caused by accumulated hardware stress or damage, but a remaining software problem is also possible. Check both — see **Troubleshooting** below.

---

## Safety feature tests

Before resuming normal use, verify the new protections actually work — don't assume:

1. **Auto-release timeout.** With the robot connected and homed, manually send a press command for one finger (e.g. via a serial terminal: `A,150.00,3,P\n`) and then send nothing else. Confirm that within ~3 seconds you see `ERROR:AUTO_RELEASE_CHx` in the Log tab and the finger physically lifts on its own. If it doesn't lift, do not proceed to playback — this safety net isn't working and a stuck press is a real risk on an overvoltage rail.
2. **RESTALL on Emergency Stop.** Start playback on dry-run **off** with at least one finger pressed, hit Emergency Stop mid-song, and confirm in the Log that `RESTALL` fires and every finger visibly returns to rest.
3. **RESTALL on normal completion.** Let a short MIDI file play to completion and confirm all fingers are at rest afterward (not just the last one used).
4. **Bounded retry behavior.** Temporarily unplug the USB cable for under a second during playback (a brief, recoverable glitch) and confirm the system retries rather than aborting outright; then unplug it for several seconds and confirm it *does* abort and still ends with fingers at rest.
5. **Thermal spot-check.** After 10–15 minutes of normal playback, feel the case of each MG90S servo and each A4988 driver. Noticeably hot-to-touch (uncomfortable to hold for >1-2 s) on a servo is not normal at the rated 6 V and is a strong sign you are still running the overvoltage condition — stop and address the power rail before continuing.

---
