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
  near each other. --observer devmap: the Vulkan client alone in single player
  (+devmap), with no remote player: a +devmap game has no async server, so addbot gets
  no client slot, and a bot's AI runs only on a server (docs/m5-plan.md step 3). It has
  no peerwalk phase and no A7/A7b; A0 checks the viewmodel only; its negative control
  requires A4, A5 and A6 to fail in the per_tick shape (the pose changes only on the draw
  pairs that cross a tick); and the run fails if a listen server starts.

For each cap (default 60, 120, 240) the observer runs these phases, each bracketed by
'echo FP_BEGIN <phase> <cap>' / 'echo FP_END' so the FRAME_POSE lines between them
are that phase's draws (the engine runs one stdin line per frame, before it draws):
  still      nobody moves
  turn       the observer turns (+left)
  walk       the observer walks forward, then back (clientMove)
  peerwalk   the headless peer walks forward, then back
An uncapped segment (com_maxFPS 0) measures the host's ceiling first. A negative
control repeats turn and the observer's walk with g_unlock_updateAngles,
g_unlock_updateViewpos and g_unlock_interpolateMoving off: the camera checks (A4, A5)
must fail there, which shows they can (on a listen host since M5 step 7, which has the
host call ClientUpdateView). The remote-player detector (A7) gets its proof
when the Entity::Present producer is restored and A7 stops being a known gap.

Checks (0.25 s dropped at each edge of a phase; a phase needs >= 20 draw pairs):
  A0 capture   the viewmodel (still, walk) and the peer (still, peerwalk) have a pose in
               >= 99% of draws, so a missing pose never passes as "did not move"
  A1 rate      from the draw timestamps, in every phase: <= 1.03 x cap; >= 0.98 x cap
               when the cap is <= 0.9 x the ceiling (the pacer sets the rate). Above
               that the host sets the rate, and per docs/m5-plan.md decision D1 whether
               it was reached is reported (the lowest phase against min(cap, ceiling),
               "reached" at 98%), not failed: no high-refresh hardware is part of testing
  A2 ticks     within each phase, game time advances 1.00 s per wall-clock second,
               +/- 0.05 plus one tick (33 ms) over the phase's length, and its steps
               between draws are one tick: no tick goes undrawn (where the cap is above
               0.9 x the ceiling, as for A1, the step count is reported, not failed: D1)
  A4 turn      the view yaw changes between consecutive draws
  A5 walk      the view origin changes between consecutive draws
  A6 viewmodel the viewmodel pose changes between consecutive draws (walk)
  A7 remote    the peer's render-world pose changes between consecutive draws
               (A2 and A4-A7 allow max(1, 1%) exceptions)
  A7b on path  each unlocked draw of the peer lies on its interpolation path: within
               0.05 u of lerp(P(n), P(n+1), fr), where P(n) is the peer's pose in the
               base draw of game time n (the base draw shows the tick before; M5 steps
               8 and 9) and fr the draw's fraction; at most max(1, 1%) draws off it (a
               draw prepared before ClientUpdateView computed its fraction shows P(n)).
               A wrong or stale fraction that is logged as drawn fails A8's wall-clock
               check instead. Not a direction check: on a network client a distant
               peer's tick poses can step back (AoR corrections), and that path is what
               is drawn (M5 step 9 review)
  A10 tick    the frames that run a tick are unlocked draws at their sub-tick fraction
               (M5 step 10, decision D7): in turn, walk and peerwalk, at least 99% of
               the draws that start a new game time are unlocked (ud=1, cu=1, a
               fraction in (0, 1)), and the mean and median of their lag from the
               render-only draws around them are within max(3 ms, a fifth of the
               cap's frame period: 3.3 ms at 60, 4.4 at 45; few render-only draws at a
               low cap sit at the top of the tick and bias the reference). A
               draw presents game time gt + fr x 33 ms at its wall time; its lag is the
               difference, taken against the median lag of the phase's render-only
               draws. A base draw (fraction 0) on a tick frame lags by the part of the
               tick already elapsed, a 30 Hz judder: half a frame period on average
               (8 ms at cap 60; less where the display phase-locks the frames to the
               ticks: +5.7 ms mean on a 120 Hz Mac). With com_unlock_tickFrames 0 (the
               negative control, at the lowest cap) both parts must fail, the lag's mean
               by at least CONTROL_MARGIN_MS beyond the bound
  A8 phase     a draw with the same game time as the previous one is an unlocked
               draw (ud=1); within one game time the fraction never falls, and at most
               max(1, 1%) draws may repeat it (timeLeft is whole milliseconds; when a
               snapshot's prediction-clock correction would move the fraction back,
               the game advances it at half the wall-clock rate instead, since M5 step
               10; docs/frame-path.md section 9). Each unlocked draw's fraction
               is also checked against wall time: it predicts when the tick ends (its
               time + (1 - fraction) x 33 ms), which must fall between the tick's last
               draw and the next tick's first draw, +/- 4 ms. A snapshot's
               prediction-clock correction (docs/frame-path.md section 2) can leave the
               draws of one tick a few ms off (the fraction slowed, or the next tick early),
               and at a high cap that is several draws, so the misses are counted by tick:
               at most max(1, 5%) of the checked ticks may have one (max(1, 8%) on a
               network client, which gets the corrections: since M5 step 10 its tick
               frames are checked too, and Windows x64 measured 8-14 of 257 ticks at cap
               240; owner decision 2026-10-05), and no draw may miss
               by more than 8 ms beyond the 4 ms (a stuck fraction or one running at the
               wrong speed misses on most ticks or by far more). Where the cap is above
               0.9 x the ceiling, as for A1, this wall-clock timing is reported, not
               failed (D1); the unlocked draws and the never-falling fraction always count
  A9 still     view, viewmodel and peer poses stay put: between consecutive draws
               < 0.05 u (a standing player's physics can still settle by about 0.01 u
               a tick) and the view angles < 0.01 degrees; over the whole phase each
               position stays within 0.1 u of where it started and the view angles
               within 0.05 degrees, so slow drift is caught too (the viewmodel's
               angles are left out: its idle sway turns it by about 0.01 degrees a draw)
After the negative control, com_unlockFPS 0 (M5 plan step 11, D8) at the highest cap,
while the observer walks: every draw is a base draw (ud=0) of a new game time (at
most max(1, 1%) repeats); the draw rate, about the tick rate, is reported.
Last, on a listen host only (M5 step 7, the draw-time viewer guard; compat-ledger 058),
at the highest cap: the host joins the spectators and, SPECTATE_ROUNDS times, free-flies
SPECTATE_HEIGHT u above the standing peer (spectate position), then follows the peer from
the console (spectate client). A round counts when the switch landed between ticks: the
peer's first draw (vp=peer) has the tick of the host's last free-fly draw, which had
canUnlockFrames (cu=1). The rest of that tick's draws of the peer are render-only frames
prepared with canUnlockFrames set, and each must show the peer's own tick view: within
SPECTATE_TOL u of the peer's first draw of the next tick. Without the guard they show the
spectator's camera (460 u away). At least SPECTATE_MIN_ROUNDS rounds must count.
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
import math
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed, connect, player_pos, spawn  # noqa: E402
from test_remote_client_map_change import READY, RemoteClient  # noqa: E402

FRAME_POSE_RE = re.compile(
    r"FRAME_POSE: n=(\d+) us=(\d+) cap=(-?\d+) gt=(-?\d+) fn=(-?\d+) ud=(\d) cu=(\d) fr=(-?[\d.]+) vp=(-?\d+)"
    r" view=([-\d.,]+) wpn=(\S+) seat=(\S+) veh=(\S+)(.*)$")
CLIENT_RE = re.compile(r" c(\d+)=([^:\s]+):aor=(-?\d+):hid=(\d):veh=(-?\d+)")
# M5 step 14b: the hull lead a seat draw used (alpha ticks past the last tick, the tick's lead)
LEAD_RE = re.compile(r" lead=(-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)")
JNT_RE = re.compile(r" jnt=(-?[\d.]+)")   # M5 step 14, gunner views: a checksum of the hull's drawn joints

EDGE_S = 0.25
CHANGED = 0.99        # A4-A7: the fraction of draw pairs that must show a change
POS_EPS = 0.01        # units
STILL_EPS = 0.05      # units: a standing player can still settle by about 0.01 u a tick
ANG_EPS = 0.001       # degrees

# Checks the current code fails, from docs/frame-path.md (section 9) and the M5 plan,
# written before this test first ran. A known gap that passes is reported too.
# (None left: A4-A6 and A8 were listen gaps until M5 step 7 had the host call
# ClientUpdateView on render-only frames, A7 a join gap until step 8 restored the
# Entity::Present producer on network clients and a listen gap until step 9 drew the
# host's remote players from presentation copies.)
KNOWN_GAPS: dict[tuple[str, str], tuple] = {}
MIN_PAIRS = 20
SPECTATE_ROUNDS = 10        # listen only: the viewer-change rounds (M5 step 7)
SPECTATE_MIN_ROUNDS = 3     # rounds whose switch must land between ticks
SPECTATE_HEIGHT = 600.0     # u above the peer that the host free-flies at
SPECTATE_TOL = 32.0         # u: a render-only draw of the peer from its next tick's view


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
            "fn": int(m.group(5)), "ud": int(m.group(6)), "cu": int(m.group(7)), "fr": float(m.group(8)),
            "vp": int(m.group(9)), "view": view, "wpn": wpn, "clients": clients,
            # the seat (entity:position, or None on foot) and the vehicle's render-world pose
            "seat": None if m.group(12) == "-" else m.group(12),
            "veh": None if m.group(13) == "-" else [float(v) for v in m.group(13).split(",")],
            "lead": (lambda g: None if g is None else (float(g.group(1)), [float(g.group(i)) for i in (2, 3, 4)]))(
                LEAD_RE.search(m.group(14))),
            "jnt": (lambda g: None if g is None else [float(g.group(1))])(JNT_RE.search(m.group(14)))}


def spectate_round(poses, host_slot, peer_slot):
    """One viewer-change round (M5 step 7): (counted, render-only draws of the peer in the
    switch tick, the farthest of them from the peer's first draw of the next tick, in u).
    Not counted when no switch is seen, the switch frame ran a tick, or canUnlockFrames was
    off before it."""
    first = next((k for k, p in enumerate(poses) if p["vp"] == peer_slot), None)
    if first is None or first == 0:
        return False, 0, 0.0
    before, tick = poses[first - 1], poses[first]["fn"]
    if before["vp"] != host_slot or before["fn"] != tick or before["cu"] != 1:
        return False, 0, 0.0
    same = [p for p in poses[first:] if p["fn"] == tick and p["vp"] == peer_slot]
    nxt = next((p for p in poses[first:] if p["fn"] > tick and p["vp"] == peer_slot), None)
    if nxt is None:
        return False, 0, 0.0
    return True, len(same), max(math.dist(p["view"][:3], nxt["view"][:3]) for p in same)


def phase_poses_raw(proc, begin, end):
    """Every FRAME_POSE draw between two line indices."""
    return [p for ln in proc.snapshot(begin, end) if (p := parse_pose(ln.text))]


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


def locked_check(poses, cap):
    """com_unlockFPS 0 (M5 plan step 11, D8): the renderer does not go faster than the
    ticks, so each draw is a base draw (ud=0) of a new game time. The draw rate (the tick
    rate, or less when one frame runs two ticks) is reported, not bounded: a render-only
    draw shows up as a repeated game time, and a slow host may not reach the tick rate (D1)."""
    pairs = list(zip(poses, poses[1:]))
    n = len(pairs)
    same = sum(1 for a, b in pairs if b["gt"] == a["gt"])
    unlocked = sum(1 for p in poses if p["ud"] == 1)
    ok = n >= MIN_PAIRS and same <= allowed_misses(n) and unlocked == 0
    return {"ok": ok, "detail": f"{rate(poses):.1f} draws/s at cap {cap} (reported); {same} of {n} draw pairs repeat a game"
                                f" time (allowed {allowed_misses(n)}); {unlocked} unlocked draws (allowed 0)"}


def phase_errors(poses):
    """A8: (draws in the previous draw's game time that are not unlocked, draws whose
    fraction falls below the previous draw's, draws that repeat it)."""
    wrong = falling = repeated = 0
    for a, b in zip(poses, poses[1:]):
        if b["gt"] != a["gt"]:
            continue
        if b["ud"] != 1:
            wrong += 1
        elif a["ud"] == 1 and b["fr"] < a["fr"]:
            falling += 1
        elif a["ud"] == 1 and b["fr"] == a["fr"]:
            repeated += 1
    return wrong, falling, repeated


FRACTION_SLACK_US = 4000       # A8: the prediction-clock nudge
FRACTION_WORST_US = 8000       # A8: the most a miss may exceed the slack by
FRACTION_TICK_ALLOWANCE = 0.05  # A8: ticks allowed a miss on an authoritative game
FRACTION_TICK_ALLOWANCE_NET = 0.08  # A8: on a network client (snapshot corrections; owner, 2026-10-05)


def fraction_timing_errors(poses):
    """A8, against wall time: (unlocked draws checked, draws whose fraction does not
    predict when the tick ends, ticks checked, ticks with such a draw, the largest miss
    beyond the slack in us). A fraction fr at time t says the tick ends at
    t + (1 - fr) x 33 ms, which must lie between the tick's last draw and the next
    tick's first draw (4 ms slack: the client nudges its prediction clock)."""
    checked = bad = 0
    ticks, bad_ticks = set(), set()
    worst = 0.0
    for i, p in enumerate(poses):
        if p["ud"] != 1:
            continue
        j = next((k for k in range(i + 1, len(poses)) if poses[k]["gt"] != p["gt"]), None)
        if j is None or poses[j]["gt"] - p["gt"] != 33:
            continue  # no next tick in the phase, or a tick went undrawn (A2)
        checked += 1
        ticks.add(p["gt"])
        predicted = p["us"] + (1.0 - p["fr"]) * 33000.0
        low, high = poses[j - 1]["us"] - FRACTION_SLACK_US, poses[j]["us"] + FRACTION_SLACK_US
        if not (0.0 < p["fr"] < 1.0):
            miss = float("inf")
        elif predicted < low:
            miss = low - predicted
        elif predicted > high:
            miss = predicted - high
        else:
            continue
        bad += 1
        bad_ticks.add(p["gt"])
        worst = max(worst, miss)
    return checked, bad, len(ticks), len(bad_ticks), worst


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


ONPATH_EPS = 0.05     # u: A7b


def onpath_check(poses, pose_of, what):
    """A7b: whether the draws lie on one interpolation path. A draw in game time n at
    fraction f (0 for a base draw) should show (1 - f) x K(n) + f x K(n + 1), where K(n) is
    the pose the path passes at game time n. The knots K are fitted by least squares over
    every draw of a run of consecutive game times (each draw constrains the two knots of
    its tick, which it shares with the neighbouring ticks), and each draw must lie within
    ONPATH_EPS of the fit. The draws beyond the number of knots (the fit's spare equations)
    must number at least MIN_PAIRS, so the fit cannot pass through every draw by
    construction (M5 step 10: no base draw shows K(n) by itself; review of #67)."""
    seq = [(p["gt"], 0.0 if p["ud"] == 0 else p["fr"], pose_of(p)[:3]) for p in poses if pose_of(p) is not None]
    runs, run = [], []
    for d in seq:
        if run and d[0] - run[-1][0] not in (0, 33):
            runs.append(run)
            run = []
        run.append(d)
    if run:
        runs.append(run)
    off = checked = spare = 0
    worst = 0.0
    for run in runs:
        g0 = run[0][0]
        m = (run[-1][0] - g0) // 33 + 2    # knots
        if len(run) <= m:
            continue
        # normal equations of the tridiagonal least-squares problem, one per coordinate
        diag = [1e-9] * m
        off_d = [0.0] * (m - 1)
        rhs = [[0.0] * m for _ in range(3)]
        for gt, f, q in run:
            k = (gt - g0) // 33
            a, b = 1.0 - f, f
            diag[k] += a * a
            diag[k + 1] += b * b
            off_d[k] += a * b
            for c in range(3):
                rhs[c][k] += a * q[c]
                rhs[c][k + 1] += b * q[c]
        knots = []
        for c in range(3):
            cp, dp = [0.0] * m, [0.0] * m
            cp[0] = off_d[0] / diag[0] if m > 1 else 0.0
            dp[0] = rhs[c][0] / diag[0]
            for k in range(1, m):
                den = diag[k] - off_d[k - 1] * cp[k - 1]
                cp[k] = off_d[k] / den if k < m - 1 else 0.0
                dp[k] = (rhs[c][k] - off_d[k - 1] * dp[k - 1]) / den
            x = [0.0] * m
            x[-1] = dp[-1]
            for k in range(m - 2, -1, -1):
                x[k] = dp[k] - cp[k] * x[k + 1]
            knots.append(x)
        spare += len(run) - m
        for gt, f, q in run:
            k = (gt - g0) // 33
            dev = max(abs((1.0 - f) * knots[c][k] + f * knots[c][k + 1] - q[c]) for c in range(3))
            checked += 1
            off += dev > ONPATH_EPS
            worst = max(worst, dev)
    return {"ok": spare >= MIN_PAIRS and off <= allowed_misses(checked), "n": spare,
            "detail": f"{what} off its interpolation path by more than {ONPATH_EPS} u in {off} of {checked} draws"
                      f" ({spare} more draws than knots; worst {worst:.3f} u)"}


TICK_LAG_MS = 3.0
CONTROL_MARGIN_MS = 1.0   # A10's negative control: its mean lag clears the bound by this much


def tick_frame_check(phases, cap):
    """A10: whether the draws that start a new game time are unlocked draws prepared at
    their fraction (cu=1, 0 < fr < 1), presented as the render-only draws are: the mean
    and the median of their lag from the phase's render-only draws within the larger of
    TICK_LAG_MS and a fifth of the cap's frame period (a base draw on a tick frame lags
    by about half a frame period; at low caps the few render-only draws sit at the top of
    the tick and bias the reference by a few ms: -2 to -3.3 ms at cap 45). "bound" is
    returned so the negative control can require a margin over it.
    "unlocked_ok" and "lag_ok" are the two parts (the negative control must fail both);
    "report" is set instead of a failure when no phase has render-only draws to compare
    with (a cap of 30 or less, or a host that cannot draw faster)."""
    tick_frames = unlocked = 0
    lags = []
    for ps in phases:
        lag = [p["us"] / 1000.0 - (p["gt"] + (0.0 if p["ud"] == 0 else p["fr"]) * 33.0) for p in ps]
        starts = [i for i in range(1, len(ps)) if ps[i]["gt"] != ps[i - 1]["gt"]]
        between = sorted(lag[i] for i in range(1, len(ps)) if ps[i]["gt"] == ps[i - 1]["gt"])
        tick_frames += len(starts)
        unlocked += sum(1 for i in starts if ps[i]["ud"] == 1 and ps[i]["cu"] == 1 and 0.0 < ps[i]["fr"] < 1.0)
        if between:
            median = between[len(between) // 2]
            lags += [lag[i] - median for i in starts]
    unlocked_ok = tick_frames >= MIN_PAIRS and tick_frames - unlocked <= allowed_misses(tick_frames)
    bound = max(TICK_LAG_MS, 0.2 * 1000.0 / cap) if cap > 0 else TICK_LAG_MS
    res = {"n": tick_frames, "unlocked_ok": unlocked_ok, "bound": bound, "mean": None}
    if not lags:
        res.update(ok=unlocked_ok, lag_ok=None,
                   detail=f"{unlocked} of {tick_frames} tick frames unlocked; no render-only draws to compare their lag with",
                   report="no draws between ticks (cap or host at the tick rate): the lag part is reported, not judged")
        return res
    lags.sort()
    mean = sum(lags) / len(lags)
    median = lags[len(lags) // 2]
    bound = max(TICK_LAG_MS, 0.2 * 1000.0 / cap) if cap > 0 else TICK_LAG_MS
    lag_ok = abs(mean) <= bound and abs(median) <= bound
    res.update(ok=unlocked_ok and lag_ok, lag_ok=lag_ok, mean=mean,
               detail=f"{unlocked} of {tick_frames} tick frames unlocked at a fraction; they present {mean:+.2f} ms"
                      f" (median {median:+.2f}) from the render-only draws (need"
                      f" {tick_frames - allowed_misses(tick_frames)}+ unlocked, mean and median within +-{bound:.1f} ms)")
    return res


def peer_pose(p, slot):
    """The peer's render-world pose in a draw, or None."""
    return (p["clients"].get(slot) or {}).get("pose")


def evaluate(cap, ceiling, slot, p_still, p_turn, p_walk, p_peer, network=False):
    """The A0-A9 results for one cap's phases. p_peer None: no remote player (devmap), so
    no peerwalk phase, no A7/A7b and A0 checks the viewmodel only."""
    def peer(p):
        return peer_pose(p, slot) if p_peer is not None else None
    res = {}
    phases = {"turn": p_turn, "walk": p_walk}
    if p_peer is not None:
        phases["peerwalk"] = p_peer
    p_peer = p_peer or []
    short = [f"{k} ({max(0, len(v) - 1)})" for k, v in {"still": p_still, **phases}.items() if len(v) - 1 < MIN_PAIRS]
    wpn_have = [p["wpn"] is not None for p in p_still + p_walk]
    have = sum(wpn_have) / max(1, len(wpn_have))
    detail = f"viewmodel present in {sum(wpn_have)}/{len(wpn_have)}"
    if "peerwalk" in phases:
        peer_have = [peer(p) is not None for p in p_still + p_peer]
        have = min(have, sum(peer_have) / max(1, len(peer_have)))
        detail += f", peer in {sum(peer_have)}/{len(peer_have)}"
    res["A0"] = {"ok": not short and have >= 0.99,
                 "detail": detail + " draws" + (f"; too few draw pairs in {', '.join(short)}" if short else "")}
    paced = cap <= 0.9 * ceiling
    timed = {"still": p_still, **phases}  # A1 and A2 cover every phase
    rates = {k: rate(v) for k, v in timed.items()}
    worst = min(rates, key=rates.get)
    need = f"{0.98 * cap:.1f}..{1.03 * cap:.1f}" if paced else f"<= {1.03 * cap:.1f}"
    lo = 0.98 * cap if paced else 0.0
    res["A1"] = {"ok": all(lo <= r <= 1.03 * cap for r in rates.values()),
                 "detail": ", ".join(f"{k} {r:.1f}" for k, r in rates.items())
                           + f" fps (cap {cap}, need {need}, ceiling {ceiling:.1f}; lowest: {worst})"}
    if not paced:
        # D1: the host, not the pacer, sets this rate; report how close it came to
        # what it could reach, min(cap, ceiling), as high-refresh-determinism does
        target = min(cap, ceiling)
        reached = "reached" if rates[worst] >= 0.98 * target else "not reached"
        res["A1"]["report"] = (f"host-limited: cap {cap} is above 0.9 x the ceiling ({ceiling:.1f} fps), so the host sets the rate:"
                               f" the lowest phase ran {rates[worst]:.1f} fps, {rates[worst] / target:.0%} of"
                               f" min(cap, ceiling) = {target:.1f} ({reached}; reported, not failed: D1)")
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
    # D1: where the host, not the pacer, sets the rate (cap above 0.9 x the ceiling), a frame
    # longer than a tick is the host's limit, so the step count is reported, not failed; the
    # game-time ratio is judged at every cap
    res["A2"] = {"ok": not ratio_bad and (misses <= 0 or not paced),
                 "detail": f"game time runs at {worst_ratio:.3f} x wall time at worst"
                           + (f" (out of bounds: {', '.join(ratio_bad)})" if ratio_bad else "")
                           + f"; steps other than one tick beyond the allowance: {max(0, misses)} ({steps} steps)"}
    if not paced and misses > 0:
        res["A2"]["report"] = (f"host-limited (cap {cap} above 0.9 x ceiling {ceiling:.1f}): {misses} steps beyond the"
                               " allowance reported, not failed (D1)")
    res["A4"] = fraction_check(p_turn, lambda a, b: abs(a["view"][4] - b["view"][4]) > ANG_EPS, "view yaw")
    res["A5"] = fraction_check(p_walk, lambda a, b: moved(a["view"], b["view"]), "view origin")
    res["A6"] = fraction_check(p_walk, lambda a, b: moved(a["wpn"], b["wpn"]), "viewmodel")
    if "peerwalk" in phases:
        res["A7"] = fraction_check(p_peer, lambda a, b: moved(peer(a), peer(b)), "peer's render-world pose")
        res["A7b"] = onpath_check(p_peer, peer, "peer's render-world pose")
    all_p = p_turn + p_walk + p_peer
    wrong8, back8, flat8 = phase_errors(all_p)
    same = sum(1 for a, b in zip(all_p, all_p[1:]) if a["gt"] == b["gt"])
    checked8 = late8 = ticks8 = late_ticks8 = 0
    worst8 = 0.0
    for p in phases.values():  # per phase: phases are separate time spans
        c, b, t, bt, w = fraction_timing_errors(p)
        checked8 += c
        late8 += b
        ticks8 += t
        late_ticks8 += bt
        worst8 = max(worst8, w)
    allowance8 = FRACTION_TICK_ALLOWANCE_NET if network else FRACTION_TICK_ALLOWANCE
    timing8 = late_ticks8 <= max(1, int(allowance8 * ticks8)) and worst8 <= FRACTION_WORST_US
    # D1, as for A2: the wall-clock timing is reported where the host sets the rate; whether
    # each draw within a tick is unlocked and its fraction never falls is judged at every cap
    res["A10"] = tick_frame_check(list(phases.values()), cap)
    res["A8"] = {"ok": wrong8 == 0 and back8 == 0 and flat8 <= allowed_misses(same) and checked8 > 0
                 and (timing8 or not paced),
                 "wrong": wrong8, "same": same,
                 "detail": f"{wrong8} of {same} draws within a tick not unlocked; {back8} step the fraction back, {flat8} repeat it;"
                           f" {late8} of {checked8} unlocked fractions off the wall clock, in {late_ticks8} of {ticks8} ticks"
                           f" (allowed {max(1, int(allowance8 * ticks8))})"
                           + (f", worst {worst8 / 1000:.1f} ms beyond the slack" if late8 else "")}
    if not paced and not timing8:
        res["A8"]["report"] = (f"host-limited (cap {cap} above 0.9 x ceiling {ceiling:.1f}): the wall-clock timing"
                               " reported, not failed (D1)")
    f9 = changed_fraction(p_still, lambda a, b: moved(a["view"], b["view"], STILL_EPS) or moved(a["wpn"], b["wpn"], STILL_EPS)
                          or rotated(a["view"], b["view"])
                          or moved(peer(a), peer(b), STILL_EPS))
    def spread(get, angular=False):
        """The largest difference from the first draw's value over the phase."""
        vals = [v for p in p_still if (v := get(p)) is not None]
        if len(vals) < 2:
            return 0.0
        if angular:
            return max(abs((x - y + 180.0) % 360.0 - 180.0) for v in vals for x, y in zip(v[3:6], vals[0][3:6]))
        return max(abs(x - y) for v in vals for x, y in zip(v[:3], vals[0][:3]))
    drift = {"view": spread(lambda p: p["view"]), "viewmodel": spread(lambda p: p["wpn"]),
             "view angles": spread(lambda p: p["view"], angular=True)}
    if "peerwalk" in phases:
        drift["peer"] = spread(peer)
    drift_ok = all(v <= 0.1 for k, v in drift.items() if k != "view angles") and drift["view angles"] <= 0.05
    res["A9"] = {"ok": len(p_still) - 1 >= MIN_PAIRS and f9 == 0.0 and drift_ok,
                 "detail": f"something moved in {f9:.1%} of {max(0, len(p_still) - 1)} still draw pairs; drift over the phase: "
                           + ", ".join(f"{k} {v:.3f}" for k, v in drift.items())}
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
        if r.get("report"):
            print(f"{'':>4} {'':<5} {'report':<18} {r['report']}")
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
    if ceiling <= 0:
        # without a ceiling no cap can be judged paced or not
        print(f"[replay {mode}] FAIL: the uncapped calibration segment has fewer than 2 draws")
        return 1
    caps = sorted({int(ln.split()[2]) for ln in lines if ln.startswith("FP_BEGIN still ")})
    results = {}
    for cap in caps:
        p_peer = None if mode == "devmap" else seg("peerwalk", cap)
        for check, res in evaluate(cap, ceiling, slot, seg("still", cap), seg("turn", cap), seg("walk", cap), p_peer,
                                   network=mode == "join").items():
            results[(cap, check)] = res
    print(f"[replay {mode}] uncapped: {ceiling:.1f} fps")
    failures = judge(mode, results)
    locked = [int(ln.split()[2]) for ln in lines if ln.startswith("FP_BEGIN locked ")]
    if locked:
        lk = locked_check(seg("locked", locked[0]), locked[0])
        print(f"[replay {mode}] com_unlockFPS 0: {lk['detail']}")
        if not lk["ok"]:
            failures.append(f"com_unlockFPS 0 does not draw once per tick: {lk['detail']}")
    ticked = [int(ln.split()[2]) for ln in lines if ln.startswith("FP_BEGIN controltick ")]
    if ticked:
        # the negative control of M5 step 10: both parts of A10 must fail
        c10 = tick_frame_check([seg("controltick", ticked[0])], ticked[0])
        print(f"[replay {mode}] com_unlock_tickFrames 0: {c10['detail']}")
        if (c10["unlocked_ok"] or c10["lag_ok"] is not False or c10["n"] < MIN_PAIRS
                or c10["mean"] is None or c10["mean"] < c10["bound"] + CONTROL_MARGIN_MS):
            failures.append(f"negative control: with com_unlock_tickFrames 0 the A10 detector did not fail ({c10['detail']})")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


class DevmapClient(VulkanClient):
    """The Vulkan client in single player: +devmap instead of +spawnServer."""

    def command_line(self):
        args = super().command_line()
        assert args[-2] == "+spawnServer"
        return args[:-2] + ["+devmap", self.listen_map]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--observer", choices=("join", "listen", "devmap"))
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
        # single player needs no dedicated binary
        ded = h.find_ded_binary(args.ded_binary) if args.observer != "devmap" else None
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
    # r_vkValidation 0: this test measures frame rates, and the Khronos validation layer,
    # where it is installed (Linux, not the macOS host), cut the uncapped rate from about
    # 370 to 56 fps on antec. campaign-client-maps keeps it on to catch spec violations.
    view_cvars = {"com_unlockFPS": "1", "r_swapInterval": "0", "r_vkValidation": "0",
                  "in_yawSpeed": "90", "in_angleSpeedKey": "1"}
    if mode == "join":
        srv = h.DedicatedServer(ded, data, args.map, args.port, log_path=log("server"), cvars=game)
        srv.name = "SERVER"
        obs = VulkanClient(vk, data, args.port + 1, connect_to=f"127.0.0.1:{args.port}", log_path=log("vulkan"),
                           cvars={"si_pure": "0", "ui_name": "Observer", "net_clientShowSnapshots": "1", **view_cvars})
        procs = [("vulkan", obs), ("server", srv)]
    elif mode == "listen":
        obs = VulkanClient(vk, data, args.port, map_name=args.map, log_path=log("host"),
                           cvars={**game, "ui_name": "Host", **view_cvars})
        srv = obs
        procs = [("host", obs)]
    else:
        # single player: cheats are allowed without net_allowCheats (no async network)
        obs = DevmapClient(vk, data, args.port, map_name=args.map, log_path=log("devmap"),
                           cvars={"si_rules": "sdGameRulesObjective", "ui_name": "Player", **view_cvars})
        srv = obs
        procs = [("devmap", obs)]
    obs.name, obs.slot = "Observer", None
    peer = None
    if mode != "devmap":
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
                raise Failed(f"the {'listen server' if mode == 'listen' else 'single-player game'} never loaded the map")
            obs.sync(60.0)
            obs.slot = 0
        spawn(srv, obs, "gdf", timeout=60.0)
        slot = None
        if peer is not None:
            connect(srv, peer, mode)
            spawn(srv, peer, "gdf")
            slot = peer.slot
        # Separate the walking lanes. On valley both spawn facing north on x = 10376, the
        # peer 64 u behind; every turn is 180 degrees, a forward leg covers ~515 u and a
        # walk ends ~190 u ahead of where it began (backpedalling is slower). On that line
        # the walks ran the players into each other, and the observer's southward legs into
        # a wall ~350 u south of its spawn; the blocked draws failed A5/A6 and changed A7's
        # shape. Strafing east puts the observer on x ~ 10530, clear from y -11490 to
        # -10430 (the span its walks cover); the peer's walks stay on x = 10376, north of
        # its spawn, which is clear to y -9830.
        obs.send("clientMove 0 127 0")
        time.sleep(0.7)
        obs.send("clientMove 0 0 0")
        time.sleep(0.5)
        mark = obs.mark()
        obs.send("set g_logFramePoses 4")
        if os.environ.get("OPENETQW_FRAME_SYNC_LOG"):
            obs.send("set r_vkLogFrameSync 1")
        if not obs.wait_for(lambda s: s.startswith("FRAME_POSE:"), 10.0, mark):
            raise Failed("no FRAME_POSE lines after 'set g_logFramePoses 4' (a cheat: are cheats allowed?)")

        # the host's ceiling: uncapped, still
        obs.send("set com_maxFPS 0")
        time.sleep(1.5)
        ceiling = rate(phase("calibrate", 0, still, args.phase_seconds))
        if ceiling <= 0:
            raise Failed("the uncapped calibration phase drew fewer than 2 frames: no ceiling to judge the caps against")
        print(f"[{mode}] uncapped: {ceiling:.1f} fps")

        for cap in caps:
            obs.send(f"set com_maxFPS {cap}")
            time.sleep(1.5)
            p_still = phase("still", cap, still, args.phase_seconds)
            p_turn = phase("turn", cap, turn, args.phase_seconds)
            p_walk = phase("walk", cap, walk_proc(obs), args.phase_seconds)
            p_peer = phase("peerwalk", cap, walk_proc(peer), args.phase_seconds) if peer is not None else None
            for check, res in evaluate(cap, ceiling, slot, p_still, p_turn, p_walk, p_peer, network=mode == "join").items():
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
        if mode != "devmap" and (c4["ok"] or c5["ok"] or c4["n"] < MIN_PAIRS or c5["n"] < MIN_PAIRS):
            failures.append(f"negative control: with the unlock updates off the A4/A5 detectors did not fail"
                            f" ({c4['detail']}; {c5['detail']})")
        if mode == "devmap":
            # the per_tick shape needs draws between ticks: at a cap of 30 or less every draw
            # pair crosses a tick, and a pose that changes on all of them would pass
            if cap <= 30:
                raise Failed(f"the devmap negative control needs its cap above 30 fps (got {cap})")
            # single player has no snapshots to nudge the clock, so with the updates off the
            # camera and the viewmodel change exactly on the draw pairs that cross a tick
            # (the weapon block is gated on updateViewpos || updateAngles, game Game_local.cpp)
            c6 = fraction_check(n_walk, lambda a, b: moved(a["wpn"], b["wpn"]), "viewmodel")
            print(f"[{mode}] negative control at cap {cap}: {c6['detail']}")
            for name, c in (("A4", c4), ("A5", c5), ("A6", c6)):
                if c["ok"] or c["n"] < MIN_PAIRS or c["misaligned"] > max(1, int(0.05 * c["n"])):
                    failures.append(f"negative control: with the unlock updates off {name} did not fail in the"
                                    f" per_tick shape ({c['detail']}; {c['misaligned']} misaligned)")
        # M5 step 10: with com_unlock_tickFrames 0 the tick frames are base draws again (A10)
        for cv in ("g_unlock_updateAngles", "g_unlock_updateViewpos", "g_unlock_interpolateMoving"):
            obs.send(f"set {cv} 1")
        obs.send("set com_unlock_tickFrames 0")
        time.sleep(1.0)
        n_tick = phase("controltick", cap, walk_proc(obs), args.phase_seconds)
        obs.send("set com_unlock_tickFrames 1")
        for cv in ("g_unlock_updateAngles", "g_unlock_updateViewpos", "g_unlock_interpolateMoving"):
            obs.send(f"set {cv} 0")
        time.sleep(1.0)
        c10 = tick_frame_check([n_tick], cap)
        print(f"[{mode}] negative control at cap {cap}: com_unlock_tickFrames 0: {c10['detail']}")
        # both parts must fail: no tick frame unlocked, and the lag detector sees the base draws
        if (c10["unlocked_ok"] or c10["lag_ok"] is not False or c10["n"] < MIN_PAIRS
                or c10["mean"] is None or c10["mean"] < c10["bound"] + CONTROL_MARGIN_MS):
            failures.append(f"negative control: with com_unlock_tickFrames 0 the A10 detector did not fail ({c10['detail']})")
        # M5 steps 8 and 9: with g_unlock_interpolateMoving off the client or listen host
        # draws the peer where the last tick left it, so the A7 and A7b detectors must fail too
        if peer is not None:
            n_peer = phase("controlpeerwalk", cap, walk_proc(peer), args.phase_seconds)
            c7 = fraction_check(n_peer, lambda a, b: moved(peer_pose(a, slot), peer_pose(b, slot)), "peer's render-world pose")
            c7b = onpath_check(n_peer, lambda p: peer_pose(p, slot), "peer's render-world pose")
            print(f"[{mode}] negative control at cap {cap}: {c7['detail']}; {c7b['detail']}")
            if c7["ok"] or c7["n"] < MIN_PAIRS or c7b["ok"] or c7b["n"] < MIN_PAIRS:
                failures.append(f"negative control: with g_unlock_interpolateMoving off the A7/A7b detectors did not fail"
                                f" ({c7['detail']}; {c7b['detail']})")

        # com_unlockFPS 0 (M5 plan step 11, D8): one draw per tick, at the highest cap
        for cv in ("g_unlock_updateAngles", "g_unlock_updateViewpos", "g_unlock_interpolateMoving"):
            obs.send(f"reset {cv}")
        cap = caps[-1]
        obs.send(f"set com_maxFPS {cap}")
        obs.send("set com_unlockFPS 0")
        time.sleep(1.0)
        p_locked = phase("locked", cap, walk_proc(obs), args.phase_seconds)
        obs.send("set com_unlockFPS 1")
        lk = locked_check(p_locked, cap)
        print(f"[{mode}] com_unlockFPS 0: {lk['detail']}")
        if not lk["ok"]:
            failures.append(f"com_unlockFPS 0 does not draw once per tick: {lk['detail']}")

        if mode == "listen":
            # M5 step 7: a change of viewer between ticks (spectate client, from the console)
            # must not have render-only frames write the new viewer's view (the viewer guard)
            pp = player_pos(srv, slot)["org"]
            obs.send("clientTeam spectator")
            time.sleep(2.0)
            counted = []
            for i in range(SPECTATE_ROUNDS):
                obs.send(f"spectate position {pp[0]:.1f} {pp[1]:.1f} {pp[2] + SPECTATE_HEIGHT:.1f} 0 0 0")
                time.sleep(0.4)
                begin = obs.mark()      # the free-fly draws before the switch belong to the round
                time.sleep(0.3)
                obs.send(f"spectate client {slot}")
                time.sleep(0.7)
                obs.send(f"echo FP_SPECTATE {i}")
                end_hit = obs.wait_for(lambda s, i=i: s.strip() == f"FP_SPECTATE {i}", 10.0, begin)
                if not end_hit:
                    raise Failed(f"the observer never echoed the end of spectate round {i}")
                ok, n, far = spectate_round(phase_poses_raw(obs, begin, end_hit[0]), obs.slot, slot)
                if ok:
                    counted.append((i, n, far))
            worst = max((far for _, _, far in counted), default=0.0)
            print(f"[{mode}] viewer change between ticks: {len(counted)} of {SPECTATE_ROUNDS} rounds switched between"
                  f" ticks, {sum(n for _, n, _ in counted)} render-only draws of the new viewer in those ticks, the farthest"
                  f" {worst:.1f} u from its next tick's view (need <= {SPECTATE_TOL:.0f})")
            if len(counted) < SPECTATE_MIN_ROUNDS:
                failures.append(f"viewer change: only {len(counted)} of {SPECTATE_ROUNDS} rounds switched between ticks"
                                f" (need {SPECTATE_MIN_ROUNDS})")
            elif worst > SPECTATE_TOL:
                failures.append(f"viewer change: a render-only draw of the new viewer was {worst:.1f} u from its tick view"
                                " (another player's view written into it: the viewer guard)")
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs:
            codes[name] = proc.quit() if proc.alive() else None
        obs_lines = [ln.text for ln in obs.snapshot()]
        for name, proc in procs:
            for f in h.scan_failures(ln.text for ln in proc.snapshot()):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            if proc is obs:
                for i, text in enumerate(obs_lines):
                    if any(m in text for m in BAD_MARKERS):
                        failures.append(f"{name} log line {i + 1}: {text}")
            proc.cleanup()
    print(f"quit exit codes: {codes}")
    if mode == "devmap" and any("Server spawned on" in s for s in obs_lines):
        failures.append("the single-player run started a listen server ('Server spawned on')")
    if not failures and any(c != 0 for c in codes.values()):
        failures.append(f"quit exit codes: {codes}")

    failures += judge(mode, results)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    limited = [f"cap {cap} {check}" for (cap, check), r in sorted(results.items()) if "host-limited" in r.get("report", "")]
    print(f"\nPASS: every {mode} check matched the expected state (known gaps: "
          f"{', '.join(sorted(c for m, c in KNOWN_GAPS if m == mode)) or 'none'})"
          + (f"; host-limited, reported not judged (D1): {', '.join(limited)}" if limited else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
