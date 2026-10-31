#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""console.log names the build that wrote it (#362).

The engine prints its two version lines (the retail-style "ETQW 1.5.<rev> <build>
<date>" and "openETQW <version>") before the file system starts, and the log file
can only open after it, so console.log never carried them: a log attached to a bug
report could not say which build it came from (a desktop launch has no stdout).
The log now starts with both lines.

The test runs the dedicated server on synthetic data (fs_validateOfficialPaks 0), as
test_retired_cvars.py does, with +set logFile 2 and a private fs_userpath, reads the
version lines from stdout, and checks that the console.log it wrote contains both.

Usage: test_log_version.py [--ded-binary PATH] [--game-module PATH] (meson passes
the binary and the game module this build directory uses).
Exit status: 0 pass, 1 fail, 77 skip (no dedicated server binary).
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import etqw_harness as h
from test_retired_cvars import run_server

ENGINE_LINE = re.compile(r"^ETQW \d+\.\d+\.\d+\.\d+ \S+ .+$", re.MULTILINE)
PRODUCT_LINE = re.compile(r"^openETQW \S+.*$", re.MULTILINE)


def main() -> int:
    ap = argparse.ArgumentParser(description="console.log carries the version lines")
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

    with h.PrivateTempDir(prefix="openetqw-log-version-") as tmp:
        root = Path(tmp)
        (root / "base").mkdir()
        (root / "save").mkdir()
        (root / "user").mkdir()
        if args.game_module:
            module = Path(args.game_module)
            shutil.copy2(module, root / "base" / module.name)
        h.write_synthetic_stub_decls(root / "base")

        code, out = run_server(ded, root, "+set", "fs_userpath", str(root / "user"), "+set", "logFile", "2")
        engine = ENGINE_LINE.search(out)
        product = PRODUCT_LINE.search(out)
        expect(engine is not None and product is not None, "stdout has both version lines", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

        logs = sorted((root / "user").rglob("console.log"))
        expect(len(logs) == 1, f"one console.log under fs_userpath ({[str(p) for p in logs]})", out)
        if logs and engine and product:
            text = logs[0].read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            expect(lines[:2] == [engine.group(0), product.group(0)],
                   f"console.log starts with {engine.group(0)!r} and {product.group(0)!r} (first lines {lines[:2]})", text)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: console.log starts with the version lines")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
