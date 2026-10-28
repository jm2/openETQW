#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The harness's private directories are made without mkdtemp's Windows DACL.

Since Python 3.13 (CVE-2024-4030), tempfile.mkdtemp and TemporaryDirectory create
their directory on Windows with an owner-only DACL. A process on a restricted token
(the Codex Windows sandbox, openai/codex#19791) cannot write through it, so the
engine could not write its generated SPIR-V (vkprogs/cache/...) under the test's
fs_savepath, Vulkan init failed, and every client run on yoga-slim7x fell back to
OpenGL on D3D12. etqw_harness.make_private_dir uses os.makedirs with the default
mode on Windows and mkdtemp's 0o700 on POSIX.

No engine, no retail data, no ports. Pinned here:
  * make_private_dir returns a new, empty, absolute directory under the parent it
    is given, named with the prefix, and never the same path twice;
  * the mode: 0o700 on POSIX (as mkdtemp), the default 0o777 on Windows;
  * PrivateTempDir yields a str path and removes the tree on exit;
  * private_savepath goes through make_private_dir;
  * no script under tests/ or tools/ that sets fs_savepath or fs_devpath calls
    tempfile.mkdtemp( or TemporaryDirectory( again.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import etqw_harness as H  # noqa: E402

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
            held = Path(tmp)
        expect(not held.exists(), "PrivateTempDir removes its tree on exit")

        save = H.private_savepath("etqw-pd-save-")
        expect(save.is_dir() and save.name.startswith("etqw-pd-save-"), "private_savepath makes the dir")
        if os.name != "nt":
            expect(stat.S_IMODE(save.stat().st_mode) == 0o700, "private_savepath keeps 0o700 on POSIX")
        shutil.rmtree(save, ignore_errors=True)
    finally:
        shutil.rmtree(parent, ignore_errors=True)

    # Regression guard: an engine dir made with mkdtemp again would bring the DACL back.
    sets_engine_dir = re.compile(r"\bfs_(savepath|devpath)\b")
    mkdtemp_call = re.compile(r"\btempfile\.mkdtemp\(|\bTemporaryDirectory\(")
    me = Path(__file__).resolve()
    for root in (REPO_ROOT / "tests", REPO_ROOT / "tools"):
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == me:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if not sets_engine_dir.search(text):
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if mkdtemp_call.search(line.split("#", 1)[0]):
                    expect(False, f"{path.relative_to(REPO_ROOT)}:{n} sets an engine dir and calls "
                                  f"mkdtemp/TemporaryDirectory; use etqw_harness.make_private_dir")

    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("PASS: harness private dirs (make_private_dir, PrivateTempDir, private_savepath)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
