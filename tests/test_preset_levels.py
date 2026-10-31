#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The graphics presets' levels (com_last*Level) are saved with the configuration.

The presets (retail's specs/*.dat) record the level chosen in the settings menu with
"seta com_lastGraphicsLevel 2" and the like, and the menu reads those back on the
next start. Nothing registered these CVars, and seta does not archive an unregistered
CVar (idCVarSystemLocal::SetA_f, as in released Doom 3), so every preset read back as
the lowest after a restart. The engine now registers them with CVAR_ARCHIVE.

This test runs the dedicated server on synthetic data (fs_validateOfficialPaks 0), so
it needs no retail data; the game module is staged into the synthetic base/ as in
test_retired_cvars.py:

1. A run that sets the six levels with seta, an unregistered CVar with seta and one
   with set, then writes the archived CVars with writeConfig (as the configuration
   file is written), writes the six levels and neither of the others.
2. The next run executes that file and reads the six levels back.

Usage: test_preset_levels.py [--ded-binary PATH] [--game-module PATH] (meson passes
the binary and the game module this build directory uses).
Exit status: 0 pass, 1 fail, 77 skip (no dedicated server binary).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import etqw_harness as h
from test_retired_cvars import reads_back, run_server

LEVELS = {"com_lastGraphicsLevel": "2", "com_lastGraphicsDetailLevel": "2", "com_lastCPULevel": "1",
          "com_lastCPUDetailLevel": "2", "com_lastFoliageLevel": "1", "com_lastLightingLevel": "2"}
# released Doom 3 behaviour, kept: seta on an unregistered CVar and set do not archive it
NOT_SAVED = (("+seta", "oe_setaUnregisteredProbe", "7"), ("+set", "oe_setNotArchived", "5"))
PROBE = "oe_preset_levels"


def main() -> int:
    ap = argparse.ArgumentParser(description="the graphics presets' levels are saved")
    ap.add_argument("--ded-binary", help="dedicated server binary (default: $OPENETQW_DED_BINARY, then build/)")
    ap.add_argument("--game-module", help="game module to stage into the synthetic base/ (default: none staged)")
    args = ap.parse_args()
    try:
        ded = h.find_ded_binary(args.ded_binary)
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP

    failures: list[str] = []

    def expect(ok: bool, what: str, out: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)
            print("    --- output (last 30 lines) ---")
            for line in out.splitlines()[-30:]:
                print(f"    {line}")

    with h.PrivateTempDir(prefix="openetqw-preset-levels-") as tmp:
        root = Path(tmp)
        (root / "base").mkdir()
        (root / "save").mkdir()
        if args.game_module:
            module = Path(args.game_module)
            shutil.copy2(module, root / "base" / module.name)
        h.write_synthetic_stub_decls(root / "base")

        print("1. seta the six levels and two other CVars, then writeConfig")
        sets = [arg for name, value in LEVELS.items() for arg in ("+seta", name, value)]
        others = [arg for triple in NOT_SAVED for arg in triple]
        code, out = run_server(ded, root, *sets, *others, "+writeConfig", PROBE)
        expect(code == 0, f"the server exits 0 (exit {code})", out)
        # writeConfig writes every CVar with CVAR_ARCHIVE, as the configuration file does
        configs = sorted((root / "save").rglob(PROBE + ".cfg"))
        expect(len(configs) == 1, f"{PROBE}.cfg is written in the profile ({[str(c.relative_to(root)) for c in configs]})", out)
        text = configs[0].read_text(encoding="utf-8", errors="replace").lower() if configs else ""
        for name, value in LEVELS.items():
            expect(f'seta {name.lower()} "{value}"' in text, f"it has seta {name} \"{value}\"", text)
        for _, name, _ in NOT_SAVED:
            expect(name.lower() not in text, f"it does not have {name} (not registered as archived)", text)

        print("2. the next start, executing it, reads the levels back")
        code, out = run_server(ded, root, "+exec", PROBE + ".cfg", *["+" + name for name in LEVELS])
        back = [name for name, value in LEVELS.items() if reads_back(out, name, value)]
        expect(back == list(LEVELS), f"each level reads back its value (read back: {back})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the graphics presets' levels are saved with the configuration and read back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
