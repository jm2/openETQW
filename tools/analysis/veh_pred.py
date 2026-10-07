#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""How well each candidate hull-lead predictor guesses a vehicle's next tick pose.

M5 step 14b (docs/hull-lead.md). Reads the VEH_TRACE lines a run with g_simHash 1 prints
(one per occupied rigid-body transport per tick: the physics origin, orientation, linear
and angular velocity, and the time of the last collision) and, for every tick n with
ticks n-2, n-1 and n+1 present, compares the pose at n+1 with each predictor's guess made
at n. The error is what a lead of one tick (L = 1) would show at the end of tick n, before
tick n+1 corrects it.

    python3 tools/analysis/veh_pred.py run.log [run2.log ...] [--worst N]

Pure Python: no third-party packages.
"""

from __future__ import annotations

import argparse
import math
import re
import sys

VEH_TRACE_RE = re.compile(
    r"VEH_TRACE: tick=(\d+) ent=(\d+) class=(\S+) org=\(([^)]*)\) quat=\(([^)]*)\)"
    r" lin=\(([^)]*)\) ang=\(([^)]*)\) rest=(\d) coll=(-?\d+)")

VEH_BODY_RE = re.compile(
    r"VEH_BODY: tick=(\d+) ent=(\d+) com=\(([^)]*)\) ground=(\d) contacts=(\d+)")

TICK_SECONDS = 0.033            # USERCMD_MSEC: the game steps 33 ms a tick
GRAVITY = (0.0, 0.0, -1066.0)   # g_gravity's default, along -z


def vec(text: str) -> tuple[float, ...]:
    return tuple(float(x) for x in text.split(","))


def add(*vs):
    return tuple(sum(c) for c in zip(*vs))


def sub(a, b):
    return tuple(x - y for x, y in zip(a, b))


def scale(a, s):
    return tuple(x * s for x in a)


def norm(a) -> float:
    return math.sqrt(sum(x * x for x in a))


def qmul(a, b):
    x1, y1, z1, w1 = a
    x2, y2, z2, w2 = b
    return (w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2)


def qinv(q):
    return (-q[0], -q[1], -q[2], q[3])


def qangle(a, b) -> float:
    """The rotation between two unit quaternions, in degrees."""
    d = abs(sum(x * y for x, y in zip(a, b)))
    return math.degrees(2.0 * math.acos(min(1.0, d)))


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def qexp(w, seconds):
    theta = norm(w) * seconds
    if theta < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    axis = scale(w, 1.0 / norm(w))
    return (*scale(axis, math.sin(theta / 2.0)), math.cos(theta / 2.0))


def qmat(q):
    """The rotation matrix of the logged quaternion, used as id's axis: a body-frame point
    c is at c . A in the world (a row vector times the matrix)."""
    x, y, z, w = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def rowmul(c, m):
    return tuple(sum(c[i] * m[i][j] for i in range(3)) for j in range(3))


def physics_rotation(c):
    """The physics' own step: the logged quaternion is the conjugate of the body-to-world
    one, so the world angular velocity composes on the right, negated."""
    return qmul(c["q"], qexp(scale(c["w"], -1.0), TICK_SECONDS))


def physics_position(c, com, gravity):
    """sdPhysics_RigidBodyMultiple::Integrate with no external force: the centre of mass
    moves by v.dt (plus a tick of gravity momentum added back), and the origin is
    re-derived from it with the new orientation."""
    if gravity is None:     # the hybrid's rule: add gravity back only off the ground
        gravity = not c.get("ground", False)
    v = add(c["v"], scale(GRAVITY, TICK_SECONDS)) if gravity else c["v"]
    lever = sub(rowmul(com, qmat(c["q"])), rowmul(com, qmat(physics_rotation(c))))
    return add(c["p"], scale(v, TICK_SECONDS), lever)


def solve3(a, b):
    """Solve the 3x3 system a.x = b (Cramer's rule); None when singular."""
    def det(m):
        return (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    d = det(a)
    if abs(d) < 1e-9:
        return None
    out = []
    for k in range(3):
        m = [list(row) for row in a]
        for i in range(3):
            m[i][k] = b[i]
        out.append(det(m) / d)
    return tuple(out)


def fit_centre_of_mass(triples, gravity: bool):
    """The body-frame centre of mass that best explains the logged steps (least squares):
    VEH_TRACE does not print mainCenterOfMass. Uses the logged next orientation, so the
    fit does not depend on the rotation predictor."""
    ata = [[0.0] * 3 for _ in range(3)]
    atb = [0.0] * 3
    for c, nxt in triples:
        v = add(c["v"], scale(GRAVITY, TICK_SECONDS)) if gravity else c["v"]
        residual = sub(nxt["p"], add(c["p"], scale(v, TICK_SECONDS)))
        a, a2 = qmat(c["q"]), qmat(nxt["q"])
        d = [[a[i][j] - a2[i][j] for j in range(3)] for i in range(3)]   # residual = com . d
        for j in range(3):
            for k in range(3):
                ata[j][k] += sum(d[j][i] * d[k][i] for i in range(3))
            atb[j] += sum(d[j][i] * residual[i] for i in range(3))
    return solve3(ata, atb) or (0.0, 0.0, 0.0)


MOVING_U = 0.5  # u per tick: a hull that moved less is parked (seat-poses' moving_poses)
SPIKE = 6.0     # u per tick^2: hull-lead.md 3.3's impact guard
SPIKE_HOLD = 3  # ticks of constant velocity after a spike


def samples_accel(a, b, c):
    return add(c["p"], sub(c["p"], b["p"]), sub(add(c["p"], a["p"]), scale(b["p"], 2.0)))


def samples_velocity(a, b, c):
    return add(c["p"], sub(c["p"], b["p"]))


def samples_rotation(b, c):
    return qmul(qmul(c["q"], qinv(b["q"])), c["q"])


def read(paths: list[str]) -> dict:
    """Tracks keyed by (log, segment, entity); a tick number that does not increase (a map
    restart) starts a new segment, so a restart never overwrites earlier samples."""
    tracks: dict = {}
    for path in paths:
        segment, last = 0, -1
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                body = VEH_BODY_RE.search(line)
                if body:
                    sample = tracks.get((path, segment, int(body[2])), {}).get(int(body[1]))
                    if sample is not None:
                        sample["com"] = vec(body[3])
                        sample["ground"] = body[4] == "1"
                    continue
                m = VEH_TRACE_RE.search(line)
                if not m:
                    continue
                tick = int(m[1])
                if tick < last:
                    segment += 1
                last = tick
                tracks.setdefault((path, segment, int(m[2])), {})[tick] = {
                    "cls": m[3].replace("vehicle_", ""), "p": vec(m[4]), "q": vec(m[5]),
                    "v": vec(m[6]), "w": vec(m[7]), "coll": int(m[9])}
    return tracks


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--worst", type=int, default=0, help="list the N worst ticks of the constant-acceleration predictor")
    ap.add_argument("--all-ticks", action="store_true",
                    help="include parked ticks (by default only ticks whose hull moves at least MOVING_U, as seat-poses does)")
    args = ap.parse_args(argv)

    # tick triples (n-2, n-1, n) with n+1 present, per class
    by_class: dict = {}
    parked: dict = {}
    for track in read(args.logs).values():
        spike_left = 0
        for t in sorted(track):
            if not all(k in track for k in (t - 2, t - 1, t + 1)):
                spike_left = 0
                continue
            a, b, c, nxt = track[t - 2], track[t - 1], track[t], track[t + 1]
            spike = norm(sub(add(c["p"], a["p"]), scale(b["p"], 2.0))) > SPIKE
            spike_left = SPIKE_HOLD + 1 if spike else max(0, spike_left - 1)
            if not args.all_ticks and norm(sub(nxt["p"], c["p"])) < MOVING_U:
                parked[c["cls"]] = parked.get(c["cls"], 0) + 1
                continue
            by_class.setdefault(c["cls"], []).append((a, b, c, nxt, spike_left > 0))
    if not by_class:
        print("no VEH_TRACE tick triples found (run with g_simHash 1 and an occupied vehicle)")
        return 1

    worst = []
    for cls, rows in by_class.items():
        # VEH_BODY (g_determinismTest 4): mainCenterOfMass is body-local (Physics_RigidBodyMultiple.cpp
        # computes it once from the bodies' moments), so any row's value holds for the class
        logged = rows[0][2].get("com")
        com = {g: logged or fit_centre_of_mass([(c, nxt) for _, _, c, nxt, _ in rows], g) for g in (False, True)}
        has_ground = "ground" in rows[0][2]
        position = {
            "hold (no lead)": lambda a, b, c, guard: c["p"],
            "physics v*dt": lambda a, b, c, guard: physics_position(c, com[False], False),
            "physics v*dt + g*dt^2": lambda a, b, c, guard: physics_position(c, com[True], True),
            "samples, constant velocity": lambda a, b, c, guard: samples_velocity(a, b, c),
            "samples, constant acceleration": lambda a, b, c, guard: samples_accel(a, b, c),
            "samples, accel + impact guard": lambda a, b, c, guard: samples_velocity(a, b, c) if guard else samples_accel(a, b, c),
        }
        if has_ground:
            position["physics, gravity off the ground"] = lambda a, b, c, guard: physics_position(c, com[True], None)
            position["hybrid (physics in the air)"] = lambda a, b, c, guard: (
                (samples_velocity(a, b, c) if guard else samples_accel(a, b, c)) if c.get("ground")
                else physics_position(c, com[True], None))
        rotation = {
            "hold (no lead)": lambda b, c: c["q"],
            "physics w*dt": lambda b, c: physics_rotation(c),
            "samples, constant rotation": samples_rotation,
        }
        motion = [norm(sub(nxt["p"], c["p"])) for _, _, c, nxt, _ in rows]
        print(f"{cls}: {len(rows)} ticks ({parked.get(cls, 0)} parked left out); motion per tick median {percentile(motion, 50):.2f} u,"
              f" p95 {percentile(motion, 95):.2f} u; "
              + (f"centre of mass {tuple(round(x, 1) for x in logged)} (VEH_BODY), on the ground"
                 f" {100.0 * sum(1 for _, _, c, _, _ in rows if c.get('ground')) / len(rows):.0f}% of ticks" if logged else
                 f"fitted centre of mass {tuple(round(x, 1) for x in com[True])} (with gravity),"
                 f" {tuple(round(x, 1) for x in com[False])} (without)"))
        for name, guess in position.items():
            errors = [norm(sub(nxt["p"], guess(a, b, c, guard))) for a, b, c, nxt, guard in rows]
            print(f"  position  {name:32s} median {percentile(errors, 50):7.3f}  p95 {percentile(errors, 95):7.3f}"
                  f"  max {max(errors):7.3f} u")
        for name, guess in rotation.items():
            errors = [qangle(nxt["q"], guess(b, c)) for _, b, c, nxt, _ in rows]
            print(f"  rotation  {name:32s} median {percentile(errors, 50):7.3f}  p95 {percentile(errors, 95):7.3f}"
                  f"  max {max(errors):7.3f} deg")
        for a, b, c, nxt, _ in rows:
            worst.append((norm(sub(nxt["p"], samples_accel(a, b, c))), cls, c, nxt))
    if args.worst:
        print(f"worst {args.worst} ticks (samples, constant acceleration): error, class, speed before and after")
        for err, cls, c, nxt in sorted(worst, key=lambda w: w[0], reverse=True)[:args.worst]:
            print(f"  {err:7.2f} u  {cls}  speed {norm(c['v']):.0f} -> {norm(nxt['v']):.0f} u/s  last collision {nxt['coll']} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
