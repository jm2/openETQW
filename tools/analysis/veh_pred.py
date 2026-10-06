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


def qexp(w, seconds):
    theta = norm(w) * seconds
    if theta < 1e-12:
        return (0.0, 0.0, 0.0, 1.0)
    axis = scale(w, 1.0 / norm(w))
    return (*scale(axis, math.sin(theta / 2.0)), math.cos(theta / 2.0))


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


# position predictors: the pose at n+1 guessed from ticks n-2, n-1 and n
POSITION = {
    "hold (no lead)": lambda a, b, c: c["p"],
    "physics v*dt": lambda a, b, c: add(c["p"], scale(c["v"], TICK_SECONDS)),
    "physics v*dt + g*dt^2": lambda a, b, c: add(c["p"], scale(add(c["v"], scale(GRAVITY, TICK_SECONDS)), TICK_SECONDS)),
    "samples, constant velocity": lambda a, b, c: add(c["p"], sub(c["p"], b["p"])),
    "samples, constant acceleration": lambda a, b, c: add(c["p"], sub(c["p"], b["p"]), sub(add(c["p"], a["p"]), scale(b["p"], 2.0))),
}

# orientation predictors
ROTATION = {
    "hold (no lead)": lambda b, c: c["q"],
    "physics w*dt": lambda b, c: qmul(qexp(c["w"], TICK_SECONDS), c["q"]),
    "samples, constant rotation": lambda b, c: qmul(qmul(c["q"], qinv(b["q"])), c["q"]),
}


def read(paths: list[str]) -> dict:
    tracks: dict = {}
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = VEH_TRACE_RE.search(line)
                if not m:
                    continue
                tracks.setdefault((path, int(m[2])), {})[int(m[1])] = {
                    "cls": m[3].replace("vehicle_", ""), "p": vec(m[4]), "q": vec(m[5]),
                    "v": vec(m[6]), "w": vec(m[7]), "coll": int(m[9])}
    return tracks


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--worst", type=int, default=0, help="list the N worst ticks of the constant-acceleration predictor")
    args = ap.parse_args(argv)

    results: dict = {}
    worst = []
    for (_, _), track in read(args.logs).items():
        for t in sorted(track):
            if not all(k in track for k in (t - 2, t - 1, t + 1)):
                continue
            a, b, c, nxt = track[t - 2], track[t - 1], track[t], track[t + 1]
            r = results.setdefault(c["cls"], {"motion": [], "pos": {}, "rot": {}})
            r["motion"].append(norm(sub(nxt["p"], c["p"])))
            for name, guess in POSITION.items():
                r["pos"].setdefault(name, []).append(norm(sub(nxt["p"], guess(a, b, c))))
            for name, guess in ROTATION.items():
                r["rot"].setdefault(name, []).append(qangle(nxt["q"], guess(b, c)))
            worst.append((r["pos"]["samples, constant acceleration"][-1], c["cls"], t, c["coll"]))

    if not results:
        print("no VEH_TRACE tick triples found (run with g_simHash 1 and an occupied vehicle)")
        return 1
    for cls, r in results.items():
        print(f"{cls}: {len(r['motion'])} ticks; motion per tick median {percentile(r['motion'], 50):.2f} u,"
              f" p95 {percentile(r['motion'], 95):.2f} u")
        for name, errors in r["pos"].items():
            print(f"  position  {name:32s} median {percentile(errors, 50):7.3f}  p95 {percentile(errors, 95):7.3f}"
                  f"  max {max(errors):7.3f} u")
        for name, errors in r["rot"].items():
            print(f"  rotation  {name:32s} median {percentile(errors, 50):7.3f}  p95 {percentile(errors, 95):7.3f}"
                  f"  max {max(errors):7.3f} deg")
    if args.worst:
        print(f"worst {args.worst} ticks (constant acceleration): error, class, tick, last collision time (ms)")
        for err, cls, t, coll in sorted(worst, reverse=True)[:args.worst]:
            print(f"  {err:7.2f} u  {cls}  tick {t}  coll {coll}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
