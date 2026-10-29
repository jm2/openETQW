#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
failed-frame-uploads (#343): a Vulkan frame that fails in EndFrame, after it was recorded, must
not leave later frames relying on work it never submitted.

Before the fix, such a frame (a failed vkEndCommandBuffer or vkQueueSubmit2, or the cheat
r_vkTestFailFrame 2) dropped what it had recorded, but the recording had already marked that
work done:
- the images it created were registered as uploaded;
- the images whose first layout transition it recorded (_currentRender and the glow buffers,
  after a swapchain recreate) were marked initialized;
- the BLASes it built were marked built.
Later frames then sampled images still in UNDEFINED layout, and recorded barriers from a layout
the images were never in. The validation layer reports both.

One single-player Vulkan client on valley, with r_vkValidation 1, and r_rayTracing 1 where ray
tracing is available:

  U1  the first frame the client draws fails (r_vkTestFailFrame 2 on the command line). That
      frame creates images (the loading screen uses them for the first time). Its "frame not
      submitted" line must report at least one image created and its uploads submitted alone.
      (The command line's +set is applied again once the game has initialized, so a second
      frame fails then; it is not judged.)
  U2  the player spawns, so every frame copies _currentRender for the first-person weapon.
      Then, with r_vkPrepareAhead 0 so that nothing is held across a recreate, RECREATES times:
      an r_swapInterval toggle and r_vkTestFailFrame 2 in one console line. The failed frame is
      the one that recreates the swapchain, so it records the new _currentRender's first layout
      transition. Each "frame not submitted" line must report at least one rolled back.
  U3  Vulkan frames go on after the injections: MIN_TIMING_WINDOWS FRAME_TIMING windows, each
      with at least MIN_FRAMES presented Vulkan frames.
  U4  no Vulkan validation error, VK_ERROR_ or device loss in the client log. This is the check
      that fails without the fix, so it needs the validation layer: without the layer the test
      skips.
  The BLAS builds each failed frame rolled back are reported, not judged: no injection here can
  be placed on a frame that builds a static BLAS.

Exit 0 pass, 1 fail, 77 no client binary, no retail data or no validation layer.
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

RECREATES = 4
MIN_TIMING_WINDOWS = 3
MIN_FRAMES = 10
FAILING_LINE = "r_vkTestFailFrame 2: failing"
ABANDON_RE = re.compile(r"Vulkan: frame not submitted: (.+?) \((\d+) images created\), rolled back (\d+) first layout"
                        r" transitions and (\d+) BLAS builds")
TIMING_RE = re.compile(r"FRAME_TIMING: .* vk_frames=(\d+)")
LAYER_MISSING = "VK_LAYER_KHRONOS_validation is not installed"


def abandon_report(lines, start):
    """The first "frame not submitted" line at or after start, as (index, match), or None."""
    for i in range(start, len(lines)):
        m = ABANDON_RE.search(lines[i])
        if m:
            return i, m
    return None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29936)
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

    # r_vkValidation 1: the layer's verdict on the layouts is this test's oracle (U4).
    # r_vkTestFailFrame 2 fails the first frame drawn (U1); cheats are refused only in multiplayer.
    cl = DevmapClient(vk, data, args.port, map_name=args.map,
                      log_path=(log_dir / "devmap.log") if log_dir else None,
                      cvars={"si_rules": "sdGameRulesObjective", "ui_name": "Player", "r_swapInterval": "1",
                             "r_vkValidation": "1", "r_rayTracing": "1", "com_logFrameTiming": "1",
                             "r_vkTestFailFrame": "2"})
    cl.name, cl.slot = "Player", None
    failures = []
    reports = []
    code = None
    try:
        cl.start()
        if not cl.wait_for_text(READY, 180.0) or not cl.wait_for_text(f"Map: maps/{args.map}", 180.0):
            raise Failed("the single-player game never loaded the map")
        if cl.sync(60.0) is None:
            raise Failed("the client never answered a console echo after the map loaded")

        lines = [ln.text for ln in cl.snapshot()]
        first = next((i for i, t in enumerate(lines) if FAILING_LINE in t), None)
        hit = abandon_report(lines, first) if first is not None else None
        if first is None:
            failures.append("U1: the first frame's injected failure (r_vkTestFailFrame 2) was never reached")
        elif hit is None:
            failures.append("U1: the first frame failed, but no 'Vulkan: frame not submitted' line followed")
        else:
            m = hit[1]
            reports.append(m.group(0))
            ok = int(m.group(2)) >= 1 and m.group(1) == "submitted its uploads alone"
            print(f"  U1 {'pass' if ok else 'FAIL'}: the first frame failed and {m.group(1)}"
                  f" ({m.group(2)} images created; need at least 1)")
            if not ok:
                failures.append(f"U1: the first failed frame did not exercise #343 or lost its uploads: {m.group(0)}")

        cl.slot = 0
        spawn(cl, cl, "gdf", timeout=60.0)
        time.sleep(1.0)
        cl.send("set r_vkPrepareAhead 0")
        time.sleep(0.5)
        rolled_back = []
        for i in range(RECREATES):
            mark = cl.mark()
            cl.send(f"set r_swapInterval {i % 2}; set r_vkTestFailFrame 2")
            if not cl.wait_for_text(FAILING_LINE, 10.0, mark):
                raise Failed(f"U2: injected failure {i + 1} was never reached: the client stopped drawing frames")
            # without its "frame not submitted" line the injections go on, so U4 still judges them
            seen = cl.wait_for(lambda s: ABANDON_RE.search(s), 5.0, mark)
            if seen:
                reports.append(seen[2].group(0))
                rolled_back.append(int(seen[2].group(3)))
            else:
                rolled_back.append(None)
            time.sleep(0.5)
        ok = all(n is not None and n >= 1 for n in rolled_back)
        print(f"  U2 {'pass' if ok else 'FAIL'}: {RECREATES} recreate frames failed; first layout transitions rolled"
              f" back {rolled_back} (need at least 1 each; None: no 'frame not submitted' line)")
        if not ok:
            failures.append(f"U2: a failed recreate frame reported no first layout transition rolled back"
                            f" ({rolled_back}): it did not exercise #343")
        cl.send("set r_vkPrepareAhead 1")

        mark = cl.mark()
        windows = []
        deadline = time.monotonic() + 15.0
        while len(windows) < MIN_TIMING_WINDOWS and time.monotonic() < deadline:
            hit = cl.wait_for(lambda s: TIMING_RE.search(s), max(0.1, deadline - time.monotonic()), mark)
            if not hit:
                break
            windows.append(int(hit[2].group(1)))
            mark = hit[0] + 1
        ok = len(windows) >= MIN_TIMING_WINDOWS and all(f >= MIN_FRAMES for f in windows)
        print(f"  U3 {'pass' if ok else 'FAIL'}: FRAME_TIMING windows after the injections: vk_frames {windows}"
              f" (need {MIN_TIMING_WINDOWS} windows of at least {MIN_FRAMES})")
        if not ok:
            failures.append(f"U3: Vulkan frames did not go on after the injected failures (vk_frames {windows})")
    except Failed as e:
        failures.append(str(e))
    finally:
        code = cl.quit() if cl.alive() else None
        lines = [ln.text for ln in cl.snapshot()]
        text = "\n".join(lines)
        layer_missing = LAYER_MISSING in text
        bad = [f"client log line {i + 1}: {t}" for i, t in enumerate(lines) if any(m in t for m in BAD_MARKERS)]
        print(f"  U4 {'FAIL' if bad else 'pass'}: {len(bad)} validation error / VK_ERROR_ / device-loss lines;"
              f" validation layer {'not installed' if layer_missing else 'active'}")
        failures.extend(bad)
        for f in h.scan_failures(lines, run_failed=bool(failures) or code != 0):
            failures.append(f"client log line {f['line']}: {f['text']}")
        cl.cleanup()
    blas = [int(ABANDON_RE.search(r).group(4)) for r in reports]
    print(f"  report (not judged): BLAS builds rolled back by each failed frame: {blas}")
    print(f"quit exit code: {code}")
    if not failures and code != 0:
        failures.append(f"quit exit code: {code}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    if layer_missing:
        print(f"[SKIP] {LAYER_MISSING}: U4, the check that fails without the fix, cannot judge this run")
        return 77
    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
