#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The harness's private directories are made without mkdtemp's Windows DACL.

Since Python 3.13 (CVE-2024-4030), tempfile.mkdtemp and TemporaryDirectory create
their directory on Windows with an owner-only DACL. A process on a restricted token
(the Codex Windows sandbox, openai/codex#19791) cannot write through it, so the
engine could not write its generated SPIR-V (vkprogs/cache/...) under the test's
fs_savepath, Vulkan init failed, and every client run on yoga-slim7x fell back to
OpenGL on D3D12, and the tool tests (release-tools, packaging-tools,
build-scripts, ...) failed at setup. private_dirs.make_private_dir (re-exported by
etqw_harness) uses os.makedirs with the default mode on Windows and mkdtemp's 0o700
on POSIX.

No engine, no retail data, no ports. Pinned here:
  * make_private_dir returns a new, empty, absolute directory under the parent it
    is given, named with the prefix, and never the same path twice;
  * the mode: 0o700 on POSIX (as mkdtemp), the default 0o777 on Windows;
  * PrivateTempDir yields a str path and removes the tree on exit, read-only
    files included (as TemporaryDirectory does);
  * etqw_harness re-exports the same objects, and importing private_dirs alone
    sets no environment variable;
  * private_savepath goes through make_private_dir;
  * no script under tests/, and none under tools/ that sets fs_savepath or
    fs_devpath, calls tempfile.mkdtemp( or TemporaryDirectory( again.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import etqw_harness as H  # noqa: E402
import private_dirs  # noqa: E402

failures: list[str] = []


def expect(ok: bool, what: str) -> None:
    if not ok:
        print("FAIL: " + what)
        failures.append(what)


def main() -> int:
    # the parent too: under the Windows sandbox a mkdtemp parent would refuse every write below it
    parent = H.make_private_dir("openetqw-private-dirs-test-")
    try:
        a = H.make_private_dir("etqw-pd-", dir=parent)
        b = H.make_private_dir("etqw-pd-", dir=parent)
        expect(a != b, "two calls give two directories")
        for d in (a, b):
            expect(d.is_dir(), f"{d} exists and is a directory")
            expect(d.is_absolute(), f"{d} is absolute")
            expect(d.parent == Path(os.path.abspath(parent)), f"{d} is directly under {parent}")
            expect(d.name.startswith("etqw-pd-") and len(d.name) > len("etqw-pd-"),
                   f"{d.name} is the prefix plus a random part")
            expect(not any(d.iterdir()), f"{d} is empty")
            (d / "base").mkdir()  # the engine's first write under fs_savepath
            (d / "base" / "etqwconfig.cfg").write_text("seta test 1\n", encoding="utf-8")

        mode = stat.S_IMODE(a.stat().st_mode)
        if os.name == "nt":
            expect(H.PRIVATE_DIR_MODE == 0o777, "Windows uses the default mode (no owner-only DACL)")
        else:
            expect(H.PRIVATE_DIR_MODE == 0o700, "POSIX keeps mkdtemp's 0o700")
            expect(mode == 0o700, f"POSIX directory mode is 0o700 (got {oct(mode)})")

        # exist_ok=False: a name is never handed out twice
        expect(len({H.make_private_dir("x-", dir=parent) for _ in range(50)}) == 50,
               "50 calls give 50 distinct directories")

        with H.PrivateTempDir(prefix="etqw-pd-ctx-", dir=parent) as tmp:
            expect(isinstance(tmp, str), "PrivateTempDir yields a str, as TemporaryDirectory does")
            (Path(tmp) / "save" / "base").mkdir(parents=True)
            ro = Path(tmp) / "save" / "base" / "readonly.cfg"
            ro.write_text("x\n", encoding="utf-8")
            os.chmod(ro, stat.S_IREAD)
            held = Path(tmp)
        expect(not held.exists(), "PrivateTempDir removes its tree on exit")

        expect(H.make_private_dir is private_dirs.make_private_dir
               and H.PrivateTempDir is private_dirs.PrivateTempDir,
               "etqw_harness re-exports private_dirs' helpers")

        save = H.private_savepath("etqw-pd-save-")
        expect(save.is_dir() and save.name.startswith("etqw-pd-save-"), "private_savepath makes the dir")
        if os.name != "nt":
            expect(stat.S_IMODE(save.stat().st_mode) == 0o700, "private_savepath keeps 0o700 on POSIX")
        shutil.rmtree(save, ignore_errors=True)
    finally:
        shutil.rmtree(parent, ignore_errors=True)

    # Importing private_dirs on its own changes no environment (etqw_harness does).
    probe = subprocess.run([sys.executable, "-c",
                            "import os, sys; sys.path.insert(0, sys.argv[1]); before = dict(os.environ); "
                            "import private_dirs; sys.exit(0 if dict(os.environ) == before else 1)", str(HERE)],
                           capture_output=True, text=True)
    expect(probe.returncode == 0, f"importing private_dirs leaves os.environ alone ({probe.stderr.strip()})")

    # Regression guard: a dir made with mkdtemp again would bring the DACL back. Every
    # script under tests/ (tool tests too: their setup failed in the sandbox), and
    # those under tools/ that hand the engine a save or dev path.
    sets_engine_dir = re.compile(r"\bfs_(savepath|devpath)\b")
    mkdtemp_call = re.compile(r"\btempfile\.mkdtemp\(|\bTemporaryDirectory\(")
    me = Path(__file__).resolve()
    for root in (REPO_ROOT / "tests", REPO_ROOT / "tools"):
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == me:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if root.name == "tools" and not sets_engine_dir.search(text):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if mkdtemp_call.search(line.split("#", 1)[0]):
                    expect(False, f"{path.relative_to(REPO_ROOT)}:{n} calls mkdtemp/TemporaryDirectory; "
                                  f"use private_dirs.make_private_dir or PrivateTempDir")

    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("PASS: harness private dirs (make_private_dir, PrivateTempDir, private_savepath)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
