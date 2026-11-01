#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed text reaches the console and chat under SDL's x11 video driver (#388, #392).

#388 moved the console and the GUI edit boxes to SDL text input
(SDL_EVENT_TEXT_INPUT). On an Xwayland display, keys sent through XTest made no text
events (#392), so this test runs the client on its own Xvfb display, an X server that
nothing else uses, and types into it with xdotool (XTest). It never touches a desktop
session. Xvfb has no GPU, so the client renders with lavapipe, Mesa's software Vulkan
driver.

The client is a listen server on valley (objective rules) with the retail default
bindings, com_allowConsole 1 (without it the retail console lock leaves the console
key closed unless Ctrl and Alt are down) and in_logTextInput 1. One GDF soldier is
spawned. Then:
1. text input started under the x11 driver
2. the console key opens the console; "echo <token> Shift_Test!@#" and Enter run it:
   the log shows the command line as typed, shifted characters included, and its output
3. T (clientMessageMode) opens chat; "<token> Hello!" and Enter send it: the log shows
   the chat line

It skips (77) without retail data, the client binary, Linux, Xvfb, xdotool or
lavapipe.

Usage: test_text_input_x11.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed, spawn  # noqa: E402

WINDOW_TITLE = "Enemy Territory: QUAKE Wars"
TYPE_DELAY_MS = "120"  # between xdotool's key events; lavapipe frames are slow
ICD_DIRS = ("/usr/share/vulkan/icd.d", "/usr/local/share/vulkan/icd.d", "/etc/vulkan/icd.d")
CONSOLE_TOKEN = "typed_ok"
CONSOLE_TEXT = f"echo {CONSOLE_TOKEN} Shift_Test!@#"
CHAT_TEXT = "chat_ok Hello!"


def lavapipe_icd() -> str | None:
    for directory in ICD_DIRS:
        for path in sorted(Path(directory).glob("lvp_icd*.json")):
            if os.uname().machine in path.name or path.name == "lvp_icd.json":
                return str(path)
    return None


class Xvfb:
    """A private X server; the display number is the first free one Xvfb finds."""

    def __init__(self, binary: str):
        read_fd, write_fd = os.pipe()
        self.proc = subprocess.Popen([binary, "-displayfd", str(write_fd), "-screen", "0", "1280x720x24",
                                      "-nolisten", "tcp"], pass_fds=(write_fd,),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.close(write_fd)
        number = b""
        deadline = time.monotonic() + 15.0
        with os.fdopen(read_fd, "rb") as pipe:
            while not number.endswith(b"\n") and time.monotonic() < deadline:
                chunk = pipe.read(1)
                if not chunk:
                    break
                number += chunk
        if not number.strip().isdigit():
            self.close()
            raise h.SkipTest("Xvfb did not start")
        self.display = f":{number.strip().decode()}"

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class XdoKeys:
    """Key events through XTest (xdotool) on the private display, always into the client."""

    def __init__(self, xdotool: str, display: str):
        self.xdotool = xdotool
        self.env = dict(os.environ, DISPLAY=display)
        self.env.pop("WAYLAND_DISPLAY", None)
        self.window: str | None = None

    def run(self, *args: str) -> str:
        return subprocess.run([self.xdotool, *args], env=self.env, capture_output=True, text=True,
                              timeout=60).stdout.strip()

    def focus(self) -> bool:
        found = self.run("search", "--name", f"^{WINDOW_TITLE}$").split()
        if not found:
            return False
        self.window = found[0]
        self.run("windowfocus", "--sync", self.window)
        time.sleep(0.5)
        return self.focused()

    def focused(self) -> bool:
        return self.window is not None and self.run("getwindowfocus") == self.window

    def _check(self) -> None:
        if not self.focused():
            raise Failed("the client's window lost the keyboard focus on the private display")

    def key(self, keysym: str) -> None:
        self._check()
        self.run("key", keysym)
        time.sleep(0.8)

    def type(self, text: str) -> None:
        self._check()
        self.run("type", "--delay", TYPE_DELAY_MS, text)
        time.sleep(0.8)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29906)
    ap.add_argument("--log-dir")
    args = ap.parse_args()
    try:
        data = h.resolve_data_path()
        client = find_client(args.client_binary)
        if not sys.platform.startswith("linux"):
            raise h.SkipTest("Xvfb and SDL's x11 driver are checked on Linux only")
        xvfb_bin = shutil.which("Xvfb")
        xdotool = shutil.which("xdotool")
        if not xvfb_bin or not xdotool:
            raise h.SkipTest("Xvfb or xdotool not found")
        icd = lavapipe_icd()
        if not icd:
            raise h.SkipTest("lavapipe (Mesa's software Vulkan driver, lvp_icd*.json) not found")
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    h.install_signal_handlers()
    log = None
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        log = Path(args.log_dir) / "text-input-x11.log"

    try:
        xvfb = Xvfb(xvfb_bin)
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    print(f"  private X display {xvfb.display}; Vulkan ICD {icd}")
    # the client inherits these: x11 on the private display, software Vulkan
    os.environ["DISPLAY"] = xvfb.display
    os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ["SDL_VIDEODRIVER"] = "x11"
    os.environ["VK_DRIVER_FILES"] = icd
    os.environ["VK_ICD_FILENAMES"] = icd  # the older loader's name for it
    keys = XdoKeys(xdotool, xvfb.display)

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                       cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0",
                              "com_allowConsole": "1", "in_logTextInput": "1"})
    gfx.name = "VulkanClient"
    gfx.slot = 0
    try:
        gfx.start()
        if not gfx.wait_for_text("Spawning entities", 300.0):
            raise Failed("the listen server never loaded valley")
        gfx.send("exec localization/english/defaultbinds.cfg")
        gfx.sync()
        spawn(gfx, gfx, "gdf")
        time.sleep(1.0)
        if not keys.focus():
            raise Failed("the client's window did not get the keyboard focus on the private display")

        print("1. text input runs under the x11 driver")
        started = gfx.wait_for(lambda s: "TEXT_INPUT: start ok" in s and "(driver x11)" in s, 1.0)
        expect(started is not None, "TEXT_INPUT: start ok ... (driver x11)")

        print("2. the console takes typed text")
        mark = gfx.mark()
        keys.key("grave")
        keys.type(CONSOLE_TEXT)
        keys.key("Return")
        keys.key("grave")
        gfx.sync()
        lines = [ln.text.rstrip() for ln in gfx.snapshot(mark)]
        texts = sum(1 for s in lines if s.startswith("TEXT_INPUT: event"))
        print(f"     {texts} text input events")
        expect(f"]{CONSOLE_TEXT}" in lines, f"the console ran ']{CONSOLE_TEXT}'")
        expect(any(s.startswith(f"{CONSOLE_TOKEN} Shift_Test") for s in lines),
               f"echo printed '{CONSOLE_TOKEN} Shift_Test ...'")

        print("3. chat takes typed text")
        mark = gfx.mark()
        keys.key("t")
        keys.type(CHAT_TEXT)
        keys.key("Return")
        chat = gfx.wait_for(lambda s: re.search(rf": {re.escape(CHAT_TEXT)}$", s.rstrip()), 10.0, mark)
        expect(chat is not None, f"the chat line '<name>: {CHAT_TEXT}'")
        if chat:
            print(f"     {chat[1].text.rstrip()}")
    except Failed as e:
        failures.append(str(e))
        print(f"FAIL: {e}")
    finally:
        code = gfx.quit()
        xvfb.close()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: typed text reaches the console and chat under SDL's x11 driver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
