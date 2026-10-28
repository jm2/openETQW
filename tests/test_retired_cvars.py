#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The retired com_unlock_* timing CVars: gone from the engine, harmless in old configs.

M5 plan step 12, decision D9 (docs/m5-plan.md): com_unlock_avgFrames,
com_unlock_safetyMargin and com_unlock_timingMethod were registered (retail 1.5
defaults 4, 0.1 and 2) and read nowhere, so they are removed (compat ledger row
053). Retail configs and users' autoexecs still set them, with seta or with a bare
"<name> <value>" line (pak000 visuals.cfg and wolle.cfg set com_unlock_timingmethod
that way). This test runs the dedicated server on synthetic data only
(fs_validateOfficialPaks 0), so it needs no retail data; the game module is staged
into the synthetic base/ (--game-module), as tools/ci/dedicated_server_smoke.py does,
with the stub decl game->Init needs on synthetic data
(etqw_harness.write_synthetic_stub_decls; a failed game->Init stops the engine), so
the server reaches its + commands in any build directory:

1. With no config, asking for each by name answers "Unknown command": the
   engine no longer registers them (this fails while they still exist).
2. A config that sets them to values other than retail's defaults, two with seta
   and one with a bare, lower-case line as in retail's visuals.cfg, and also sets
   com_unlockFPS and com_maxFPS, execs without a warning, an error or an unknown
   command naming any of them (the bare line fails this before the retired-name
   list in idCVarSystemLocal::Command), and the server exits 0. Each retired name
   then reads back the value the config gave it, as a user CVar that nothing reads.

Usage: test_retired_cvars.py [--ded-binary PATH] [--game-module PATH] (meson passes
the binary and the game module this build directory uses).
Exit status: 0 pass, 1 fail, 77 skip (no dedicated server binary).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

import etqw_harness as h

# name -> (the line the config sets it with, the value it must read back)
RETIRED = {
    "com_unlock_avgFrames": ('seta com_unlock_avgFrames "7"', "7"),
    "com_unlock_safetyMargin": ('seta com_unlock_safetyMargin "0.25"', "0.25"),
    "com_unlock_timingMethod": ('com_unlock_timingmethod "1"', "1"),  # bare, as pak000 visuals.cfg:28
}
KEPT = ['seta com_unlockFPS "1"', 'seta com_maxFPS "60"']
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


def reads_back(out: str, name: str, value: str) -> bool:
    """The server printed the CVar with this value (CVar names are not case-sensitive)."""
    want = f'" is:"{value}"'.lower()
    return any(f'"{name.lower()}' + want in line.lower() for line in out.splitlines())


def main() -> int:
    ap = argparse.ArgumentParser(description="the retired com_unlock_* CVars on synthetic data")
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

    queries = ["+" + name for name in RETIRED]  # a bare name prints the CVar, or "Unknown command"
    with h.PrivateTempDir(prefix="openetqw-retired-cvars-") as tmp:
        root = Path(tmp)
        (root / "base").mkdir()
        (root / "save").mkdir()
        if args.game_module:
            module = Path(args.game_module)
            shutil.copy2(module, root / "base" / module.name)
        h.write_synthetic_stub_decls(root / "base")

        print("1. no config: the engine does not register them")
        code, out = run_server(ded, root, *queries)
        unregistered = [n for n in RETIRED if f"Unknown command '{n}'" in out]
        expect(unregistered == list(RETIRED),
               f"each retired name is an unknown command (unknown: {unregistered}, exit {code})", out)

        print(f"2. a retail-style config setting them ({CONFIG})")
        lines = [line for line, _ in RETIRED.values()] + KEPT
        (root / "base" / CONFIG).write_text("// retail-style settings\n" + "\n".join(lines) + "\n", encoding="utf-8")
        code, out = run_server(ded, root, "+exec", CONFIG, *queries)
        expect(f"execing {CONFIG}" in out, "the config is executed", out)
        names = [n.lower() for n in RETIRED] + ["com_unlockfps", "com_maxfps"]
        noisy = [ln for ln in out.splitlines()
                 if any(n in ln.lower() for n in names) and ("WARNING" in ln or "ERROR" in ln or "Unknown command" in ln)]
        expect(not noisy, f"no warning, error or unknown command names them ({noisy[:3]})", out)
        back = [n for n, (_, value) in RETIRED.items() if reads_back(out, n, value)]
        expect(back == list(RETIRED), f"each reads back the config's value (read back: {back})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the retired com_unlock_* CVars are gone, and retail configs that set them still load cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
