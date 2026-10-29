#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
rt-build-prewarm (#338): every acceleration-structure build configuration is built once at
ray-tracing init, so no build recorded in play is the first of its kind.

A driver may compile the shaders behind a configuration's BVH builds on its first build.
AMD's does, and caches them per executable: on a cold cache the first deforming BLAS,
built when the first deforming surface came into view, stalled a frame for about 650 ms
(frame-poses-devmap's cap-60 turn on every fresh build). r_rayTracingPrewarmBuilds
(default 1) builds every row of draw_raytracing.cpp's RAY_TRACING_BUILD_CONFIGS at init,
at each size of a ladder, and records each build's key: everything in the build and its
geometry a driver may compile against (type, flags, mode, geometry type and flags, vertex
format and stride, index type, transform, array of pointers) and the ladder rung its size
falls under. A build in play whose key was not prewarmed (a changed flag or format, a build
bypassing the table, a size past the ladder) prints RAY_TRACING_UNWARMED_BUILD, once per key.

Two single-player Vulkan clients on valley with ray tracing on (and the Khronos validation
layer, where it is installed). Each spawns and turns a full circle (+left at in_yawSpeed 90
for 4 s), which brings deforming surfaces into view:

  R0  ray tracing is active ("Ray tracing: Vulkan ray-query lighting active"). Without it
      the test skips (exit 77): the prewarm is skipped with ray tracing.
  R1  r_rayTracingPrewarmBuilds 1: "Ray tracing: prewarmed N acceleration-structure
      builds" at init, and no RAY_TRACING_UNWARMED_BUILD line in the run. Any
      acceleration-structure build over 20 ms (r_vkLogSlowCalls) is reported, not judged:
      cold-cache timing is not deterministic.
  R2  negative control, r_rayTracingPrewarmBuilds 0: RAY_TRACING_UNWARMED_BUILD lines for
      the static BLAS, the deforming BLAS and the TLAS. This shows that the run builds a
      deforming BLAS in play, and that the detector sees a build that was not prewarmed.

Exit 0 pass, 1 fail, 77 no client binary, no retail data or no ray tracing.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_frame_poses import DevmapClient  # noqa: E402
from test_reconnect_restart import Failed, spawn  # noqa: E402
from test_remote_client_map_change import READY  # noqa: E402

RT_ACTIVE = "Ray tracing: Vulkan ray-query lighting active"
RT_UNAVAILABLE_RE = re.compile(r"Vulkan ray tracing (unavailable|initialization failed).*")
PREWARM_RE = re.compile(r"Ray tracing: prewarmed (\d+) acceleration-structure builds \((\d+) configurations\)"
                        r" in ([\d.]+) ms")
UNWARMED_RE = re.compile(r"RAY_TRACING_UNWARMED_BUILD: (.+?) build, (\d+) primitives")
SLOW_BUILD_RE = re.compile(r"VK_SLOW_CALL: .*(BLAS build|BuildAccelerationStructures).*")
SLOW_CALL_MS = 20
KINDS = ("static BLAS", "deforming BLAS", "TLAS")
TURN_SECONDS = 4.0      # 360 degrees at in_yawSpeed 90


class SkipRun(Exception):
    pass


def run_client(vk, data, port, map_name, log_path, prewarm):
    """Spawn, turn a full circle, quit. Returns (log lines, failures)."""
    cl = DevmapClient(vk, data, port, map_name=map_name, log_path=log_path,
                      cvars={"si_rules": "sdGameRulesObjective", "ui_name": "Player", "r_rayTracing": "1",
                             "r_rayTracingPrewarmBuilds": "1" if prewarm else "0",
                             "r_vkLogSlowCalls": str(SLOW_CALL_MS) if prewarm else "0",
                             "in_yawSpeed": "90", "in_angleSpeedKey": "1"})
    cl.name, cl.slot = "Player", None
    failures = []
    code = None
    try:
        cl.start()
        if not cl.wait_for_text(READY, 180.0) or not cl.wait_for_text(f"Map: maps/{map_name}", 180.0):
            raise Failed("the single-player game never loaded the map")
        lines = [ln.text for ln in cl.snapshot()]
        if not any(RT_ACTIVE in s for s in lines):
            reason = next((m.group(0) for s in lines if (m := RT_UNAVAILABLE_RE.search(s))), "no ray-tracing line")
            raise SkipRun(f"ray tracing is not active on this host: {reason.strip()}")
        cl.sync(60.0)
        cl.slot = 0
        spawn(cl, cl, "gdf", timeout=60.0)
        time.sleep(1.0)
        cl.send("+left")
        time.sleep(TURN_SECONDS)
        cl.send("-left")
        time.sleep(1.0)
    except Failed as e:
        failures.append(str(e))
    finally:
        code = cl.quit() if cl.alive() else None
        lines = [ln.text for ln in cl.snapshot()]
        for f in h.scan_failures(lines, run_failed=bool(failures) or code != 0):
            failures.append(f"client log line {f['line']}: {f['text']}")
        for i, text in enumerate(lines):
            if any(m in text for m in BAD_MARKERS):
                failures.append(f"client log line {i + 1}: {text}")
        cl.cleanup()
    if not failures and code != 0:
        failures.append(f"quit exit code: {code}")
    return lines, failures


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29890)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)
    try:
        vk = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    try:
        lines, run_failures = run_client(vk, data, args.port, args.map,
                                         (log_dir / "prewarm.log") if log_dir else None, prewarm=True)
    except SkipRun as e:
        print(f"[SKIP] {e}")
        return 77
    failures += [f"R1 run: {f}" for f in run_failures]
    print(f"  R0 pass: {RT_ACTIVE}")
    prewarm = next((m for s in lines if (m := PREWARM_RE.search(s))), None)
    unwarmed = [s.strip() for s in lines if UNWARMED_RE.search(s)]
    if prewarm is None:
        failures.append("R1: no 'Ray tracing: prewarmed N acceleration-structure builds' line at init")
    elif unwarmed:
        failures.append(f"R1: {len(unwarmed)} build key(s) in play were not prewarmed: {'; '.join(unwarmed)}")
    else:
        print(f"  R1 pass: prewarmed {prewarm.group(1)} builds of {prewarm.group(2)} configurations"
              f" in {prewarm.group(3)} ms; no RAY_TRACING_UNWARMED_BUILD line in the run")
    slow = [m.group(0).strip() for s in lines if (m := SLOW_BUILD_RE.search(s))]
    print(f"  R1 report (not judged): {len(slow)} acceleration-structure build call(s) over {SLOW_CALL_MS} ms"
          + (": " + "; ".join(slow[:6]) if slow else ""))

    try:
        lines, run_failures = run_client(vk, data, args.port + 1, args.map,
                                         (log_dir / "control.log") if log_dir else None, prewarm=False)
    except SkipRun as e:
        failures.append(f"R2: ray tracing was active in the first run but not in the control: {e}")
        lines, run_failures = [], []
    failures += [f"R2 run: {f}" for f in run_failures]
    seen = {}   # configuration -> primitives of its first unwarmed build
    for s in lines:
        if m := UNWARMED_RE.search(s):
            seen.setdefault(m.group(1), int(m.group(2)))
    missing = [k for k in KINDS if k not in seen]
    if any(PREWARM_RE.search(s) for s in lines):
        failures.append("R2: the control prewarmed with r_rayTracingPrewarmBuilds 0")
    if missing:
        failures.append(f"R2: with r_rayTracingPrewarmBuilds 0 no RAY_TRACING_UNWARMED_BUILD line for"
                        f" {', '.join(missing)} (seen: {seen or 'none'}): the run does not exercise that build"
                        f" in play, or the detector does not see it")
    else:
        print("  R2 pass: negative control r_rayTracingPrewarmBuilds 0: first builds in play reported for "
              + ", ".join(f"{k} ({seen[k]} primitives)" for k in KINDS))

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
