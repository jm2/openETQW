#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A decl index of -1 is "none" and is not warned about (#377).

The scripts look materials up by name (`sys.getDeclIndex`), which returns -1 for an
empty or unknown name, and pass the result on; on valley, `sys.setCMIconMaterial`
gets -1 for command map icons whose material key is unset. idDeclManager::DeclByIndex
warned "index -1 out of range for 'material'" for each, a burst right after the player
entered the game, which retail 1.5 does not log. It now returns NULL for -1 quietly;
any other out-of-range index still warns.

The Vulkan client hosts valley (objective rules), a GDF soldier spawns and the game runs
for SECONDS; then:
  D1 the client logged no "DeclByIndex: index -1" warning (4 on valley before the fix)

Usage: test_decl_index_none.py [--client-binary PATH] [--port N] [--log-dir DIR]
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
from test_reconnect_restart import Failed, spawn  # noqa: E402

SECONDS = 10.0
WARNING = "DeclByIndex: index -1 out of range"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29915)
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
        log = Path(args.log_dir) / "decl-index-none.log"

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
        spawn(gfx, gfx, "gdf")
        time.sleep(SECONDS)
        gfx.sync()
        warned = [ln.text for ln in gfx.snapshot(0) if WARNING in ln.text]
        expect(not warned, f"D1 no '{WARNING}' warning ({len(warned)} logged)")
    except Failed as e:
        failures.append(str(e))
        print(f"FAIL: {e}")
    finally:
        code = gfx.quit()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: a decl index of -1 is looked up quietly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
