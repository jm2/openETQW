#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The engine refuses to start without the retail ET:QW 1.5 data.

The retail-install check (src/framework/FileSystem.cpp, ported from openQ4)
compares each required base pak, pak000.pk4 to pak008.pk4, with its retail
checksum when the file system starts, and stops with an error naming what is
missing or modified. `fs_validateOfficialPaks 0` turns it off for tests on
synthetic data. This test runs the dedicated server on synthetic data only, so
it needs no retail data:

1. An empty base/ plus a synthetic game000.pk4: the server exits with an error
   that lists pak000.pk4 to pak008.pk4 as missing, and it skips the retail game
   module pak ("Ignoring unneeded game binary pk4").
2. A synthetic pak000.pk4: "checksum mismatch for pak000.pk4", and the other
   eight are still reported missing.
3. The empty base/ with `+set fs_validateOfficialPaks 0`: no retail-install
   error; the file system starts.

Usage: test_retail_install_check.py [--ded-binary PATH] (meson passes the binary
this build directory produced).
Exit status: 0 pass, 1 fail, 77 skip (no dedicated server binary).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import etqw_harness as h

REQUIRED = [f"pak{n:03d}.pk4" for n in range(9)]
FATAL = "Required official Enemy Territory: QUAKE WARS media pk4 files are missing"
FS_STARTED = "------ Initializing File System ------"


def run_server(ded: Path, root: Path, *extra: str, timeout: float = 30.0) -> tuple[int | None, str]:
    """Start the dedicated server on root (fs_basepath) and quit at once."""
    args = [str(ded), "+set", "dedicated", "1", "+set", "fs_basepath", str(root),
            "+set", "fs_savepath", str(root / "save"), "+set", "fs_game", "base",
            "+set", "net_port", "0", *extra, "+quit"]
    try:
        proc = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", timeout=timeout, cwd=str(root))
        return proc.returncode, proc.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return None, out


def fake_pak(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("readme.txt", "not a retail pak\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="the retail-install check on synthetic data")
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

    with tempfile.TemporaryDirectory(prefix="openetqw-install-check-") as tmp:
        root = Path(tmp)
        (root / "base").mkdir()
        (root / "save").mkdir()
        fake_pak(root / "base" / "game000.pk4")

        print("1. empty base/ (plus a retail game module pak)")
        code, out = run_server(ded, root)
        expect(code not in (0, None), f"the server exits with an error (exit {code})", out)
        expect(FATAL in out, "the retail-install error is printed", out)
        missing = [p for p in REQUIRED if f"missing {p}" not in out]
        expect(not missing, f"every required pak is listed as missing (not listed: {missing})", out)
        expect("Ignoring unneeded game binary pk4" in out and "game000.pk4" in out,
               "the retail game module pak is skipped", out)
        expect("content search diagnostics" in out, "the search diagnostics are printed", out)

        print("2. a pak000.pk4 that is not the retail one")
        fake_pak(root / "base" / "pak000.pk4")
        code, out = run_server(ded, root)
        expect(code not in (0, None), f"the server exits with an error (exit {code})", out)
        expect("checksum mismatch for pak000.pk4" in out, "pak000.pk4 is reported as modified", out)
        expect(all(f"missing {p}" in out for p in REQUIRED[1:]), "pak001.pk4 to pak008.pk4 are still missing", out)
        (root / "base" / "pak000.pk4").unlink()

        print("3. the same empty base/ with +set fs_validateOfficialPaks 0")
        code, out = run_server(ded, root, "+set", "fs_validateOfficialPaks", "0")
        expect(FS_STARTED in out and FATAL not in out and "missing pak000.pk4" not in out,
               "no retail-install error with the check turned off", out)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the engine refuses a base/ without the retail 1.5 paks, and fs_validateOfficialPaks 0 turns that off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
