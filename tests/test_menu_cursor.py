#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""In a window, a menu's cursor sits under the system pointer, and the mouse is captured
only in play (#382).

The SDL clients captured the mouse (relative mode) in play and kept it captured in the
menus, where the game's cursor moved by mouse deltas only, away from the system pointer.
Now, while a fullscreen menu shows its cursor, the mouse is not captured and the cursor
is placed at the pointer's position in the window (owner decision, ledger row 083).

The keys and pointer moves must be real events, which only the windowing system can make,
so the test drives the Vulkan client under X11 with XTest, as test_escape_menu.py does, and
skips (77) without retail data, the client binary, an X display or libX11/libXtst. Before
every key press it checks that the focused window is the client's.

The client is a listen server on valley with the retail default bindings; one GDF soldier
is spawned, and Escape opens the main menu. Then:
  C2 the menu has the mouse: a relative pointer move of TURN_PX turns the player's view
     (PLAYER_POS yaw) by less than STILL degrees
  C3 the menu cursor follows the pointer: with the pointer at two places in the window
     (30%, 35% and 70%, 65% of it), the two screenshots differ within CURSOR_BOX of each
     place (main: the cursor stays where its deltas left it, and neither box changes)
That the mouse turns the view in play is not checked here: on the private X display the
tests use, SDL's relative mode gets no XTest relative motion (0 degrees on every build).
The screenshots show retail content and are deleted with the test's temporary directory.

Usage: test_menu_cursor.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_escape_menu import WINDOW_TITLE, XKeys  # noqa: E402
from test_limbo_command_map import read_tga  # noqa: E402
from test_reconnect_restart import Failed, player_pos, spawn  # noqa: E402

POINTS = ((0.30, 0.35), (0.70, 0.65))  # pointer positions, as fractions of the window
CURSOR_BOX = 0.06                      # half-size of the box around each, as a fraction of the window
CHANGED = 40                           # a pixel changed when a channel moved by more than this
MIN_CHANGED = 12                       # changed pixels the box must hold (the cursor is ~16x16 GUI units)
TURN_PX = 200    # a relative pointer move, in pixels
STILL = 1.0      # degrees of yaw it may turn with the menu open


class XPointer:
    """Pointer moves and grab probes on the X display, through the test's XKeys connection."""

    def __init__(self, keys: XKeys):
        self.keys = keys
        x11, xtst = keys.x11, keys.xtst
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x11.XTranslateCoordinates.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int,
                                              ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                              ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong)]
        x11.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                     ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int),
                                     ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint),
                                     ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
        xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        xtst.XTestFakeRelativeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
        self.root = x11.XDefaultRootWindow(keys.dpy)

    def window_rect(self) -> tuple[int, int, int, int]:
        """The client window's x, y (on the root window), width and height."""
        window = self.keys.find_window(WINDOW_TITLE)
        if window is None:
            raise Failed("the client's window was not found")
        x11, dpy = self.keys.x11, self.keys.dpy
        root, gx, gy = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
        w, hgt, border, depth = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        x11.XGetGeometry(dpy, window, ctypes.byref(root), ctypes.byref(gx), ctypes.byref(gy),
                         ctypes.byref(w), ctypes.byref(hgt), ctypes.byref(border), ctypes.byref(depth))
        rx, ry, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
        x11.XTranslateCoordinates(dpy, window, self.root, 0, 0, ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(child))
        return rx.value, ry.value, w.value, hgt.value

    def move_to(self, x: int, y: int) -> None:
        self.keys.xtst.XTestFakeMotionEvent(self.keys.dpy, -1, x, y, 0)
        self.keys.x11.XFlush(self.keys.dpy)

    def move_by(self, dx: int, steps: int = 10) -> None:
        """A relative move of dx pixels, in steps, as a hand would make it."""
        for _ in range(steps):
            self.keys.xtst.XTestFakeRelativeMotionEvent(self.keys.dpy, dx // steps, 0, 0)
            self.keys.x11.XFlush(self.keys.dpy)
            time.sleep(0.02)


def turn(gfx, pointer: XPointer) -> float:
    """Degrees of yaw a TURN_PX relative pointer move turns the player's view."""
    before = player_pos(gfx, 0)["yaw"]
    pointer.move_by(TURN_PX)
    time.sleep(0.5)
    after = player_pos(gfx, 0)["yaw"]
    if before is None or after is None:
        raise Failed("PLAYER_POS has no yaw in this build")
    return abs((after - before + 180.0) % 360.0 - 180.0)


def screenshot(gfx, path: Path) -> Path:
    gfx.send(f'set r_vkScreenshot "{path}"')
    gfx.sync()
    deadline = time.monotonic() + 20.0
    last = -1
    while time.monotonic() < deadline:
        size = path.stat().st_size if path.exists() else -1
        if size > 18 and size == last:
            return path
        last = size
        time.sleep(0.5)
    raise Failed(f"r_vkScreenshot wrote no complete {path.name}")


def changed_near(a: Path, b: Path, fx: float, fy: float) -> int:
    """Pixels within CURSOR_BOX of (fx, fy), as fractions of the frame, that differ between a and b."""
    width, height, pa = read_tga(a)
    wb, hb, pb = read_tga(b)
    if (width, height) != (wb, hb):
        raise Failed(f"the screenshots differ in size ({width}x{height}, {wb}x{hb})")
    x0, x1 = int((fx - CURSOR_BOX) * width), int((fx + CURSOR_BOX) * width)
    y0, y1 = int((fy - CURSOR_BOX) * height), int((fy + CURSOR_BOX) * height)
    count = 0
    for y in range(max(0, y0), min(height, y1)):
        for x in range(max(0, x0), min(width, x1)):
            if max(abs(c - d) for c, d in zip(pa(x, y), pb(x, y))) > CHANGED:
                count += 1
    return count


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29913)
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
        log = Path(args.log_dir) / "menu-cursor.log"

    failures: list[str] = []
    skipped = None

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    pointer = XPointer(keys)
    with h.PrivateTempDir(prefix="openetqw-menu-cursor-") as tmp:
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
            spawn(gfx, gfx, "gdf")
            time.sleep(1.0)
            print(f"  the client's window has the keyboard focus: {keys.focus(WINDOW_TITLE)}")
            wx, wy, ww, wh = pointer.window_rect()
            pointer.move_to(wx + ww // 2, wy + wh // 2)
            time.sleep(1.0)

            keys.hold("Escape", 0.08)
            time.sleep(1.5)
            turned = turn(gfx, pointer)
            expect(turned < STILL, f"C2 with the menu open the mouse does not turn the view ({turned:.1f} deg;"
                   f" need < {STILL})")

            shots = []
            for i, (fx, fy) in enumerate(POINTS):
                pointer.move_to(wx + int(fx * ww), wy + int(fy * wh))
                time.sleep(1.0)
                shots.append(screenshot(gfx, Path(tmp) / f"cursor{i}.tga"))
            for i, (fx, fy) in enumerate(POINTS):
                n = changed_near(shots[0], shots[1], fx, fy)
                expect(n >= MIN_CHANGED, f"C3 the cursor follows the pointer to ({fx:.0%}, {fy:.0%}) of the window"
                       f" ({n} changed pixels there between the two screenshots; need >= {MIN_CHANGED})")
        except Failed as e:
            failures.append(str(e))
            print(f"FAIL: {e}")
        except h.SkipTest as e:
            skipped = str(e)
        finally:
            keys.close()
            code = gfx.quit()
            gfx.cleanup()
    if skipped:
        print(f"[SKIP] {skipped}")
        return h.EXIT_SKIP
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: in a window the menu cursor follows the pointer, and the menu keeps the mouse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
