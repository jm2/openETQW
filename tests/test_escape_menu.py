#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Escape in game opens the main menu, and Escape again closes it.

idTech 4's session opens the main menu on an Escape that no GUI takes. Ours passed
it on to the usercmd generator, where nothing is bound to it, so Escape did nothing
in game. idSessionLocal::ProcessEvent now calls StartMenu() for it; the main menu,
once active, takes the next Escape itself (game HandleGuiEvent) and closes.

The keys must be real key events, which only the windowing system can make, so the
test drives the Vulkan client under X11 with XTest (through ctypes; libX11 and
libXtst). It skips (77) without retail data, the client binary, an X display or
those libraries. The client's window must have the keyboard focus, which a new
window gets on a private display (a headless mutter's Xwayland, as on the Linux x64
host); do not run it on a desktop in use.

The client is a listen server on valley (objective rules) with the retail default
bindings; one GDF soldier is spawned and has landed. Then, by its PLAYER_POS:
1. hold W for 1 s: the player moves (keys reach the game)
2. tap Escape, hold W for 1 s: the player does not move (the menu has the keys)
3. tap Escape, hold W for 1 s: the player moves again (the menu closed)

Usage: test_escape_menu.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import math
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed, player_pos, spawn  # noqa: E402

MOVED = 100.0   # units a 1 s run must cover (a soldier runs ~250/s)
STILL = 16.0    # units the player may drift while the menu has the keys
HOLD = 1.0      # seconds W is held


class XKeys:
    """Real key presses on the X display through XTest."""

    def __init__(self):
        x11_name = ctypes.util.find_library("X11")
        xtst_name = ctypes.util.find_library("Xtst")
        if not x11_name or not xtst_name:
            raise h.SkipTest("libX11 or libXtst not found")
        self.x11 = ctypes.CDLL(x11_name)
        self.xtst = ctypes.CDLL(xtst_name)
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XStringToKeysym.restype = ctypes.c_ulong
        self.x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.dpy = self.x11.XOpenDisplay(None)
        if not self.dpy:
            raise h.SkipTest(f"cannot open X display {os.environ.get('DISPLAY')!r}")

    def hold(self, keysym: str, seconds: float) -> None:
        code = self.x11.XKeysymToKeycode(self.dpy, self.x11.XStringToKeysym(keysym.encode()))
        self.xtst.XTestFakeKeyEvent(self.dpy, code, 1, 0)
        self.x11.XFlush(self.dpy)
        time.sleep(seconds)
        self.xtst.XTestFakeKeyEvent(self.dpy, code, 0, 0)
        self.x11.XFlush(self.dpy)
        time.sleep(0.3)

    def close(self) -> None:
        self.x11.XCloseDisplay(self.dpy)


def distance(a, b) -> float:
    return math.dist(a["org"][:2], b["org"][:2])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29908)
    ap.add_argument("--log-dir")
    args = ap.parse_args()
    try:
        client = find_client(args.client_binary)
        data = h.resolve_data_path()
        if not os.environ.get("DISPLAY"):
            raise h.SkipTest("no X display (DISPLAY is not set)")
        keys = XKeys()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    h.install_signal_handlers()
    os.environ["SDL_VIDEODRIVER"] = "x11"  # XTest reaches X11 windows only
    log = None
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        log = Path(args.log_dir) / "escape-menu.log"

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
        gfx.send("exec localization/english/defaultbinds.cfg")
        gfx.sync()
        start = spawn(gfx, gfx, "gdf")
        time.sleep(1.0)

        print("1. W moves the player")
        keys.hold("w", HOLD)
        p1 = player_pos(gfx, 0)
        moved = distance(start, p1)
        expect(moved >= MOVED, f"moved {moved:.0f} units (need >= {MOVED:.0f})")

        print("2. Escape opens the menu; W does not move the player")
        keys.hold("Escape", 0.08)
        time.sleep(1.0)
        before = player_pos(gfx, 0)
        keys.hold("w", HOLD)
        p2 = player_pos(gfx, 0)
        moved = distance(before, p2)
        expect(moved <= STILL, f"moved {moved:.0f} units with the menu open (need <= {STILL:.0f})")

        print("3. Escape closes the menu; W moves the player again")
        keys.hold("Escape", 0.08)
        time.sleep(1.0)
        before = player_pos(gfx, 0)
        keys.hold("w", HOLD)
        p3 = player_pos(gfx, 0)
        moved = distance(before, p3)
        expect(moved >= MOVED, f"moved {moved:.0f} units after the menu closed (need >= {MOVED:.0f})")
    except Failed as e:
        failures.append(str(e))
        print(f"FAIL: {e}")
    finally:
        keys.close()
        code = gfx.quit()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: Escape opens the main menu in game and closes it again")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
