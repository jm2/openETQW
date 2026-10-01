#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mixed-cap live session (M5 step 15; docs/m5-plan.md step 15).

Two Vulkan clients at different frame caps and a headless peer play on one dedicated
server (valley, all on GDF, joining in that order: A, B, the peer). Each client prints a
FRAME_POSE line per draw (g_logFramePoses 4) and is checked against its own cap with the
frame-pose checks of M5 step 3 (test_frame_poses.evaluate).

Rounds: both clients uncapped (calibrate: each client's ceiling while the other draws),
then caps A/B = 60/240 and 120/144 (the plan's pairs, for hosts with faster displays)
and 45/90 (both under a 120 Hz display, so both paced). The caps change at run time
(M5 step 17); after each change the test waits for each client's FRAME_POSE cap= to show
it, then settles. Each round's phases, bracketed by echo markers on both clients:
  still       nobody moves (2 s)
  turnA       A turns (+left, 2 s)          walkA   A walks forward 2 s, then back 2 s
  turnB       B turns                        walkB   B walks
  peerwalk    the peer walks forward and back
Valley's spawn spots vary by platform and timing (the pick is random), so the lanes come
from where the players spawned: the middle player in x stays and the outer two strafe
outward, east or west whatever their spawn yaw, until 300 u from it; the server's positions
must then be 250 u apart, and W1 checks every walk. The peer, which never turns, alternates
its walk's order each round so that it does not drift.

Checks, per client and round, against that client's cap:
  C0   every draw between the markers has cap= equal to the client's intended cap (0 while
       calibrating): on a 120 Hz display this is what shows each client ran its own cap
  A0-A9  step 3's checks (test_frame_poses.evaluate) with the peer as the remote player
  A0o, A7o, A9o  the same for the other client: present in >= 99% of draws, its pose
       changing on every draw of its walk (at the other cap), still while still
  T1   the view yaw turns 90 deg/s +-2% over the trimmed turn phase at every cap (summed
       per draw pair; pairs more than 133 ms apart left out and reported)
  W1   each walk's lane is clear: over every draw of the walk, the walker gets LANE_TRAVEL
       from where the phase began and never comes within LANE_GAP of another player (A and B
       from their own logs, the peer from A's), so a blocked lane fails here rather than as
       an obscure A5
  S1   (run validity) over the dedicated server's FRAME_TIMING windows from the first
       phase marker to the last, |sum of ticks - sum of window_ms / 33| <= 2
Known gaps (frame-poses' shapes): A7 and A7o are per_tick until M5 step 8 restores the
Entity::Present producer on network clients; then they must pass, and an unexpected pass
fails the test. Each round reports its measured rate pair and whether it was mixed.

Usage: python tests/test_mixed_caps.py --ded-binary build/openETQW-ded_arm64
       --client-binary build/openETQW-client_arm64
Needs retail data (ETQW_DATA_PATH; exits 77 without it) and a display. --replay <dir>
re-judges the a.log and b.log of an earlier --log-dir (S1 needs a live run).
"""

import argparse
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_frame_poses import EDGE_S, MIN_PAIRS, evaluate, parse_pose, rate  # noqa: E402
from test_reconnect_restart import Failed, connect, player_pos, spawn  # noqa: E402
from test_remote_client_map_change import READY, RemoteClient  # noqa: E402

ROUNDS = [(60, 240), (120, 144), (45, 90)]   # caps (A, B)
PHASE_S = 2.0
SETTLE_S = 1.5
# W1: a walk's lane is clear when the walker gets this far from where its phase began and
# never this close to another player (x/y; spawn spots vary by platform and timing, so the
# test checks the lanes, not the spots)
LANE_TRAVEL = 200.0          # a back-first walk gets about 250 u from its start
LANE_GAP = 48.0
# lanes: the middle player (by spawn x) stays, and the outer two strafe outward, east or
# west, until LANE_SPACING from it; a strafe runs at about STRAFE_U_PER_S and may take
# MAX_STRAFE_S at most; afterwards the server's positions must be MIN_LANE_X apart
LANE_SPACING = 300.0
STRAFE_U_PER_S = 245.0
MAX_STRAFE_S = 1.5
MIN_LANE_X = 250.0
SPAWN_ANGLES_RE = re.compile(r"Player (\d+) \('[^']*'\) spawned at \([^)]*\) angles \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)"
                             r" team (\S+) spectating (\d+)")
TURN_DEG_S = 90.0             # in_yawSpeed with in_angleSpeedKey 1
TURN_TOL = 0.02
TICK_MS = 33.0               # USERCMD_MSEC: the server runs 30.3 ticks a second
STALL_US = 133000            # 4 ticks: TurnTimeScale's clamp
# frame-poses' join shape: a remote player's drawn pose changes only on draws that cross
# a tick (the Entity::Present producer is compiled out until M5 step 8)
KNOWN_GAPS = {"A7": "per_tick", "A7o": "per_tick"}
MARK_RE = re.compile(r"^MC_(BEGIN|END) (\S+) (\S+)\s*$")
TIMING_RE = re.compile(r"FRAME_TIMING: window_ms=([\d.]+) frames=(\d+) ticks=(\d+)")


def lane_strafes(xs):
    """{key: signed x distance to strafe, + east}: the middle player (by spawn x, ties by key)
    stays; the outer two move outward until LANE_SPACING from it, when they are closer."""
    lo, mid, hi = sorted(xs, key=lambda k: (xs[k], k))
    plan = {k: 0.0 for k in xs}
    plan[lo] = min(0.0, xs[mid] - LANE_SPACING - xs[lo])
    plan[hi] = max(0.0, xs[mid] + LANE_SPACING - xs[hi])
    return plan


def min_gap(xs):
    """The smallest x distance between two of the players."""
    after = sorted(xs.values())
    return min(b - a for a, b in zip(after, after[1:]))


def spawn_yaw(lines, slot):
    """The yaw of a client's last non-spectating spawn in the server's log, or None."""
    yaw = None
    for s in lines:
        m = SPAWN_ANGLES_RE.search(s)
        if m and int(m.group(1)) == slot and m.group(6) == "0":
            yaw = float(m.group(3))
    return yaw


def xy(v):
    return (v[0], v[1])


def pose_xy(p, slot):
    c = p["clients"].get(slot)
    return xy(c["pose"]) if c and c.get("pose") else None


def lane(walk, mover, others):
    """(travel, closest approach, gap samples, draws) of a walker over its walk's draws:
    mover(p) and others(p) give x/y or None."""
    pos = [m for p in walk if (m := mover(p)) is not None]
    travel = max((math.dist(q, pos[0]) for q in pos), default=0.0)
    gaps = [math.dist(m, o) for p in walk if (m := mover(p)) is not None for o in others(p) if o is not None]
    return travel, min(gaps, default=float("inf")), len(gaps), len(pos)


def round_name(a, b):
    # echo splits tokens on '-', so the names use letters and digits only
    return f"r{a}x{b}"


def segments(lines):
    """(round, phase) -> (FRAME_POSE draws between that client's markers, EDGE_S trimmed;
    the same untrimmed)."""
    out, open_ = {}, {}
    for i, s in enumerate(lines):
        m = MARK_RE.match(s.strip())
        if not m:
            continue
        key = (m.group(2), m.group(3))
        if m.group(1) == "BEGIN":
            open_[key] = i
        elif key in open_:
            every = [p for ln in lines[open_.pop(key):i] if (p := parse_pose(ln))]
            poses = every
            if len(every) >= 3:
                t0, t1 = every[0]["us"] + EDGE_S * 1e6, every[-1]["us"] - EDGE_S * 1e6
                poses = [p for p in every if t0 <= p["us"] <= t1]
            out[key] = (poses, every)
    return out


def turn_rate(poses):
    """(degrees of view yaw per second, draw pairs left out): each draw pair's yaw change
    (yaw is view[4], wrapped to +-180) over its gap, summed. A pair more than STALL_US apart
    is left out: the turn scale is clamped at 4 ticks (UsercmdGen.cpp), so a stall loses yaw
    for a reason unrelated to the cap."""
    turned = gap = 0.0
    skipped = counted = 0
    for a, b in zip(poses, poses[1:]):
        dt = b["us"] - a["us"]
        if dt > STALL_US:
            skipped += 1
            continue
        turned += ((b["view"][4] - a["view"][4] + 180.0) % 360.0) - 180.0
        gap += dt
        counted += 1
    return (abs(turned) / (gap / 1e6) if gap > 0 else 0.0), skipped, counted


def gap_shaped(r):
    """frame-poses' per_tick shape (test_frame_poses.judge): enough draw pairs, and the pose
    changed exactly on the pairs that crossed a tick, but for 5% (at least 1)."""
    n = r.get("n", 0)
    return n >= MIN_PAIRS and r.get("misaligned", 1 << 30) <= max(1, int(0.05 * n))


def evaluate_client(name, segs, slots, other):
    """name: 'A' or 'B'; slots: {'peer': n, 'A': n, 'B': n}. Returns (results, report):
    results maps (round, cap, check) -> {'ok', 'detail', ...}."""
    results, report = {}, []
    cal, cal_every = segs.get(("calibrate", "still"), ([], []))
    ceiling = rate(cal)
    caps0 = sorted({p["cap"] for p in cal_every})
    results[("calibrate", 0, "C0")] = {"ok": len(cal_every) > MIN_PAIRS and caps0 == [0],
                                       "detail": f"cap= {caps0} over {len(cal_every)} calibrate draws (need [0])"}
    report.append(f"{name}: uncapped {ceiling:.1f} fps while the other client drew too")
    if ceiling <= 0:
        results[("calibrate", 0, "A1")] = {"ok": False, "detail": "fewer than 2 calibrate draws: no ceiling, so no round"
                                                                   " of this client can be judged"}
        return results, report
    for capa, capb in ROUNDS:
        rnd = round_name(capa, capb)
        cap = capa if name == "A" else capb
        names = ("still", "turnA", "walkA", "turnB", "walkB", "peerwalk")
        seg = {ph: segs.get((rnd, ph), ([], []))[0] for ph in names}
        own_turn, own_walk = seg["turn" + name], seg["walk" + name]
        other_walk = seg["walk" + other]
        drawn = [p for ph in names for p in segs.get((rnd, ph), ([], []))[1]]
        caps = sorted({p["cap"] for p in drawn})
        results[(rnd, cap, "C0")] = {"ok": bool(drawn) and caps == [cap],
                                     "detail": f"cap= {caps} over {len(drawn)} draws (need [{cap}])"}
        for check, r in evaluate(cap, ceiling, slots["peer"], seg["still"], own_turn, own_walk, seg["peerwalk"]).items():
            results[(rnd, cap, check)] = r
        o = evaluate(cap, ceiling, slots[other], seg["still"], own_turn, own_walk, other_walk)
        results[(rnd, cap, "A0o")] = {**o["A0"], "detail": o["A0"]["detail"].replace("peer in", f"client {other} in")
                                      .replace("peerwalk", f"walk{other}")}
        results[(rnd, cap, "A7o")] = {**o["A7"], "detail": o["A7"]["detail"].replace("peer's", f"client {other}'s")}
        results[(rnd, cap, "A9o")] = {**o["A9"], "detail": o["A9"]["detail"].replace("peer", f"client {other}")}
        tr, stalls, counted = turn_rate(own_turn)
        results[(rnd, cap, "T1")] = {"ok": counted >= MIN_PAIRS and abs(tr - TURN_DEG_S) <= TURN_TOL * TURN_DEG_S,
                                     "detail": f"view yaw {tr:.1f} deg/s over {counted} draw pairs of turn{name}"
                                               f" (need {TURN_DEG_S:.0f} +-{TURN_TOL:.0%}; {stalls} pairs over 133 ms left out)"}
        # W1 on every draw of the walk (untrimmed): travel from where the phase began
        whole = {ph: segs.get((rnd, ph), ([], []))[1] for ph in names}
        walkers = [(f"walk{name}", whole["walk" + name], lambda p: xy(p["view"]),
                    lambda p: [pose_xy(p, slots["peer"]), pose_xy(p, slots[other])])]
        if name == "A":
            walkers.append(("peerwalk", whole["peerwalk"], lambda p: pose_xy(p, slots["peer"]),
                            lambda p: [xy(p["view"]), pose_xy(p, slots[other])]))
        details, ok = [], True
        for label, walk, mover, others in walkers:
            travel, gap, samples, n = lane(walk, mover, others)
            ok = ok and n > MIN_PAIRS and samples > MIN_PAIRS and travel >= LANE_TRAVEL and gap >= LANE_GAP
            details.append(f"{label}: travelled {travel:.0f} u, closest {gap:.0f} u")
        results[(rnd, cap, "W1")] = {"ok": ok, "detail": "; ".join(details)
                                     + f" (need >= {LANE_TRAVEL:.0f} u and >= {LANE_GAP:.0f} u)"}
        report.append(f"{name} {rnd}: still {rate(seg['still']):.1f} fps at cap {cap}")
    return results, report


def judge(results):
    failures = []
    print(f"\n{'client':<6} {'round':<10} {'cap':>4} {'check':<5} {'result':<18} detail")
    order = ["calibrate"] + [round_name(a, b) for a, b in ROUNDS]
    for (client, rnd, cap, check), r in sorted(results.items(), key=lambda kv: (kv[0][0], order.index(kv[0][1]), kv[0][3])):
        gap = KNOWN_GAPS.get(check)
        if gap is None:
            verdict = "pass" if r["ok"] else "FAIL"
            if not r["ok"]:
                failures.append(f"{client} {rnd} {check}: {r['detail']}")
        elif r["ok"]:
            verdict = "UNEXPECTED PASS"
            failures.append(f"{client} {rnd} {check} passes but is a known gap: update KNOWN_GAPS ({r['detail']})")
        else:
            verdict = "known gap" if gap_shaped(r) else "GAP CHANGED SHAPE"
            if verdict != "known gap":
                failures.append(f"{client} {rnd} {check}: the known gap is no longer {gap}: {r['detail']}")
        print(f"{client:<6} {rnd:<10} {cap:>4} {check:<5} {verdict:<18} {r['detail']}")
        if r.get("report"):
            print(f"{'':<6} {'':<10} {'':>4} {'':<5} {'report':<18} {r['report']}")
    return failures


def finish(a_lines, b_lines, slots, server_windows, failures):
    sa, sb = segments(a_lines), segments(b_lines)
    results = {}
    for name, segs, other in (("A", sa, "B"), ("B", sb, "A")):
        res, report = evaluate_client(name, segs, slots, other)
        for line in report:
            print(f"  {line}")
        for (rnd, cap, check), r in res.items():
            results[(name, rnd, cap, check)] = r
    for capa, capb in ROUNDS:
        rnd = round_name(capa, capb)
        ra = rate(sa.get((rnd, "still"), ([], []))[0])
        rb = rate(sb.get((rnd, "still"), ([], []))[0])
        mixed = abs(ra - rb) > 0.05 * max(ra, rb, 1.0)
        print(f"  round {rnd}: A {ra:.1f} fps, B {rb:.1f} fps ({'mixed' if mixed else 'not mixed on this host'})")
    if server_windows is None:
        print("  server: S1 not judged (it needs a live run's server log with harness times)")
    else:
        # the windows tile time exactly, so their sums compare ticks with time
        ms = sum(w for w, _ in server_windows)
        ticks = sum(t for _, t in server_windows)
        ok = len(server_windows) >= 2 and abs(ticks - ms / TICK_MS) <= 2.0
        print(f"  server: S1 {'pass' if ok else 'FAIL'} ({len(server_windows)} windows, {ms:.0f} ms, {ticks} ticks;"
              f" expected {ms / TICK_MS:.1f} +-2)")
        if not ok:
            failures.append(f"S1: the server ran {ticks} ticks in {ms:.0f} ms of windows (expected {ms / TICK_MS:.1f} +-2)")
    failures = failures + judge(results)
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: each client held its own cap and passed the frame-pose checks (known gaps: A7, A7o until M5 step 8)")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29880)
    ap.add_argument("--log-dir")
    ap.add_argument("--replay", help="re-judge the a.log and b.log of an earlier --log-dir")
    args = ap.parse_args(argv)
    slots_default = {"A": 0, "B": 1, "peer": 2}
    if args.replay:
        d = Path(args.replay)
        missing = [str(d / n) for n in ("a.log", "b.log") if not (d / n).is_file()]
        if missing:
            print(f"[replay] FAIL: missing {', '.join(missing)}")
            return 1
        return finish((d / "a.log").read_text(errors="replace").splitlines(),
                      (d / "b.log").read_text(errors="replace").splitlines(), slots_default, None, [])

    try:
        ded = h.find_ded_binary(args.ded_binary)
        vk = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    def log(name):
        return (log_dir / f"{name}.log") if log_dir else None

    # frame-poses' settings: three GDF players need team balancing off; g_logFramePoses is
    # a cheat, set once each client is in
    game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0", "si_teamForceBalance": "0",
            "net_allowCheats": "1", "com_logFrameTiming": "1"}
    view = {"si_pure": "0", "net_clientShowSnapshots": "1", "com_unlockFPS": "1", "r_swapInterval": "0",
            "r_vkValidation": "0", "in_yawSpeed": "90", "in_angleSpeedKey": "1"}
    srv = h.DedicatedServer(ded, data, "valley", args.port, log_path=log("server"), cvars=game)
    srv.name = "SERVER"
    a = VulkanClient(vk, data, args.port + 1, connect_to=f"127.0.0.1:{args.port}", log_path=log("a"),
                     cvars={"ui_name": "ClientA", **view})
    b = VulkanClient(vk, data, args.port + 3, connect_to=f"127.0.0.1:{args.port}", log_path=log("b"),
                     cvars={"ui_name": "ClientB", **view})
    peer = RemoteClient(ded, data, args.port, args.port + 5, log_path=log("peer"))
    peer.cvars["ui_name"] = "Peer"
    for proc, name in ((a, "ClientA"), (b, "ClientB"), (peer, "Peer")):
        proc.name, proc.slot = name, None
    procs = [("peer", peer), ("b", b), ("a", a), ("server", srv)]
    failures = []
    phase_marks = []   # harness times of the first BEGIN and each END on A

    def both(text):
        a.send(f"echo {text}")
        b.send(f"echo {text}")

    def phase(rnd, name, drive, seconds):
        mark = a.mark()
        both(f"MC_BEGIN {rnd} {name}")
        t0 = time.monotonic()
        drive(seconds)
        both(f"MC_END {rnd} {name}")
        if not a.wait_for(lambda s: s.strip() == f"MC_END {rnd} {name}", 10.0, mark):
            raise Failed(f"client A never echoed the end of {rnd}/{name}")
        phase_marks.append((t0, time.monotonic()))

    def still(seconds):
        time.sleep(seconds)

    def turn(proc):
        def drive(seconds):
            proc.send("+left")
            time.sleep(seconds)
            proc.send("-left")
        return drive

    def walk(proc, first=127):
        """Forward then back (first=127), or back then forward (first=-127)."""
        def drive(seconds):
            proc.send(f"clientMove {first} 0 0")
            time.sleep(seconds)
            proc.send(f"clientMove {-first} 0 0")
            time.sleep(seconds)
            proc.send("clientMove 0 0 0")
        return drive

    def set_caps(capa, capb):
        marks = (a.mark(), b.mark())
        a.send(f"set com_maxFPS {capa}")
        b.send(f"set com_maxFPS {capb}")
        for proc, cap, mark in ((a, capa, marks[0]), (b, capb, marks[1])):
            if not proc.wait_for(lambda s, c=cap: (p := parse_pose(s)) is not None and p["cap"] == c, 10.0, mark):
                raise Failed(f"{proc.name} never drew at cap {cap} after 'set com_maxFPS {cap}'")
        time.sleep(SETTLE_S)

    try:
        srv.start()
        if not srv.wait_for_text(READY, 180.0):
            raise Failed("the server never became ready")
        srv.sync()
        spawned = {}
        for proc, key in ((a, "A"), (b, "B")):
            connect(srv, proc, "join")
            spawned[key] = spawn(srv, proc, "gdf", timeout=60.0)
        connect(srv, peer, "join")
        spawned["peer"] = spawn(srv, peer, "gdf")
        print("[spawn] " + ", ".join(f"{k} at ({spawned[k]['org'][0]:.0f}, {spawned[k]['org'][1]:.0f})" for k in ("A", "B", "peer")))
        # separate lanes from where they actually spawned (docs/m5-plan.md step 15): strafe
        # right is east when the player faces north (sin(yaw) > 0) and west when it faces south
        procs_by_key = {"A": a, "B": b, "peer": peer}
        xs = {k: spawned[k]["org"][0] for k in procs_by_key}
        plan = lane_strafes(xs)
        server_lines = [ln.text for ln in srv.snapshot()]
        sines = {}
        for k, dx in plan.items():
            yaw = spawn_yaw(server_lines, procs_by_key[k].slot)
            if dx and (yaw is None or abs(math.sin(math.radians(yaw))) < 0.5):
                raise Failed(f"{k} spawned facing yaw {yaw}: strafing would not move it east or west")
            sines[k] = math.sin(math.radians(yaw)) if yaw is not None else 0.0
        print("[lanes] " + ", ".join(f"{k} {plan[k]:+.0f} u" if plan[k] else f"{k} stays" for k in plan))

        def strafe(moves):
            """moves: key -> signed x distance. A strafe moves sin(yaw) of its length in x."""
            runs = {}
            for k, dx in moves.items():
                seconds = abs(dx) / (STRAFE_U_PER_S * abs(sines[k]))
                if seconds > MAX_STRAFE_S:
                    raise Failed(f"spawns too bunched for separate lanes: {xs}; {k} would strafe {dx:+.0f} u in x")
                east = 1 if sines[k] > 0 else -1
                runs[k] = (127 * east * (1 if dx > 0 else -1), seconds)
            t0 = time.monotonic()
            for k, (side, _) in runs.items():
                procs_by_key[k].send(f"clientMove 0 {side} 0")
            for k, (_, seconds) in sorted(runs.items(), key=lambda kv: kv[1][1]):
                time.sleep(max(0.0, t0 + seconds - time.monotonic()))
                procs_by_key[k].send("clientMove 0 0 0")
            time.sleep(0.5)
            return {k: player_pos(srv, procs_by_key[k].slot)["org"][0] for k in procs_by_key}

        lanes = strafe({k: dx for k, dx in plan.items() if dx})
        # one correction: a strafe can come up short (acceleration, a slope); top it up
        short = {k: xs[k] + plan[k] - lanes[k] for k in plan if plan[k] and abs(xs[k] + plan[k] - lanes[k]) > 30.0}
        if short:
            print("[lanes] topping up: " + ", ".join(f"{k} {dx:+.0f} u" for k, dx in short.items()))
            lanes = strafe(short)
        print("[lanes] x after the strafes: " + ", ".join(f"{k} {x:.0f}" for k, x in lanes.items()))
        if min_gap(lanes) < MIN_LANE_X:
            raise Failed(f"the lanes are {min_gap(lanes):.0f} u apart after the strafes (need {MIN_LANE_X:.0f}): {lanes}")
        for proc in (a, b):
            mark = proc.mark()
            proc.send("set g_logFramePoses 4")
            if not proc.wait_for(lambda s: s.startswith("FRAME_POSE:"), 10.0, mark):
                raise Failed(f"no FRAME_POSE lines from {proc.name} after 'set g_logFramePoses 4' (are cheats allowed?)")

        set_caps(0, 0)
        phase("calibrate", "still", still, PHASE_S)
        for index, (capa, capb) in enumerate(ROUNDS):
            rnd = round_name(capa, capb)
            set_caps(capa, capb)
            phase(rnd, "still", still, PHASE_S)
            phase(rnd, "turnA", turn(a), PHASE_S)
            phase(rnd, "walkA", walk(a), PHASE_S)
            phase(rnd, "turnB", turn(b), PHASE_S)
            phase(rnd, "walkB", walk(b), PHASE_S)
            # a walk ends ~200 u ahead (backpedalling is slower); A and B turn 180 degrees each
            # round, so theirs alternate, but the peer never turns: alternate its order
            phase(rnd, "peerwalk", walk(peer, 127 if index % 2 == 0 else -127), PHASE_S)
            print(f"[{rnd}] flown")
        for proc in (a, b):
            proc.send("set g_logFramePoses 0")
        srv.sync()
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs:
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs:
            for f in h.scan_failures(ln.text for ln in proc.snapshot()):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            for i, ln in enumerate(proc.snapshot()):
                if any(m in ln.text for m in BAD_MARKERS):
                    failures.append(f"{name} log line {i + 1}: {ln.text}")
        a_lines = [ln.text for ln in a.snapshot()]
        b_lines = [ln.text for ln in b.snapshot()]
        server_windows = None
        if phase_marks:
            t_first, t_last = phase_marks[0][0], phase_marks[-1][1]
            server_windows = [(float(m.group(1)), int(m.group(3))) for ln in srv.snapshot()
                              if t_first <= ln.t <= t_last and (m := TIMING_RE.search(ln.text))]
        slots_final = {"A": a.slot, "B": b.slot, "peer": peer.slot}
        for _, proc in procs:
            proc.cleanup()
    print(f"quit exit codes: {codes}")
    if not failures and any(c != 0 for c in codes.values()):
        failures.append(f"quit exit codes: {codes}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return finish(a_lines, b_lines, slots_final, server_windows, failures)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
