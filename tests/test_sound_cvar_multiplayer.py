#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""s_maxSoundsPerShader can be changed in a multiplayer game (#423).

The settings menu's overall preset (setMachineSpec) sets s_maxSoundsPerShader: 3 below
High, 0 at High. The SDK registers it CVAR_SOUND | CVAR_ARCHIVE (snd_system.cpp), so
it is not a cheat, and a multiplayer game must accept a change to it as it accepts one
to s_volume_dB. But snd_system.cpp isn't built here, so the preset's set made it an
unregistered CVar, which counts as a cheat: on a listen server "set
s_maxSoundsPerShader 2" printed "s_maxSoundsPerShader cannot be changed in
multiplayer." src/sound/SoundShader.cpp registers it now.

The Vulkan client hosts valley (a listen server: a multiplayer game, no net_allowCheats):
  S1 "set s_maxSoundsPerShader 2" is not refused, and the CVar reads 2
  S2 "set s_maxSoundsPerShader 0" puts it back, and it reads 0
  S3 the control: s_volume_dB changes too

Usage: test_sound_cvar_multiplayer.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_cheat_cvar_lock import LOCKED, run, value  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29935)
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
        log = Path(args.log_dir) / "sound-cvar-multiplayer.log"

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
        for want in ("2", "0"):
            out = run(gfx, f"set s_maxSoundsPerShader {want}")
            now = value(gfx, "s_maxSoundsPerShader")
            check = "S1" if want == "2" else "S2"
            expect(not any(LOCKED in line for line in out) and now == want,
                   f"{check} set s_maxSoundsPerShader {want} is accepted in a multiplayer game"
                   f" (reads {now}; {[line.strip() for line in out if LOCKED in line][:1]})")
        before = value(gfx, "s_volume_dB")
        want = "-3" if before != "-3" else "-4"
        out = run(gfx, f"set s_volume_dB {want}")
        now = value(gfx, "s_volume_dB")
        expect(not any(LOCKED in line for line in out) and now is not None and float(now) == float(want),
               f"S3 the control: s_volume_dB changes too ({before} to {now})")
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
    print("PASS: s_maxSoundsPerShader can be changed in a multiplayer game")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
