#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assemble a universal2 macOS staging tree from an arm64 and an x64 build.

After openQ4's tools/build/assemble_macos_universal2.py (jm2/openQ4 b37b1456),
fail-closed like it, for openETQW's layout. Each input is a thin build's
directory holding install/ (the tree `meson install` staged) and
build-info.json ({"arch", "openETQW", "openETQW-game", "dirty"}, written by the
workflow). The two builds must come from the same clean engine and game commits.

Output tree (then packaged with package_release.py --arch universal2):
  openETQW-client_universal2, openETQW-ded_universal2
      lipo of the two thin executables (each slice looks for its own module)
  game_arm64.dylib, game_x64.dylib
      the thin game modules, unchanged: the engine loads game_<arch>.dylib
  everything else (base/vkprogs, licenses/)
      must be byte-identical in both inputs
  universal2-info.json
      the sources and the SHA-256 of every input and output code file

Exit codes: 0 assembled, 1 a check failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

THIN = {"arm64": "arm64", "x64": "x86_64"}
EXECUTABLES = ("openETQW-client", "openETQW-ded")


class AssemblyError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list[str]) -> str:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssemblyError(f"{' '.join(command)} failed: {completed.stderr.strip()}")
    return completed.stdout


def load_thin(directory: Path, arch: str) -> tuple[Path, dict]:
    install = directory / "install"
    info_path = directory / "build-info.json"
    if not install.is_dir() or not info_path.is_file():
        raise AssemblyError(f"{directory} lacks install/ or build-info.json")
    info = json.loads(info_path.read_text(encoding="utf-8"))
    if info.get("arch") != arch:
        raise AssemblyError(f"{directory} is a {info.get('arch')} build, expected {arch}")
    if info.get("dirty"):
        raise AssemblyError(f"the {arch} build came from a dirty tree")
    for path in install.rglob("*"):
        if path.is_symlink():
            raise AssemblyError(f"symlink in the {arch} tree: {path.relative_to(install)}")
    for name in [f"{e}_{arch}" for e in EXECUTABLES] + [f"game_{arch}.dylib"]:
        archs = run(["lipo", "-archs", str(install / name)]).split()
        if archs != [THIN[arch]]:
            raise AssemblyError(f"{arch}/{name} has slices {archs}, expected [{THIN[arch]}]")
    return install, info


def assemble(arm_dir: Path, x64_dir: Path, output: Path) -> dict:
    arm, arm_info = load_thin(arm_dir, "arm64")
    x64, x64_info = load_thin(x64_dir, "x64")
    for key in ("openETQW", "openETQW-game"):
        if not arm_info.get(key) or arm_info.get(key) != x64_info.get(key):
            raise AssemblyError(f"the thin builds come from different {key} commits: {arm_info.get(key)} and {x64_info.get(key)}")
    if output.exists():
        raise AssemblyError(f"{output} already exists")
    output.mkdir(parents=True)

    code = {f"{e}_{a}" for e in EXECUTABLES for a in THIN} | {f"game_{a}.dylib" for a in THIN}
    arm_files = {p.relative_to(arm).as_posix() for p in arm.rglob("*") if p.is_file()}
    x64_files = {p.relative_to(x64).as_posix() for p in x64.rglob("*") if p.is_file()}
    shared = sorted((arm_files | x64_files) - code)
    for rel in shared:
        if rel not in arm_files or rel not in x64_files:
            raise AssemblyError(f"{rel} is in only one of the thin builds")
        if (arm / rel).read_bytes() != (x64 / rel).read_bytes():
            raise AssemblyError(f"{rel} differs between the thin builds")
        (output / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(arm / rel, output / rel)

    hashes = {}
    for exe in EXECUTABLES:
        target = output / f"{exe}_universal2"
        run(["lipo", "-create", str(arm / f"{exe}_arm64"), str(x64 / f"{exe}_x64"), "-output", str(target)])
        archs = sorted(run(["lipo", "-archs", str(target)]).split())
        if archs != ["arm64", "x86_64"]:
            raise AssemblyError(f"{target.name} has slices {archs}")
        hashes[target.name] = sha256(target)
    for arch in THIN:
        module = f"game_{arch}.dylib"
        shutil.copy2((arm if arch == "arm64" else x64) / module, output / module)
        hashes[module] = sha256(output / module)
    for arch, root in (("arm64", arm), ("x64", x64)):
        for exe in EXECUTABLES:
            hashes[f"input/{arch}/{exe}_{arch}"] = sha256(root / f"{exe}_{arch}")

    info = {
        "openETQW": arm_info["openETQW"],
        "openETQW-game": arm_info["openETQW-game"],
        "shared_files": len(shared),
        "sha256": hashes,
    }
    (output / "universal2-info.json").write_text(json.dumps(info, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return info


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arm64-dir", type=Path, required=True)
    parser.add_argument("--x64-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("lipo needs a macOS host")
    try:
        info = assemble(args.arm64_dir.resolve(), args.x64_dir.resolve(), args.output_dir.resolve())
    except (AssemblyError, OSError, ValueError) as error:
        print(f"assemble_macos_universal2: FAILED: {error}", file=sys.stderr)
        return 1
    print(f"assemble_macos_universal2: {args.output_dir} from openETQW {info['openETQW'][:12]}, "
          f"openETQW-game {info['openETQW-game'][:12]}; {info['shared_files']} shared files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
