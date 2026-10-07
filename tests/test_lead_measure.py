#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hull-lead measurement: every vehicle class's one-tick predictor errors (M5 step 14b).

docs/hull-lead.md step 2. The game's lead-measurement replay (g_determinismTest 4 on
valley) spawns each rigid-body vehicle class in turn on one open spot, enters it, and
drives it (ground: throttle, a right and a left turn at speed, brake and reverse, forward
again) or flies it (aircraft: lift-off, nose down, a yaw and roll ramp, level, descent).
With g_simHash 1 every tick prints VEH_TRACE, and in this schedule only VEH_BODY (the
body's centre of mass and ground contacts). Any other vehicle that appears during the
run (valley respawns its badger) is removed on the tick it appears and logged as LEAD_FOREIGN.

The test runs it in single player at com_maxFPS 60 and 144 and fails when:
  M1  a class is missing: not spawned, not entered, or fewer than 150 traced ticks;
  M2  a class did not move (median motion per tick under 1 u): the drive did not reach it;
  M3  VEH_BODY is missing for a traced tick;
  M4  VEH_TRACE or VEH_BODY differs between the caps (the replay must not depend on the
      render rate).
It then prints tools/analysis/veh_pred.py's table for every class. The predictor errors
are a measurement for the owner's per-class decision (docs/hull-lead.md §4), not a check.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

REPO_ROOT = h.REPO_ROOT
sys.path.insert(0, str(REPO_ROOT / "tools" / "analysis"))
import veh_pred  # noqa: E402

CAPS = (60, 144)
RUN_TIMEOUT_S = 240.0
CLASSES = ("titan", "hog", "desecrator", "husky", "badger", "trojan", "anansi", "bumblebee", "hornet")
MIN_TICKS = 150
MIN_MOTION_U = 1.0

SEGMENT_RE = re.compile(r"LEAD_SEGMENT: tick=(\d+) class=vehicle_(\S+) ent=(-?\d+)")
ENTER_RE = re.compile(r"ENTER_VEHICLE: client \d+ entered sdVehicle_RigidBody_vehicle_(\S+?)_\d+ ")


def run_schedule(client: Path, data: Path, savepath: Path, cap: int) -> list[str]:
    args = [
        str(client),
        "+set", "fs_basepath", str(data),
        "+set", "fs_savepath", str(savepath / f"cap{cap}"),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "r_swapInterval", "0",
        "+set", "com_maxFPS", str(cap),
        "+set", "g_determinismTest", "4",
        "+set", "g_simHash", "1",
        "+devmap", "valley",
        "+clientTeam", "gdf",
        "+clientClass", "soldier",
        "+closeLimbo",
    ]
    print(f"--- lead-measurement replay at com_maxFPS {cap} ---", flush=True)
    try:
        done = subprocess.run(args, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace", timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"cap {cap}: the replay did not quit within {RUN_TIMEOUT_S:.0f} s") from exc
    lines = done.stdout.splitlines()
    failures = h.scan_failures(lines)
    if done.returncode != 0 or failures:
        tail = "\n".join(lines[-15:])
        raise AssertionError(f"cap {cap}: exit {done.returncode}, failure markers {failures[:3]}\n{tail}")
    return lines


def check_run(lines: list[str]) -> list[str]:
    problems = []
    spawned = {m[2] for ln in lines if (m := SEGMENT_RE.search(ln)) and int(m[3]) >= 0}
    entered = {m[1] for ln in lines if (m := ENTER_RE.search(ln))}
    for cls in CLASSES:
        if cls not in spawned:
            problems.append(f"M1 {cls}: not spawned")
        elif cls not in entered:
            problems.append(f"M1 {cls}: not entered")
    traced: dict[str, list[tuple[int, tuple[float, ...]]]] = {}
    bodies = set()
    for ln in lines:
        if (m := veh_pred.VEH_TRACE_RE.search(ln)):
            traced.setdefault(m[3].replace("vehicle_", ""), []).append((int(m[1]), veh_pred.vec(m[4])))
        elif (m := veh_pred.VEH_BODY_RE.search(ln)):
            bodies.add((int(m[1]), int(m[2])))
    traced_ticks = {(int(m[1]), int(m[2])) for ln in lines if (m := veh_pred.VEH_TRACE_RE.search(ln))}
    for cls in CLASSES:
        track = traced.get(cls, [])
        if len(track) < MIN_TICKS:
            problems.append(f"M1 {cls}: {len(track)} traced ticks, expected at least {MIN_TICKS}")
            continue
        motion = [veh_pred.norm(veh_pred.sub(b[1], a[1])) for a, b in zip(track, track[1:]) if b[0] == a[0] + 1]
        if veh_pred.percentile(motion, 50) < MIN_MOTION_U:
            problems.append(f"M2 {cls}: median motion {veh_pred.percentile(motion, 50):.2f} u per tick:"
                            " the drive did not move it")
    missing = traced_ticks - bodies
    if missing:
        problems.append(f"M3 {len(missing)} traced ticks have no VEH_BODY (first {sorted(missing)[:3]})")
    return problems


def records(lines: list[str]) -> list[str]:
    return [ln.strip() for ln in lines if ln.startswith(("VEH_TRACE:", "VEH_BODY:"))]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log", help="also write the first run's console log here")
    args = parser.parse_args(argv)

    data = h.env_data_path()
    client = h.build_binary("client")
    h.skip_unless_available(data, client)
    savepath = h.private_savepath("etqw-lead-measure-")

    try:
        runs = {cap: run_schedule(client, data, savepath, cap) for cap in CAPS}
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        return h.EXIT_FAIL
    failures = check_run(runs[CAPS[0]])
    a, b = records(runs[CAPS[0]]), records(runs[CAPS[-1]])
    if a != b:
        first = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
        failures.append(f"M4 VEH_TRACE/VEH_BODY differ between com_maxFPS {CAPS[0]} and {CAPS[-1]} at record {first}"
                        f" ({len(a)} and {len(b)} records)")

    foreign = [ln.strip() for ln in runs[CAPS[0]] if ln.startswith("LEAD_FOREIGN:")]
    print(f"foreign vehicles removed during the drive (valley's respawns): {len(foreign)}"
          + (f", first: {foreign[0]}" if foreign else ""))

    log = Path(args.log) if args.log else savepath / "lead-measure.log"
    log.write_text("\n".join(runs[CAPS[0]]) + "\n", encoding="utf-8")
    print(f"--- one-tick predictor errors (tools/analysis/veh_pred.py {log}) ---", flush=True)
    veh_pred.main([str(log), "--worst", "8"])

    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return h.EXIT_FAIL
    print(f"PASS: all {len(CLASSES)} classes spawned, entered and moved; VEH_TRACE and VEH_BODY identical at"
          f" com_maxFPS {CAPS[0]} and {CAPS[-1]}")
    return h.EXIT_PASS


if __name__ == "__main__":
    h.install_signal_handlers()
    sys.exit(main(sys.argv[1:]))
