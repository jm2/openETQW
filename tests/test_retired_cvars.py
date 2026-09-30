#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The retired com_unlock_* timing CVars: gone from the engine, harmless in old configs.

M5 plan step 12, decision D9 (docs/m5-plan.md): com_unlock_avgFrames,
com_unlock_safetyMargin and com_unlock_timingMethod were registered (retail 1.5
defaults 4, 0.1 and 2) and read nowhere, so they are removed; the frame pacing
of step 10 replaces what they did in retail. Retail configs and users' autoexecs
may still set them. This test runs the dedicated server on synthetic data only
(fs_validateOfficialPaks 0), so it needs no retail data:

1. With no config, asking for each by name answers "Unknown command": the
   engine no longer registers them (this fails while they still exist).
2. A config in the retail form ('seta <name> "<value>"', as retail writes and
   users copy) that also sets com_unlockFPS and com_maxFPS execs without a
   warning or an error naming any of them, and the server exits 0. Each retired
   name then reads back the value the config gave it: the CVar system creates
   an unregistered name as a user CVar, silently, as Doom 3 does. `seta` does
   not mark such a CVar for archiving (CVarSystem.cpp SetA_f), so it does not
   come back into the saved config.

Usage: test_retired_cvars.py [--ded-binary PATH] (meson passes the binary this
build directory produced).
Exit status: 0 pass, 1 fail, 77 skip (no dedicated server binary).
"""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path

import etqw_harness as h

RETIRED = {"com_unlock_avgFrames": "4", "com_unlock_safetyMargin": "0.1", "com_unlock_timingMethod": "2"}
KEPT = {"com_unlockFPS": "1", "com_maxFPS": "60"}
CONFIG = "retired_cvars.cfg"


def run_server(ded: Path, root: Path, *extra: str, timeout: float = 30.0) -> tuple[int | None, str]:
    """Start the dedicated server on root (fs_basepath) with synthetic data, run extra, quit."""
    args = [str(ded), "+set", "dedicated", "1", "+set", "fs_basepath", str(root),
            "+set", "fs_savepath", str(root / "save"), "+set", "fs_game", "base",
            "+set", "fs_validateOfficialPaks", "0", "+set", "net_port", "0", *extra, "+quit"]
    try:
        proc = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", timeout=timeout, cwd=str(root))
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return None, out


def main() -> int:
    ap = argparse.ArgumentParser(description="the retired com_unlock_* CVars on synthetic data")
    ap.add_argument("--ded-binary", help="dedicated server binary (default: $OPENETQW_DED_BINARY, then build/)")
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

    queries = ["+" + name for name in RETIRED]  # a bare name prints the CVar, or "Unknown command"
    with tempfile.TemporaryDirectory(prefix="openetqw-retired-cvars-") as tmp:
        root = Path(tmp)
        (root / "base").mkdir()
        (root / "save").mkdir()

        print("1. no config: the engine does not register them")
        code, out = run_server(ded, root, *queries)
        unregistered = [n for n in RETIRED if f"Unknown command '{n}'" in out]
        expect(unregistered == list(RETIRED),
               f"each retired name is an unknown command (unknown: {unregistered}, exit {code})", out)

        print(f"2. a retail-style config setting them ({CONFIG})")
        lines = [f'seta {name} "{value}"' for name, value in {**RETIRED, **KEPT}.items()]
        (root / "base" / CONFIG).write_text("// retail-style settings\n" + "\n".join(lines) + "\n", encoding="utf-8")
        code, out = run_server(ded, root, "+exec", CONFIG, *queries)
        expect(f"execing {CONFIG}" in out, "the config is executed", out)
        noisy = [ln for ln in out.splitlines()
                 if any(n in ln for n in {**RETIRED, **KEPT}) and ("WARNING" in ln or "ERROR" in ln or "Unknown command" in ln)]
        expect(not noisy, f"no warning, error or unknown command names them ({noisy[:3]})", out)
        read_back = [n for n, v in RETIRED.items() if f'"{n}" is:"{v}"' in out]
        expect(read_back == list(RETIRED), f"each reads back the config's value as a user CVar (read back: {read_back})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the retired com_unlock_* CVars are gone, and retail configs that set them still load cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
