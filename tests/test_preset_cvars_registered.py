#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""bse_detailLevel and r_stuffCacheMegs are registered as retail 1.5 registers them (#423).

The presets set both: bse_detailLevel 0.5 in Low, and r_stuffCacheMegs 8, 16 and 32.
Retail 1.5 registers bse_detailLevel as an archived float, default 1.0, "Detail setting
for effects", and r_stuffCacheMegs as an archived integer, default 6. openETQW didn't
register bse_detailLevel at all, and registered r_stuffCacheMegs without
CVAR_ARCHIVE. A CVar the code doesn't register as archived counts as a cheat, so in a
multiplayer game neither could be changed once set, and neither was saved.

The Vulkan client hosts valley (a listen server: a multiplayer game, no net_allowCheats):
  P1 both read their retail defaults (1.0 and 6) before anything sets them
  P2 "set bse_detailLevel 0.5" and "set r_stuffCacheMegs 16" are accepted, twice
  P3 "writeConfig" saves both with seta

Usage: test_preset_cvars_registered.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_cheat_cvar_lock import LOCKED, run, value  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed  # noqa: E402

CVARS = (("bse_detailLevel", "1.0", "0.5", "0.25"), ("r_stuffCacheMegs", "6", "16", "32"))
CONFIG = "preset_cvars_probe.cfg"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29937)
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
        log = Path(args.log_dir) / "preset-cvars-registered.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    # the presets would set both on a fresh profile; fix the High one out of the way
    gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                       cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0", "com_machineSpec": "2"})
    gfx.name = "VulkanClient"
    try:
        gfx.start()
        if not gfx.wait_for_text("Spawning entities", 180.0):
            raise Failed("the listen server never loaded valley")
        gfx.sync()
        for name, default, first, second in CVARS:
            now = value(gfx, name)
            expect(now is not None and float(now) == float(default), f"P1 {name} reads its retail default {default} ({now})")
        for name, default, first, second in CVARS:
            out = run(gfx, f"set {name} {first}") + run(gfx, f"set {name} {second}")
            now = value(gfx, name)
            expect(not any(LOCKED in line for line in out) and now is not None and float(now) == float(second),
                   f"P2 {name} changes in a multiplayer game ({now};"
                   f" {[line.strip() for line in out if LOCKED in line][:1]})")
        run(gfx, f"writeConfig {CONFIG}")
        saved = ""
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            found = list(Path(gfx.savepath).rglob(CONFIG))
            if found:
                saved = found[0].read_text(encoding="utf-8", errors="replace")
                break
            time.sleep(0.25)
        for name, default, first, second in CVARS:
            line = re.search(rf'^seta {re.escape(name)} "([^"]*)"', saved, re.MULTILINE | re.IGNORECASE)
            expect(line is not None and float(line.group(1)) == float(second),
                   f"P3 writeConfig saves {name} with seta ({line.group(0) if line else 'not saved'})")
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
    print("PASS: bse_detailLevel and r_stuffCacheMegs are registered, archived and changeable in a multiplayer game")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
