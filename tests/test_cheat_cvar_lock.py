#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A cheat CVar stays locked in a multiplayer game, whichever set command is used.

The engine treats a CVar as a cheat unless the code registered it as archived,
NOCHEAT, INIT, ROM, user info, server info or network-synced (idInternalCVar::
UpdateCheat), and refuses to change a cheat CVar in a multiplayer game without
net_allowCheats. seta, sets and setu add CVAR_ARCHIVE (and server or user info) to the
CVar they set; seta does so since it saves like retail 1.5. Those flags used to count
as the code's, so "seta X 1" followed by "set X 1" twice changed a cheat CVar in a
multiplayer game: the refused seta archived it, the next set's UpdateCheat cleared the
cheat flag, and the one after went through. The engine now decides by the code's flags
alone.

The Vulkan client hosts valley (a listen server: a multiplayer game, no net_allowCheats).
For each of seta, sets and setu, on its own CVar the game registers without any of
those flags:
  C1 the command is refused ("cannot be changed in multiplayer")
  C2 two sets after it are refused too, and the CVar keeps its default
  C3 a CVar the code registered as archived (ui_name) still changes

Usage: test_cheat_cvar_lock.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed  # noqa: E402

# game CVars registered CVAR_GAME | CVAR_BOOL, default 0: cheats (game repo src/game/)
CHEATS = (("seta", "g_debugBulletFiring"), ("sets", "g_debugForceFields"), ("setu", "g_debugLocationalDamage"))
LOCKED = "cannot be changed in multiplayer"


def run(gfx, cmd: str) -> list[str]:
    mark = gfx.mark()
    gfx.send(cmd)
    gfx.sync()
    return [ln.text for ln in gfx.snapshot(mark)]


def value(gfx, name: str) -> str | None:
    want = re.compile(rf'"{re.escape(name)}" is:"([^"]*)"', re.IGNORECASE)
    for line in run(gfx, name):
        m = want.search(line)
        if m:
            return m.group(1)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29929)
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
        log = Path(args.log_dir) / "cheat-cvar-lock.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                       cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0"})
    gfx.name = "VulkanClient"
    try:
        gfx.start()
        if not gfx.wait_for_text("Spawning entities", 180.0):
            raise Failed("the listen server never loaded valley")
        gfx.sync()
        for command, name in CHEATS:
            out = run(gfx, f"{command} {name} 1")
            expect(any(LOCKED in line for line in out), f"C1 {command} {name} 1 is refused ({out[:2]})")
            later = run(gfx, f"set {name} 1") + run(gfx, f"set {name} 1")
            now = value(gfx, name)
            expect(sum(LOCKED in line for line in later) == 2 and now == "0",
                   f"C2 two sets after {command} are refused too, and {name} stays 0 ({now})")
        run(gfx, 'set ui_name "OE-CheatLockProbe"')
        expect(value(gfx, "ui_name") == "OE-CheatLockProbe", "C3 an archived CVar (ui_name) still changes")
    except Failed as e:
        failures.append(str(e))
        print(f"FAIL: {e}")
    finally:
        code = gfx.quit()
        gfx.cleanup()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: cheat CVars stay locked in a multiplayer game, whichever set command is used")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
