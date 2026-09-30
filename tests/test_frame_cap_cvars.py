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
4. The saved config (etqwdedconfig.cfg) sets com_maxFPS 144 and the command line
   com_unlock_maxFPS 75: both read 75.
5. The saved config sets com_maxFPS 144 and autoexec.cfg com_unlock_maxFPS 90: both
   read 90.
Frames run before every query (+wait 5 / wait 5), so the old per-frame rule gets its
chance: it read 100 in both orders of 1 and 2, and 144 in 4 and 5.

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

    # "+wait 5" lets frames run before the queries: before step 11 the frame loop synced the
    # caps once per frame (com_maxFPS won whenever both had changed)
    queries = ["+wait", "5", "+com_maxFPS", "+com_unlock_maxFPS"]  # a bare name prints the CVar
    both = lambda v: [("com_maxFPS", v), ("com_unlock_maxFPS", v)]  # noqa: E731
    with tempfile.TemporaryDirectory(prefix="openetqw-frame-cap-") as tmp:

        def fresh_root(name: str, configs: dict[str, str]) -> Path:
            """A synthetic data root of its own (the server writes its config back)."""
            root = Path(tmp) / name
            (root / "base").mkdir(parents=True)
            (root / "save").mkdir()
            if args.game_module:
                module = Path(args.game_module)
                shutil.copy2(module, root / "base" / module.name)
            for file, text in configs.items():
                (root / "base" / file).write_text(text, encoding="utf-8")
            return root

        for number, (first, second) in enumerate(((("com_maxFPS", "100"), ("com_unlock_maxFPS", "75")),
                                                   (("com_unlock_maxFPS", "75"), ("com_maxFPS", "100"))), start=1):
            print(f"{number}. +set {first[0]} {first[1]} +set {second[0]} {second[1]}")
            code, out = run_server(ded, fresh_root(f"case{number}", {}), "+set", *first, "+set", *second, *queries)
            values = shown(out)
            expect(values == both(second[1]), f"both read {second[1]}, the last write (read: {values}, exit {code})", out)

        print("3. in play, from an exec'd config")
        root = fresh_root("case3", {CONFIG: "set com_maxFPS 100\nset com_unlock_maxFPS 90\nwait 5\ncom_maxFPS\n"
                                            "set com_maxFPS 45\nwait 5\ncom_unlock_maxFPS\n"})
        code, out = run_server(ded, root, "+exec", CONFIG)
        values = shown(out)
        expect(values == [("com_maxFPS", "90"), ("com_unlock_maxFPS", "45")],
               f"com_maxFPS follows com_unlock_maxFPS 90, and com_unlock_maxFPS follows com_maxFPS 45 (read: {values})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

        print("4. the saved config sets com_maxFPS 144, the command line com_unlock_maxFPS 75")
        root = fresh_root("case4", {"etqwdedconfig.cfg": 'seta com_maxFPS "144"\n'})
        code, out = run_server(ded, root, "+set", "com_unlock_maxFPS", "75", *queries)
        values = shown(out)
        expect(values == both("75"), f"both read 75: the command line comes after the configs (read: {values})", out)

        print("5. the saved config sets com_maxFPS 144, autoexec.cfg com_unlock_maxFPS 90")
        root = fresh_root("case5", {"etqwdedconfig.cfg": 'seta com_maxFPS "144"\n',
                                    "autoexec.cfg": 'seta com_unlock_maxFPS "90"\n'})
        code, out = run_server(ded, root, *queries)
        values = shown(out)
        expect(values == both("90"), f"both read 90: autoexec.cfg runs after the saved config (read: {values})", out)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: com_unlock_maxFPS and com_maxFPS are one setting; the last write wins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
