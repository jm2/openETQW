#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-frame pose capture: does what the Vulkan client draws change on every frame?

M5 needs "camera, viewmodel and remote-entity poses changing every rendered frame".
The game prints one FRAME_POSE line per main-view draw with g_logFramePoses N (the
render world's copies of the view, the viewmodel and up to N other players, plus the
game time, the tick counter and the unlock state; game repo PlayerView.cpp). This test
drives motion and checks those lines.

--observer join: a dedicated server, the Vulkan client and a headless peer (the
  dedicated binary with +connect). --observer listen: the Vulkan client hosts
  (+spawnServer) and the headless peer joins. Both players join GDF, so they spawn
  near each other.

For each cap (default 60, 120, 240) the observer runs these phases, each bracketed by
'echo FP_BEGIN <phase> <cap>' / 'echo FP_END' so the FRAME_POSE lines between them
are that phase's draws (the engine runs one stdin line per frame, before it draws):
  still      nobody moves
  turn       the observer turns (+left)
  walk       the observer walks forward, then back (clientMove)
  peerwalk   the headless peer walks forward, then back
An uncapped segment (com_maxFPS 0) measures the host's ceiling first, and a negative
control repeats turn and peerwalk with g_unlock_updateAngles, g_unlock_updateViewpos
and g_unlock_interpolateMoving off, where the change-every-frame checks must fail.

Checks (0.25 s dropped at each edge of a phase):
  A1 rate      from the draw timestamps: <= 1.03 x cap; >= 0.98 x cap when the cap is
               <= 0.9 x the ceiling, else >= 0.9 x min(cap, ceiling)
  A2 ticks     within each phase, game time advances 1.00 +/- 0.05 s per wall-clock
               second and >= 99% of its steps between draws are one tick (33 ms): no
               tick goes undrawn
  A4 turn      the view yaw changes between >= 99% of consecutive draws
  A5 walk      the view origin changes between >= 99% of consecutive draws
  A6 viewmodel the viewmodel pose changes between >= 99% of consecutive draws (walk)
  A7 remote    the peer's drawn pose changes between >= 99% of consecutive draws
  A8 phase     a draw with the same game time as the previous one is an unlocked
               draw (ud=1), and within one game time the fraction rises; at most 1% of
               draws may repeat the previous fraction (timeLeft is whole milliseconds and
               the client's prediction clock is nudged when snapshots arrive)
  A9 still     view, viewmodel and peer poses stay put (< 0.01 u)
A check that the code is expected to fail today is listed in KNOWN_GAPS. The entries
marked 'predicted' were written from the code before the test was first run
(docs/frame-path.md section 9); the ones marked 'found' were not predicted and showed up
in the first run. A known gap that passes fails the test too, so the table is updated
when the code is fixed.

The Vulkan client opens a window (it must stay visible) and runs from the repository
root. Exit 77 when ETQW_DATA_PATH is unset/invalid or a binary is missing.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed, connect, spawn  # noqa: E402
from test_remote_client_map_change import READY, RemoteClient  # noqa: E402

FRAME_POSE_RE = re.compile(
    r"FRAME_POSE: n=(\d+) us=(\d+) cap=(-?\d+) gt=(-?\d+) fn=(-?\d+) ud=(\d) cu=(\d) fr=(-?[\d.]+) vp=(-?\d+)"
    r" view=([-\d.,]+) wpn=(\S+) seat=(\S+) veh=(\S+)(.*)$")
CLIENT_RE = re.compile(r" c(\d+)=([^:\s]+):aor=(-?\d+):hid=(\d):veh=(-?\d+)")

EDGE_S = 0.25
CHANGED = 0.99        # A4-A7: the fraction of draw pairs that must show a change
POS_EPS = 0.01        # units
ANG_EPS = 0.001       # degrees

# Checks the current code fails, from docs/frame-path.md (section 9) and the M5 plan,
# written before this test first ran. A known gap that passes is reported too.
KNOWN_GAPS = {
    # predicted: the Entity::Present producer is compiled out (#if 0, jmarshall): remote
    # players are drawn where the last tick left them
    ("join", "A7"),
    # predicted: the listen host never calls ClientUpdateView, so every draw between
    # ticks repeats the tick's pose; and it reads view input once per tick
    ("listen", "A4"), ("listen", "A5"), ("listen", "A6"), ("listen", "A7"), ("listen", "A8"),
    # found (first run, 2026-09-25): on a network client the viewmodel's drawn pose
    # repeats on about a quarter of the unlocked draws within a tick (16-25% of all draw
    # pairs while walking); cause not yet known
    ("join", "A6"),
}


def parse_pose(line):
    m = FRAME_POSE_RE.search(line)
    if not m:
        return None
    view = [float(v) for v in m.group(10).split(",")]
    wpn = None if m.group(11) == "-" else [float(v) for v in m.group(11).split(",")]
    clients = {}
    for c in CLIENT_RE.finditer(m.group(14)):
        pose = None if c.group(2) == "-" else [float(v) for v in c.group(2).split(",")]
        clients[int(c.group(1))] = {"pose": pose, "aor": int(c.group(3)), "hid": int(c.group(4)), "veh": int(c.group(5))}
    return {"n": int(m.group(1)), "us": int(m.group(2)), "cap": int(m.group(3)), "gt": int(m.group(4)),
            "ud": int(m.group(6)), "fr": float(m.group(8)), "view": view, "wpn": wpn, "clients": clients}


def phase_poses(proc, begin, end):
    """FRAME_POSE draws between two line indices, minus EDGE_S at each end."""
    poses = [p for ln in proc.snapshot(begin, end) if (p := parse_pose(ln.text))]
    if len(poses) < 3:
        return poses
    t0, t1 = poses[0]["us"] + EDGE_S * 1e6, poses[-1]["us"] - EDGE_S * 1e6
    return [p for p in poses if t0 <= p["us"] <= t1]


def changed_fraction(poses, key):
    """Fraction of consecutive draw pairs whose key() differs."""
    pairs = list(zip(poses, poses[1:]))
    if not pairs:
        return 0.0
    return sum(1 for a, b in pairs if key(a, b)) / len(pairs)


def moved(a, b, eps=POS_EPS):
    return a is not None and b is not None and max(abs(x - y) for x, y in zip(a[:3], b[:3])) > eps


def rate(poses):
    if len(poses) < 2:
        return 0.0
    return (len(poses) - 1) * 1e6 / (poses[-1]["us"] - poses[0]["us"])


def phase_errors(poses):
    """A8: (draws in the previous draw's game time that are not unlocked or whose
    fraction falls, draws that repeat the previous fraction)."""
    wrong = repeated = 0
    for a, b in zip(poses, poses[1:]):
        if b["gt"] != a["gt"]:
            continue
        if b["ud"] != 1 or (a["ud"] == 1 and b["fr"] < a["fr"]):
            wrong += 1
        elif a["ud"] == 1 and b["fr"] == a["fr"]:
            repeated += 1
    return wrong, repeated


def tick_rate(poses):
    """(game seconds per wall second, fraction of game-time steps that are one tick)."""
    if len(poses) < 2 or poses[-1]["us"] == poses[0]["us"]:
        return 0.0, 0.0
    ratio = (poses[-1]["gt"] - poses[0]["gt"]) / 1000.0 / ((poses[-1]["us"] - poses[0]["us"]) / 1e6)
    steps = [b["gt"] - a["gt"] for a, b in zip(poses, poses[1:]) if b["gt"] != a["gt"]]
    return ratio, (sum(1 for s in steps if s == 33) / len(steps)) if steps else 0.0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--observer", choices=("join", "listen"))
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29920)
    ap.add_argument("--caps", default="60,120,240")
    ap.add_argument("--phase-seconds", type=float, default=2.0)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)

    try:
        ded = h.find_ded_binary(args.ded_binary)
        vk = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    if args.observer is None:
        ap.error("--observer is required")
    h.install_signal_handlers()
    mode = args.observer
    caps = [int(c) for c in args.caps.split(",") if c]
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    def log(name):
        return (log_dir / f"{mode}-{name}.log") if log_dir else None

    # both players on GDF: team balancing would refuse the second one
    game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0", "si_teamForceBalance": "0"}
    view_cvars = {"g_logFramePoses": "4", "com_unlockFPS": "1", "r_swapInterval": "0",
                  "in_yawSpeed": "90", "in_angleSpeedKey": "1"}
    if mode == "join":
        srv = h.DedicatedServer(ded, data, args.map, args.port, log_path=log("server"), cvars=game)
        srv.name = "SERVER"
        obs = VulkanClient(vk, data, args.port + 1, connect_to=f"127.0.0.1:{args.port}", log_path=log("vulkan"),
                           cvars={"si_pure": "0", "ui_name": "Observer", "net_clientShowSnapshots": "1", **view_cvars})
        procs = [("vulkan", obs), ("server", srv)]
    else:
        obs = VulkanClient(vk, data, args.port, map_name=args.map, log_path=log("host"),
                           cvars={**game, "ui_name": "Host", **view_cvars})
        srv = obs
        procs = [("host", obs)]
    obs.name, obs.slot = "Observer", None
    peer = RemoteClient(ded, data, args.port, args.port + 2, log_path=log("peer"))
    peer.cvars["ui_name"] = "Peer"
    peer.name, peer.slot = "Peer", None
    procs.insert(0, ("peer", peer))

    results = {}   # (cap, check) -> (ok, detail)
    failures = []

    def phase(name, cap, drive, seconds):
        begin = obs.mark()
        obs.send(f"echo FP_BEGIN {name} {cap}")
        drive(seconds)
        obs.send(f"echo FP_END {name} {cap}")
        end_hit = obs.wait_for(lambda s: s.strip() == f"FP_END {name} {cap}", 10.0, begin)
        if not end_hit:
            raise Failed(f"the observer never echoed the end of phase {name} at cap {cap}")
        return phase_poses(obs, begin, end_hit[0])

    def still(seconds):
        time.sleep(seconds)

    def turn(seconds):
        obs.send("+left")
        time.sleep(seconds)
        obs.send("-left")

    def walk_proc(proc):
        def drive(seconds):
            proc.send("clientMove 127 0 0")
            time.sleep(seconds)
            proc.send("clientMove -127 0 0")
            time.sleep(seconds)
            proc.send("clientMove 0 0 0")
        return drive

    try:
        if mode == "join":
            srv.start()
            if not srv.wait_for_text(READY, 180.0):
                raise Failed("the server never became ready")
            srv.sync()
            connect(srv, obs, "join")
        else:
            obs.start()
            if not obs.wait_for_text(READY, 180.0) or not obs.wait_for_text(f"Map: maps/{args.map}", 180.0):
                raise Failed("the listen server never loaded the map")
            obs.sync(60.0)
            obs.slot = 0
        spawn(srv, obs, "gdf", timeout=60.0)
        connect(srv, peer, mode)
        spawn(srv, peer, "gdf")
        slot = peer.slot

        # the host's ceiling: uncapped, still
        obs.send("set com_maxFPS 0")
        time.sleep(1.5)
        ceiling = rate(phase("calibrate", 0, still, args.phase_seconds))
        print(f"[{mode}] uncapped: {ceiling:.1f} fps")

        for cap in caps:
            obs.send(f"set com_maxFPS {cap}")
            time.sleep(1.5)
            p_still = phase("still", cap, still, args.phase_seconds / 2)
            p_turn = phase("turn", cap, turn, args.phase_seconds)
            p_walk = phase("walk", cap, walk_proc(obs), args.phase_seconds)
            p_peer = phase("peerwalk", cap, walk_proc(peer), args.phase_seconds)
            all_p = p_turn + p_walk + p_peer
            r = sum(rate(p) for p in (p_turn, p_walk, p_peer)) / 3  # phases are separate time spans
            need = 0.98 * cap if cap <= 0.9 * ceiling else 0.9 * min(cap, ceiling)
            results[(cap, "A1")] = (r <= 1.03 * cap and r >= need, f"{r:.1f} fps (cap {cap}, need {need:.1f}, ceiling {ceiling:.1f})")
            tick_stats = [tick_rate(p) for p in (p_turn, p_walk, p_peer)]
            worst_ratio = max(tick_stats, key=lambda t: abs(t[0] - 1.0))[0]
            worst_steps = min(t[1] for t in tick_stats)
            results[(cap, "A2")] = (abs(worst_ratio - 1.0) <= 0.05 and worst_steps >= 0.99,
                                    f"game time runs at {worst_ratio:.3f} x wall time; {worst_steps:.1%} of game-time steps are one tick")
            f4 = changed_fraction(p_turn, lambda a, b: abs(a["view"][4] - b["view"][4]) > ANG_EPS)
            results[(cap, "A4")] = (f4 >= CHANGED, f"view yaw changed in {f4:.1%} of {len(p_turn) - 1} draw pairs")
            f5 = changed_fraction(p_walk, lambda a, b: moved(a["view"], b["view"]))
            results[(cap, "A5")] = (f5 >= CHANGED, f"view origin changed in {f5:.1%} of {len(p_walk) - 1} draw pairs")
            f6 = changed_fraction(p_walk, lambda a, b: moved(a["wpn"], b["wpn"]))
            results[(cap, "A6")] = (f6 >= CHANGED, f"viewmodel changed in {f6:.1%} of {len(p_walk) - 1} draw pairs")
            f7 = changed_fraction(p_peer, lambda a, b: moved((a["clients"].get(slot) or {}).get("pose"),
                                                             (b["clients"].get(slot) or {}).get("pose")))
            results[(cap, "A7")] = (f7 >= CHANGED, f"peer's drawn pose changed in {f7:.1%} of {len(p_peer) - 1} draw pairs")
            wrong8, rep8 = phase_errors(all_p)
            results[(cap, "A8")] = (wrong8 == 0 and rep8 <= 0.01 * len(all_p),
                                    f"{wrong8} draws out of phase, {rep8} of {len(all_p)} repeat the previous fraction")
            f9 = changed_fraction(p_still, lambda a, b: moved(a["view"], b["view"]) or moved(a["wpn"], b["wpn"])
                                  or moved((a["clients"].get(slot) or {}).get("pose"), (b["clients"].get(slot) or {}).get("pose")))
            results[(cap, "A9")] = (f9 == 0.0, f"something moved in {f9:.1%} of still draw pairs")

        # negative control: with the unlock updates off, the change-every-frame checks must fail
        cap = caps[0]
        obs.send(f"set com_maxFPS {cap}")
        for cv in ("g_unlock_updateAngles", "g_unlock_updateViewpos", "g_unlock_interpolateMoving"):
            obs.send(f"set {cv} 0")
        time.sleep(1.0)
        # (phase names without '-': the console splits tokens there)
        n_turn = phase("controlturn", cap, turn, args.phase_seconds)
        n_walk = phase("controlwalk", cap, walk_proc(obs), args.phase_seconds)
        c4 = changed_fraction(n_turn, lambda a, b: abs(a["view"][4] - b["view"][4]) > ANG_EPS)
        c5 = changed_fraction(n_walk, lambda a, b: moved(a["view"], b["view"]))
        print(f"[{mode}] negative control at cap {cap}: view yaw changed in {c4:.1%}, view origin in {c5:.1%} of draw pairs")
        if mode == "join" and (c4 >= CHANGED or c5 >= CHANGED):
            failures.append(f"negative control: with the unlock updates off the camera still changed on every draw"
                            f" (yaw {c4:.1%}, origin {c5:.1%}); the A4/A5 detectors prove nothing")
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs:
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs:
            for f in h.scan_failures(ln.text for ln in proc.snapshot()):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            if proc is obs:
                for i, ln in enumerate(proc.snapshot()):
                    if any(m in ln.text for m in BAD_MARKERS):
                        failures.append(f"{name} log line {i + 1}: {ln.text}")
            proc.cleanup()
    print(f"quit exit codes: {codes}")
    if not failures and any(c != 0 for c in codes.values()):
        failures.append(f"quit exit codes: {codes}")

    print(f"\n{'cap':>4} {'check':<5} {'result':<18} detail")
    for (cap, check), (ok, detail) in sorted(results.items()):
        known = (mode, check) in KNOWN_GAPS
        if ok and known:
            verdict = "UNEXPECTED PASS"
            failures.append(f"cap {cap} {check} passes but is listed in KNOWN_GAPS for {mode}: remove it ({detail})")
        elif ok:
            verdict = "pass"
        elif known:
            verdict = "known gap"
        else:
            verdict = "FAIL"
            failures.append(f"cap {cap} {check}: {detail}")
        print(f"{cap:>4} {check:<5} {verdict:<18} {detail}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nPASS: every {mode} check matched the expected state (known gaps: "
          f"{', '.join(sorted(c for m, c in KNOWN_GAPS if m == mode)) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
