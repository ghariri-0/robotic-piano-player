"""
test_motion_planner.py — Unit Tests
=====================================
Run with:  python -m pytest Software/tests/ -v
No hardware required — all tests run offline.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from Software.core.motion_planner import MotionPlanner, key_under_finger
from Software.core.midi_parser    import NoteEvent
from Config.config import (
    LEFT_WHITE_KEYS, RIGHT_WHITE_KEYS, FINGERS_PER_HAND,
    RAIL_A_MAX_MM, RAIL_B_MAX_MM, RAIL_SAFE_MARGIN_MM,
)


def ev(note_id, action="PRESS", t=0.0):
    return NoteEvent(timestamp_s=t, note_id=note_id, action=action, velocity=80)


def melody(notes, step=0.5, hold=0.4):
    out = []
    for i, n in enumerate(notes):
        out += [ev(n, "PRESS", i * step), ev(n, "RELEASE", i * step + hold)]
    return out


class TestMotionPlanner:
    def setup_method(self):
        self.planner = MotionPlanner()

    # ── Arm assignment ────────────────────────────────────────────────────────
    def test_left_key_maps_to_arm_a(self):
        assert self.planner.plan([ev(LEFT_WHITE_KEYS[0])])[0].arm == 'A'

    def test_right_key_maps_to_arm_b(self):
        assert self.planner.plan([ev(RIGHT_WHITE_KEYS[0])])[0].arm == 'B'

    def test_black_key_is_skipped(self):
        assert self.planner.plan([ev(61)]) == []

    # ── The key invariant: every press lands on the intended key ─────────────
    def test_every_press_lands_on_the_right_key(self):
        notes = LEFT_WHITE_KEYS + RIGHT_WHITE_KEYS + LEFT_WHITE_KEYS[::-1] + RIGHT_WHITE_KEYS[::-1]
        cmds = self.planner.plan(melody(notes))
        presses = [c for c in cmds if c.action == 'P']
        assert len(presses) == len(notes)
        for c in presses:
            assert key_under_finger(c.arm, c.x_mm, c.servo) == c.note_id, c

    def test_release_uses_same_finger_and_position_as_press(self):
        cmds = self.planner.plan(melody([60, 64, 50, 59]))
        last_press = {}
        for c in cmds:
            if c.action == 'P':
                last_press[c.note_id] = (c.x_mm, c.servo)
            elif c.action == 'R':
                assert last_press[c.note_id] == (c.x_mm, c.servo)

    def test_no_move_while_a_finger_is_down(self):
        # 48 held while 62 (outside the hand window) is pressed → 48 must be released first
        events = [ev(48, "PRESS", 0.0), ev(62, "PRESS", 0.2),
                  ev(48, "RELEASE", 1.0), ev(62, "RELEASE", 1.2)]
        cmds = self.planner.plan(events)
        pos, down = {}, {}
        for c in cmds:
            if c.arm in pos and c.x_mm != pos[c.arm]:
                assert not down.get(c.arm), f"hand moved with fingers down: {c}"
            pos[c.arm] = c.x_mm
            fingers = down.setdefault(c.arm, set())
            (fingers.add if c.action == 'P' else fingers.discard)(c.servo)
        assert self.planner.last_stats.early_releases == 1

    def test_notes_inside_the_hand_do_not_move_it(self):
        five = LEFT_WHITE_KEYS[:FINGERS_PER_HAND]
        cmds = self.planner.plan(melody(five))
        assert len({c.x_mm for c in cmds}) == 1
        assert sorted(c.servo for c in cmds if c.action == 'P') == [1, 2, 3, 4, 5]

    def test_chord_in_one_hand(self):
        chord = [ev(n, "PRESS", 0.0) for n in (48, 52, 55)] + \
                [ev(n, "RELEASE", 1.0) for n in (48, 52, 55)]
        cmds = self.planner.plan(chord)
        presses = [c for c in cmds if c.action == 'P']
        assert len({c.x_mm for c in presses}) == 1 and len({c.servo for c in presses}) == 3

    def test_repeated_note_is_released_then_pressed(self):
        cmds = self.planner.plan([ev(60, "PRESS", 0.0), ev(60, "RELEASE", 0.5),
                                  ev(60, "PRESS", 0.5), ev(60, "RELEASE", 1.0)])
        assert [c.action for c in cmds] == ['P', 'R', 'P', 'R']

    def test_lookahead_avoids_unneeded_moves(self):
        # C3 D3 E3 F3 G3 A3 → a hand at base 1..5 covers D3..A3 after C3
        cmds = self.planner.plan(melody([48, 50, 52, 53, 55, 57]))
        assert self.planner.last_stats.moves <= 1

    # ── Limits and format ─────────────────────────────────────────────────────
    def test_positions_within_rail_bounds(self):
        cmds = self.planner.plan(melody(LEFT_WHITE_KEYS + RIGHT_WHITE_KEYS))
        for c in cmds:
            rail_max = RAIL_A_MAX_MM if c.arm == 'A' else RAIL_B_MAX_MM
            assert RAIL_SAFE_MARGIN_MM <= c.x_mm <= rail_max - RAIL_SAFE_MARGIN_MM

    def test_servo_in_range_1_to_5(self):
        cmds = self.planner.plan(melody(LEFT_WHITE_KEYS + RIGHT_WHITE_KEYS))
        assert all(1 <= c.servo <= 5 for c in cmds)

    def test_serial_format(self):
        s = self.planner.plan([ev(LEFT_WHITE_KEYS[2])])[0].to_serial()
        parts = s.split(',')
        assert len(parts) == 4 and parts[0] in ('A', 'B') and parts[3] in ('P', 'R', 'M')
        float(parts[1]); int(parts[2])

    def test_output_sorted_by_timestamp(self):
        events = [ev(LEFT_WHITE_KEYS[0], t=1.5), ev(RIGHT_WHITE_KEYS[0], t=0.5),
                  ev(LEFT_WHITE_KEYS[1], t=1.0)]
        ts = [c.timestamp_s for c in self.planner.plan(events)]
        assert ts == sorted(ts)

    def test_empty_event_list(self):
        assert self.planner.plan([]) == []
