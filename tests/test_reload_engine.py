#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""reloadEngine starts the engine again, at the main menu (#420).

Retail's settings menu answers its "restart required" prompt, its restore-defaults path
and its mod chooser with "reloadEngine [resetConfigs] menu" (guis/mainmenu). The engine
had no such command, so the menu faded to black and waited. openETQW now shuts down as
quit does and starts the same executable again with the same command line (owner
decision, 2026-11-02); "menu" leaves out the commands that start or join a game.

On Linux and macOS the engine execs itself, so the process, its pipes and this test's
console stay. On Windows it starts a new process, which this test can't follow: it skips
there (exit 77).

The Vulkan client hosts valley with +set ui_name on its command line:
  R1 "reloadEngine menu" shuts the engine down and starts it again: the process
     prints the start-up banner a second time, and the second start doesn't load
     valley (the command line's +spawnServer is left out)
  R2 the command line's +set ui_name is passed on
  R3 the saved settings survive: com_showFPS, set to 1, reads back after the restart
  R4 "reloadEngine resetConfigs menu" starts again without etqwconfig.cfg: the
     com_showFPS is back at its default and the profile is fresh (the client logs
     its preset choice)
  R5 the process exits 0 on quit

Usage: test_reload_engine.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (Windows, no client binary or no retail data).
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

READY = "------------- ETQW initialized -------------"
NAME = "OE-ReloadProbe"
CHOICE = "machine spec:"


def value(gfx, name: str) -> str | None:
    mark = gfx.mark()
    gfx.send(name)
    gfx.sync()
    want = re.compile(rf'"{re.escape(name)}" is:"([^"]*)"', re.IGNORECASE)
    for line in gfx.snapshot(mark):
        m = want.search(line.text)
        if m:
            return m.group(1)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29933)
    ap.add_argument("--log-dir")
    args = ap.parse_args()
    if os.name == "nt":
        print("[SKIP] on Windows reloadEngine starts a new process, which this test can't follow")
        return h.EXIT_SKIP
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
        log = Path(args.log_dir) / "reload-engine.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                       cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0", "ui_name": NAME})
    gfx.name = "VulkanClient"
    try:
        gfx.start()
        if not gfx.wait_for_text("Spawning entities", 180.0):
            raise Failed("the listen server never loaded valley")
        gfx.sync()
        gfx.send("seta com_showFPS 1")
        gfx.sync()

        print("1. reloadEngine menu")
        mark = gfx.mark()
        gfx.send("reloadEngine menu")
        if not gfx.wait_for_text(READY, 180.0, start=mark):
            raise Failed("the engine never started again")
        gfx.sync()
        lines = [ln.text for ln in gfx.snapshot(mark)]
        expect(any("reloadEngine: starting the engine again" in t for t in lines),
               "R1 the engine shuts down and starts again (start-up banner a second time)")
        expect(not any("Spawning entities" in t for t in lines), "R1 the second start doesn't load valley")
        expect(value(gfx, "ui_name") == NAME, f"R2 the command line's +set ui_name {NAME} is passed on")
        expect(value(gfx, "com_showFPS") == "1", "R3 the saved settings survive (com_showFPS 1)")

        print("2. reloadEngine resetConfigs menu")
        mark = gfx.mark()
        gfx.send("reloadEngine resetConfigs menu")
        if not gfx.wait_for_text(READY, 180.0, start=mark):
            raise Failed("the engine never started again after resetConfigs")
        gfx.sync()
        lines = [ln.text for ln in gfx.snapshot(mark)]
        expect(value(gfx, "com_showFPS") == "0", "R4 without the saved settings: com_showFPS is back at 0")
        expect(any(CHOICE in t for t in lines), "R4 the profile is fresh: the client logs its preset choice")
    except Failed as e:
        failures.append(str(e))
        print(f"FAIL: {e}")
    finally:
        code = gfx.quit()
        gfx.cleanup()
    expect(code == 0, f"R5 the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: reloadEngine starts the engine again at the main menu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
