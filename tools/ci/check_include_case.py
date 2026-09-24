#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail if an #include names an existing file with the wrong letter case.

macOS and Windows file systems ignore case, Linux file systems do not: an
include of "megatexture/MegaTexture.h" builds on a Mac and fails on Linux when
the file is megatexture.h. The engine is only built on macOS so far, so this
checks every quoted #include in tracked C/C++ sources the way the compiler
searches for it: the including file's directory, then the -I directories in
order. With a configured build (build/compile_commands.json) a source file is
checked with the -I list of its own compile command, and a header with each
distinct -I list in the build. Without one (hosted CI runs this before Meson
setup) it uses the engine targets' list read from meson.build (the repository
root, then engine_include_dirs in order).

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


def include_dirs(compile_commands: Path) -> dict[str, list[tuple[str, ...]]] | None:
    """For every compiled file, the -I lists of its compile commands (in order),
    relative to the repo."""
    if not compile_commands.is_file():
        return None
    per_file: dict[str, list[tuple[str, ...]]] = {}
    for entry in json.loads(compile_commands.read_text()):
        base = entry.get("directory", ".")
        source = os.path.relpath(os.path.normpath(os.path.join(base, entry["file"])), REPO_ROOT)
        args = entry.get("arguments") or shlex.split(entry.get("command", ""))
        dirs: list[str] = []
        for i, arg in enumerate(args):
            path = arg[2:] if arg.startswith("-I") and len(arg) > 2 else (args[i + 1] if arg == "-I" and i + 1 < len(args) else None)
            if path is None:
                continue
            path = os.path.relpath(os.path.normpath(os.path.join(base, path)), REPO_ROOT)
            if path not in dirs:
                dirs.append(path)
        lists = per_file.setdefault(source, [])
        if tuple(dirs) not in lists:
            lists.append(tuple(dirs))
    return per_file


def meson_include_dirs() -> list[str]:
    """The engine targets' -I order from meson.build: the repository root, then the
    directories of engine_include_dirs in order."""
    text = Path("meson.build").read_text()
    named = dict(re.findall(r"^(\w+)\s*=\s*include_directories\(\s*'([^']+)'\s*\)", text, re.M))
    block = re.search(r"^engine_include_dirs\s*=\s*\[(.*?)\]", text, re.M | re.S)
    order = [named[name] for name in re.findall(r"\w+", block.group(1)) if name in named] if block else []
    dirs = ["."]
    for d in order:
        if d not in dirs:
            dirs.append(d)
    return dirs


def meson_sources() -> set[str]:
    """Every source file named in tools/build/meson_sources.py (the build's file lists)."""
    text = Path("tools/build/meson_sources.py").read_text()
    return set(re.findall(r'"((?:src|tests)/[^"]+\.(?:c|cpp|mm))"', text))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--compile-commands", type=Path, default=REPO_ROOT / "build" / "compile_commands.json",
                    help="Meson's compile database, for the real -I directories (default: build/)")
    args = ap.parse_args(argv)
    os.chdir(REPO_ROOT)
    database = include_dirs(args.compile_commands)
    if database is not None:
        # a source file with its own command's -I list(s); a header with every distinct list
        all_lists = sorted({dirs for lists in database.values() for dirs in lists})
    else:
        fallback = tuple(meson_include_dirs())
        listed = meson_sources()
    files = subprocess.run(["git", "ls-files", "src", "tests"], capture_output=True, text=True,
                           check=True).stdout.split()
    problems = []
    for f in files:
        if not f.endswith(SOURCE_SUFFIXES) or f.startswith(EXCLUDE) or not os.path.exists(f):
            continue
        header = f.endswith((".h", ".hpp", ".inl"))
        if database is None:
            if not header and f not in listed and not f.startswith("tests/"):
                continue	# a source file the build does not compile
            search_lists = [fallback]
        elif header:
            search_lists = all_lists
        elif f in database:
            search_lists = database[f]
        else:
            continue	# a source file the build does not compile
        with open(f, errors="replace") as source:
            for number, line in enumerate(source, 1):
                m = INCLUDE_RE.match(line)
                if not m:
                    continue
                target = m.group(1)
                for search in search_lists:
                    # the compiler's order: the including file's directory, then the -I list
                    bad = None
                    for directory in [os.path.dirname(f)] + list(search):
                        found = resolve(os.path.join(directory, target))
                        if found is None:
                            continue
                        if found is False:
                            bad = directory
                        break
                    if bad is not None:
                        problems.append(f"{f}:{number}: #include \"{target}\" first matches {bad}/ only when case is ignored")
                        break
    for p in problems:
        print(p)
    source = "compile_commands.json -I order" if database is not None else "fallback search list"
    print(f"check_include_case: {len(problems)} case-mismatched include(s) ({source})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
