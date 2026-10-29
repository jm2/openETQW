#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
acquire-ahead (#335): a Vulkan frame's swapchain-image acquire falls before the frame
computes its fraction, not between that fraction and the draw.

On a client that cannot reach its cap, vkAcquireNextImageKHR blocks until the compositor
frees a drawable. On MoltenVK that took 12-18 ms on render-only frames and about 4.6 ms on
tick frames, so render-only draws presented the pose of a fraction sampled that much
earlier, and mixed-caps A10 saw the tick frames present 3-8 ms early relative to them.
r_vkPrepareAhead (default 1) now acquires the next frame's image at the end of EndFrame,
with the fence wait and the command-pool reset it already did there.

One single-player Vulkan client on valley, uncapped, with FIFO presentation
(r_swapInterval 1, set during play so the swapchain is recreated while EndFrame holds an
image acquired ahead), logs FRAME_POSE (just before the draw) and FRAME_SYNC (BeginFrame's
fence wait began, and the frame's acquire time). For each draw, "begin-to-draw" is the
FRAME_POSE time minus the FRAME_SYNC time of the same frame: BeginFrame's waits plus the
draw's preparation.

  B0  'set r_swapInterval 1' recreated the swapchain in FIFO mode (a new "Vulkan swapchain:"
      line with present mode 2)
  B1  r_vkPrepareAhead 1: the median begin-to-draw is at most BEGIN_TO_DRAW_MS. It is
      judged on every host, but it only shows the acquire was taken ahead where the
      acquire blocks (B2's precondition); elsewhere it guards the fence wait and the
      command-pool reset that r_vkPrepareAhead already moved, and its line says so.
  B2  negative control, r_vkPrepareAhead 0 (the acquire in BeginFrame again): where the
      acquire blocks (its median at least BLOCKING_ACQUIRE_MS), the median begin-to-draw
      exceeds BEGIN_TO_DRAW_MS by CONTROL_MARGIN_MS. Where it does not block (a present
      mode that waits elsewhere), B2 is reported, not judged.

Exit 0 pass, 1 fail, 77 no client binary or retail data.
"""

import argparse
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_frame_poses import DevmapClient, parse_pose  # noqa: E402
from test_reconnect_restart import Failed, spawn  # noqa: E402
from test_remote_client_map_change import READY  # noqa: E402

BEGIN_TO_DRAW_MS = 2.0
BLOCKING_ACQUIRE_MS = 2.0
CONTROL_MARGIN_MS = 1.0
MIN_FRAMES = 100
FIFO_PRESENT_MODE = 2   # VK_PRESENT_MODE_FIFO_KHR
SWAPCHAIN_RE = re.compile(r"Vulkan swapchain: .* present mode (\d+)")
SYNC_RE = re.compile(r"FRAME_SYNC: us=(\d+) fence_us=(\d+) acquire_us=(\d+)")


def frames(lines):
    """(begin-to-draw ms, acquire ms) for each FRAME_POSE followed by its frame's FRAME_SYNC."""
    out = []
    pose = None
    for text in lines:
        p = parse_pose(text)
        if p is not None:
            pose = p
            continue
        m = SYNC_RE.search(text)
        if m and pose is not None:
            out.append(((pose["us"] - int(m.group(1))) / 1000.0, int(m.group(3)) / 1000.0))
            pose = None
    return out


def summary(rows):
    return statistics.median(r[0] for r in rows), statistics.median(r[1] for r in rows)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29930)
    ap.add_argument("--phase-seconds", type=float, default=3.0)
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

    # r_vkValidation 0 as in frame-poses: this test measures frame timing
    cl = DevmapClient(vk, data, args.port, map_name=args.map,
                      log_path=(log_dir / "devmap.log") if log_dir else None,
                      cvars={"si_rules": "sdGameRulesObjective", "ui_name": "Player", "com_unlockFPS": "1",
                             "r_swapInterval": "0", "r_vkValidation": "0"})
    cl.name, cl.slot = "Player", None
    failures = []

    def phase(name, seconds):
        begin = cl.mark()
        cl.send(f"echo AA_BEGIN {name}")
        time.sleep(seconds)
        cl.send(f"echo AA_END {name}")
        if not cl.wait_for(lambda s: s.strip() == f"AA_END {name}", 10.0, begin):
            raise Failed(f"the client never echoed the end of phase {name}")
        lines = [ln.text for ln in cl.snapshot(begin)]
        start = next((i for i, s in enumerate(lines) if s.strip() == f"AA_BEGIN {name}"), None)
        end = next((i for i, s in enumerate(lines) if s.strip() == f"AA_END {name}"), None)
        if start is None or end is None:
            raise Failed(f"phase {name}: the client's log lacks AA_BEGIN or AA_END after the phase's mark")
        rows = frames(lines[start:end])
        if len(rows) < MIN_FRAMES:
            raise Failed(f"phase {name}: only {len(rows)} frames with FRAME_POSE and FRAME_SYNC (need {MIN_FRAMES})")
        return rows

    try:
        cl.start()
        if not cl.wait_for_text(READY, 180.0) or not cl.wait_for_text(f"Map: maps/{args.map}", 180.0):
            raise Failed("the single-player game never loaded the map")
        cl.sync(60.0)
        cl.slot = 0
        spawn(cl, cl, "gdf", timeout=60.0)
        mark = cl.mark()
        cl.send("set g_logFramePoses 1")
        cl.send("set r_vkLogFrameSync 1")
        if not cl.wait_for(lambda s: s.startswith("FRAME_POSE:"), 10.0, mark):
            raise Failed("no FRAME_POSE lines after 'set g_logFramePoses 1' (a cheat: are cheats allowed?)")
        cl.send("set com_maxFPS 0")
        # FIFO from here on: BeginFrame recreates the swapchain while EndFrame holds an
        # image acquired ahead (ReleaseAheadImage)
        mark = cl.mark()
        cl.send("set r_swapInterval 1")
        hit = cl.wait_for(lambda s: (m := SWAPCHAIN_RE.search(s)) is not None
                          and int(m.group(1)) == FIFO_PRESENT_MODE, 10.0, mark)
        if not hit:
            raise Failed("B0: no new 'Vulkan swapchain:' line with present mode 2 (FIFO) after"
                         " 'set r_swapInterval 1': the swapchain was not recreated")
        line = getattr(hit[1], "text", hit[1])
        print(f"  B0 pass: the swapchain was recreated in FIFO mode ({str(line).strip()})")
        time.sleep(1.5)

        ahead = phase("ahead", args.phase_seconds)
        cl.send("set r_vkPrepareAhead 0")
        time.sleep(1.0)
        control = phase("control", args.phase_seconds)
        cl.send("set r_vkPrepareAhead 1")
        cl.send("set r_swapInterval 0")
        time.sleep(1.0)

        b2d, acq = summary(ahead)
        c_b2d, c_acq = summary(control)
        blocking = c_acq >= BLOCKING_ACQUIRE_MS
        ok = b2d <= BEGIN_TO_DRAW_MS
        scope = ("the acquire blocks on this host, so this shows it was taken ahead" if blocking else
                 "the acquire does not block on this host, so this guards only the fence wait and pool reset")
        print(f"  B1 {'pass' if ok else 'FAIL'}: r_vkPrepareAhead 1: {len(ahead)} frames, median begin-to-draw"
              f" {b2d:.2f} ms (need <= {BEGIN_TO_DRAW_MS:.1f}), median acquire {acq:.2f} ms; {scope}")
        if not ok:
            failures.append(f"B1: with r_vkPrepareAhead 1 the median begin-to-draw is {b2d:.2f} ms"
                            f" (need <= {BEGIN_TO_DRAW_MS:.1f}): a wait still falls between the fraction and the draw")

        if not blocking:
            print(f"  B2 report: r_vkPrepareAhead 0: median acquire {c_acq:.2f} ms, below {BLOCKING_ACQUIRE_MS:.1f}:"
                  f" the acquire does not block on this host, so the control is not judged"
                  f" (median begin-to-draw {c_b2d:.2f} ms)")
        else:
            ok = c_b2d >= BEGIN_TO_DRAW_MS + CONTROL_MARGIN_MS
            print(f"  B2 {'pass' if ok else 'FAIL'}: negative control r_vkPrepareAhead 0: {len(control)} frames,"
                  f" median acquire {c_acq:.2f} ms, median begin-to-draw {c_b2d:.2f} ms"
                  f" (need >= {BEGIN_TO_DRAW_MS + CONTROL_MARGIN_MS:.1f})")
            if not ok:
                failures.append(f"B2: with r_vkPrepareAhead 0 and a blocking acquire ({c_acq:.2f} ms) the median"
                                f" begin-to-draw is {c_b2d:.2f} ms: the detector did not see the wait")
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
    print(f"quit exit code: {code}")
    if not failures and code != 0:
        failures.append(f"quit exit code: {code}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
