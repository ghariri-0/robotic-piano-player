"""
app.py — Piano Robot Streamlit Interface
==========================================
Run with:  streamlit run Software/app.py

Provides:
  • MIDI file upload and preview
  • Connection management with status indicators
  • Dry-run mode (simulate without hardware)
  • Live command preview table
  • Real-time playback progress
  • Emergency stop button
"""

import sys
import time
import logging
import threading
from pathlib import Path

import streamlit as st
import pandas as pd

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from Config.config import SERIAL_PORT, SERIAL_BAUD
from Software.core.midi_parser   import parse_midi, get_midi_duration_s
from Software.core.motion_planner import MotionPlanner
from Software.core.serial_comm   import SerialComm
from Software.core.scheduler     import Scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
log = logging.getLogger(__name__)

# ── Page Config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Piano Robot Controller",
    page_icon="🎹",
    layout="wide",
)

# ── Session State Defaults ───────────────────────────────────────────────────
defaults = {
    "connected":   False,
    "homed":       False,
    "comm":        None,
    "scheduler":   None,
    "commands":    [],
    "stop_event":  threading.Event(),
    "log_msgs":    [],
    # Non-blocking playback tracking (real or dry-run). The worker thread
    # only ever touches `playback_progress` (under `playback_lock`) — it
    # never calls Streamlit display functions or mutates other session_state
    # keys directly, since both are unsafe from a non-script thread. The
    # main script thread reads the dict on each rerun and renders it.
    "playback_thread":   None,
    "playback_lock":     threading.Lock(),
    "playback_progress": {
        "idx": 0, "total": 0, "cmd_str": "", "done": False,
        "success": None, "errors": [], "log_lines": [],
    },
    # Whichever object Emergency Stop should call .stop() on for the run
    # currently in flight — the real Scheduler for live playback, or a tiny
    # stub for dry-run. Kept separate from `scheduler` so dry-run can never
    # clobber the real Scheduler instance used for live playback.
    "active_stopper":    None,
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ── Helpers ──────────────────────────────────────────────────────────────────
def add_log(msg: str):
    st.session_state.log_msgs.append(msg)
    if len(st.session_state.log_msgs) > 100:
        st.session_state.log_msgs = st.session_state.log_msgs[-100:]

def status_badge(ok: bool, label_ok: str, label_fail: str) -> str:
    return f"🟢 {label_ok}" if ok else f"🔴 {label_fail}"

# ── Sidebar ──────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("🎹 Piano Robot")
    st.markdown("---")

    # Connection
    st.subheader("🔌 Hardware Connection")
    port_input = st.text_input("COM Port", value=SERIAL_PORT)

    col_con, col_dis = st.columns(2)

    with col_con:
        if st.button("Connect", disabled=st.session_state.connected):
            comm = SerialComm(port=port_input, baud=SERIAL_BAUD)
            if comm.connect():
                st.session_state.comm      = comm
                st.session_state.scheduler = Scheduler(comm)
                st.session_state.connected = True
                add_log(f"✓ Connected to {port_input}")
                st.rerun()
            else:
                st.error(f"Cannot connect to {port_input}")
                add_log(f"✗ Connection failed: {port_input}")

    with col_dis:
        if st.button("Disconnect", disabled=not st.session_state.connected):
            if st.session_state.comm:
                st.session_state.comm.disconnect()
            st.session_state.connected = False
            st.session_state.homed     = False
            st.session_state.comm      = None
            st.session_state.scheduler = None
            add_log("Disconnected")
            st.rerun()

    st.markdown(
        status_badge(st.session_state.connected, "Connected", "Disconnected")
    )

    st.markdown("---")

    # Homing
    st.subheader("🏠 Homing")
    st.caption("Must home before first playback after power-up.")

    if st.button("Run Homing Sequence", disabled=not st.session_state.connected):
        with st.spinner("Homing in progress (up to 30 s)..."):
            ok = st.session_state.comm.send_home_command()
        if ok:
            st.session_state.homed = True
            add_log("✓ Homing complete")
            st.success("Homed!")
        else:
            add_log("✗ Homing failed")
            st.error("Homing failed — check wiring")

    st.markdown(status_badge(st.session_state.homed, "Arms homed", "Not homed"))

    st.markdown("---")

    # Dry-run toggle
    st.subheader("⚙️ Options")
    dry_run = st.toggle("Dry-run mode (no hardware)", value=False)
    if dry_run:
        st.info("Commands will be simulated — no serial output.")

    st.markdown("---")
    st.caption("Robotic Piano Player")

# ── Main Area ─────────────────────────────────────────────────────────────────
st.title("Piano Robot — Control Panel")

tab_upload, tab_preview, tab_play, tab_log = st.tabs(
    ["📂 Upload MIDI", "📋 Motion Plan", "▶️ Playback", "📜 Log"]
)

# ── TAB 1: Upload ─────────────────────────────────────────────────────────────
with tab_upload:
    st.header("Upload a MIDI File")
    uploaded = st.file_uploader("Choose a .mid or .midi file", type=["mid", "midi"])

    if uploaded:
        st.success(f"File loaded: **{uploaded.name}**")

        duration = get_midi_duration_s(uploaded)
        if duration:
            st.metric("Estimated duration", f"{duration:.1f} s")
        uploaded.seek(0)

        with st.spinner("Parsing MIDI..."):
            try:
                events = parse_midi(uploaded)
                planner = MotionPlanner()
                commands = planner.plan(events)
                st.session_state.commands = commands
                add_log(f"Parsed {len(events)} events → {len(commands)} commands from {uploaded.name}")
                st.metric("Playable commands", len(commands))
                st.metric("Notes parsed", len(events))
            except Exception as e:
                st.error(f"Failed to parse MIDI: {e}")
                add_log(f"Parse error: {e}")

# ── TAB 2: Motion Plan Preview ────────────────────────────────────────────────
with tab_preview:
    st.header("Motion Plan Preview")
    cmds = st.session_state.commands

    if not cmds:
        st.info("Upload a MIDI file first to see the motion plan.")
    else:
        rows = [{
            "Time (s)":  f"{c.timestamp_s:.3f}",
            "Arm":       c.arm,
            "Position (mm)": f"{c.x_mm:.1f}",
            "Finger":    c.servo,
            "Action":    "PRESS" if c.action == "P" else ("RELEASE" if c.action == "R" else "MOVE"),
            "Note ID":   c.note_id,
            "Serial cmd": c.to_serial(),
        } for c in cmds]

        df = pd.DataFrame(rows)

        col_a, col_b, col_m = st.columns(3)
        arm_a = df[df["Arm"] == "A"]
        arm_b = df[df["Arm"] == "B"]
        col_a.metric("Arm A commands", len(arm_a))
        col_b.metric("Arm B commands", len(arm_b))
        col_m.metric("Total", len(df))

        st.dataframe(df, use_container_width=True, height=400)

        csv = df.to_csv(index=False).encode()
        st.download_button(
            "Download plan as CSV",
            data=csv,
            file_name="motion_plan.csv",
            mime="text/csv",
        )

# ── TAB 3: Playback ───────────────────────────────────────────────────────────
with tab_play:
    st.header("Playback Control")

    cmds = st.session_state.commands
    is_playing = (
        st.session_state.playback_thread is not None
        and st.session_state.playback_thread.is_alive()
    )
    ready_to_play = (
        bool(cmds)
        and not is_playing
        and (dry_run or (st.session_state.connected and st.session_state.homed))
    )

    if not cmds:
        st.warning("No commands loaded. Upload a MIDI file first.")
    elif is_playing:
        st.info("Playback in progress...")
    elif not dry_run and not st.session_state.connected:
        st.warning("Not connected to Arduino. Connect in the sidebar or enable Dry-run mode.")
    elif not dry_run and not st.session_state.homed:
        st.warning("Arms not homed. Run Homing in the sidebar first.")
    else:
        st.success(f"Ready — {len(cmds)} commands queued")

    col_play, col_stop = st.columns([1, 1])

    progress_bar  = st.progress(0)
    status_text   = st.empty()
    command_text  = st.empty()

    with col_play:
        start_clicked = st.button(
            "▶️  Start Playback",
            disabled=not ready_to_play,
            type="primary",
        )

    with col_stop:
        stop_clicked = st.button("⏹  Emergency Stop", type="secondary", disabled=not is_playing)

    # ── Emergency Stop ───────────────────────────────────────────────────────
    # IMPORTANT: this must be reachable on a quick, unblocked rerun — it does
    # NOT wait on the worker thread. Calling .stop() just sets a
    # threading.Event the worker thread is already polling between commands.
    if stop_clicked and st.session_state.active_stopper:
        st.session_state.active_stopper.stop()
        st.warning("Stop signal sent — waiting for current command to finish.")
        add_log("Emergency stop triggered by user")

    # ── Start a new playback run (non-blocking) ──────────────────────────────
    if start_clicked and ready_to_play:
        total = len(cmds)
        add_log(f"Playback started — {total} commands" + (" (dry-run)" if dry_run else ""))

        with st.session_state.playback_lock:
            st.session_state.playback_progress = {
                "idx": 0, "total": total, "cmd_str": "", "done": False,
                "success": None, "errors": [], "log_lines": [],
            }

        def _update_progress(idx, total, cmd, error=None, log_line=None):
            """Called from the worker thread — touches ONLY this plain dict
            under the lock. Never calls st.* or other session_state keys
            here; those are unsafe to touch from a non-script thread."""
            with st.session_state.playback_lock:
                p = st.session_state.playback_progress
                p["idx"] = idx
                p["total"] = total
                p["cmd_str"] = cmd.to_serial() if cmd is not None else p["cmd_str"]
                if error:
                    p["errors"].append(error)
                if log_line:
                    p["log_lines"].append(log_line)

        def _run_dry(commands, stopper):
            for i, cmd in enumerate(commands):
                if stopper._stop.is_set():
                    _update_progress(i, len(commands), cmd, log_line="Dry-run stopped by user")
                    break
                _update_progress(i + 1, len(commands), cmd)
                time.sleep(0.02)
            with st.session_state.playback_lock:
                st.session_state.playback_progress["done"] = True
                st.session_state.playback_progress["success"] = True

        def _run_real(sched, commands):
            def on_progress(idx, total, cmd):
                _update_progress(idx, total, cmd)
            def on_error(msg):
                _update_progress(
                    st.session_state.playback_progress["idx"],
                    len(commands), None, error=msg, log_line=f"ERROR: {msg}",
                )
            ok = sched.play(commands, on_progress=on_progress, on_error=on_error)
            with st.session_state.playback_lock:
                st.session_state.playback_progress["done"] = True
                st.session_state.playback_progress["success"] = ok

        if dry_run:
            # A lightweight stop stub lets dry-run share the exact same
            # Emergency Stop button and polling logic as real playback,
            # instead of running as an unstoppable blocking loop. This is
            # kept separate from `scheduler` (see active_stopper above) so
            # it never overwrites the real Scheduler used for live playback.
            class _DryStop:
                def __init__(self):
                    self._stop = threading.Event()
                def stop(self):
                    self._stop.set()
            stopper = _DryStop()
            st.session_state.active_stopper = stopper
            thread = threading.Thread(target=_run_dry, args=(cmds, stopper), daemon=True)
        else:
            sched = st.session_state.scheduler
            st.session_state.active_stopper = sched
            thread = threading.Thread(target=_run_real, args=(sched, cmds), daemon=True)

        st.session_state.playback_thread = thread
        thread.start()
        st.rerun()

    # ── Poll and render progress (does NOT block on the thread) ─────────────
    if is_playing or (
        st.session_state.playback_thread is not None
        and st.session_state.playback_progress["done"]
        and not st.session_state.playback_progress.get("_rendered")
    ):
        with st.session_state.playback_lock:
            p = dict(st.session_state.playback_progress)
            # Flush any new log lines collected by the worker into the real
            # log — this mutation happens here, in the script thread, which
            # is safe (unlike mutating session_state from the worker thread).
            for line in p["log_lines"]:
                add_log(line)
            st.session_state.playback_progress["log_lines"] = []

        total = max(p["total"], 1)
        progress_bar.progress(min(p["idx"] / total, 1.0))
        status_text.text(f"Playing {p['idx']}/{p['total']}" + (f"  —  {p['cmd_str']}" if p["cmd_str"] else ""))
        if p["cmd_str"]:
            command_text.code(p["cmd_str"])

        if p["done"]:
            if p["success"]:
                st.success("🎉 Dry-run complete!" if dry_run else "🎉 Playback complete!")
                add_log("Dry-run completed successfully" if dry_run else "Playback completed successfully")
            else:
                st.error(f"Playback failed: {'; '.join(p['errors']) if p['errors'] else 'see log'}")
            st.session_state.playback_progress["_rendered"] = True
            st.session_state.playback_thread = None
        else:
            time.sleep(0.2)
            st.rerun()

# ── TAB 4: Log ────────────────────────────────────────────────────────────────
with tab_log:
    st.header("Session Log")
    msgs = st.session_state.log_msgs
    if msgs:
        log_text = "\n".join(reversed(msgs))
        st.text_area("Events (newest first)", value=log_text, height=400)
        if st.button("Clear log"):
            st.session_state.log_msgs = []
            st.rerun()
    else:
        st.info("No log entries yet.")
