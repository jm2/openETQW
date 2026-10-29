#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
abandon-acquired-image (#337): a Vulkan frame that fails after its swapchain image was
acquired gives the image up, so the next frames go on.

Before the fix, an early return after the acquire (a failed vkResetFences,
vkResetCommandPool, vkBeginCommandBuffer, vkEndCommandBuffer or vkQueueSubmit2) left the
frame's imageAvailable semaphore with a pending signal and the image owned by the client.
The next acquire into that semaphore is invalid (VUID-vkAcquireNextImageKHR-semaphore-01286),
and each failure kept one more image, until the acquire blocked for good. The cheat cvar
r_vkTestFailFrame makes the next frame fail at one of those points: 1 in BeginFrame, after
the acquire, 2 in EndFrame, before the submit.

One single-player Vulkan client on valley, FIFO presentation (r_swapInterval 1), with
r_vkValidation 1 so the Khronos layer reports misuse wherever it is installed. It injects
FAILURES_PER_MODE failures of each kind, more than the swapchain holds images, and then:

  F1  every injected failure was reached (its "r_vkTestFailFrame N" line)
  F2  Vulkan frames go on: at least MIN_TIMING_WINDOWS FRAME_TIMING windows after the last
      injection, each with at least MIN_FRAMES presented Vulkan frames (vk_frames, not the
      engine's frames, which keep counting while BeginFrame fails)
  F3  no Vulkan validation error, VK_ERROR_ or device loss in the client log; whether the
      layer was active is reported

Exit 0 pass, 1 fail, 77 no client binary or retail data.
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
from test_reconnect_restart import Failed  # noqa: E402
from test_remote_client_map_change import READY  # noqa: E402

FAILURES_PER_MODE = 6
MIN_TIMING_WINDOWS = 3
MIN_FRAMES = 10
TIMING_RE = re.compile(r"FRAME_TIMING: .* vk_frames=(\d+)")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29931)
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

    # r_vkValidation 1: this test reads the layer's verdict on the semaphore (F3)
    cl = DevmapClient(vk, data, args.port, map_name=args.map,
                      log_path=(log_dir / "devmap.log") if log_dir else None,
                      cvars={"si_rules": "sdGameRulesObjective", "ui_name": "Player",
                             "r_swapInterval": "1", "r_vkValidation": "1", "com_logFrameTiming": "1"})
    cl.name, cl.slot = "Player", None
    failures = []
    code = None
    try:
        cl.start()
        if not cl.wait_for_text(READY, 180.0) or not cl.wait_for_text(f"Map: maps/{args.map}", 180.0):
            raise Failed("the single-player game never loaded the map")
        if cl.sync(60.0) is None:
            raise Failed("the client never answered a console echo after the map loaded")

        reached = 0
        for i in range(FAILURES_PER_MODE):
            for mode in (1, 2):
                mark = cl.mark()
                cl.send(f"set r_vkTestFailFrame {mode}")
                if cl.wait_for_text(f"r_vkTestFailFrame {mode}: failing", 10.0, mark):
                    reached += 1
                else:
                    raise Failed(f"F1: injected failure {2 * i + mode} (r_vkTestFailFrame {mode}) was never"
                                 " reached: the client stopped drawing frames")
                time.sleep(0.3)
        print(f"  F1 pass: {reached} injected failures reached ({FAILURES_PER_MODE} in BeginFrame,"
              f" {FAILURES_PER_MODE} in EndFrame)")

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
        print(f"  F2 {'pass' if ok else 'FAIL'}: FRAME_TIMING windows after the injections: vk_frames {windows}"
              f" (need {MIN_TIMING_WINDOWS} windows of at least {MIN_FRAMES})")
        if not ok:
            failures.append(f"F2: Vulkan frames did not go on after the injected failures (vk_frames {windows})")
    except Failed as e:
        failures.append(str(e))
    finally:
        code = cl.quit() if cl.alive() else None
        lines = [ln.text for ln in cl.snapshot()]
        text = "\n".join(lines)
        layer = ("not installed" if "VK_LAYER_KHRONOS_validation is not installed" in text else
                 "active" if "Vulkan validation warning" in text else "active or not reported")
        bad = [f"client log line {i + 1}: {t}" for i, t in enumerate(lines) if any(m in t for m in BAD_MARKERS)]
        print(f"  F3 {'FAIL' if bad else 'pass'}: {len(bad)} validation error / VK_ERROR_ / device-loss lines;"
              f" validation layer {layer}")
        failures.extend(bad)
        for f in h.scan_failures(lines, run_failed=bool(failures) or code != 0):
            failures.append(f"client log line {f['line']}: {f['text']}")
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
