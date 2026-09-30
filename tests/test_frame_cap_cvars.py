#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""com_unlock_maxFPS is an alias of com_maxFPS: the last write wins.

M5 plan step 11, decision D8 (docs/m5-plan.md): retail's com_unlock_maxFPS (the cap
on unlocked frames, default 60) and OpenETQW's com_maxFPS (the cap on every frame)
are one setting. A change to either sets the other through CVar change callbacks
registered before any config runs, so the last write wins: in the order the
configs set them, then the command line's +set, then in play. Before step 11 the
frame loop copied com_maxFPS over com_unlock_maxFPS whenever both had changed, so
a config or command line that set com_unlock_maxFPS last lost to com_maxFPS. This
test runs the dedicated server on synthetic data only (fs_validateOfficialPaks 0),
so it needs no retail data:

1. +set com_maxFPS 100 +set com_unlock_maxFPS 75: both read 75.
2. +set com_unlock_maxFPS 75 +set com_maxFPS 100: both read 100.
3. In play, from an exec'd config: set com_maxFPS 100, set com_unlock_maxFPS 90,
   then com_maxFPS reads 90; set com_maxFPS 45, then com_unlock_maxFPS reads 45.

The game module is staged into the synthetic base/ (--game-module), as
tools/ci/dedicated_server_smoke.py does, so the server reaches its + commands in any
build directory.

Usage: test_frame_cap_cvars.py [--ded-binary PATH] [--game-module PATH] (meson passes
the binary and the game module this build directory uses).
Exit status: 0 pass, 1 fail, 77 skip (no dedicated server binary).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import etqw_harness as h

CONFIG = "frame_cap.cfg"
SHOWN = re.compile(r'"(com_maxFPS|com_unlock_maxFPS)" is:"([^"]*)"')


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


def shown(out: str) -> list[tuple[str, str]]:
    """The (name, value) pairs the server printed for bare queries, in order."""
    return SHOWN.findall(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="com_unlock_maxFPS as an alias of com_maxFPS, on synthetic data")
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

    queries = ["+com_maxFPS", "+com_unlock_maxFPS"]  # a bare name prints the CVar
    with tempfile.TemporaryDirectory(prefix="openetqw-frame-cap-") as tmp:
        root = Path(tmp)
        (root / "base").mkdir()
        (root / "save").mkdir()
        if args.game_module:
            module = Path(args.game_module)
            shutil.copy2(module, root / "base" / module.name)

        for number, (first, second) in enumerate(((("com_maxFPS", "100"), ("com_unlock_maxFPS", "75")),
                                                   (("com_unlock_maxFPS", "75"), ("com_maxFPS", "100"))), start=1):
            print(f"{number}. +set {first[0]} {first[1]} +set {second[0]} {second[1]}")
            code, out = run_server(ded, root, "+set", *first, "+set", *second, *queries)
            values = shown(out)
            want = [("com_maxFPS", second[1]), ("com_unlock_maxFPS", second[1])]
            expect(values == want, f"both read {second[1]}, the last write (read: {values}, exit {code})", out)

        print("3. in play, from an exec'd config")
        (root / "base" / CONFIG).write_text("set com_maxFPS 100\nset com_unlock_maxFPS 90\ncom_maxFPS\n"
                                            "set com_maxFPS 45\ncom_unlock_maxFPS\n", encoding="utf-8")
        code, out = run_server(ded, root, "+exec", CONFIG)
        values = shown(out)
        expect(values == [("com_maxFPS", "90"), ("com_unlock_maxFPS", "45")],
               f"com_maxFPS follows com_unlock_maxFPS 90, and com_unlock_maxFPS follows com_maxFPS 45 (read: {values})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: com_unlock_maxFPS and com_maxFPS are one setting; the last write wins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
