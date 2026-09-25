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
An uncapped segment (com_maxFPS 0) measures the host's ceiling first. A negative
control (join only) repeats turn and the observer's walk with g_unlock_updateAngles,
g_unlock_updateViewpos and g_unlock_interpolateMoving off: the camera checks (A4, A5)
must fail there, which shows they can. The remote-player detector (A7) gets its proof
when the Entity::Present producer is restored and A7 stops being a known gap.

Checks (0.25 s dropped at each edge of a phase; a phase needs >= 20 draw pairs):
  A0 capture   the viewmodel (still, walk) and the peer (still, peerwalk) have a pose in
               >= 99% of draws, so a missing pose never passes as "did not move"
  A1 rate      from the draw timestamps, in every phase: <= 1.03 x cap; >= 0.98 x cap
               when the cap is <= 0.9 x the ceiling (the pacer sets the rate), else
               >= 0.85 x the ceiling (the host does, and scenes differ in cost from the
               still scene the ceiling was measured on)
  A2 ticks     within each phase, game time advances 1.00 s per wall-clock second,
               +/- 0.05 plus one tick (33 ms) over the phase's length, and its steps
               between draws are one tick: no tick goes undrawn
  A4 turn      the view yaw changes between consecutive draws
  A5 walk      the view origin changes between consecutive draws
  A6 viewmodel the viewmodel pose changes between consecutive draws (walk)
  A7 remote    the peer's render-world pose changes between consecutive draws
               (A2 and A4-A7 allow max(1, 1%) exceptions)
  A8 phase     a draw with the same game time as the previous one is an unlocked
               draw (ud=1); within one game time the fraction rises, except that at most
               max(1, 1%) draws may repeat it (timeLeft is whole milliseconds) or step
               back: when a snapshot arrives the client corrects its prediction clock,
               and the presented time can move back by a millisecond or two (seen on
               2026-09-25; docs/frame-path.md section 9)
  A9 still     view, viewmodel and peer poses stay put (< 0.05 u: a standing player's
               physics can still settle by about 0.01 u a tick), and the view angles
               do not turn (< 0.01 degrees; the viewmodel's angles are left out: its
               idle sway turns it by about 0.01 degrees a draw)
A check that the code is expected to fail today is listed in KNOWN_GAPS with the shape
the failure must have: 'per_tick' (the pose changes once per tick: it changes on the
draw pairs that cross a tick and on no others, with max(1, 5%) of the pairs allowed to
disagree), 'band' (the changed
fraction stays in a range) or 'never_unlocked' (no draw between ticks is unlocked). A
gap outside its shape fails as "gap changed shape", and a gap that passes fails too, so
the table is updated when the code changes. Entries marked 'predicted' were written from
the code before the test was first run (docs/frame-path.md section 9); 'found' ones
showed up in the first run.

--replay-log <observer log> evaluates a saved observer log (FRAME_POSE lines and the
FP_BEGIN/FP_END markers) without running anything.

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
STILL_EPS = 0.05      # units: a standing player can still settle by about 0.01 u a tick
ANG_EPS = 0.001       # degrees

# Checks the current code fails, from docs/frame-path.md (section 9) and the M5 plan,
# written before this test first ran. A known gap that passes is reported too.
KNOWN_GAPS = {
    # predicted: the Entity::Present producer is compiled out (#if 0, jmarshall): remote
    # players are drawn where the last tick left them
    ("join", "A7"): ("per_tick",),
    # predicted: the listen host never calls ClientUpdateView, so every draw between
    # ticks repeats the tick's pose, and it reads view input once per tick
    ("listen", "A4"): ("per_tick",), ("listen", "A5"): ("per_tick",),
    ("listen", "A6"): ("per_tick",), ("listen", "A7"): ("per_tick",),
    ("listen", "A8"): ("never_unlocked",),
    # found (first run, 2026-09-25), cause found by review: when a snapshot arrives on a
    # draw between ticks the client re-runs prediction with isNewFrame false, so the
    # weapon's Present() in ClientUpdateView returns early (game Entity.cpp) and the
    # viewmodel stays put until the next tick (16-25% of draw pairs while walking)
    ("join", "A6"): ("band", 0.70, 0.95),
}
MIN_PAIRS = 20


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


def rotated(a, b, eps=0.01):
    """Whether the pitch/yaw/roll of two poses (components 3-5) differ by more than eps
    degrees, angles compared modulo 360."""
    if a is None or b is None or len(a) < 6 or len(b) < 6:
        return False
    return max(abs((x - y + 180.0) % 360.0 - 180.0) for x, y in zip(a[3:6], b[3:6])) > eps


def rate(poses):
    if len(poses) < 2:
        return 0.0
    return (len(poses) - 1) * 1e6 / (poses[-1]["us"] - poses[0]["us"])


def phase_errors(poses):
    """A8: (draws in the previous draw's game time that are not unlocked, draws whose
    fraction repeats or falls below the previous draw's)."""
    wrong = not_rising = 0
    for a, b in zip(poses, poses[1:]):
        if b["gt"] != a["gt"]:
            continue
        if b["ud"] != 1:
            wrong += 1
        elif a["ud"] == 1 and b["fr"] <= a["fr"]:
            not_rising += 1
    return wrong, not_rising


def tick_rate(poses):
    """(game seconds per wall second, fraction of game-time steps that are one tick)."""
    if len(poses) < 2 or poses[-1]["us"] == poses[0]["us"]:
        return 0.0, 0.0
    ratio = (poses[-1]["gt"] - poses[0]["gt"]) / 1000.0 / ((poses[-1]["us"] - poses[0]["us"]) / 1e6)
    steps = [b["gt"] - a["gt"] for a, b in zip(poses, poses[1:]) if b["gt"] != a["gt"]]
    return ratio, (sum(1 for s in steps if s == 33) / len(steps)) if steps else 0.0


def allowed_misses(n):
    return max(1, int(0.01 * n))


def fraction_check(poses, key, what):
    """(changed fraction, crossing fraction, ok, detail) for a change-every-draw check."""
    pairs = list(zip(poses, poses[1:]))
    flags = [(bool(key(a, b)), a["gt"] != b["gt"]) for a, b in pairs]
    changed = sum(1 for c, _ in flags if c)
    crossing = sum(1 for _, x in flags if x)
    n = len(pairs)
    f = changed / n if n else 0.0
    ok = n >= MIN_PAIRS and n - changed <= allowed_misses(n)
    # pairs where the pose changed without crossing a tick, or crossed a tick without changing
    misaligned = sum(1 for c, x in flags if c != x)
    return {"ok": ok, "f": f, "tick": crossing / n if n else 0.0, "n": n, "misaligned": misaligned,
            "detail": f"{what} changed in {f:.1%} of {n} draw pairs ({crossing / n if n else 0:.1%} cross a tick)"}


def evaluate(cap, ceiling, slot, p_still, p_turn, p_walk, p_peer):
    """The A0-A9 results for one cap's phases."""
    def peer(p):
        return (p["clients"].get(slot) or {}).get("pose")
    res = {}
    phases = {"turn": p_turn, "walk": p_walk, "peerwalk": p_peer}
    short = [f"{k} ({max(0, len(v) - 1)})" for k, v in {"still": p_still, **phases}.items() if len(v) - 1 < MIN_PAIRS]
    wpn_have = [p["wpn"] is not None for p in p_still + p_walk]
    peer_have = [peer(p) is not None for p in p_still + p_peer]
    have = min(sum(wpn_have) / max(1, len(wpn_have)), sum(peer_have) / max(1, len(peer_have)))
    res["A0"] = {"ok": not short and have >= 0.99,
                 "detail": f"viewmodel present in {sum(wpn_have)}/{len(wpn_have)}, peer in {sum(peer_have)}/{len(peer_have)} draws"
                           + (f"; too few draw pairs in {', '.join(short)}" if short else "")}
    paced = cap <= 0.9 * ceiling
    lo = 0.98 * cap if paced else 0.85 * ceiling
    timed = {"still": p_still, **phases}  # A1 and A2 cover every phase
    rates = {k: rate(v) for k, v in timed.items()}
    worst = min(rates, key=rates.get)
    res["A1"] = {"ok": all(lo <= r <= 1.03 * cap for r in rates.values()),
                 "detail": ", ".join(f"{k} {r:.1f}" for k, r in rates.items())
                           + f" fps (cap {cap}, need {lo:.1f}..{1.03 * cap:.1f}, ceiling {ceiling:.1f}; lowest: {worst})"}
    ratios, misses, steps = [], 0, 0
    ratio_bad = []
    for name, p in timed.items():
        ratio, _ = tick_rate(p)
        ratios.append(ratio)
        seconds = (p[-1]["us"] - p[0]["us"]) / 1e6 if len(p) > 1 else 0.0
        # game time moves in 33 ms steps: allow one step over the phase's length
        if seconds <= 0 or abs(ratio - 1.0) > 0.05 + 0.033 / seconds:
            ratio_bad.append(f"{name} {ratio:.3f}")
        s = [b["gt"] - a["gt"] for a, b in zip(p, p[1:]) if b["gt"] != a["gt"]]
        steps += len(s)
        misses = max(misses, sum(1 for d in s if d != 33) - allowed_misses(len(s)))
    worst_ratio = max(ratios, key=lambda r: abs(r - 1.0))
    res["A2"] = {"ok": not ratio_bad and misses <= 0,
                 "detail": f"game time runs at {worst_ratio:.3f} x wall time at worst"
                           + (f" (out of bounds: {', '.join(ratio_bad)})" if ratio_bad else "")
                           + f"; steps other than one tick beyond the allowance: {max(0, misses)} ({steps} steps)"}
    res["A4"] = fraction_check(p_turn, lambda a, b: abs(a["view"][4] - b["view"][4]) > ANG_EPS, "view yaw")
    res["A5"] = fraction_check(p_walk, lambda a, b: moved(a["view"], b["view"]), "view origin")
    res["A6"] = fraction_check(p_walk, lambda a, b: moved(a["wpn"], b["wpn"]), "viewmodel")
    res["A7"] = fraction_check(p_peer, lambda a, b: moved(peer(a), peer(b)), "peer's render-world pose")
    all_p = p_turn + p_walk + p_peer
    wrong8, flat8 = phase_errors(all_p)
    same = sum(1 for a, b in zip(all_p, all_p[1:]) if a["gt"] == b["gt"])
    res["A8"] = {"ok": wrong8 == 0 and flat8 <= allowed_misses(same), "wrong": wrong8, "same": same,
                 "detail": f"{wrong8} of {same} draws within a tick not unlocked; {flat8} repeat or step back the fraction"}
    f9 = changed_fraction(p_still, lambda a, b: moved(a["view"], b["view"], STILL_EPS) or moved(a["wpn"], b["wpn"], STILL_EPS)
                          or rotated(a["view"], b["view"])
                          or moved(peer(a), peer(b), STILL_EPS))
    res["A9"] = {"ok": len(p_still) - 1 >= MIN_PAIRS and f9 == 0.0, "detail": f"something moved in {f9:.1%} of {max(0, len(p_still) - 1)} still draw pairs"}
    return res


def judge(mode, results):
    """Print the table; return the failures (unexpected fails, gaps that changed shape
    or went away)."""
    failures = []
    print(f"\n{'cap':>4} {'check':<5} {'result':<18} detail")
    for (cap, check), r in sorted(results.items()):
        gap = KNOWN_GAPS.get((mode, check))
        if gap is None:
            verdict = "pass" if r["ok"] else "FAIL"
            if not r["ok"]:
                failures.append(f"cap {cap} {check}: {r['detail']}")
        elif r["ok"]:
            verdict = "UNEXPECTED PASS"
            failures.append(f"cap {cap} {check} passes but is a known gap for {mode}: update KNOWN_GAPS ({r['detail']})")
        else:
            if gap[0] == "per_tick":
                shaped = r["misaligned"] <= max(1, int(0.05 * r["n"]))
            elif gap[0] == "band":
                shaped = gap[1] <= r["f"] <= gap[2]
            else:  # never_unlocked
                shaped = r["same"] > 0 and r["wrong"] >= 0.95 * r["same"]
            verdict = "known gap" if shaped else "GAP CHANGED SHAPE"
            if not shaped:
                failures.append(f"cap {cap} {check}: the known gap is no longer {gap}: {r['detail']}")
        print(f"{cap:>4} {check:<5} {verdict:<18} {r['detail']}")
    return failures


def replay(path, mode, slot):
    """Evaluate a saved observer log."""
    lines = open(path, errors="replace").read().splitlines()

    def seg(name, cap):
        b = [i for i, ln in enumerate(lines) if ln.strip() == f"FP_BEGIN {name} {cap}"]
        e = [i for i, ln in enumerate(lines) if ln.strip() == f"FP_END {name} {cap}"]
        if not b or not e:
            return []
        poses = [p for ln in lines[b[0]:e[0]] if (p := parse_pose(ln))]
        if len(poses) < 3:
            return poses
        t0, t1 = poses[0]["us"] + EDGE_S * 1e6, poses[-1]["us"] - EDGE_S * 1e6
        return [p for p in poses if t0 <= p["us"] <= t1]
    ceiling = rate(seg("calibrate", 0))
    caps = sorted({int(ln.split()[2]) for ln in lines if ln.startswith("FP_BEGIN still ")})
    results = {}
    for cap in caps:
        for check, res in evaluate(cap, ceiling, slot, seg("still", cap), seg("turn", cap), seg("walk", cap), seg("peerwalk", cap)).items():
            results[(cap, check)] = res
    print(f"[replay {mode}] uncapped: {ceiling:.1f} fps")
    failures = judge(mode, results)
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


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
    ap.add_argument("--replay-log", help="evaluate a saved observer log instead of running (with --observer)")
    ap.add_argument("--peer-slot", type=int, default=1, help="the peer's client number in --replay-log")
    args = ap.parse_args(argv)
    if args.replay_log:
        return replay(args.replay_log, args.observer or "join", args.peer_slot)

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

    # both players on GDF: team balancing would refuse the second one. g_logFramePoses is a
    # cheat (it logs other players' positions whatever their visibility): the server allows
    # cheats, and the observer sets it once connected, when it has the server's net_allowCheats
    game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0", "si_teamForceBalance": "0",
            "net_allowCheats": "1"}
    view_cvars = {"com_unlockFPS": "1", "r_swapInterval": "0",
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
        mark = obs.mark()
        obs.send("set g_logFramePoses 4")
        if not obs.wait_for(lambda s: s.startswith("FRAME_POSE:"), 10.0, mark):
            raise Failed("no FRAME_POSE lines after 'set g_logFramePoses 4' (a cheat: are cheats allowed?)")

        # the host's ceiling: uncapped, still
        obs.send("set com_maxFPS 0")
        time.sleep(1.5)
        ceiling = rate(phase("calibrate", 0, still, args.phase_seconds))
        print(f"[{mode}] uncapped: {ceiling:.1f} fps")

        for cap in caps:
            obs.send(f"set com_maxFPS {cap}")
            time.sleep(1.5)
            p_still = phase("still", cap, still, args.phase_seconds)
            p_turn = phase("turn", cap, turn, args.phase_seconds)
            p_walk = phase("walk", cap, walk_proc(obs), args.phase_seconds)
            p_peer = phase("peerwalk", cap, walk_proc(peer), args.phase_seconds)
            for check, res in evaluate(cap, ceiling, slot, p_still, p_turn, p_walk, p_peer).items():
                results[(cap, check)] = res

        # negative control: with the unlock updates off, the change-every-frame checks must fail
        cap = caps[0]
        obs.send(f"set com_maxFPS {cap}")
        for cv in ("g_unlock_updateAngles", "g_unlock_updateViewpos", "g_unlock_interpolateMoving"):
            obs.send(f"set {cv} 0")
        time.sleep(1.0)
        # (phase names without '-': the console splits tokens there)
        n_turn = phase("controlturn", cap, turn, args.phase_seconds)
        n_walk = phase("controlwalk", cap, walk_proc(obs), args.phase_seconds)
        # the same detectors as A4 and A5: each must fail here
        c4 = fraction_check(n_turn, lambda a, b: abs(a["view"][4] - b["view"][4]) > ANG_EPS, "view yaw")
        c5 = fraction_check(n_walk, lambda a, b: moved(a["view"], b["view"]), "view origin")
        print(f"[{mode}] negative control at cap {cap}: {c4['detail']}; {c5['detail']}")
        if mode == "join" and (c4["ok"] or c5["ok"] or c4["n"] < MIN_PAIRS or c5["n"] < MIN_PAIRS):
            failures.append(f"negative control: with the unlock updates off the A4/A5 detectors did not fail"
                            f" ({c4['detail']}; {c5['detail']})")
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

    failures += judge(mode, results)

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
