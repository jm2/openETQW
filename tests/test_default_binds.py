#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A fresh profile gets retail's default key bindings.

Retail 1.5 ships its default bindings in the language paks, as
localization/<sys_lang>/defaultbinds.cfg (zpak_english*.pk4; it binds F3 to _ready,
among the rest), and executes that file before the saved binding file. The engine
executed only default.cfg, which retail does not ship, so a new install had no keys
bound and the warmup hint read "Press <_ready> to ready up!". Both the defaults file
and the saved binding file start with unbindall, so a saved profile replaces the
defaults completely.

The test runs the dedicated server on synthetic data (fs_validateOfficialPaks 0), as
test_retired_cvars.py does, with stand-in defaults files under base/localization/:

1. No saved bindings: listBinds shows the English defaults (F3 "_ready"). This fails
   while the engine skips the defaults file.
2. sys_lang french with no French file: the English defaults still load.
3. sys_lang french with a French file: that file's bindings load, not the English ones.
4. A saved binding file (fs_savepath/base/etqwdedbinds.cfg, starting with unbindall as
   the engine writes it): its bindings replace the defaults.

Usage: test_default_binds.py [--ded-binary PATH] [--game-module PATH] (meson passes
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

DEFAULTS = {
    "english": 'unbindall\nbind F3 "_ready"\nbind w "_forward"\n',
    "french": 'unbindall\nbind F4 "_ready"\n',
}
SAVED = 'unbindall\nbind "F5" "_ready" "" "default"\n'
SAVED_FILE = "etqwdedbinds.cfg"  # the dedicated server's BINDING_FILE


def ready_keys(out: str) -> list[str]:
    """The keys listBinds shows bound to _ready."""
    return [m.group(1) for m in re.finditer(r'^(\S+)\s+"_ready"', out, re.MULTILINE)]


def main() -> int:
    ap = argparse.ArgumentParser(description="retail's default key bindings on a fresh profile")
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

    def write_defaults(base: Path, language: str) -> None:
        path = base / "localization" / language / "defaultbinds.cfg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DEFAULTS[language], encoding="utf-8")

    with h.PrivateTempDir(prefix="openetqw-default-binds-") as tmp:
        root = Path(tmp)
        base = root / "base"
        base.mkdir()
        (root / "save").mkdir()
        if args.game_module:
            module = Path(args.game_module)
            shutil.copy2(module, base / module.name)
        h.write_synthetic_stub_decls(base)
        write_defaults(base, "english")

        print("1. no saved bindings: the English defaults")
        code, out = run_server(ded, root, "+listBinds")
        expect(ready_keys(out) == ["F3"], f"F3 is bound to _ready (keys: {ready_keys(out)}, exit {code})", out)
        expect(re.search(r'^w\s+"_forward"', out, re.MULTILINE) is not None, "w is bound to _forward", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

        print("2. sys_lang french, no French defaults: the English defaults")
        code, out = run_server(ded, root, "+set", "sys_lang", "french", "+listBinds")
        expect(ready_keys(out) == ["F3"], f"F3 is bound to _ready (keys: {ready_keys(out)}, exit {code})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

        print("3. sys_lang french with French defaults: the French ones")
        write_defaults(base, "french")
        code, out = run_server(ded, root, "+set", "sys_lang", "french", "+listBinds")
        expect(ready_keys(out) == ["F4"], f"only F4 is bound to _ready (keys: {ready_keys(out)}, exit {code})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

        print(f"4. a saved binding file ({SAVED_FILE}) replaces the defaults")
        saved = root / "save" / "base"
        saved.mkdir(parents=True, exist_ok=True)
        (saved / SAVED_FILE).write_text(SAVED, encoding="utf-8")
        code, out = run_server(ded, root, "+listBinds")
        expect(ready_keys(out) == ["F5"], f"only F5 is bound to _ready (keys: {ready_keys(out)}, exit {code})", out)
        expect(code == 0, f"the server exits 0 (exit {code})", out)

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: a fresh profile gets the default bindings for its language, and a saved profile replaces them")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
