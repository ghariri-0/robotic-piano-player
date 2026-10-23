"""
End-to-end: planner → scheduler → SerialComm → firmware simulator.
Checks the serial protocol, homing guard, handshake, and that every key the
simulated robot actually presses is the intended note.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from Software.core.motion_planner import MotionPlanner, key_under_finger
from Software.core.scheduler import Scheduler
from Software.core.serial_comm import SerialComm
from tools.firmware_sim import FirmwareSim
from tools.songs import ode_to_joy


def connect():
    sim = FirmwareSim(time_scale=0)
    comm = SerialComm("SIM", serial_factory=lambda *a: sim)
    assert comm.connect()
    return comm, sim


def test_refuses_playback_before_homing():
    comm, _ = connect()
    assert comm.send_command('A', 50.0, 1, 'P') is False


def test_full_song_plays_correct_keys():
    comm, sim = connect()
    assert comm.send_home_command()
    events = ode_to_joy(bpm=600)            # fast tempo: the test should not take long
    cmds = MotionPlanner().plan(events)
    assert Scheduler(comm).play(cmds) is True
    played = [key_under_finger(arm, x, f) for _, arm, x, f, a in sim.presses if a == 'P']
    expected = [e.note_id for e in sorted(events, key=lambda e: e.timestamp_s) if e.action == "PRESS"]
    assert sorted(played) == sorted(expected)
    assert not sim.pressed, "all fingers must be at rest after playback"


def test_auto_release_notice_does_not_abort(monkeypatch):
    comm, sim = connect()
    comm.send_home_command()
    sim._out.append("ERROR:AUTO_RELEASE_CH3")          # asynchronous watchdog notice
    assert comm.send_command('A', 50.0, 1, 'P') is True
