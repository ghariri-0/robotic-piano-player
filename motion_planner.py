"""
motion_planner.py — Motion Planning Engine
============================================
Converts a list of NoteEvent objects into a sequence of RobotCommand objects
that the scheduler feeds to the Arduino one by one.

Hand model
----------
Each arm carries a hand with 5 fingers, one white key apart. The carriage
position x_mm is where finger 1 sits, so with the hand at "base" key index b,
finger f is above key index b + (f - 1). To press key k the planner must
choose a hand position b and the finger f = k - b + 1 together.

(An earlier approach moved the carriage to key k AND rotated the finger number,
so finger f landed (f - 1) keys to the right of the intended key for 4 out of
5 notes.)

Planning rules
--------------
  1. Assign each note to Arm A (left) or Arm B (right).
  2. If the key is already under a finger of the current hand position,
     press it without moving.
  3. Otherwise move the hand. Among the positions that cover the key, pick
     the one that also covers the most upcoming notes of that arm
     (look-ahead), then the one closest to the current position.
  4. Never slide the hand while a finger is down: any held finger on that
     arm is released first (an early release is better than dragging a
     finger across the keys).
  5. A RELEASE uses the same finger that pressed the key and does not move.
  6. All positions are checked against the safe rail limits.

Independent dual-rail architecture means arms cannot physically collide,
so no inter-arm separation logic is needed.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from Config.config import (
    FINGERS_PER_HAND, FINGER_SPACING_MM,
    HAND_OFFSET_A_MM, HAND_OFFSET_B_MM, PLANNER_LOOKAHEAD,
    LEFT_WHITE_KEYS, RIGHT_WHITE_KEYS,
    RAIL_A_MAX_MM, RAIL_B_MAX_MM, RAIL_SAFE_MARGIN_MM,
)
from Software.core.midi_parser import NoteEvent

log = logging.getLogger(__name__)


@dataclass
class RobotCommand:
    """
    One complete instruction for the Arduino.
    Sent as: "<arm>,<x_mm>,<servo>,<action>"
    """
    timestamp_s: float   # When to send this command (seconds from start)
    arm:         str     # 'A' or 'B'
    x_mm:        float   # Carriage position in mm (where finger 1 sits)
    servo:       int     # Finger 1–5, or 0 for move-only
    action:      str     # 'P' | 'R' | 'M'
    note_id:     int     # Original MIDI note (for diagnostics)

    def to_serial(self) -> str:
        """Format as the serial protocol string the Arduino expects."""
        return f"{self.arm},{self.x_mm:.2f},{self.servo},{self.action}"

    def __repr__(self):
        return (f"RobotCommand(t={self.timestamp_s:.3f}s  "
                f"{self.arm}  x={self.x_mm:.1f}mm  "
                f"servo={self.servo}  {self.action}  note={self.note_id})")


@dataclass
class _ArmState:
    keys:     List[int]
    offset:   float
    rail_max: float
    base:     Optional[int] = None                      # key index under finger 1
    held:     Dict[int, int] = field(default_factory=dict)   # note → finger

    @property
    def max_base(self) -> int:
        return max(0, len(self.keys) - FINGERS_PER_HAND)

    def x_mm(self, base: int) -> float:
        return self.offset + base * FINGER_SPACING_MM


@dataclass
class PlanStats:
    presses: int = 0
    moves: int = 0
    early_releases: int = 0
    skipped_notes: int = 0


class MotionPlanner:
    """
    NoteEvent list → RobotCommand list.

    Call plan(events) to get the full command sequence; `last_stats` then
    holds counts of presses, hand moves and early releases.
    """

    def __init__(self):
        self.last_stats = PlanStats()

    def plan(self, events: List[NoteEvent]) -> List[RobotCommand]:
        """
        Convert parsed MIDI events into robot commands, sorted by timestamp.
        At equal timestamps releases are handled before presses, so a
        repeated note is released and pressed again.
        """
        arms = {
            'A': _ArmState(LEFT_WHITE_KEYS,  HAND_OFFSET_A_MM, RAIL_A_MAX_MM),
            'B': _ArmState(RIGHT_WHITE_KEYS, HAND_OFFSET_B_MM, RAIL_B_MAX_MM),
        }
        stats = PlanStats()
        ordered = sorted(events, key=lambda e: (e.timestamp_s, e.action != "RELEASE"))
        commands: List[RobotCommand] = []

        for i, ev in enumerate(ordered):
            arm_id = self._arm_for(ev.note_id)
            if arm_id is None:
                log.debug(f"Note {ev.note_id} not in any arm mapping — skipped")
                stats.skipped_notes += 1
                continue
            arm = arms[arm_id]
            k = arm.keys.index(ev.note_id)

            if ev.action == "RELEASE":
                finger = arm.held.pop(ev.note_id, None)
                if finger is not None:
                    commands.append(self._cmd(ev, arm_id, arm, finger, 'R'))
                continue    # already released early, or never pressed

            # ── PRESS ────────────────────────────────────────────────────
            if ev.note_id in arm.held:                      # pressed again without release
                commands.append(self._cmd(ev, arm_id, arm, arm.held.pop(ev.note_id), 'R'))

            if arm.base is None or not (arm.base <= k < arm.base + FINGERS_PER_HAND):
                upcoming = self._upcoming_keys(ordered, i, arm_id, arm)
                new_base = self._choose_base(k, arm, upcoming)
                # Rule 4: never slide with a finger down
                for note, finger in list(arm.held.items()):
                    commands.append(self._cmd(ev, arm_id, arm, finger, 'R', note_id=note))
                    stats.early_releases += 1
                arm.held.clear()
                if arm.base is not None:
                    stats.moves += 1
                arm.base = new_base

            finger = k - arm.base + 1
            arm.held[ev.note_id] = finger
            commands.append(self._cmd(ev, arm_id, arm, finger, 'P'))
            stats.presses += 1

        self.last_stats = stats
        log.info(f"Motion plan: {len(commands)} commands, {stats.presses} presses, "
                 f"{stats.moves} hand moves, {stats.early_releases} early releases, "
                 f"{stats.skipped_notes} notes skipped")
        return sorted(commands, key=lambda c: c.timestamp_s)   # stable sort

    # ── Internal ─────────────────────────────────────────────────────────────

    @staticmethod
    def _arm_for(note: int) -> Optional[str]:
        if note in LEFT_WHITE_KEYS:
            return 'A'
        if note in RIGHT_WHITE_KEYS:
            return 'B'
        return None

    @staticmethod
    def _upcoming_keys(ordered: List[NoteEvent], i: int, arm_id: str, arm: _ArmState) -> List[int]:
        keys: List[int] = []
        for ev in ordered[i + 1:]:
            if len(keys) >= PLANNER_LOOKAHEAD:
                break
            if ev.action == "PRESS" and ev.note_id in arm.keys and \
                    MotionPlanner._arm_for(ev.note_id) == arm_id:
                keys.append(arm.keys.index(ev.note_id))
        return keys

    @staticmethod
    def _choose_base(k: int, arm: _ArmState, upcoming: List[int]) -> int:
        """Hand position covering key k that covers the most upcoming keys."""
        lo = max(0, k - FINGERS_PER_HAND + 1)
        hi = min(k, arm.max_base)
        candidates = range(lo, hi + 1) if lo <= hi else [min(max(0, k), arm.max_base)]

        def score(b: int) -> Tuple[int, float]:
            covered = 0
            for u in upcoming:              # count consecutive upcoming keys covered
                if b <= u < b + FINGERS_PER_HAND:
                    covered += 1
                else:
                    break
            dist = abs(b - arm.base) if arm.base is not None else abs(b - (k - FINGERS_PER_HAND // 2))
            return (covered, -dist)

        return max(candidates, key=score)

    @staticmethod
    def _cmd(ev: NoteEvent, arm_id: str, arm: _ArmState, finger: int, action: str,
             note_id: Optional[int] = None) -> RobotCommand:
        x = arm.x_mm(arm.base)
        x_min, x_max = RAIL_SAFE_MARGIN_MM, arm.rail_max - RAIL_SAFE_MARGIN_MM
        if not (x_min <= x <= x_max):
            log.error(f"Hand position {x:.1f} mm outside safe range [{x_min}, {x_max}] "
                      f"for Rail {arm_id} — check HAND_OFFSET and rail length in config")
            x = max(x_min, min(x, x_max))
        return RobotCommand(
            timestamp_s = ev.timestamp_s,
            arm         = arm_id,
            x_mm        = round(x, 2),
            servo       = finger,
            action      = action,
            note_id     = ev.note_id if note_id is None else note_id,
        )


def key_under_finger(arm: str, x_mm: float, finger: int) -> Optional[int]:
    """
    Inverse of the plan: which MIDI note is under `finger` when the carriage
    is at x_mm? Used by tests and the simulator to verify every press.
    """
    keys   = LEFT_WHITE_KEYS if arm == 'A' else RIGHT_WHITE_KEYS
    offset = HAND_OFFSET_A_MM if arm == 'A' else HAND_OFFSET_B_MM
    pos = (x_mm - offset) / FINGER_SPACING_MM + (finger - 1)
    idx = round(pos)
    if abs(pos - idx) > 0.05 or not (0 <= idx < len(keys)):
        return None
    return keys[idx]
