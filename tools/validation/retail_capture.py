#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture retail ET:QW 1.5 reference frames on Linux x64, in a private headless compositor.

The retail client (etqw.x86, 32-bit X11) runs under its own `mutter --headless` Xwayland,
never on a desktop someone is using. Input goes in through XTest and frames come back with
XGetImage; both through ctypes, so nothing beyond the system libX11/libXtst is needed.

    retail_capture.py --retail-dir /var/tmp/etqw-retail/opt/etqw --out /var/tmp/retail-shots \\
        --map valley --anchors ../shots/main/screenshot_set.json --views views.json

The retail tree is the extracted retail package (etqw.x86 plus base/ with the 1.5 paks),
for example from `tar --zstd -xf etqw-1.5-*.pkg.tar.zst` and the data package into one
scratch directory (docs/retail-comparison.md § 1). The output directory must be outside
both repositories: retail frames never enter them (AGENTS.md rule 3). Each view writes
<map>_<view>.png (the mean of --frames grabs) and one capture.json with the mean RGB of the
docs/retail-comparison.md § 4 boxes, which are the part that can be published.

Views: --views is a JSON list of {"name", "x", "y", "z", "yaw", "pitch"}; without it the
script takes screenshot_set.py's own views (its VIEWS table) around the --anchors position
for the map. The retail-settings resets of docs/retail-comparison.md § 3 are applied unless
--keep-profile-spec is given; --set adds console commands (`--set "g_skipPostProcess 1"`).
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import importlib.util
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# docs/retail-comparison.md § 3: a profile retail makes itself is auto-detected low-spec
SPEC_RESETS = ["com_machineSpec 3", "r_shaderQuality 0", "r_detailTexture 1", "r_skipStuff 0",
               "r_visDistMult 1", "r_renderProgramLodDistance 200"]

# docs/retail-comparison.md § 4, at 640x480
BOXES = {"near_ground": (20, 300, 300, 470), "mid": (20, 200, 300, 300),
         "sky": (20, 20, 200, 120), "rifle": (400, 330, 500, 400)}

KEYSYMS = {"`": 0x60, " ": 0x20, ".": 0x2e, "-": 0x2d, "\n": 0xff0d}


def load_screenshot_set():
    spec = importlib.util.spec_from_file_location("screenshot_set", REPO_ROOT / "tools/validation/screenshot_set.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- the private compositor -------------------------------------------------------------------

class Compositor:
    """mutter --headless with its own Wayland socket; its Xwayland is where retail runs."""

    def __init__(self, workdir: Path, size: str = "1024x768"):
        self.name = f"oe-retail-{os.getpid()}"
        self.log_path = workdir / "mutter.log"
        self.log = open(self.log_path, "wb")
        self.proc = subprocess.Popen(
            ["mutter", "--headless", "--wayland", f"--wayland-display={self.name}",
             f"--virtual-monitor={size}"],
            stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        self.started = time.time()
        self.display, self.xauth = None, None

    def wait_for_x(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"mutter exited ({self.proc.returncode}); see {self.log_path}")
            m = re.search(r"Using public X11 display (:\d+)", self.log_path.read_text(errors="replace"))
            if m:
                self.display = m.group(1)
                return
            time.sleep(0.5)
        raise RuntimeError(f"no X11 display from mutter within {timeout:.0f} s; see {self.log_path}")

    def auth_candidates(self) -> list[str]:
        # mutter starts Xwayland on demand, at the first X client, so there is no Xwayland
        # -auth argument to read yet. Its auth file is a .mutter-Xwaylandauth.* made after this
        # compositor started; the newest one overall can belong to someone else's compositor
        # (docs/retail-comparison.md § 1), so X.open tries each made since, newest first.
        run = Path(f"/run/user/{os.getuid()}")
        files = [p for p in run.glob(".mutter-Xwaylandauth.*") if p.stat().st_mtime >= self.started - 1]
        return [str(p) for p in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]

    def close(self) -> None:
        if self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
        self.log.close()


# --- X11 through ctypes -------------------------------------------------------------------------

class XImage(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
                ("format", ctypes.c_int), ("data", ctypes.POINTER(ctypes.c_ubyte)),
                ("byte_order", ctypes.c_int), ("bitmap_unit", ctypes.c_int),
                ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
                ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int),
                ("bits_per_pixel", ctypes.c_int)]


class XWindowAttributes(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int), ("height", ctypes.c_int),
                ("border_width", ctypes.c_int), ("depth", ctypes.c_int), ("visual", ctypes.c_void_p),
                ("root", ctypes.c_ulong), ("c_class", ctypes.c_int), ("bit_gravity", ctypes.c_int),
                ("win_gravity", ctypes.c_int), ("backing_store", ctypes.c_int),
                ("backing_planes", ctypes.c_ulong), ("backing_pixel", ctypes.c_ulong),
                ("save_under", ctypes.c_int), ("colormap", ctypes.c_ulong), ("map_installed", ctypes.c_int),
                ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
                ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
                ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p)]


# Xlib's default error handler exits the process, which would leave retail and the compositor
# running; record the error instead and let the caller see the failed call
X_ERRORS: list[int] = []
_XErrorHandler = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


@_XErrorHandler
def _record_x_error(_dpy, event):
    X_ERRORS.append(ctypes.cast(event, ctypes.POINTER(ctypes.c_ubyte))[16])  # XErrorEvent.error_code
    return 0


class X:
    ZPixmap, AllPlanes = 2, ctypes.c_ulong(~0 & 0xFFFFFFFFFFFFFFFF)

    def __init__(self, compositor: Compositor, timeout: float = 20.0):
        self.x = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self.xt = ctypes.CDLL(ctypes.util.find_library("Xtst") or "libXtst.so.6")
        x = self.x
        x.XOpenDisplay.restype = ctypes.c_void_p
        x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong
        x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                 ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                                 ctypes.POINTER(ctypes.c_uint)]
        x.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)] + \
            [ctypes.POINTER(ctypes.c_int)] * 2 + [ctypes.POINTER(ctypes.c_uint)] * 4
        x.XGetImage.restype = ctypes.POINTER(XImage)
        x.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
        x.XDestroyImage = None  # a macro; free through XFree on the struct's data and itself
        x.XFree.argtypes = [ctypes.c_void_p]
        x.XKeysymToKeycode.restype = ctypes.c_ubyte
        x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XFlush.argtypes = [ctypes.c_void_p]
        x.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        x.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        x.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(XWindowAttributes)]
        x.XSetErrorHandler.argtypes = [_XErrorHandler]
        x.XSetErrorHandler.restype = ctypes.c_void_p
        x.XSetErrorHandler(_record_x_error)
        self.xt.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        # the first connection starts Xwayland, which can take a few seconds
        self.dpy = None
        deadline = time.monotonic() + timeout
        while not self.dpy and time.monotonic() < deadline:
            for auth in compositor.auth_candidates():
                os.environ["XAUTHORITY"] = auth
                self.dpy = x.XOpenDisplay(compositor.display.encode())
                if self.dpy:
                    compositor.xauth = auth
                    break
            else:
                time.sleep(1.0)
        if not self.dpy:
            raise RuntimeError(f"cannot open X display {compositor.display} with this compositor's auth")
        self.root = x.XDefaultRootWindow(self.dpy)

    def geometry(self, win: int) -> tuple[int, int]:
        root, x, y = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
        w, h, b, d = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        if not self.x.XGetGeometry(self.dpy, win, ctypes.byref(root), ctypes.byref(x), ctypes.byref(y),
                                   ctypes.byref(w), ctypes.byref(h), ctypes.byref(b), ctypes.byref(d)):
            return 0, 0
        return w.value, h.value

    def drawable(self, win: int) -> bool:
        """Viewable and InputOutput: XGetImage on an InputOnly window (the compositor makes a
        screen-sized one) is a BadMatch."""
        attrs = XWindowAttributes()
        if not self.x.XGetWindowAttributes(self.dpy, win, ctypes.byref(attrs)):
            return False
        return attrs.map_state == 2 and attrs.c_class == 1  # IsViewable, InputOutput

    def children(self, win: int) -> list[int]:
        root, parent = ctypes.c_ulong(), ctypes.c_ulong()
        kids, n = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
        if not self.x.XQueryTree(self.dpy, win, ctypes.byref(root), ctypes.byref(parent),
                                 ctypes.byref(kids), ctypes.byref(n)):
            return []
        out = [kids[i] for i in range(n.value)]
        if kids:
            self.x.XFree(kids)
        return out

    def game_window(self, min_width: int = 600) -> int | None:
        """The largest drawable top-level window, then down through its largest drawable child
        while one is at least min_width wide: the compositor's frame holds the title bar, and
        the client window inside it is retail's own r_mode-sized surface."""
        def largest(wins):
            best, area = None, 0
            for w in wins:
                ww, hh = self.geometry(w)
                if ww >= min_width and ww * hh > area and self.drawable(w):
                    best, area = w, ww * hh
            return best
        win = largest(self.children(self.root))
        self.top = win
        while win is not None:
            inner = largest(self.children(win))
            if inner is None:
                return win
            win = inner
        return None

    def focus(self, win: int) -> None:
        # keys reach retail through its top-level (the compositor's frame), not the inner window
        win = getattr(self, "top", None) or win
        self.x.XRaiseWindow(self.dpy, win)
        self.x.XSetInputFocus(self.dpy, win, 1, 0)  # RevertToPointerRoot, CurrentTime
        self.x.XSync(self.dpy, 0)

    def key(self, keysym: int, hold: float = 0.04) -> None:
        code = self.x.XKeysymToKeycode(self.dpy, keysym)
        if not code:
            raise ValueError(f"no keycode for keysym {keysym:#x}")
        self.xt.XTestFakeKeyEvent(self.dpy, code, 1, 0)
        self.x.XFlush(self.dpy)
        time.sleep(hold)
        self.xt.XTestFakeKeyEvent(self.dpy, code, 0, 0)
        self.x.XFlush(self.dpy)
        time.sleep(0.03)

    def type(self, text: str) -> None:
        for ch in text:
            if ch.isalnum() and ch.isascii() and not ch.isupper():
                self.key(ord(ch))
            elif ch in KEYSYMS:
                self.key(KEYSYMS[ch])
            else:
                # XTest can't type an underscore or a capital without a modifier dance;
                # commands go through an exec'd .cfg instead (docs/retail-comparison.md § 2)
                raise ValueError(f"cannot type {ch!r}: put the command in a .cfg")

    def grab(self, win: int) -> tuple[int, int, bytes]:
        w, h = self.geometry(win)
        del X_ERRORS[:]
        img = self.x.XGetImage(self.dpy, win, 0, 0, w, h, self.AllPlanes, self.ZPixmap)
        if not img:
            raise RuntimeError(f"XGetImage of window {win:#x} ({w}x{h}) failed, X errors {X_ERRORS}")
        im = img.contents
        if im.bits_per_pixel != 32:
            raise RuntimeError(f"unexpected {im.bits_per_pixel} bpp")
        raw = ctypes.string_at(im.data, im.bytes_per_line * h)
        stride = im.bytes_per_line
        self.x.XFree(im.data)
        self.x.XFree(img)
        rgb = bytearray(w * h * 3)
        for row in range(h):  # BGRX, little-endian
            src = raw[row * stride: row * stride + w * 4]
            out = rgb[row * w * 3:(row + 1) * w * 3]
            out[0::3], out[1::3], out[2::3] = src[2::4], src[1::4], src[0::4]
            rgb[row * w * 3:(row + 1) * w * 3] = out
        return w, h, bytes(rgb)


# --- images ---------------------------------------------------------------------------------------

def mean_frames(frames: list[tuple[int, int, bytes]]) -> tuple[int, int, bytes]:
    w, h = frames[0][0], frames[0][1]
    if any((f[0], f[1]) != (w, h) for f in frames):
        raise RuntimeError("the window changed size between grabs")
    n = len(frames)
    sums = [0] * (w * h * 3)
    for _, _, data in frames:
        for i, v in enumerate(data):
            sums[i] += v
    return w, h, bytes((s + n // 2) // n for s in sums)


def write_png(path: Path, w: int, h: int, rgb: bytes) -> None:
    rows = b"".join(b"\0" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(rows, 6)) + chunk(b"IEND", b""))


def box_means(w: int, h: int, rgb: bytes) -> dict:
    # the boxes are defined at 640x480; scale them to the grab
    sx, sy = w / 640.0, h / 480.0
    out = {}
    for name, (x0, y0, x1, y1) in BOXES.items():
        X0, Y0, X1, Y1 = int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)
        acc, n = [0, 0, 0], 0
        for y in range(Y0, Y1):
            base = (y * w + X0) * 3
            for i in range(X1 - X0):
                p = base + i * 3
                acc[0] += rgb[p]; acc[1] += rgb[p + 1]; acc[2] += rgb[p + 2]
                n += 1
        out[name] = [round(c / n, 2) for c in acc]
    return out


# --- retail ---------------------------------------------------------------------------------------

def inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


class Retail:
    def __init__(self, retail_dir: Path, profile: Path, x: X, compositor: Compositor, log_path: Path):
        self.dir, self.profile, self.xw = retail_dir, profile, x
        (profile / "base").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, DISPLAY=compositor.display, XAUTHORITY=compositor.xauth,
                   LD_LIBRARY_PATH=str(retail_dir))
        env.pop("WAYLAND_DISPLAY", None)
        self.log = open(log_path, "wb")
        self.cfg_n = 0
        self.proc = subprocess.Popen(
            ["./etqw.x86", "+set", "fs_basepath", str(retail_dir), "+set", "fs_savepath", str(profile),
             "+set", "fs_userpath", str(profile), "+set", "r_fullscreen", "0", "+set", "r_mode", "3",
             "+set", "s_driver", "null", "+set", "logFile", "2", "+set", "com_allowConsole", "1"],
            cwd=retail_dir, env=env, stdout=self.log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        self.win = None

    def wait_window(self, timeout: float = 90.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"retail exited ({self.proc.returncode})")
            win = self.xw.game_window()
            if win:
                self.win = win
                return win
            time.sleep(1.0)
        raise RuntimeError("retail opened no window")

    def console_open(self) -> bool:
        """The console's cyan bottom line sits near row 235 of the 480-row window when it is
        open (docs/retail-comparison.md § 2): look for a mostly cyan row in rows 220-250."""
        w, h, rgb = self.xw.grab(self.win)
        sy = h / 480.0
        for y in range(int(220 * sy), min(h, int(250 * sy))):
            row = rgb[y * w * 3:(y + 1) * w * 3]
            cyan = sum(1 for i in range(0, len(row), 3) if row[i] < 110 and row[i + 1] > 150 and row[i + 2] > 150)
            if cyan > w * 0.6:
                return True
        return False

    def set_console(self, want_open: bool) -> None:
        for _ in range(4):
            if self.console_open() == want_open:
                return
            self.xw.game_window()  # the frame can be replaced as the window maps
            self.xw.focus(self.win)
            self.xw.key(KEYSYMS["`"])
            time.sleep(0.8)
        w, h, rgb = self.xw.grab(self.win)
        write_png(Path(self.log.name).parent / "console-state.png", w, h, rgb)
        raise RuntimeError(f"could not {'open' if want_open else 'close'} the console "
                           f"(last grab: {Path(self.log.name).parent / 'console-state.png'})")

    def run(self, commands: list[str]) -> None:
        """Run console commands through an exec'd .cfg: XTest types only the exec line."""
        self.cfg_n += 1
        name = f"oecap{self.cfg_n}"
        (self.profile / "base" / f"{name}.cfg").write_text("\n".join(commands) + "\n", encoding="utf-8")
        self.set_console(True)
        self.xw.type(f"exec {name}.cfg\n")
        time.sleep(0.4)
        self.set_console(False)

    def console_log(self) -> str:
        for p in (self.profile / "base" / "qconsole.log", self.profile / "qconsole.log"):
            if p.is_file():
                return p.read_text(errors="replace")
        return ""

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(5)
        self.log.close()


def views_for(args, ss) -> list[dict]:
    if args.views:
        return json.loads(Path(args.views).read_text(encoding="utf-8"))
    anchors = {k: tuple(v) for k, v in getattr(ss, "ANCHORS", {}).items()}
    if args.anchors:
        anchors.update({k: tuple(v) for k, v in
                        json.loads(Path(args.anchors).read_text(encoding="utf-8")).get("anchors", {}).items()})
    if args.map not in anchors:
        raise SystemExit(f"no anchor for {args.map}: pass --anchors <screenshot_set.json> or --views")
    x, y, z, yaw = anchors[args.map][:4]
    spawn = (x, y, z, yaw, 0.0)
    out = [{"name": name, **dict(zip(("x", "y", "z", "yaw", "pitch"), ss.view_target(spawn, offset, dyaw, pitch))),
            "noclip": noclip} for name, offset, dyaw, pitch, noclip in ss.VIEWS]
    out += [{"name": name, "x": at[0], "y": at[1], "z": at[2], "yaw": at[3], "pitch": pitch, "noclip": noclip}
            for name, at, pitch, noclip in ss.EXTRA_VIEWS.get(args.map, [])]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--retail-dir", required=True, type=Path, help="extracted retail tree with etqw.x86 and base/")
    ap.add_argument("--out", required=True, type=Path, help="output directory, outside both repositories")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--views", help="JSON list of {name, x, y, z, yaw, pitch}")
    ap.add_argument("--anchors", help="screenshot_set.json whose anchor positions to reuse")
    ap.add_argument("--set", action="append", default=[], help="extra console command, repeatable")
    ap.add_argument("--keep-profile-spec", action="store_true", help="don't apply the § 3 resets")
    ap.add_argument("--frames", type=int, default=4, help="grabs averaged per view (§ 4: 3 to 6)")
    ap.add_argument("--settle", type=float, default=12.0, help="seconds after each setviewpos")
    ap.add_argument("--load-wait", type=float, default=75.0, help="seconds for the map to load")
    a = ap.parse_args()

    game_repo = Path(os.environ.get("OPENETQW_GAME_REPO", REPO_ROOT.parent / "openETQW-game"))
    for root in (REPO_ROOT, game_repo):
        if inside(a.out, root):
            raise SystemExit(f"--out {a.out} is inside {root}: retail frames never enter a repository")
    if not (a.retail_dir / "etqw.x86").is_file() or not (a.retail_dir / "base" / "pak000.pk4").is_file():
        raise SystemExit(f"{a.retail_dir} has no etqw.x86 and base/pak000.pk4")
    if shutil.which("mutter") is None:
        raise SystemExit("mutter is not installed")
    ss = load_screenshot_set()
    views = views_for(a, ss)

    a.out.mkdir(parents=True, exist_ok=True)
    work = a.out / "work"
    work.mkdir(exist_ok=True)
    comp = Compositor(a.out)
    retail = None
    report = {"map": a.map, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "settings": [], "views": []}
    try:
        comp.wait_for_x()
        xw = X(comp)
        retail = Retail(a.retail_dir, work / "profile", xw, comp, a.out / "retail-stdout.log")
        retail.wait_window()
        time.sleep(8.0)  # the intro and the main menu
        settings = ([] if a.keep_profile_spec else SPEC_RESETS) + a.set
        report["settings"] = settings
        # retail's default rules are a campaign, which refuses a single map
        # ("User Map Start Denied 'Unknown Campaign'"); screenshot_set.py plays objective too
        retail.run(settings + ["si_rules sdGameRulesObjective", f"devmap {a.map}"])
        print(f"retail: loading {a.map}, waiting {a.load_wait:.0f} s", flush=True)
        time.sleep(a.load_wait)
        # limbo is open after the load: 0 picks GDF, Return deploys, console closed (§ 2)
        retail.set_console(False)
        xw.key(ord("0"))
        time.sleep(0.5)
        xw.key(KEYSYMS["\n"])
        time.sleep(4.0)
        noclip = False
        for v in views:
            # noclip for air views only: retail ignores pitch while falling, and in noclip
            # the player's own model shows on the ground (§ 2; screenshot_set.py's VIEWS)
            cmds = []
            if bool(v.get("noclip", False)) != noclip:
                cmds.append("noclip")
                noclip = not noclip
            cmds.append(f"setviewpos {v['x']:.2f} {v['y']:.2f} {v['z']:.2f} {v['yaw']:.2f} {v.get('pitch', 0):.2f} 0")
            retail.run(cmds)
            time.sleep(a.settle)
            frames = []
            for _ in range(max(1, a.frames)):
                frames.append(xw.grab(retail.win))
                time.sleep(0.7)
            w, h, rgb = mean_frames(frames)
            png = a.out / f"{a.map}_{v['name']}.png"
            write_png(png, w, h, rgb)
            report["views"].append({**v, "file": png.name, "size": [w, h], "boxes": box_means(w, h, rgb)})
            print(f"  {v['name']}: {w}x{h} -> {png}", flush=True)
        log = retail.console_log()
        report["console_errors"] = [l for l in log.splitlines() if "ERROR" in l or "WARNING: Unknown command" in l][:20]
    finally:
        if retail:
            retail.close()
        comp.close()
        (a.out / "capture.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
    return 0 if report["views"] else 1


if __name__ == "__main__":
    sys.exit(main())
