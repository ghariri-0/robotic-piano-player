"""Short public-domain melodies as NoteEvent lists (no MIDI file needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Software.core.midi_parser import NoteEvent  # noqa: E402

# Beethoven — Ode to Joy (right hand) in C major, with a simple left-hand bass.
ODE_RH = "E4 E4 F4 G4 G4 F4 E4 D4 C4 C4 D4 E4 E4 D4 D4".split()
ODE_RH_BEATS = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.5, 0.5, 2]
ODE_LH = [("C3", 0, 4), ("G3", 4, 4), ("C3", 8, 4), ("G3", 12, 4)]   # (note, start beat, beats)

NOTE_OFFSETS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def midi_number(name: str) -> int:
    return 12 * (int(name[-1]) + 1) + NOTE_OFFSETS[name[0]]


def ode_to_joy(bpm: float = 100, transpose_octaves: int = 1):
    """Right hand transposed up one octave so it lands on the right arm's keys."""
    beat = 60.0 / bpm
    events, t = [], 0.0
    for name, beats in zip(ODE_RH, ODE_RH_BEATS):
        n = midi_number(name) + 12 * transpose_octaves
        events.append(NoteEvent(round(t, 4), n, "PRESS", 80))
        events.append(NoteEvent(round(t + beats * beat * 0.85, 4), n, "RELEASE", 0))
        t += beats * beat
    for name, start, beats in ODE_LH:
        n = midi_number(name)
        events.append(NoteEvent(round(start * beat, 4), n, "PRESS", 70))
        events.append(NoteEvent(round((start + beats) * beat * 0.97, 4), n, "RELEASE", 0))
    return sorted(events, key=lambda e: e.timestamp_s)
