# Software Design Document — Piano Robot

## Architecture Philosophy

The software is split into four single-responsibility modules that form a clean pipeline:

```
MIDI file → [midi_parser] → NoteEvent list
                          → [motion_planner] → RobotCommand list
                                             → [scheduler] → [serial_comm] → Arduino
```

Each module has one job and can be tested or replaced independently.

---

## Module Descriptions

### `Config/config.py`
The single source of truth. Every constant — COM port, steps/mm, rail lengths, servo angles, key mappings — lives here and nowhere else. Changing a value here propagates to all modules automatically.

### `core/midi_parser.py`
Reads a MIDI file using `mido` and extracts a flat list of `NoteEvent` dataclasses. Uses `mido.MidiFile` merged iteration which handles multi-track synchronisation and tempo map changes automatically. Absolute timestamps are in seconds. Black keys and out-of-range notes are filtered here so downstream modules never see them.

### `core/motion_planner.py`
Takes `NoteEvent` list, returns `RobotCommand` list. It plans the **hand position and finger together**:
- Arm assignment from `LEFT_WHITE_KEYS` / `RIGHT_WHITE_KEYS` lookup tables in config
- Hand model: the carriage position `x_mm` is where finger 1 sits; finger `f` sits `(f−1) × FINGER_SPACING_MM` further. With the hand at key index `b`, finger `f` is above key `b + f − 1`.
- If the key is already under a finger, press it without moving. Otherwise choose a new hand position that covers the key and the most upcoming notes (look-ahead), closest to the current position.
- Never slide the hand while a finger is down — held fingers on that arm are released first.
- A release uses the same finger and position as its press.
- `x_mm = HAND_OFFSET + b × FINGER_SPACING_MM`, checked against `[SAFE_MARGIN, rail_max − SAFE_MARGIN]`
- Action character mapping: PRESS→P, RELEASE→R

(An earlier planner used `x = key_index × KEY_WIDTH` *and* a rotating finger `(key_index % 5) + 1`, which pressed the wrong key for 4 of every 5 notes.)

### `core/serial_comm.py`
Wraps `pyserial`. Owns the connection lifecycle, the READY handshake, homing, ping, and status requests. All other modules call methods here and never import pyserial directly. Supports Python context manager (`with SerialComm() as comm:`).

### `core/scheduler.py`
Executes a `RobotCommand` list in real time. Waits until `cmd.timestamp_s` seconds have elapsed since playback start, then sends to `serial_comm`. Detects and logs timing lag when falling more than 100 ms behind MIDI tempo. Supports `stop()` for emergency halt. Progress callbacks allow both CLI and Streamlit UI to receive updates without the scheduler knowing about either.

### `Software/app.py`
Streamlit UI. Handles file upload, connection management, motion plan preview, CSV export, and live playback with progress display. All heavy work delegates to the four core modules.

### `utils/calibrate.py`
Interactive CLI calibration wizard. Steps the user through homing, the 100 mm reference move, ruler measurement, and servo angle sweep. Prints exact config values to copy into `config.py`.

---

## Serial Protocol Design

The handshake is sequential: Python sends one command, waits for `READY`, then sends the next. This guarantees:
- No command is lost (Arduino's 64-byte serial buffer can overflow on fast bursts)
- Every mechanical movement completes before the next starts
- Errors are caught per-command rather than discovered later

The trade-off is that chords requiring both arms simultaneously play one arm after the other, not at the same instant.

---

## Error Handling Strategy

| Layer | Error | Handling |
|-------|-------|----------|
| `serial_comm` | Port not found | Returns False from `connect()`, logs error |
| `serial_comm` | Timeout waiting for READY | Returns False from `send_command()`, logs error |
| `serial_comm` | Arduino sends `ERROR:*` | Returns False, logs specific firmware error |
| `scheduler` | `send_command` returns False | Calls `on_error` callback, stops playback |
| `motion_planner` | Position out of range | Clamps with warning log, never crashes |
| `midi_parser` | Unreadable file | Raises `ValueError` caught by Streamlit UI |
| Arduino firmware | `NOT_HOMED` | Returns `ERROR:NOT_HOMED` to Python |
| Arduino firmware | Bad command format | Returns `ERROR:BAD_FORMAT` |

---

## Testing Strategy

Unit tests in `Software/tests/` cover all motion planner logic and require no hardware. Run with `pytest`. The dry-run mode in the Streamlit UI allows full end-to-end simulation of any MIDI file on a laptop.
