# Robotic Piano Player — Dual-Arm

A robot with two independent arms that plays piano from a standard **MIDI file**. Each arm slides on its own rail (NEMA 17 stepper + GT2 belt) and presses keys with 5 servo-driven fingers. A Python/Streamlit app on a laptop plans every move and sends it to an **Arduino UNO R4** over a serial protocol with handshaking.

> Graduation project — B.Sc. Mechatronics Engineering, University of Baghdad, Al-Khwarizmi College of Engineering (2025–2026).
> Team: Hawraa Hassan (software & electrical lead) and Shaimaa Hussain.
> Supervisors: Asst. Lect. Mohanad Ghazi and Asst. Lect. Maryam Sadeq.

<table>
<tr>
<td width="25%"><img src="docs/images/robot_two_arms.jpg" alt="Both arms on their rails, labelled"><br><sub>Both arms on their rails, with the electronics and the power supply.</sub></td>
<td width="25%"><img src="docs/images/robot_two_arms_side.jpg" alt="The same setup from another angle, labelled"><br><sub>The same setup from another angle.</sub></td>
<td width="25%"><img src="docs/images/robot_hand_closeup.jpg" alt="One hand with servos, stepper and belt, labelled"><br><sub>One hand: five servo-driven fingers, stepper, belt and controller.</sub></td>
<td width="25%"><img src="docs/images/robot_fingers_closeup.jpg" alt="Both hands over the keyboard, labelled"><br><sub>Both hands over the keyboard.</sub></td>
</tr>
</table>

---

## Project status

This is a **work in progress**. Honest summary of where it stands:

| Part | Status |
|---|---|
| Mechanical frame, two rails, belts, steppers | ✅ Assembled |
| Electronics (UNO R4, PCA9685, 2 × A4988) | ✅ Wired |
| Finger servos through the PCA9685 | ✅ Tested on the robot (clip below) |
| Software: MIDI → plan → serial → firmware | ✅ Verified end-to-end in simulation (18 automated tests) |
| Full song played on the real robot | ⏳ Not yet — power-rail and calibration work remaining |
| `STEPS_PER_MM`, hand offsets | ⏳ Must be measured on the robot (`Config/config.py`) |

<table>
<tr>
<td width="38%"><img src="docs/images/servo_fingers_test.gif" alt="Finger servo test"><br><sub><b>Real hardware:</b> finger servos driven through the PCA9685.</sub></td>
<td><img src="docs/images/simulation.gif" alt="Simulation of the motion plan"><br><sub><b>Simulation:</b> <i>Ode to Joy</i> planned by the real motion planner and executed by a firmware simulator that uses the same move timing as AccelStepper.</sub></td>
</tr>
</table>

The simulation is not a mock-up: `tools/visualize.py` runs the actual `Software/core` code (planner → scheduler → serial protocol) against `tools/firmware_sim.py`, which answers `READY` / `PONG` like the Arduino and rejects any carriage move while a finger is pressed.

---

## How it works

```
 MIDI file
    │
    ▼
┌──────────────────────────── Laptop (Python) ────────────────────────────┐
│  MIDI parser  →  Motion planner  →  Real-time scheduler  →  Serial comm │
│  (note events)   (arm, position,     (timing, retries,      (protocol,  │
│                   finger, press)      emergency stop)        handshake) │
│                         Streamlit UI (upload, plan, play, log)          │
└─────────────────────────────────────┬───────────────────────────────────┘
                                      │ USB serial, 115200 baud
┌─────────────────────────────────────▼───────────────────────────────────┐
│ Arduino UNO R4 Minima                                                   │
│   AccelStepper ──▶ 2 × A4988 ──▶ 2 × NEMA 17 (Rail A 280 mm, Rail B 400 mm)│
│   I2C ──────────▶ PCA9685 ────▶ 10 × MG90S finger servos (5 per hand)   │
│   Safety: homing, servo auto-release watchdog, RESTALL                  │
└─────────────────────────────────────────────────────────────────────────┘
```

The two arms run on separate sets of steel rods, so they cannot collide mechanically.

## Features

- **MIDI → robot commands**: notes are assigned to the left or right arm, converted to a rail position in mm, and given a finger.
- **Serial protocol with handshake**: every command waits for `READY`; errors come back as `ERROR:<reason>`.
- **Safety**
  - Emergency stop from the UI (stops after the current command finishes)
  - `RESTALL` command puts every finger at rest after any stop
  - Firmware releases any servo held pressed for more than 3 s
  - Servos move one at a time on startup to avoid a current spike
  - Bounded retries on serial timeouts
- **Calibration tool** for steps-per-mm and servo press angle
- **Dry-run mode**: run the full plan without the robot connected
- **18 automated tests** that run without hardware, including a firmware simulator

## Serial protocol

| Direction | Message | Meaning |
|---|---|---|
| PC → Arduino | `A,150.00,3,P` | Move arm A to 150 mm, finger 3, press |
| PC → Arduino | `B,200.00,1,R` | Move arm B to 200 mm, finger 1, release |
| PC → Arduino | `H` | Homing |
| PC → Arduino | `PING` / `STATUS` / `CAL` | Health check / position / 100 mm calibration move |
| PC → Arduino | `RESTALL` | All fingers to rest (safety) |
| PC → Arduino | `TESTANGLE,<deg>` | Calibration only: press at a given angle |
| Arduino → PC | `READY` / `HOMED` / `PONG` | Command done / homing done / ping reply |
| Arduino → PC | `ERROR:<msg>` | Fault, e.g. `ERROR:NOT_HOMED`, `ERROR:AUTO_RELEASE_CH3` |

All messages are newline-terminated text at 115200 baud.

## Hardware

| Part | Qty |
|---|---|
| Arduino UNO R4 Minima | 1 |
| NEMA 17 stepper + A4988 driver | 2 |
| PCA9685 16-channel servo driver (I2C, 0x40) | 1 |
| MG90S micro servo (fingers) | 10 |
| GT2 belt + 20-tooth pulley, LM8UU bearings, 8 mm rods | per arm |
| Regulated supply (6 V for the servo rail) | 1 |

Wiring summary:

| From | To |
|---|---|
| Pins 2 / 3 | STEP / DIR — Rail A driver |
| Pins 5 / 4 | STEP / DIR — Rail B driver |
| SDA / SCL | PCA9685 |
| PCA9685 ch 0–4 | Left hand fingers |
| PCA9685 ch 5–9 | Right hand fingers |

⚠ The MG90S servos are rated 4.8–6 V. Read [`docs/power_notes.md`](docs/power_notes.md) before powering the robot.

## Repository layout

```
Config/config.py                          All constants (single source of truth)
Firmware/PianoRobot_Firmware/*.ino        Arduino firmware
Software/app.py                           Streamlit UI (entry point)
Software/core/midi_parser.py              MIDI → note events
Software/core/motion_planner.py           Note events → robot commands
Software/core/scheduler.py                Real-time playback, stop, retries
Software/core/serial_comm.py              Serial protocol
Software/utils/calibrate.py               Interactive calibration
Software/tests/                           Unit + end-to-end tests (no hardware needed)
tools/firmware_sim.py                     Serial-compatible simulator of the firmware
tools/visualize.py                        Renders docs/images/simulation.gif from a real plan
tools/songs.py                            Built-in test song (Ode to Joy)
docs/                                     Design, testing, power notes, troubleshooting
```

## Getting started

```bash
pip install -r requirements.txt
```

1. Upload `Firmware/PianoRobot_Firmware/PianoRobot_Firmware.ino` (libraries: **AccelStepper**, **Adafruit PWM Servo Driver**; board: Arduino UNO R4 Minima).
2. Set `SERIAL_PORT` in `Config/config.py`.
3. Calibrate once: `python -m Software.utils.calibrate`
4. Run the app: `streamlit run Software/app.py`
5. In the app: **Connect → Run homing → Upload MIDI → check the motion plan → Start playback**.

## Tests

```bash
python -m pytest Software/tests/ -v
```

The tests check that every press lands on the intended key, that the hand never slides with a finger down, chords, repeated notes, limits, the serial format, and a full song through the scheduler, serial link, and a firmware simulator. Test descriptions and the dry-run procedure are in [`docs/testing_report.md`](docs/testing_report.md).

## Run the simulation

```bash
pip install matplotlib
python -m tools.visualize                 # built-in song → docs/images/simulation.gif
python -m tools.visualize --midi song.mid
```

## Engineering review

A code review found these problems; each fix has a test (details in [CHANGELOG.md](CHANGELOG.md)):

| Problem found | Effect | Fix |
|---|---|---|
| Planner moved the carriage so finger *f* sat at the note, but the hand geometry puts finger 1 at the carriage position | Wrong key pressed on most notes | Hand model: finger *f* is over key *base + f − 1*; window planning with look-ahead → all 19 notes of *Ode to Joy* land on the right key (tested) |
| Carriage could move while a finger was still down | Finger dragged across the keys | Fingers released before any move; never slides with a finger down |
| Note released with a different finger than it was pressed with | Pressed finger left down | Release uses the finger recorded at press time |
| Connect waited for a `READY` sent at reset | UNO R4 (native USB) does not reset on port open → connect always timed out | Connect with `PING`/`PONG` (3 tries) |
| `ERROR:AUTO_RELEASE` treated as fatal | Playback aborted on a safety message | Logged and skipped; playback continues |
| Homing travel fixed at 310 mm | Rail B is 400 mm — could stop before reaching the end | Homing travel = rail length + 20 mm, per rail |
| `mm → steps` truncated | Up to 1 step error per move, accumulating | Rounded with `lround` |

The firmware still needs a real build and a run on the robot.

## Known limitations

- No limit switches — homing drives each carriage past its rail length into the frame end.
- Arms move one at a time, so two-hand chords are played one arm after the other, not at the same instant.
- Very fast passages can fall behind the MIDI tempo (a warning is logged when playback lags).
- White keys only.

## Future work

- Limit switches for precise homing
- Both arms moving at the same time (non-blocking stepper control)
- Black keys with a second axis
- Tempo slider in the UI
- Position feedback with encoders

## More documentation

- [Software design](docs/software_design.md)
- [Code explanation](docs/code_explanation.md)
- [Testing report](docs/testing_report.md)
- [Power notes](docs/power_notes.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)

## License

MIT — see [LICENSE](LICENSE).
