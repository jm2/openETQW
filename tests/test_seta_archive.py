#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""seta saves its CVar with the configuration, also one nothing registers.

The graphics presets (retail's specs/*.dat) record the level chosen in the settings
menu with "seta com_lastGraphicsLevel 2" and the like, and the menu reads those back
on the next start. idCVarSystemLocal::SetA_f had Doom 3's development-time line
"cvar->flags |= CVAR_ARCHIVE" commented out ("enable this for ship"), so these CVars
were never written to the configuration and every preset read back as the lowest
after a restart.

This test runs the dedicated server on synthetic data (fs_validateOfficialPaks 0), so
it needs no retail data; the game module is staged into the synthetic base/ as in
test_retired_cvars.py:

1. A run that sets two unregistered CVars with seta and one with set, then quits,
   leaves a configuration that has both seta values and not the set one.
2. The next run, on the same profile, reads the seta values back.

Usage: test_seta_archive.py [--ded-binary PATH] [--game-module PATH] (meson passes
the binary and the game module this build directory uses).
Exit status: 0 pass, 1 fail, 77 skip (no dedicated server binary).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import etqw_harness as h
from test_retired_cvars import reads_back, run_server

SETA = {"com_lastGraphicsLevel": "2", "oe_setaArchiveProbe": "7"}
SET = ("oe_setNotArchived", "5")


def main() -> int:
    ap = argparse.ArgumentParser(description="seta saves its CVar with the configuration")
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

    with h.PrivateTempDir(prefix="openetqw-seta-archive-") as tmp:
        root = Path(tmp)
        (root / "base").mkdir()
        (root / "save").mkdir()
        if args.game_module:
            module = Path(args.game_module)
            shutil.copy2(module, root / "base" / module.name)
        h.write_synthetic_stub_decls(root / "base")

        print("1. seta and set, then quit: what the configuration keeps")
        sets = [arg for name, value in SETA.items() for arg in ("+seta", name, value)]
        code, out = run_server(ded, root, *sets, "+set", *SET)
        expect(code == 0, f"the server exits 0 (exit {code})", out)
        configs = sorted((root / "save").rglob("etqwconfig.cfg"))
        expect(len(configs) == 1, f"one etqwconfig.cfg is written in the profile ({[str(c.relative_to(root)) for c in configs]})", out)
        text = configs[0].read_text(encoding="utf-8", errors="replace").lower() if configs else ""
        for name, value in SETA.items():
            expect(f'seta {name.lower()} "{value}"' in text, f"the configuration has seta {name} \"{value}\"", text)
        expect(SET[0].lower() not in text, f"the configuration does not have {SET[0]}, which was set without seta", text)

        print("2. the next start reads the seta values back")
        code, out = run_server(ded, root, *["+" + name for name in SETA])
        back = [name for name, value in SETA.items() if reads_back(out, name, value)]
        expect(back == list(SETA), f"each seta CVar reads back its value (read back: {back})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: seta saves its CVar with the configuration, and the next start reads it back")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
