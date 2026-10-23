# Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Needed progressively more voltage (9→10→11→12 V) to get the same motion, then it stopped working | Possibly servo overvoltage causing heat and component damage (see `docs/power_notes.md`); not confirmed | Stop running the servo rail above 6 V. If the servos have been run like this repeatedly, inspect them and replace any that are damaged — a hardware fault would not be fixed by a config change |
| `ERROR:AUTO_RELEASE_CHx` appears during playback | A finger was held pressed >3 s without a release — possibly a missed/garbled command, or possibly the system struggling under thermal/voltage stress | Check the Log around that timestamp for nearby errors; if it keeps recurring, consider a power-stability problem as one possible cause |
| `ERROR:NOT_HOMED` when trying to play | Homing wasn't run after the last connect/power cycle | Sidebar → Run Homing Sequence |
| Commands intermittently retry (visible as repeated TX in debug log) before succeeding | Transient serial glitch — could be USB, could be a power-rail blip affecting the Arduino | Occasional retries are tolerated by design; if it happens on nearly every command, suspect supply instability, not the USB cable first |
| Playback aborts mid-song with "Arduino failed on command N" | All retries exhausted, or firmware sent an explicit `ERROR:` (bad format, out-of-range position, etc.) | Check the Log tab for the specific Arduino error string; if it's a timeout-type failure recurring across many songs, that's consistent with intermittent power instability |
| A servo feels hot to the touch after normal use | Possibly running above its rated 6 V | At 12 V this is likely and could damage the servo — see `docs/power_notes.md` |
| A4988 driver feels very hot / intermittently stalls | Thermal limiting from running outside its comfortable current/voltage window for this current-limit setting | Reduce `STEPPER_MAX_SPEED`/`STEPPER_ACCEL` further in `Config/config.py` (mirror the same values into the firmware `.ino` and re-upload) and let it cool before retesting |
| Homing seems to grind or skip at the end of travel | Normal for this design — no limit switches, it crawls into the frame. Worse skipping than usual may indicate step loss, possibly from voltage instability | Re-run homing; if skipping is new or worse than before, a power-rail problem is one possible cause |
| Emergency Stop doesn't seem to do anything | Earlier bug, now fixed — the Stop button is only enabled while playback is actually running, and works whether you're in dry-run or real playback | Use the latest code; confirm the button is enabled (greyed out = nothing is playing) |
| Angle sweep in the calibration tool always "feels the same" at every angle | Earlier bug, now fixed — the sweep used to always press at the fixed firmware angle regardless of which value it printed | Use the latest code and re-upload the firmware so `TESTANGLE` is available |

---
