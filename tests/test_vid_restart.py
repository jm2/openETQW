#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""vid_restart applies r_fullscreen to the game window (#365).

Alt+Enter flips r_fullscreen and buffers "vid_restart", as retail 1.5 does, but the
engine never registered vid_restart: every press logged "Unknown command
'vid_restart'" and the window stayed as it was. The SDL client now registers it,
switching the window in place ("vid_restart windowed" clears r_fullscreen first, as
retail's does).

The Vulkan client starts windowed at the main menu (no map), then:
1. set r_fullscreen 1; vid_restart  -> "vid_restart: fullscreen"
2. vid_restart windowed             -> "vid_restart: windowed", r_fullscreen reads 0
and no "Unknown command 'vid_restart'" appears. The reported state is the window's
own SDL_WINDOW_FULLSCREEN flag after SDL_SyncWindow, not the cvar.

Usage: test_vid_restart.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402

READY = "------------- ETQW initialized -------------"
ATTEMPTS = 3  # vid_restart requests per state, for desktops that switch asynchronously


class MenuClient(VulkanClient):
    """The Vulkan client left at the main menu: no map, no server."""

    def command_line(self):
        args = super().command_line()
        assert args[-2] == "+connect"
        return args[:-2]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29902)
    ap.add_argument("--log-dir")
    args = ap.parse_args()
    try:
        client = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    h.install_signal_handlers()
    log = None
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        log = Path(args.log_dir) / "vid-restart.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    gfx = MenuClient(client, data, args.port, connect_to="unused", log_path=log, cvars={"r_vkValidation": "0"})
    try:
        gfx.start()
        if not gfx.wait_for_text(READY, 180.0):
            print("FAIL: the client never finished starting")
            return 1
        gfx.sync()

        print("1. r_fullscreen 1, vid_restart")
        mark = gfx.mark()
        gfx.send("set r_fullscreen 1")
        state = []
        # SDL_SyncWindow can return before a desktop's fullscreen animation (macOS
        # Spaces) finishes; vid_restart is idempotent, so ask again a few times
        for _ in range(ATTEMPTS):
            gfx.send("vid_restart")
            gfx.sync()
            gfx.wait_for(lambda s: s.startswith("vid_restart: "), 15.0, mark)
            state = [ln.text for ln in gfx.snapshot(mark) if ln.text.startswith("vid_restart: ")]
            if state[-1:] == ["vid_restart: fullscreen"]:
                break
            time.sleep(1.0)
        expect(state[-1:] == ["vid_restart: fullscreen"], f"the window is fullscreen (reported {state})")

        print("2. vid_restart windowed")
        mark = gfx.mark()
        state = []
        for _ in range(ATTEMPTS):  # leaving fullscreen can be asynchronous too
            gfx.send("vid_restart windowed")
            gfx.sync()
            gfx.wait_for(lambda s: s.startswith("vid_restart: "), 15.0, mark)
            state = [ln.text for ln in gfx.snapshot(mark) if ln.text.startswith("vid_restart: ")]
            if state[-1:] == ["vid_restart: windowed"]:
                break
            time.sleep(1.0)
        gfx.send("r_fullscreen")
        gfx.sync()
        lines = [ln.text for ln in gfx.snapshot(mark)]
        expect(state[-1:] == ["vid_restart: windowed"], f"the window is windowed (reported {state})")
        expect(any('"r_fullscreen" is:"0"' in t for t in lines), "r_fullscreen reads 0")

        unknown = [ln.text for ln in gfx.snapshot(0) if "Unknown command 'vid_restart'" in ln.text]
        expect(not unknown, f"no Unknown command 'vid_restart' ({len(unknown)})")
    finally:
        code = gfx.quit()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: vid_restart switches the game window to and from fullscreen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
