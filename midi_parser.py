"""
midi_parser.py — MIDI File Parser
===================================
Reads a MIDI file and converts it into a flat, time-ordered list of note
events. Each event carries an absolute timestamp in seconds, so the
scheduler can honour real musical tempo.

Only white keys within the robot's playable range are kept.
Black keys and out-of-range notes are silently filtered.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

try:
    import mido
except ImportError:
    raise ImportError("Install mido:  pip install mido")

from Config.config import ALL_PLAYABLE_KEYS

log = logging.getLogger(__name__)


@dataclass
class NoteEvent:
    """A single note on/off event extracted from a MIDI file."""
    timestamp_s: float   # Absolute time from file start, in seconds
    note_id:     int     # MIDI note number (21–108 for standard piano)
    action:      str     # "PRESS" or "RELEASE"
    velocity:    int     # 0–127 (0 always means RELEASE)

    def __repr__(self):
        return (f"NoteEvent(t={self.timestamp_s:.3f}s  "
                f"note={self.note_id}  {self.action}  vel={self.velocity})")


def parse_midi(source) -> List[NoteEvent]:
    """
    Parse a MIDI file into a list of NoteEvent objects sorted by timestamp.

    Args:
        source: File path (str) or file-like object (e.g. Streamlit UploadedFile)

    Returns:
        List of NoteEvent, filtered to robot-playable white keys only,
        sorted by ascending timestamp.

    Raises:
        ValueError: If the file cannot be parsed.
    """
    try:
        mid = mido.MidiFile(file=source) if hasattr(source, "read") else mido.MidiFile(source)
    except Exception as e:
        raise ValueError(f"Cannot read MIDI file: {e}") from e

    log.info(f"MIDI type={mid.type}  ticks_per_beat={mid.ticks_per_beat}  "
             f"tracks={len(mid.tracks)}")

    events: List[NoteEvent] = []
    skipped_black = 0
    skipped_range = 0

    # mido.MidiFile merged iteration handles multi-track sync and tempo maps
    current_s = 0.0
    for msg in mid:
        current_s += msg.time   # msg.time is already in seconds for MidiFile iteration

        if msg.type not in ("note_on", "note_off"):
            continue

        velocity = getattr(msg, "velocity", 0)
        is_press = (msg.type == "note_on" and velocity > 0)
        action   = "PRESS" if is_press else "RELEASE"

        # Filter: keep only robot-playable white keys
        if msg.note not in ALL_PLAYABLE_KEYS:
            # Distinguish black keys from out-of-range for diagnostics
            if _is_black_key(msg.note):
                skipped_black += 1
            else:
                skipped_range += 1
            continue

        events.append(NoteEvent(
            timestamp_s = round(current_s, 4),
            note_id     = msg.note,
            action      = action,
            velocity    = velocity,
        ))

    events.sort(key=lambda e: e.timestamp_s)

    log.info(f"Parsed {len(events)} playable events  "
             f"(skipped {skipped_black} black-key, {skipped_range} out-of-range)")

    if not events:
        log.warning("No playable notes found. Check key mapping in config.py")

    return events


def get_midi_duration_s(source) -> Optional[float]:
    """Return total duration of a MIDI file in seconds, or None on error."""
    try:
        mid = mido.MidiFile(file=source) if hasattr(source, "read") else mido.MidiFile(source)
        return mid.length
    except Exception:
        return None


# ── Internal helpers ──────────────────────────────────────────────────────────

# MIDI note positions within an octave that are black keys
_BLACK_KEY_POSITIONS = {1, 3, 6, 8, 10}   # C#, D#, F#, G#, A#

def _is_black_key(note_id: int) -> bool:
    return (note_id % 12) in _BLACK_KEY_POSITIONS
