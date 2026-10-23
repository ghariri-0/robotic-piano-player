# CODE_EXPLANATION.md — Piano Robot Software, Explained Top-Down

This document explains the *entire* software stack — Python application and Arduino
firmware — starting from "what runs first" and working down into every file, every
function, and every engineering decision behind them. It's written so a mechatronics
student can understand the whole system **without opening the source code first**,
then use the source as confirmation rather than as the starting point.

---

## 1. Project Execution Overview

### How the program actually starts

There are **two separate programs** in this project, running on two separate
processors, talking over a USB-serial link. Neither is "the main program" on its
own — they're a client and a server.

```
 ┌─────────────────────────────┐         USB Serial          ┌─────────────────────────────┐
 │   PYTHON SIDE (your laptop)  │ <───────  115200 baud  ───────> │  ARDUINO SIDE (the robot)  │
 │   Entry point: Software/app.py│                              │  Entry point: setup()/loop() │
 │   Run with: streamlit run ... │                              │  in PianoRobot_Firmware.ino │
 └─────────────────────────────┘                              └─────────────────────────────┘
```

**The first file that runs, in practice, is whichever you start first:**

- **Arduino side:** the moment the board powers on or is reset (including the reset
  that happens automatically when a serial connection opens), `setup()` in
  `PianoRobot_Firmware.ino` runs once, then `loop()` runs forever, checking for
  incoming serial bytes on every pass.
- **Python side:** you run `streamlit run Software/app.py`. Streamlit imports and
  executes `app.py` top-to-bottom. Unlike a normal script, **Streamlit re-runs the
  entire file from top to bottom every single time you click a button or change a
  widget** — this is the single most important thing to understand before reading
  any of the Python code, and it's covered in detail in §2.

### Execution sequence, step by step (a full "play a song" session)

1. You run `streamlit run Software/app.py`. The script executes top to bottom once,
   builds the sidebar and the four tabs, and renders the page in your browser.
2. You plug in / power on the Arduino. Its `setup()` runs: it configures both
   stepper motors, initializes the PCA9685 servo driver over I2C, parks all 10
   fingers at rest (staggered, to avoid a current spike), and prints `BOOT_OK`
   followed by `ERROR:NOT_HOMED` over serial. It then enters `loop()` and waits.
3. In the browser, you type the COM port and click **Connect**. This calls
   `SerialComm.connect()`, which opens the port, waits ~2.5 s for the Arduino's
   reset-on-serial-open to finish, and reads lines until it sees `BOOT_OK`.
4. You click **Run Homing Sequence**. Python sends `H\n`. The Arduino's `loop()`
   sees this, calls `performHoming()`, which crawls both rails slowly toward their
   physical frame stops (no limit switches — this design uses a timed slow crawl
   instead), retracts 10 mm, and calls that position zero. It replies `HOMED`.
5. You upload a `.mid` file in **Tab 1**. Python's `parse_midi()` reads it with
   `mido` into a flat, time-ordered list of `NoteEvent`s, then `MotionPlanner.plan()`
   converts that into a list of `RobotCommand`s (one per key press/release, with an
   arm, position, finger, and absolute timestamp). This list is stashed in
   `st.session_state.commands` so it survives the next full-script rerun.
6. You glance at **Tab 2** to sanity-check the command table before touching real
   hardware — this never talks to the Arduino, it just renders the list already
   computed in step 5.
7. You click **Start Playback** in **Tab 3**. Python starts a background thread
   running `Scheduler.play()`. For each `RobotCommand`, in timestamp order, it:
   waits until that command's musical timestamp has actually arrived in wall-clock
   time, sends the command string over serial, and blocks until the Arduino replies
   `READY` (or times out / errors).
8. On the Arduino, each command line arriving in `loop()` goes through
   `parseAndExecute()`: move the named rail's stepper to the target position
   (blocking until it arrives), then actuate the named finger servo to PRESS or
   RELEASE, then print `READY`.
9. While all of this is happening, the Arduino's `checkServoSafety()` watchdog is
   continuously checking whether any finger has been sitting in the PRESS state for
   more than 3 seconds without a matching RELEASE — protecting against a dropped
   command leaving a servo energized indefinitely.
10. If you click **Emergency Stop**, or the song finishes, or any error occurs,
    `Scheduler.play()`'s `finally` block fires `send_rest_all()`, which sends
    `RESTALL\n` — the Arduino immediately parks every finger at rest regardless of
    what it was doing, and confirms with `RESTALL_OK`.
11. Every step above writes a line to **Tab 4**'s log, which is the place to verify
    after the fact that the safety behaviors actually fired.

---

## 2. The Single Most Important Python Concept: Streamlit's Rerun Model

Before describing files individually, understand this, because it shapes the design
of nearly every line in `app.py`:

> **Streamlit has no event loop in the traditional sense.** Every time the user
> interacts with a widget (clicks a button, flips a toggle, uploads a file),
> Streamlit re-executes `app.py` **from the top**, in a fresh pass. Variables you
> declared as plain Python locals (`x = 5`) are gone by the next rerun. The *only*
> thing that survives between reruns is `st.session_state`.

This is why:
- Connection status, homed status, the parsed command list, and the Scheduler/
  SerialComm objects all live in `st.session_state` — otherwise switching tabs would
  silently "forget" that you're connected.
- Playback cannot simply run a `for` loop and block the script for the whole song —
  doing so would freeze the one rerun handling that click, and any button click that
  arrives while a script run is still in flight (like Emergency Stop) has nowhere to
  go until that run finishes. The fix applied in this codebase (see `app.py`,
  §3 below) is to run playback in a background thread that **only** touches a plain,
  lock-protected dictionary, while the main script polls that dictionary and
  re-renders on short `st.rerun()` cycles — keeping every rerun fast enough that the
  Stop button is always reachable almost immediately.

---

## 3. File-by-File Explanation (in the order data actually flows through them)

### `Config/config.py`

**Purpose of this file:** the single source of numeric/physical truth for the whole
project — pin assignments aren't here (those are firmware-only), but every tunable
constant a human might need to change is.

**What this file contains:** plain module-level constants — serial port/baud/
timeouts, stepper steps-per-mm and speed/acceleration, rail lengths and safety
margins, servo angles and timing, the power-stress mitigation constants
(`SERVO_STAGGER_MS`, `SERVO_MAX_HOLD_MS`, `MAX_CONSECUTIVE_SERIAL_ERRORS`), the
PCA9685 channel-to-finger mapping, and the MIDI note-to-key mapping tables
(`LEFT_WHITE_KEYS`, `RIGHT_WHITE_KEYS`).

**Why this file exists:** so nobody has to hunt through five files to change one
number. It also documents, in comments, *why* each value is what it is (e.g. why
`STEPPER_MAX_SPEED` was reduced) — config-as-documentation.

**How it connects with other files:** every Python module (`midi_parser`,
`motion_planner`, `serial_comm`, `app.py`) imports specific constants from here.
Nothing computes these values at runtime; they're static, hand-calibrated numbers.
**Engineering note:** these constants are mirrored by hand into the `.ino` firmware
file (steps/mm, speed, accel, angles) — there is no automatic sync between the two,
which is called out explicitly in both files' comments. This is a known limitation,
not an oversight (see §6, Known Limitations).

---

### `Software/core/midi_parser.py`

**Purpose:** turn an arbitrary `.mid` file into a flat, time-ordered list of notes
the rest of the system can reason about, filtering out anything the robot can't
physically play.

**Contains:**
- `NoteEvent` — a `@dataclass` (a class that's just a typed bag of fields with an
  auto-generated constructor) holding `timestamp_s`, `note_id`, `action` ("PRESS"/
  "RELEASE"), `velocity`.
- `parse_midi(source)` — the main function. **Inputs:** a file path or a file-like
  object (so a Streamlit `UploadedFile` works directly). **Output:** a
  timestamp-sorted `List[NoteEvent]`.
- `get_midi_duration_s(source)` — quick helper for the UI's duration display.
- `_is_black_key(note_id)` — internal helper using MIDI note-number arithmetic
  (`note_id % 12` gives the pitch class within an octave; positions 1,3,6,8,10 are
  the black keys C#, D#, F#, G#, A#).

**Logic flow / algorithm:**
1. Open the file with the `mido` library, which handles the binary MIDI format and
   (critically) **merges all tracks into one time-ordered stream while resolving
   tempo-map changes**, so you don't have to do tempo math yourself.
2. Walk every message. `mido`'s iteration protocol gives you `msg.time` as the
   *delta* in seconds since the previous message — so a running total
   (`current_s += msg.time`) gives you absolute time.
3. For each `note_on`/`note_off` message: a `note_on` with velocity 0 is treated as
   a release (this is a real MIDI convention some software uses instead of a
   distinct `note_off`).
4. Filter: if the note isn't in `ALL_PLAYABLE_KEYS` (built from the config's
   white-key lists), it's silently dropped — but the code still distinguishes *why*
   (black key vs. simply out of range) for diagnostic logging, even though both
   produce the same practical outcome.
5. Sort by timestamp and return.

**What happens when bad input arrives:** if `mido` can't parse the file at all,
the exception is caught and re-raised as a `ValueError` with a clear message — this
is the layer the Streamlit UI's `try/except` is built to catch.

**Connects to:** consumes nothing from other project files except `config.py`'s
key lists; its output (`List[NoteEvent]`) is the sole input to `motion_planner.py`.

---

### `Software/core/motion_planner.py`

**Purpose:** convert musical information (which note, when) into *robot* information
(which arm, what position in millimeters, which of 5 fingers, press or release).

**Contains:**
- `RobotCommand` — another `@dataclass`. Carries everything downstream code needs:
  `timestamp_s`, `arm`, `x_mm`, `servo`, `action`, plus the original `note_id` kept
  around purely for diagnostics/logging (it's never sent to the Arduino).
  `to_serial()` formats it exactly as the Arduino's parser expects:
  `"A,150.00,3,P"`.
- `MotionPlanner` — a small stateless class (it holds no instance data; `plan()`
  could be a plain function, but is kept as a class method so it can be swapped or
  subclassed later without touching call sites — a basic "favor a class boundary
  where extension is plausible" choice, not because it needs object state).
- `MotionPlanner.plan(events)` — the public entry point.
- `MotionPlanner._map_event(event)` — the per-note logic (a "private" helper by
  Python's underscore convention).

**Logic flow:**
1. For every `NoteEvent`, decide which arm owns that MIDI note by checking
   membership in `LEFT_WHITE_KEYS` / `RIGHT_WHITE_KEYS`. Unknown notes return `None`
   and are skipped (already filtered upstream, but defensive nonetheless).
2. Plan the hand position together with the finger. The carriage position is
   where finger 1 sits, so with the hand at key index `b` finger `f` is above key
   `b + f − 1`. If the key is already under a finger, no move is needed; otherwise
   the planner picks a new `b` that covers this key and as many upcoming notes as
   possible, and releases any held finger before the hand slides.
3. **Clamp**, never crash: if a computed position would fall inside the safety
   margin at either end of the rail, it's pulled back to the nearest safe value and
   a warning is logged. This is a deliberate "fail toward safe, not toward
   exception" choice — a clamped note plays slightly wrong; an unhandled exception
   here would kill an in-progress performance.
4. The finger follows from the hand position: `servo = key_index − b + 1`.
   (An earlier approach rotated the finger with `(key_index % 5) + 1` while the carriage
   moved to the key itself — that combination put finger `f` over the wrong key.)
5. Map the MIDI action string to the single-character protocol code the firmware
   expects (`"PRESS"→"P"`, `"RELEASE"→"R"`, anything else→`"M"` for move-only).
6. Collect every mapped command, sort by timestamp (defensive — input is already
   sorted, but a planner shouldn't assume its caller never changes), and return.

**Engineering note on this layer's philosophy:** this module is *stateless* and
*pure* (same input always gives same output, no side effects) by design — that's
exactly why `Software/tests/test_motion_planner.py` can unit-test it completely
without any hardware or even a running Streamlit session.

**Connects to:** consumes `NoteEvent` from `midi_parser.py` and constants from
`config.py`; produces `RobotCommand` list consumed by `app.py` (for display/CSV) and
`scheduler.py` (for execution).

---

### `Software/core/serial_comm.py`

**Purpose:** the *only* place in the Python codebase allowed to touch `pyserial`
directly — every other module talks to the Arduino exclusively through this class's
methods. This is a deliberate **encapsulation boundary**: if the transport ever
changed (e.g. Bluetooth instead of USB), only this file would need to change.

**Contains:** the `SerialComm` class, which is effectively a small **state
machine** wrapping a raw serial port with the specific request/response protocol
the firmware speaks.

**Key methods, in the order a session uses them:**

- **`connect()`** — opens the port, sleeps `SERIAL_CONNECT_WAIT` seconds (classic
  boards reset when the port opens), clears the input buffer, then confirms the
  firmware with `PING`/`PONG` (up to 3 tries). It no longer waits for `BOOT_OK`:
  the UNO R4's native USB does not reset on port open, so `BOOT_OK` may have been
  printed long before. Returns a `bool` rather than raising.
- **`send_command(arm, x_mm, servo, action)`** — the workhorse used during
  playback. Formats the protocol string, writes it, and calls `_wait_for_ready()`.
  **Retry logic is the interesting part here:** a *timeout* (no response at all) is
  treated as possibly transient — e.g. a brief brownout blip — and retried up to
  `MAX_CONSECUTIVE_SERIAL_ERRORS` times with a short cooldown between attempts. An
  explicit `ERROR:` response from the firmware is **not** retried — the firmware
  actively rejected that exact command, so resending identical bytes can't help and
  could compound a real fault. This is a meaningful engineering distinction: not
  all failures deserve the same response.
- **`send_home_command()`**, **`send_ping()`**, **`request_status()`**,
  **`send_calibration_command()`**, **`send_test_angle()`** — each wraps one
  request/response pair of the protocol, with its own appropriate timeout (homing
  can legitimately take up to 35 s; a ping should answer in 3).
- **`send_rest_all()`** — the safety command. Notably **does not require homing**
  on the firmware side, and is the one method `Scheduler.play()` always calls on
  the way out, success or failure.
- **`_wait_for_ready()` / `_read_line()`** — internal helpers. `_read_line()` is the
  single point where raw bytes become a decoded, trimmed string; `_wait_for_ready()`
  classifies what comes back into exactly three outcomes (`"ready"`, `"error"`,
  `"timeout"`) so callers never have to parse strings themselves.

**Inputs/outputs:** every public method takes plain Python values in, and returns
either a `bool` (success/fail) or an `Optional[str]` (status text) — it never
raises on a *protocol* failure, only on a genuine I/O exception during write
(`serial.SerialException`), which is itself caught and converted to a logged `False`.

**Connects to:** used by `app.py` (connect/home/calibrate buttons) and
`scheduler.py` (`send_command` during playback) and `utils/calibrate.py` (the whole
calibration wizard).

---

### `Software/core/scheduler.py`

**Purpose:** the real-time conductor. Given a list of `RobotCommand`s, actually
play them at the right moments, honoring both the music's timing and the operator's
ability to stop everything immediately.

**Contains:** the `Scheduler` class — holds a `threading.Event` (`_stop`) used as
a cooperative cancellation flag, and a `_running` flag for status display.

**`play(commands, on_progress, on_error)` — the central algorithm, step by step:**

1. Guard clauses first: empty list → trivially succeed; not connected → fail
   immediately with a clear error. **Fail fast on preconditions, before doing any
   work**, is the pattern here.
2. Record `start_wall = time.monotonic()` — `monotonic()` specifically, not
   `time.time()`, because monotonic clocks can't jump backward (e.g. from an NTP
   sync), which matters for a real-time scheduling loop that does subtraction on
   clock readings.
3. For each command, in order:
   - Check the stop flag *before* doing anything — this is what makes Stop
     responsive between commands.
   - Compute how long to wait until this command's absolute timestamp
     (`target_wall = start_wall + cmd.timestamp_s`), then sleep in small
     **10 ms increments**, rechecking the stop flag each increment — this is the
     mechanism that makes Emergency Stop interrupt a *wait*, not just a send. A
     single long `time.sleep(wait_s)` would make Stop unresponsive for the entire
     wait duration; chunking the sleep trades a tiny bit of timing precision for
     much better responsiveness.
   - After waiting, measure actual lag (`time.monotonic() - target_wall`) and log a
     warning if it exceeds `LAG_WARN_THRESHOLD_S` (100 ms) — a diagnostic, not a
     correction; the design intentionally falls behind tempo rather than ever
     dropping a note (documented in the module's own header comment).
   - Send the command via `serial_comm.send_command()`. If it fails, call
     `on_error` and break out — one failed command aborts the whole run rather than
     silently continuing into an unknown robot state.
   - On success, call `on_progress(idx+1, total, cmd)` so the caller (the UI) can
     update a progress bar without `Scheduler` knowing anything about Streamlit.
     This is a classic **callback / dependency-inversion** pattern: the low-level
     module doesn't import or know about the high-level UI at all.
4. **The `finally` block always runs**, regardless of whether the loop completed
   normally, broke on a Stop, or broke on an error: it calls
   `self._comm.send_rest_all()`. This is the single most important safety guarantee
   in the whole system — there is no code path through `play()` that skips it.

**`stop()`** — just sets the threading event. Doesn't touch the serial port, doesn't
block, doesn't try to interrupt an in-flight serial write — it relies entirely on the
play loop's own polling to notice and react, which is why the play loop polls as
finely as it does.

**Connects to:** consumes `RobotCommand` from `motion_planner.py`, drives
`serial_comm.py`, is itself driven by `app.py` from a background thread.

---

### `Software/app.py`

**Purpose:** the Streamlit user interface — the only file that's actually "run" by
the user, and the file that wires every other module together for a human operator.

**Structure, top to bottom, matching the actual execution order on every rerun:**

1. **Imports and page config** — `st.set_page_config()` must be the first Streamlit
   call in the script (a Streamlit requirement).
2. **Session-state defaults** — a dictionary of every piece of state that must
   survive a rerun, written with the `if k not in st.session_state` guard so it
   only initializes once per browser session, not on every rerun.
3. **Helper functions** — `add_log()` (append + cap the log at 100 entries — a
   simple **bounded buffer** pattern so the log can't grow unbounded over a long
   session) and `status_badge()` (pure presentation).
4. **Sidebar** — rendered top-to-bottom on *every* rerun (sidebars in Streamlit are
   not tab-scoped, hence "always visible"):
   - **Connect/Disconnect buttons** — note the `disabled=` parameter pattern used
     throughout this file: rather than letting the user click Connect twice and
     handling the resulting mess, the button is simply disabled when it wouldn't
     make sense. This is "make the invalid state unreachable" rather than "validate
     after the fact."
   - **Homing button** — disabled until connected; shows a spinner during the
     blocking `send_home_command()` call (this one *is* allowed to block the script,
     because homing has no "abort mid-way" requirement the way live playback does).
   - **Dry-run toggle** — a plain Streamlit widget; its boolean return value is read
     fresh on every rerun (Streamlit widgets persist their own value across reruns
     automatically, keyed by label/position).
5. **Tabs** — `st.tabs()` creates four containers; code inside each `with tab_x:`
   block only renders into that tab, but **all four blocks still execute on every
   rerun** regardless of which tab is visually selected — there's no "lazy" tab
   loading in this design.
6. **Tab 1 (Upload):** on file upload, runs the full pipeline
   (`parse_midi → MotionPlanner().plan()`) synchronously and stores the result in
   `st.session_state.commands`. Wrapped in `try/except` so a malformed MIDI file
   shows a friendly error instead of crashing the whole app.
7. **Tab 2 (Preview):** pure read/render of `st.session_state.commands` into a
   `pandas` DataFrame, with a CSV export button. No hardware interaction at all.
8. **Tab 3 (Playback)** — the most involved piece of UI logic. **Engineering
   problem this section solves:** Streamlit's rerun model (§2) means you cannot
   simply loop through commands inside a button's `if` block and expect the
   Emergency Stop button to be clickable mid-loop — that would tie the
   responsiveness of Stop to internals you don't control. The solution implemented
   here:
   - A worker thread (`_run_real` for real playback, `_run_dry` for dry-run) is
     started and **never joined** in the script thread.
   - The worker thread touches **only** a single plain dictionary
     (`st.session_state.playback_progress`), and only while holding
     `st.session_state.playback_lock` — never a Streamlit display call, never any
     other session-state key. This is the load-bearing rule: Streamlit elements and
     arbitrary session-state mutation are not safe to perform from a thread other
     than the active script-run thread.
   - The main script polls: while the worker is alive, it reads the dict (under the
     lock), renders the progress bar/status text from it, flushes any new log lines
     into the real log (a script-thread-only mutation, which *is* safe), sleeps
     ~200 ms, and calls `st.rerun()` again. Each of these reruns is fast, so a click
     on Emergency Stop is reachable almost immediately rather than only after the
     whole song finishes.
   - **Emergency Stop** itself doesn't touch the thread at all — it calls
     `.stop()` on whichever object is currently in `st.session_state.active_stopper`
     (the real `Scheduler` for live playback, or a tiny `_DryStop` stub for
     dry-run — kept as two *separate* references specifically so dry-run can never
     accidentally overwrite the real `Scheduler` object that live playback depends
     on).
   - `ready_to_play` / `is_playing` booleans gate which buttons are enabled,
     following the same "disable invalid actions" philosophy as the sidebar.
9. **Tab 4 (Log):** renders `st.session_state.log_msgs` reversed (newest first), with
   a Clear button.

**Connects to:** imports and orchestrates every other Python module; is the only
file that imports `streamlit`.

---

### `Software/utils/calibrate.py`

**Purpose:** a one-time (or occasional) interactive command-line wizard, separate
from the Streamlit app, for physically calibrating the two values that can't be
known in advance: real-world steps-per-mm and the servo press angle.

**Why a separate CLI script and not a Streamlit page:** calibration is an
inherently *blocking*, *sequential*, human-paced conversation ("press Enter when
ready", "measure with a ruler, type the number") — exactly the shape a plain
top-to-bottom script handles better than a rerun-based UI.

**Logic flow:**
1. Connect, then `send_ping()` as a fast sanity check before doing anything that
   moves a motor.
2. Run homing interactively (with an explicit "clear the rails" prompt — a real
   safety pause, not decoration).
3. Send the `CAL` command (moves Arm A exactly 100 mm per the *current* config
   value), ask the human to measure the actual physical distance with a ruler, and
   compute the corrected `STEPS_PER_MM` via simple proportional math:
   `corrected = current_spm * 100 / measured`.
4. Sweep candidate servo angles by calling `send_test_angle(angle)` for each value
   in `[50, 60, 70, 75, 80, 85, 90]`, asking after each one whether the key was
   actually depressed, and stopping at the first "yes". This uses the firmware's
   `TESTANGLE` debug command specifically so each iteration of the loop tests a
   *genuinely different* physical angle (see the Firmware section below for why
   that command exists).
5. Prints a final summary block telling the human exactly which two values to copy
   into `Config/config.py` **and** the `.ino` firmware file (the values are not
   auto-synced — calibration updates the *numbers*, but propagating them into both
   files is still a manual step).

**Connects to:** uses `SerialComm` directly; reads (but doesn't write) `config.py`
for the current `STEPS_PER_MM` baseline.

---

### `Software/tests/test_motion_planner.py`

**Purpose:** automated, hardware-free verification of `motion_planner.py`'s
correctness — the only code in this project that's pure and deterministic enough to
unit-test meaningfully without simulating serial hardware.

**Contains:** a `pytest` test class, one test method per behavior: arm assignment,
action-character mapping, the key invariant (every press lands on the intended
key), release-with-the-same-finger, no hand movement with a finger down, chords,
repeated notes, look-ahead, rail limits, serial string formatting, and timestamp
order. `test_end_to_end.py` runs planner → scheduler → serial → firmware simulator. Each test builds a minimal
`NoteEvent` list with `make_event()` and asserts on the resulting `RobotCommand`
list — a standard **arrange/act/assert** unit test shape.

**Connects to:** imports `motion_planner.py`, `midi_parser.py` (for `NoteEvent`),
and `config.py` (for the key lists used as test fixtures). Runs with no Arduino, no
Streamlit, and no serial port.

---

### `Firmware/PianoRobot_Firmware/PianoRobot_Firmware.ino`

**Purpose:** the real-time control program running on the Arduino itself —
everything that must happen with hard timing guarantees (stepping motors, writing
PWM, watching elapsed time) lives here, because Python over USB serial cannot
guarantee timing tight enough for motor control.

**Global state:**
- `stepperA`, `stepperB` — `AccelStepper` objects, one per rail, each managing
  acceleration-ramped step/direction pulse generation.
- `pca` — the `Adafruit_PWMServoDriver` object talking to the PCA9685 over I2C.
- `isHomed` — `bool`, gates all playback commands.
- `channelPressed[10]` / `channelPressedAt[10]` — parallel arrays tracking, per
  PCA9685 channel, whether a finger is currently pressed and the `millis()`
  timestamp it was pressed at. This is the data the safety watchdog reads.

**`setup()` — runs once at boot:**
1. Start serial at 115200 baud, then `while (!Serial) { ; }` — a busy-wait specific
   to USB-native boards like the UNO R4, which need the USB connection actually
   enumerated before `Serial` is usable.
2. Configure both steppers' max speed/acceleration from the tuned constants.
3. Initialize I2C and the PCA9685, set its PWM frequency to 50 Hz (the standard rate
   hobby servos expect).
4. Park all 10 fingers at rest — staggered (`SERVO_STAGGER_MS` between each), and
   with an extended settle delay after — specifically to avoid 10 servos all
   drawing their startup current spike at the exact same instant, which is harder
   on a marginal power rail.
5. Print `BOOT_OK`, then immediately `ERROR:NOT_HOMED` — telling the Python side,
   unprompted, exactly what it still needs to do before anything else will work.

**`loop()` — runs forever, every pass:**
1. **`checkServoSafety()` runs first, unconditionally, every single pass** — this
   ordering matters: the watchdog must never depend on a command actually arriving.
2. If a serial line is available, read it, trim it, and dispatch by exact string
   match: `H`/`HOME` → `performHoming()`; `CAL` → `performCalibrationMove()`;
   `PING` → reply `PONG`; `STATUS` → `printStatus()`; `RESTALL` → `raiseAllFingers()`
   + reply; `TESTANGLE,<deg>` → calibration-only single-finger angle test. **All of
   these are intentionally allowed before homing** — they're either harmless
   queries or explicit safety/calibration actions, not playback.
3. Anything else falls through to the homed guard: if `!isHomed`, reply
   `ERROR:NOT_HOMED` and do nothing else. Only past this guard does
   `parseAndExecute(cmd)` run — this is the firmware's own enforcement of the same
   rule the Python UI also enforces, so a misbehaving or bypassed UI can never get
   the robot to move before it's homed.

**`parseAndExecute(cmd)` — one playback command, in full:**
1. Validate minimum length and that the first character is a valid arm letter —
   reject early with a specific `ERROR:` rather than attempting to parse garbage.
2. Find the three comma separators with `indexOf`, and reject with `ERROR:BAD_FORMAT`
   if any are missing — a deliberately simple, dependency-free parser (no
   `sscanf`/regex) appropriate for a fixed, known protocol on a microcontroller.
3. Parse `x_mm` (float), `servo_num` (int), `action` (char).
4. **Clamp** `x_mm` into `[SAFE_MARGIN_MM, rail_max - SAFE_MARGIN_MM]` using
   `constrain()` — the firmware's own last line of defense, independent of
   `motion_planner.py`'s clamping on the Python side; a command that somehow arrived
   unclamped (corrupted bytes, a future caller that isn't `motion_planner.py`)
   still can't drive a rail into its physical end-stop.
5. Convert millimeters to steps (`mmToSteps`) and command the relevant stepper to
   move there, **blocking** in a `while (distanceToGo() != 0) { run(); checkServoSafety(); }`
   loop until it arrives. Calling the safety check inside this loop (not only once
   per outer `loop()` pass) matters specifically because a long carriage move can
   otherwise run for well over a second — long enough, on its own, to silently
   consume most of the `SERVO_MAX_HOLD_MS` budget for a finger that was pressed just
   before the move started.
6. If a finger action is requested (`servo_num` 1–5 and action isn't move-only),
   look up the PCA9685 channel for that arm/finger, compute the target angle
   (press vs. rest), call `setServoAngle()`, and wait `SERVO_SETTLE_MS` for the
   mechanism to physically settle.
7. Print `READY` — the handshake Python's `_wait_for_ready()` is listening for.

**`performHoming()`:** raises all fingers first (so nothing is dragging during the
crawl), then for each arm in turn: switch to the slow `HOMING_SPEED`/`HOMING_ACCEL`
profile, crawl a large negative distance (deliberately slightly more than the rail's
physical length, so it reliably reaches the frame even with positional drift), call
that position zero, retract `HOMING_RETRACT_MM`, and re-zero from there. Restores
the normal motion profile at the end and sets `isHomed = true`.

**`performCalibrationMove()`:** moves Arm A exactly 100 mm according to the
*currently configured* `STEPS_PER_MM`, so a human can measure the real distance and
back-calculate the true value — this is the firmware half of `calibrate.py` step 3.

**`printStatus()`:** reports homed state and both arms' current position in mm —
a read-only diagnostic, no motion.

**`setServoAngle(channel, degrees, isPress)`:**
1. `constrain()` the angle into a valid 0–180° range defensively.
2. `map()` degrees onto PCA9685 pulse-count units: the comment block explains the
   math plainly — 50 Hz means a 20 ms period, which the PCA9685 quantizes into 4096
   counts; this firmware uses a 0.5–2.5 ms pulse-width range (counts 102–512)
   rather than the more common 1–2 ms range, because MG90S servos in practice often
   need the wider range to reach their full mechanical travel.
3. Updates `channelPressed[]`/`channelPressedAt[]` — **this bookkeeping update is
   the only place those arrays are ever written**, which is what makes the watchdog
   trustworthy: every path that presses or releases a finger goes through this one
   function.

**`raiseAllFingers()`:** loops over both arms' 5 channels, calling
`setServoAngle(..., REST, false)` for each with `SERVO_STAGGER_MS` between writes —
used at boot, at the start of homing, and by `RESTALL`. The staggering is the same
current-spike mitigation as in `setup()`.

**`checkServoSafety()` — the watchdog, called from every blocking loop in this
file:** for each of the 10 channels, if it's marked pressed and
`millis() - channelPressedAt[ch] > SERVO_MAX_HOLD_MS`, force it to REST via a direct
PCA9685 write, clear its pressed flag, and report `ERROR:AUTO_RELEASE_CH<n>`. This
is a **pure software safety net** — it cannot and does not compensate for an
overvoltage condition (that's a hardware fix, documented in `README.md`); it only
guarantees nothing stays mechanically loaded indefinitely if a RELEASE command is
ever lost (e.g. a comms drop mid-song).

**`mmToSteps()` / `stepsToMm()`:** trivial linear unit conversions using
`STEPS_PER_MM`, used everywhere position math happens.

**Connects to:** speaks the exact line-based protocol that `serial_comm.py`
implements on the Python side; every constant referenced here has a hand-mirrored
counterpart in `Config/config.py`.

---

## 4. Software Architecture — The Full Flow

```
                         ┌──────────────────────────┐
                         │   Software/app.py         │   ← what the human runs
                         │   (entry point, UI)       │
                         └────────────┬─────────────┘
                                      │
              ┌───────────────────────┼────────────────────────┐
              ▼                       ▼                        ▼
   ┌─────────────────┐     ┌─────────────────────┐    ┌──────────────────┐
   │  midi_parser.py  │ →  │  motion_planner.py   │ →  │   scheduler.py    │
   │ INITIALIZATION:  │    │ PROCESSING LOGIC:     │    │ CONTROL DECISION: │
   │ parse the file   │    │ note → arm/position/  │    │ when to send each │
   │ into NoteEvents  │    │ finger/action mapping │    │ command, in real  │
   └─────────────────┘    └─────────────────────┘    │ time, with stop    │
                                                       │ checks throughout  │
                                                       └─────────┬─────────┘
                                                                 ▼
                                                       ┌──────────────────┐
                                                       │  serial_comm.py   │
                                                       │ ACTUATOR OUTPUT:  │
                                                       │ formats + sends   │
                                                       │ the protocol line │
                                                       └─────────┬─────────┘
                                                                 │ USB Serial
                                                                 ▼
                                            ┌───────────────────────────────────┐
                                            │     PianoRobot_Firmware.ino        │
                                            │  SENSOR READING: none (no limit    │
                                            │   switches/encoders — open-loop)   │
                                            │  PROCESSING LOGIC: parseAndExecute │
                                            │  CONTROL DECISION: clamp position,  │
                                            │   pick channel, pick angle          │
                                            │  ACTUATOR OUTPUT: stepper.run(),    │
                                            │   pca.setPWM()                      │
                                            └────────────────┬────────────────────┘
                                                              │
                                                              ▼
                                                     ┌─────────────────┐
                                                     │ FEEDBACK/UPDATE  │
                                                     │ "READY"/"ERROR"  │
                                                     │ handshake back   │
                                                     │ to Python, which │
                                                     │ updates the      │
                                                     │ progress UI and  │
                                                     │ log              │
                                                     └─────────────────┘
```

**A note on "sensor reading" in this design:** this robot is explicitly **open-loop**
— there are no limit switches, no rotary encoders, no force sensors. "Homing" is a
timed slow crawl into a mechanical end-stop rather than a sensor-confirmed zero, and
"how far did the arm move" is *inferred* from how many steps were commanded, not
measured. This is a real, documented limitation (see `README.md` → Known
Limitations and Future Improvements), not a bug — and it's important to call out
because it shapes several other design choices (the calibration ritual with a
physical ruler; clamping positions defensively in two separate places; the
auto-release watchdog as a software-only safety net rather than something a sensor
could close the loop on).

---

## 5. Programming Concepts Used, Section by Section

| Concept | Where it shows up | Why it's the right tool here |
|---|---|---|
| **Functions** | Nearly everywhere (`parse_midi()`, `setServoAngle()`, etc.) | Single-responsibility units that are independently testable and readable |
| **Classes** | `NoteEvent`, `RobotCommand` (dataclasses); `MotionPlanner`, `SerialComm`, `Scheduler` | Dataclasses give you a typed, self-documenting record with free `__init__`/`__repr__`; the "real" classes encapsulate state (an open serial port, a stop flag) that a free function would have to pass around awkwardly |
| **Conditions (if/elif)** | Arm assignment, action mapping, every guard clause (`if not connected: return False`) | Fail-fast validation and branching on robot/connection state |
| **Loops (`for`)** | Iterating MIDI events, iterating `RobotCommand`s, the angle sweep, raising all fingers | Bulk, ordered processing of a known sequence |
| **Loops (`while`)** | `Scheduler.play()`'s chunked sleep; every Arduino blocking stepper-move loop (`while distanceToGo() != 0`) | Condition-driven waiting where the exit condition (time elapsed, motor arrived) isn't known in advance as a count |
| **Modulo arithmetic** | `note_id % 12` black-key detection | Cyclic mapping — wrapping a growing index back into a fixed-size range (one octave) |
| **State machines** | `SerialComm` (disconnected → connected → homed-via-firmware-state); the Arduino's `isHomed` flag gating command execution | Some operations are only valid in some states; encoding that explicitly prevents whole classes of bugs |
| **Threading** | Streamlit's background playback worker thread; `threading.Event` as a cooperative stop flag; `threading.Lock` protecting the shared progress dict | Lets a long-running operation (a multi-minute song) proceed without freezing the UI thread, while a `Lock` prevents the UI thread and worker thread from reading/writing the same dictionary at the same instant and corrupting it |
| **Callbacks (dependency inversion)** | `Scheduler.play(on_progress=..., on_error=...)` | Lets the low-level scheduler report progress without ever importing or knowing about Streamlit |
| **Communication protocol (custom, line-based, over serial)** | The entire PC↔Arduino exchange | A simple, human-readable, easily-logged text protocol — appropriate given the data rate needed (a handful of short commands per second, not megabits) |
| **Handshaking** | Every command waits for `READY` before the next is sent | Guarantees the Arduino's small serial input buffer is never overrun and that each mechanical action is confirmed complete before the next begins |
| **Retry logic with classification** | `send_command()`'s timeout-vs-error distinction | Not all failures are equal; treating them identically either masks real faults (retrying forever) or aborts unnecessarily on a one-off blip |
| **PWM (Pulse-Width Modulation)** | `pca.setPWM(channel, 0, pulse)` driving each MG90S servo | Hobby servos are positioned by pulse width within a fixed-frequency cycle — the PCA9685 generates this in hardware so the Arduino doesn't have to bit-bang 10 channels' timing itself |
| **Open-loop motion control** | `AccelStepper` step/direction pulses with no position feedback | The whole stepper-motion subsystem trusts that "I commanded N steps" equals "I moved N steps" — true only if the motor never stalls, which is why speed/accel were deliberately reduced (less risk of missed steps) |
| **Software timers (non-blocking elapsed-time checks)** | `millis() - channelPressedAt[ch] > SERVO_MAX_HOLD_MS` | The classic Arduino pattern for "has X time passed" without ever calling `delay()`, which would freeze everything else |
| **Watchdog pattern** | `checkServoSafety()` | A periodic, independent check that catches a fault (a stuck press) regardless of what the "main" logic was doing when the fault occurred |
| **Bounded buffers** | The 100-entry log cap in `app.py` | Prevents unbounded memory growth over a long session without losing recent (most useful) history |
| **Unit testing / pure functions** | `motion_planner.py` + its test file | Code with no I/O and no hidden state is the code you can actually verify automatically, which is why the design deliberately keeps this one module side-effect-free |

---

## 6. Quick-Reference: "What happens when...?"

- **...the program starts?** Arduino: `setup()` parks fingers and announces
  `BOOT_OK`/`NOT_HOMED`. Python: `app.py` renders the sidebar and four tabs with
  everything disabled until you connect.
- **...a sensor gives data?** This design has no sensors in the conventional sense
  (no limit switches/encoders) — the closest equivalent is the Arduino reading the
  *serial input buffer* (a command "arriving") and the Python side reading the
  Arduino's text *replies* (`READY`/`HOMED`/`ERROR:...`) as its only feedback signal.
- **...an actuator receives a command?** A stepper receives a target position and
  blocks (while still running the safety watchdog) until it physically arrives; a
  servo receives a target angle via PWM and the firmware records when it was
  pressed, so the watchdog can act later if needed.
- **...something goes wrong mid-song?** `on_error` fires, the play loop breaks,
  and — no matter why it stopped — the `finally` block forces every finger to rest
  before control returns to the user.
- **...you click Emergency Stop?** A `threading.Event` is set; the play loop (or
  dry-run loop) notices on its next check (within ~10 ms for real playback), exits
  its loop, and the same `finally`/rest-all path runs as on any other stop.

---

*This document describes the software as it currently is (see `CHANGELOG.md` for changes). It is not a substitute for reading `docs/software_design.md` (a shorter,
module-level summary) or the source files themselves when making changes — but it
should mean you never have to start cold.*
