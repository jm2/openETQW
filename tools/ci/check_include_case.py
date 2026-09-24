#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail if an #include names an existing file with the wrong letter case.

macOS and Windows file systems ignore case, Linux file systems do not: an
include of "megatexture/MegaTexture.h" builds on a Mac and fails on Linux when
the file is megatexture.h. The engine is only built on macOS so far, so this
checks every quoted #include in tracked C/C++ sources the way the compiler
searches for it: the including file's directory, then the -I directories in
order. With a configured build (build/compile_commands.json) those are the real
-I directories of the engine targets; without one (hosted CI) it falls back to
src/, the repository root and the staged game tree (.tmp/gamelibs_stage).

It is an error when the first directory that holds the file only holds it
under another letter case: Linux then fails, or skips to a later directory and
may pick a different file, while macOS takes the earlier one. Includes that
resolve nowhere (system headers, generated files) are not this check's business.

src/tools/radiant is excluded: the MFC level editor is Windows-only and is not
built by Meson.

Exit codes: 0 pass, 1 mismatches found.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STAGE = Path(".tmp/gamelibs_stage")
EXCLUDE = ("src/tools/radiant/",)
SOURCE_SUFFIXES = (".c", ".cpp", ".h", ".hpp", ".inl", ".mm")
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*"([^"]+)"')

_listing: dict[str, list[str] | None] = {}


def listing(directory: str) -> list[str] | None:
    if directory not in _listing:
        try:
            _listing[directory] = os.listdir(directory)
        except OSError:
            _listing[directory] = None
    return _listing[directory]


def resolve(path: str) -> bool | None:
    """True: exists with this exact case; False: exists only ignoring case; None: absent."""
    current = "."
    exact = True
    for part in os.path.normpath(path).split(os.sep):
        if part in ("", "."):
            continue
        if part == "..":
            current = os.path.normpath(os.path.join(current, ".."))
            continue
        names = listing(current)
        if names is None:
            return None
        if part not in names:
            folded = [n for n in names if n.lower() == part.lower()]
            if not folded:
                return None
            exact = False
            part = folded[0]
        current = os.path.join(current, part)
    return exact


def include_dirs(compile_commands: Path) -> tuple[list[str], set[str]] | None:
    """The -I directories of every translation unit, in first-seen order, and the compiled
    files, both relative to the repo."""
    if not compile_commands.is_file():
        return None
    dirs: list[str] = []
    compiled: set[str] = set()
    for entry in json.loads(compile_commands.read_text()):
        compiled.add(os.path.relpath(os.path.normpath(os.path.join(entry.get("directory", "."), entry["file"])), REPO_ROOT))
        args = entry.get("arguments") or shlex.split(entry.get("command", ""))
        base = entry.get("directory", ".")
        for i, arg in enumerate(args):
            path = arg[2:] if arg.startswith("-I") and len(arg) > 2 else (args[i + 1] if arg == "-I" and i + 1 < len(args) else None)
            if path is None:
                continue
            path = os.path.relpath(os.path.normpath(os.path.join(base, path)), REPO_ROOT)
            if path not in dirs:
                dirs.append(path)
    return dirs, compiled


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--compile-commands", type=Path, default=REPO_ROOT / "build" / "compile_commands.json",
                    help="Meson's compile database, for the real -I directories (default: build/)")
    args = ap.parse_args(argv)
    os.chdir(REPO_ROOT)
    database = include_dirs(args.compile_commands)
    if database is not None:
        # the compiler's order: the including file's directory, then the -I list. Source
        # files are checked only when the build compiles them; headers always are.
        search, compiled = database
        twin = False
    else:
        search, compiled = ["src", "."] + ([str(STAGE / "src"), str(STAGE)] if STAGE.is_dir() else []), None
        twin = STAGE.is_dir()	# approximate: the staged copy of the including file's directory
    files = subprocess.run(["git", "ls-files", "src", "tests"], capture_output=True, text=True,
                           check=True).stdout.split()
    problems = []
    for f in files:
        if not f.endswith(SOURCE_SUFFIXES) or f.startswith(EXCLUDE) or not os.path.exists(f):
            continue
        if compiled is not None and not f.endswith((".h", ".hpp", ".inl")) and f not in compiled:
            continue
        with open(f, errors="replace") as source:
            for number, line in enumerate(source, 1):
                m = INCLUDE_RE.match(line)
                if not m:
                    continue
                target = m.group(1)
                dirs = [os.path.dirname(f)] + ([str(STAGE / os.path.dirname(f))] if twin else []) + search
                for directory in dirs:
                    found = resolve(os.path.join(directory, target))
                    if found is None:
                        continue
                    if found is False:
                        problems.append(f"{f}:{number}: #include \"{target}\" first matches {directory}/ only when case is ignored")
                    break
    for p in problems:
        print(p)
    source = "compile_commands.json -I order" if database is not None else "fallback search list"
    print(f"check_include_case: {len(problems)} case-mismatched include(s) ({source})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
