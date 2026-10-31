#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The display's modes are available fullscreen (#383).

The game's graphics menu lists, in fullscreen, only the r_mode table's modes marked
available ("don't show modes that the monitor says it doesn't support"), and no
platform layer marked any, so the list was empty. The SDL layer now marks those the
display lists (src/sys/sdl/VideoModeAvailability.h).

The Vulkan client hosts valley on a real display; `listModes` then prints the table,
with " - Not available for fullscreen" after each mode not marked:
  V1 at least one mode is available
  V2 the client logged how many ("SDL3: N of 26 video modes are available fullscreen")
It skips (77) without retail data, the client binary or an X display, and on SDL's
headless drivers, which report no real display.

Usage: test_video_modes.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed  # noqa: E402

MODE_RE = re.compile(r"^Mode +\d+: \d+x\d+")
COUNT_RE = re.compile(r"SDL3: (\d+) of (\d+) video modes are available fullscreen")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29917)
    ap.add_argument("--log-dir")
    args = ap.parse_args()
    try:
        client = find_client(args.client_binary)
        data = h.resolve_data_path()
        if not os.environ.get("DISPLAY"):
            raise h.SkipTest("no X display (DISPLAY is not set)")
        if os.environ.get("SDL_VIDEODRIVER") in ("offscreen", "dummy"):
            raise h.SkipTest(f"SDL_VIDEODRIVER={os.environ['SDL_VIDEODRIVER']} has no real display")
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    h.install_signal_handlers()
    os.environ["SDL_VIDEODRIVER"] = "x11"
    log = None
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        log = Path(args.log_dir) / "video-modes.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                       cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0"})
    gfx.name = "VulkanClient"
    gfx.slot = 0
    try:
        gfx.start()
        if not gfx.wait_for_text("Spawning entities", 180.0):
            raise Failed("the listen server never loaded valley")
        gfx.sync()
        mark = gfx.mark()
        gfx.send("listModes")
        gfx.sync()
        modes = [ln.text for ln in gfx.snapshot(mark) if MODE_RE.match(ln.text)]
        available = [m for m in modes if "Not available" not in m]
        print(f"  listModes: {len(available)} of {len(modes)} available ({', '.join(m.split(': ')[1] for m in available)})")
        expect(len(modes) > 0, "listModes printed the mode table")
        expect(len(available) > 0, "V1 at least one mode is available fullscreen")
        counts = [m for m in (COUNT_RE.search(ln.text) for ln in gfx.snapshot(0)) if m]
        expect(bool(counts) and int(counts[-1].group(1)) == len(available),
               f"V2 the client logged the count ({counts[-1].group(0) if counts else 'no line'})")
    except Failed as e:
        failures.append(str(e))
        print(f"FAIL: {e}")
    finally:
        code = gfx.quit()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the display's modes are available fullscreen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
