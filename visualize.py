"""
visualize.py — Animate a planned performance (top view)
========================================================
Runs the real MotionPlanner on a melody, executes the commands on the
firmware simulator (same timing rules as the Scheduler: wait for the note's
timestamp, then move with the AccelStepper speed/acceleration profile, then
press), and renders the result as an animated GIF.

    python tools/visualize.py                       # Ode to Joy → docs/images/simulation.gif
    python tools/visualize.py --midi song.mid       # any MIDI file (needs mido)
"""
import argparse
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, FancyBboxPatch
from matplotlib.animation import FuncAnimation, PillowWriter

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from Config.config import (  # noqa: E402
    LEFT_WHITE_KEYS, RIGHT_WHITE_KEYS, HAND_OFFSET_A_MM, HAND_OFFSET_B_MM,
    FINGER_SPACING_MM, FINGERS_PER_HAND,
)
from Software.core.motion_planner import MotionPlanner  # noqa: E402
from tools.firmware_sim import FirmwareSim  # noqa: E402

WHITE = sorted(LEFT_WHITE_KEYS + RIGHT_WHITE_KEYS)          # 48 … 83, contiguous white keys
NAMES = {0: "C", 2: "D", 4: "E", 5: "F", 7: "G", 9: "A", 11: "B"}
ARM = {
    'A': dict(keys=LEFT_WHITE_KEYS,  offset=HAND_OFFSET_A_MM, color="#2f6fdf", label="Hand A"),
    'B': dict(keys=RIGHT_WHITE_KEYS, offset=HAND_OFFSET_B_MM, color="#e8772e", label="Hand B"),
}


def note_name(n):
    return f"{NAMES[n % 12]}{n // 12 - 1}"


def simulate(events):
    cmds = MotionPlanner().plan(events)
    sim = FirmwareSim(time_scale=0)
    sim.write(b"H\n")
    sim.clock, sim.timeline, sim.presses, sim._out = 0.0, [], [], []
    for c in cmds:
        sim.clock = max(sim.clock, c.timestamp_s)       # scheduler waits for the note time
        sim.write((c.to_serial() + "\n").encode())
    return cmds, sim


def hand_base(arm, t, sim, start_x):
    """Carriage position → key index under finger 1 at time t."""
    x = start_x[arm]
    for t0, t1, a, x0, x1 in sim.timeline:
        if a != arm or t0 > t:
            continue
        x = x1 if t >= t1 else x0 + (x1 - x0) * (t - t0) / max(t1 - t0, 1e-6)
    return (x - ARM[arm]['offset']) / FINGER_SPACING_MM


def fingers_down(arm, t, sim):
    down = {}
    for tp, a, x, f, action in sim.presses:
        if a != arm or tp > t:
            continue
        if action == 'P':
            down[f] = True
        else:
            down.pop(f, None)
    return set(down)


def render(events, title, out_path, fps=12):
    cmds, sim = simulate(events)
    first_x = {}
    for t0, t1, a, x0, x1 in sim.timeline:
        first_x.setdefault(a, x0)
    for c in cmds:
        first_x.setdefault(c.arm, c.x_mm)
    end_t = max([p[0] for p in sim.presses] + [0]) + 0.8
    midi_end = max(e.timestamp_s for e in events)

    fig, ax = plt.subplots(figsize=(9, 3.6), dpi=90)
    fig.patch.set_facecolor("#14161c")

    def draw(frame):
        t = frame / fps
        ax.clear()
        ax.set_xlim(-0.5, len(WHITE) + 0.5); ax.set_ylim(-0.2, 6.2); ax.axis("off")
        ax.set_facecolor("#14161c")
        pressed_keys = {}
        hands = {}
        for arm, cfg in ARM.items():
            base = hand_base(arm, t, sim, first_x)
            g0 = WHITE.index(cfg['keys'][0]) + base          # global white index of finger 1
            hands[arm] = g0
            for f in fingers_down(arm, t, sim):
                gi = round(g0 + f - 1)
                if 0 <= gi < len(WHITE):
                    pressed_keys[gi] = cfg['color']
        # keyboard
        for i, n in enumerate(WHITE):
            ax.add_patch(Rectangle((i, 0), 0.96, 2.6, color=pressed_keys.get(i, "#f4f4f2"), zorder=1))
            ax.text(i + 0.48, 0.25, note_name(n), ha="center", va="bottom", fontsize=6.5,
                    color="#222" if i not in pressed_keys else "white", zorder=3)
            if n % 12 in (0, 2, 5, 7, 9) and n + 1 <= 83:   # black key to the right
                ax.add_patch(Rectangle((i + 0.68, 1.05), 0.58, 1.55, color="#111", zorder=2))
        # rail and hands
        ax.plot([-0.3, len(WHITE) + 0.3], [4.55, 4.55], color="#666", lw=3, zorder=0)
        for arm, cfg in ARM.items():
            g0 = hands[arm]; down = fingers_down(arm, t, sim)
            ax.add_patch(FancyBboxPatch((g0 - 0.1, 3.9), FINGERS_PER_HAND + 0.0, 1.3,
                         boxstyle="round,pad=0.05", color=cfg['color'], alpha=0.9, zorder=4))
            ax.text(g0 + FINGERS_PER_HAND / 2 - 0.1, 4.55, cfg['label'], ha="center", va="center",
                    color="white", fontsize=9, weight="bold", zorder=5)
            for f in range(1, FINGERS_PER_HAND + 1):
                x = g0 + f - 1 + 0.18
                y0, h = (2.0, 1.9) if f in down else (2.75, 1.15)
                ax.add_patch(Rectangle((x, y0), 0.6, h, color="white" if f in down else "#cfd3da",
                                       ec=cfg['color'], lw=1.5, zorder=4))
        ax.text(0, 5.75, title, color="white", fontsize=11, weight="bold", va="center")
        ax.text(len(WHITE), 5.75, f"t = {t:4.1f} s", color="#bbb", fontsize=10, ha="right", va="center")
        return []

    frames = int(end_t * fps)
    anim = FuncAnimation(fig, draw, frames=frames, interval=1000 / fps)
    anim.save(out_path, writer=PillowWriter(fps=fps))
    plt.close(fig)
    return cmds, sim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--midi", help="MIDI file (needs mido)")
    ap.add_argument("--bpm", type=float, default=100)
    ap.add_argument("--out", default=str(ROOT / "docs" / "images" / "simulation.gif"))
    args = ap.parse_args()
    if args.midi:
        from Software.core.midi_parser import parse_midi
        events, title = parse_midi(args.midi), Path(args.midi).stem
    else:
        from tools.songs import ode_to_joy
        events, title = ode_to_joy(bpm=args.bpm), "Ode to Joy — planned by the real motion planner"
    cmds, sim = render(events, title, args.out)
    presses = sum(1 for c in cmds if c.action == 'P')
    print(f"{presses} presses, {len(sim.timeline)} hand moves → {args.out}")


if __name__ == "__main__":
    main()
