# Testing Report — Piano Robot

## Test Categories

### T1 — Unit and end-to-end tests (no hardware required)
Run: `python -m pytest Software/tests/ -v`

| Test | What it checks |
|---|---|
| test_left_key_maps_to_arm_a / test_right_key_maps_to_arm_b | Arm assignment |
| test_black_key_is_skipped | Unplayable notes are dropped |
| test_every_press_lands_on_the_right_key | For every press, the finger under `(x_mm, servo)` is the intended key |
| test_release_uses_same_finger_and_position_as_press | Release matches its press |
| test_no_move_while_a_finger_is_down | Held fingers are released before the hand slides |
| test_notes_inside_the_hand_do_not_move_it | 5 adjacent keys → one position, fingers 1–5 |
| test_chord_in_one_hand | 3-note chord → one position, 3 fingers |
| test_repeated_note_is_released_then_pressed | Release before press at equal timestamps |
| test_lookahead_avoids_unneeded_moves | Look-ahead keeps the hand still for a scale |
| test_positions_within_rail_bounds / test_servo_in_range_1_to_5 | Limits |
| test_serial_format / test_output_sorted_by_timestamp / test_empty_event_list | Protocol and ordering |
| test_refuses_playback_before_homing | Firmware guard (via simulator) |
| test_full_song_plays_correct_keys | Ode to Joy through scheduler + serial + firmware simulator: every key correct, all fingers at rest afterwards |
| test_auto_release_notice_does_not_abort | Watchdog notice is not treated as a command failure |

Result: **18 / 18 pass.**

---

### T2 — Dry-Run Test (No Hardware Required)
1. Run `streamlit run Software/app.py`
2. Enable **Dry-run mode** in sidebar
3. Upload any `.mid` file
4. Click Start Playback
5. Verify progress bar advances and all commands appear in the log

Expected: completes without errors, motion plan table matches command count.

---

## Known Issues to Verify on Your Hardware

1. **`STEPS_PER_MM` calibration** — With NEMA 17 at full-step, GT2 belt, 20T pulley, the correct formula gives 5.0 steps/mm. Measure and confirm.

2. **Servo PWM pulse width** — MG90S servos sometimes need a wider pulse range than standard (0.5 ms–2.5 ms instead of 1 ms–2 ms). The firmware uses 102–512 counts at 50 Hz which covers 0.5–2.5 ms. If servos move to unexpected positions, adjust the `map()` range in `setServoAngle()`.

3. **PCA9685 I2C address** — Default is 0x40. If you see I2C errors in the Arduino Serial Monitor, check the solder bridge pads on the PCA9685 board and update `PCA9685_ADDRESS` in firmware and `config.py`.
