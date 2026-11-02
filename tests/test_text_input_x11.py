#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Typed text reaches the console and chat under SDL's x11 video driver (#388, #392),
and text input is on only while they take it (#394).

#388 moved the console and the GUI edit boxes to SDL text input
(SDL_EVENT_TEXT_INPUT). On an Xwayland display, keys sent through XTest made no text
events (#392), so this test runs the client on its own Xvfb display, an X server that
nothing else uses, and types into it with xdotool (XTest). It never touches a desktop
session. Xvfb has no GPU, so the client renders with lavapipe, Mesa's software Vulkan
driver.

The client is a listen server on valley (objective rules) with the retail default
bindings, com_allowConsole 1 (without it the retail console lock leaves the console
key closed unless Ctrl and Alt are down) and in_logTextInput 1. One GDF soldier is
spawned. The client inherits no input-method settings (XMODIFIERS, the IM modules,
the session bus; SDL_IM_MODULE none), so a desktop's ibus or fcitx cannot take the keys.
Then:
1. in play, text input is off: a key-down logs "text input active 0"
2. the console key opens the console and starts text input under the x11 driver;
   "echo <token> Shift_Test!@#" and Enter run it: the log shows the command line as
   typed, shifted characters included, and its output. Closing the console stops text
   input, and a key-down in play logs it off again
3. T (clientMessageMode) opens chat and starts text input for its edit box;
   "<token> Hello!" and Enter send it: the log shows the chat line. Sending closes chat
   and stops text input

It skips (77) without retail data, the client binary, Linux, Xvfb, xdotool or
lavapipe.

Usage: test_text_input_x11.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip.
"""

from __future__ import annotations

import argparse
import os
import re
import select
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
LINE_TIMEOUT = 15.0  # seconds for a typed line to show in the log; lavapipe frames are slow
# input-method settings a desktop session exports; the client must not inherit them
IM_ENVIRONMENT = ("XMODIFIERS", "GTK_IM_MODULE", "QT_IM_MODULE", "IBUS_ADDRESS", "DBUS_SESSION_BUS_ADDRESS")


def lavapipe_icd() -> str | None:
    for directory in ICD_DIRS:
        for path in sorted(Path(directory).glob("lvp_icd*.json")):
            if os.uname().machine in path.name or path.name == "lvp_icd.json":
                return str(path)
    return None


class Xvfb:
    """A private X server; the display number is the first free one Xvfb finds."""

    START_TIMEOUT = 15.0

    def __init__(self, binary: str):
        read_fd, write_fd = os.pipe()
        try:
            self.proc = subprocess.Popen([binary, "-displayfd", str(write_fd), "-screen", "0", "1280x720x24",
                                          "-nolisten", "tcp"], pass_fds=(write_fd,),
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            os.close(write_fd)
        # killed with the engine processes at exit or on SIGTERM/SIGHUP (meson's timeout)
        with h._LIVE_PROCS_LOCK:
            h._LIVE_PROCS.append(self.proc)
        try:
            number = self._read_display(read_fd)
        except BaseException:
            self.close()
            raise
        if not number.isdigit():
            self.close()
            raise h.SkipTest(f"Xvfb did not report a display within {self.START_TIMEOUT:.0f} s")
        self.display = f":{number}"

    def _read_display(self, read_fd: int) -> str:
        """The display number Xvfb writes to -displayfd, or '' at the deadline or EOF."""
        number = b""
        deadline = time.monotonic() + self.START_TIMEOUT
        with os.fdopen(read_fd, "rb", buffering=0) as pipe:
            while not number.endswith(b"\n"):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                ready, _, _ = select.select([pipe], [], [], remaining)
                if not ready:
                    break
                chunk = pipe.read(1)
                if not chunk:
                    break
                number += chunk
        return number.strip().decode(errors="replace")

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        with h._LIVE_PROCS_LOCK:
            if self.proc in h._LIVE_PROCS:
                h._LIVE_PROCS.remove(self.proc)


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

    def key(self, keysym: str, settle: float = 0.8) -> None:
        self._check()
        self.run("key", keysym)
        time.sleep(settle)

    def type(self, text: str) -> None:
        self._check()
        self.run("type", "--delay", TYPE_DELAY_MS, text)
        time.sleep(0.8)


def expect_text_input_off(gfx: VulkanClient, keys: XdoKeys, expect) -> None:
    """A Shift key-down in play (bound to nothing by default) logs text input off."""
    mark = gfx.mark()
    keys.key("shift", settle=0.3)
    down = gfx.wait_for(lambda s: s.startswith("TEXT_INPUT: key down"), LINE_TIMEOUT, mark)
    expect(down is not None and "text input active 0" in down[1].text,
           "in play a key-down logs 'text input active 0'"
           + (f" (got: {down[1].text.rstrip()})" if down is not None else " (no key-down logged)"))


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

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    gfx = None
    code = None
    try:
        print(f"  private X display {xvfb.display}; Vulkan ICD {icd}")
        # the client inherits these: x11 on the private display, software Vulkan
        os.environ["DISPLAY"] = xvfb.display
        os.environ.pop("WAYLAND_DISPLAY", None)
        os.environ["SDL_VIDEODRIVER"] = "x11"
        os.environ["VK_DRIVER_FILES"] = icd
        os.environ["VK_ICD_FILENAMES"] = icd  # the older loader's name for it
        # no input method: the keys must not go through a desktop's ibus or fcitx
        for name in IM_ENVIRONMENT:
            os.environ.pop(name, None)
        os.environ["SDL_IM_MODULE"] = "none"
        keys = XdoKeys(xdotool, xvfb.display)

        gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                           cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0",
                                  "com_allowConsole": "1", "in_logTextInput": "1"})
        gfx.name = "VulkanClient"
        gfx.slot = 0
        gfx.start()
        if not gfx.wait_for_text("Spawning entities", 300.0):
            raise Failed("the listen server never loaded valley")
        gfx.send("exec localization/english/defaultbinds.cfg")
        gfx.sync()
        spawn(gfx, gfx, "gdf")
        time.sleep(1.0)
        if not keys.focus():
            raise Failed("the client's window did not get the keyboard focus on the private display")

        print("1. text input is off in play")
        expect_text_input_off(gfx, keys, expect)

        print("2. the console takes typed text")
        mark = gfx.mark()
        keys.key("grave")
        started = gfx.wait_for(lambda s: "TEXT_INPUT: start ok" in s and "(driver x11)" in s
                               and "console 1" in s, LINE_TIMEOUT, mark)
        expect(started is not None, "the console starts text input: TEXT_INPUT: start ok ... (driver x11), console 1")
        keys.type(CONSOLE_TEXT)
        keys.key("Return")
        ran = gfx.wait_for(lambda s: s.rstrip() == f"]{CONSOLE_TEXT}", LINE_TIMEOUT, mark)
        expect(ran is not None, f"the console ran ']{CONSOLE_TEXT}'")
        echoed = gfx.wait_for(lambda s: s.startswith(f"{CONSOLE_TOKEN} Shift_Test"), LINE_TIMEOUT, mark)
        expect(echoed is not None, f"echo printed '{CONSOLE_TOKEN} Shift_Test ...'")
        texts = sum(1 for ln in gfx.snapshot(mark) if ln.text.startswith("TEXT_INPUT: event"))
        print(f"     {texts} text input events")
        mark = gfx.mark()
        keys.key("grave")
        stopped = gfx.wait_for(lambda s: "TEXT_INPUT: stop ok" in s and "console 0" in s, LINE_TIMEOUT, mark)
        expect(stopped is not None, "closing the console stops text input: TEXT_INPUT: stop ok ... console 0")
        expect_text_input_off(gfx, keys, expect)

        print("3. chat takes typed text")
        mark = gfx.mark()
        keys.key("t")
        started = gfx.wait_for(lambda s: "TEXT_INPUT: start ok" in s and "edit box 1" in s, LINE_TIMEOUT, mark)
        expect(started is not None, "chat starts text input: TEXT_INPUT: start ok ... edit box 1")
        keys.type(CHAT_TEXT)
        sent = gfx.mark()
        keys.key("Return")
        chat = gfx.wait_for(lambda s: re.search(rf": {re.escape(CHAT_TEXT)}$", s.rstrip()), LINE_TIMEOUT, mark)
        expect(chat is not None, f"the chat line '<name>: {CHAT_TEXT}'")
        if chat:
            print(f"     {chat[1].text.rstrip()}")
        stopped = gfx.wait_for(lambda s: "TEXT_INPUT: stop ok" in s and "edit box 0" in s, LINE_TIMEOUT, sent)
        expect(stopped is not None, "sending closes chat and stops text input: TEXT_INPUT: stop ok ... edit box 0")
        expect_text_input_off(gfx, keys, expect)
    except Failed as e:
        failures.append(str(e))
        print(f"FAIL: {e}")
    finally:
        if gfx is not None:
            code = gfx.quit()
            gfx.cleanup()
        xvfb.close()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: typed text reaches the console and chat under SDL's x11 driver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
