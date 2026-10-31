#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The BSE particle system draws effects (#368).

The client's effect path ran end to end but its simulation and geometry were stubs
(src/renderer/RenderBSE.h): every effect had 0 particles and no surfaces, so no
muzzle flash, tracer, impact, smoke or explosion was ever drawn. The DarklightNG BSE
in src/bse is now built into the client, and an effect decl's body is parsed into its
template (src/decllib/declEffect.cpp).

The Vulkan client hosts valley (objective rules), a GDF soldier spawns and the view
stays in the world for SECONDS; valley's ambient effects (smoke, flags, fires) run on
their own. `bseStats` then reports, since start:
  B1 effects serviced > 0       (effect defs reach the simulation)
  B2 particles rendered > 0     (the simulation spawns particles; 0 with the stubs)
  B3 effect models drawn > 0    (BSE_BuildRenderModel gave the renderer geometry)
Then `reloadDecls all` reparses every decl while the effects run, which frees and
rebuilds their templates; the running effects must be re-initialised, not left on
the freed ones:
  B4 after RELOAD_SECONDS more, particles rendered and effect models drawn grew
On a build without the BSE the command does not exist ("Unknown command 'bseStats'").

Usage: test_bse_effects.py [--client-binary PATH] [--port N] [--log-dir DIR]
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
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed, spawn  # noqa: E402

SECONDS = 10.0
RELOAD_SECONDS = 8.0
STATS_RE = re.compile(r"^BSE: (\d+) effects serviced, (\d+) segments, (\d+) particles evaluated, (\d+) rendered, "
                      r"(\d+) capped; (\d+) effect models drawn with (\d+) surfaces")


def bse_stats(gfx) -> list[str]:
    mark = gfx.mark()
    gfx.send("bseStats")
    gfx.sync()
    return [ln.text for ln in gfx.snapshot(mark)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29914)
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
        log = Path(args.log_dir) / "bse-effects.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                       cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0", "net_allowCheats": "1"})
    gfx.name = "VulkanClient"
    gfx.slot = 0
    try:
        gfx.start()
        if not gfx.wait_for_text("Spawning entities", 180.0):
            raise Failed("the listen server never loaded valley")
        gfx.sync()
        spawn(gfx, gfx, "gdf")
        time.sleep(SECONDS)
        lines = bse_stats(gfx)
        stats = next((m for m in (STATS_RE.match(t) for t in lines) if m), None)
        unknown = any("Unknown command 'bseStats'" in t for t in lines)
        expect(stats is not None, "bseStats reports the BSE counts"
               + (" (Unknown command 'bseStats': no BSE in this build)" if unknown else ""))
        if stats:
            serviced, _segments, _evaluated, rendered, _capped, models, surfaces = (int(g) for g in stats.groups())
            print(f"  {stats.group(0)}")
            expect(serviced > 0, f"B1 effects were serviced ({serviced})")
            expect(rendered > 0, f"B2 particles were rendered ({rendered})")
            expect(models > 0 and surfaces > 0, f"B3 effect models were drawn ({models}, {surfaces} surfaces)")

            gfx.send("reloadDecls all")
            gfx.sync()
            time.sleep(RELOAD_SECONDS)
            after = next((m for m in (STATS_RE.match(t) for t in bse_stats(gfx)) if m), None)
            if after:
                print(f"  {after.group(0)}")
            rendered2, models2 = (int(after.group(4)), int(after.group(6))) if after else (rendered, models)
            expect(rendered2 > rendered and models2 > models,
                   f"B4 effects kept rendering after reloadDecls all (particles {rendered} -> {rendered2}, "
                   f"models {models} -> {models2})")
    except Failed as e:
        failures.append(str(e))
        print(f"FAIL: {e}")
    finally:
        code = gfx.quit()
    expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the BSE simulation renders valley's effects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
