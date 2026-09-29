#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the glslangValidator that openETQW packages carry.

The Vulkan renderer compiles its GLSL (base/vkprogs) to SPIR-V at run time
with glslangValidator (src/renderer/RuntimeSpirvCompiler.cpp), which it finds
through r_vkShaderCompiler, $VULKAN_SDK, the executable's directory, then PATH.
A package therefore ships one next to the engine. This builds it from a pinned
Khronos glslang release so every package carries the same compiler: static,
GLSL only (no HLSL front end, no SPIRV-Tools optimizer, which the engine never
asks for), with a static C runtime on Windows, for macOS 11.0 and, on request,
as a universal2 binary.

Output (--output-dir): glslangValidator[.exe] and LICENSE.txt, glslang's
licence text, which a package installs under licenses/glslang/.

Usage:
  build_glslang.py --output-dir DIR [--work-dir DIR] [--tag TAG --commit SHA]
                   [--macos-universal2] [--cmake-arg ARG ...]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Bump deliberately: tag and commit together (git ls-remote --tags).
GLSLANG_REPO = "https://github.com/KhronosGroup/glslang.git"
GLSLANG_TAG = "16.6.0"
GLSLANG_COMMIT = "e1b562a8bed273a02f30b59b66a5d499793cede5"
MACOS_DEPLOYMENT_TARGET = "11.0"


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def fetch(source: Path, tag: str, commit: str) -> None:
    run(["git", "init", "--quiet", str(source)])
    run(["git", "-C", str(source), "fetch", "--quiet", "--depth", "1", GLSLANG_REPO, f"refs/tags/{tag}"])
    actual = subprocess.run(["git", "-C", str(source), "rev-parse", "FETCH_HEAD^{commit}"],
                            capture_output=True, text=True, check=True).stdout.strip()
    if actual != commit:
        raise SystemExit(f"build_glslang: tag {tag} is {actual}, expected {commit}; refusing to build")
    run(["git", "-C", str(source), "checkout", "--quiet", "--detach", commit])


def configure_and_build(source: Path, build: Path, universal2: bool, extra: list[str]) -> Path:
    args = [
        "cmake", "-S", str(source), "-B", str(build),
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=OFF",
        "-DENABLE_OPT=OFF",
        "-DENABLE_HLSL=OFF",
        "-DENABLE_GLSLANG_BINARIES=ON",
        "-DGLSLANG_TESTS=OFF",
        "-DGLSLANG_ENABLE_INSTALL=OFF",
        # a standalone process: a static C runtime needs no redistributable
        "-DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded",
        "-DCMAKE_POLICY_DEFAULT_CMP0091=NEW",
    ]
    if sys.platform == "darwin":
        args.append(f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MACOS_DEPLOYMENT_TARGET}")
        if universal2:
            args.append("-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64")
    # Windows: the Visual Studio generator finds MSVC without a developer prompt
    if shutil.which("ninja") and not sys.platform.startswith("win"):
        args += ["-G", "Ninja"]
    run(args + extra)
    run(["cmake", "--build", str(build), "--config", "Release", "--target", "glslang-standalone", "--parallel"])
    name = "glslang.exe" if sys.platform.startswith("win") else "glslang"
    candidates = [p for p in build.rglob(name) if p.is_file() and not p.is_symlink()]
    if len(candidates) != 1:
        raise SystemExit(f"build_glslang: expected one built {name}, found {[str(p) for p in candidates]}")
    return candidates[0]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=None, help="source and build tree (default: a temporary directory, removed after)")
    parser.add_argument("--tag", default=GLSLANG_TAG)
    parser.add_argument("--commit", default=GLSLANG_COMMIT)
    parser.add_argument("--macos-universal2", action="store_true", help="build arm64 and x86_64 into one binary")
    parser.add_argument("--cmake-arg", action="append", default=[], help="extra CMake argument (repeatable)")
    args = parser.parse_args(argv)
    if args.macos_universal2 and sys.platform != "darwin":
        parser.error("--macos-universal2 needs a macOS host")

    work = args.work_dir or Path(tempfile.mkdtemp(prefix="openetqw-glslang-"))
    try:
        source = work / "glslang"
        if not (source / ".git").is_dir():
            fetch(source, args.tag, args.commit)
        binary = configure_and_build(source, work / "build", args.macos_universal2, args.cmake_arg)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".exe" if sys.platform.startswith("win") else ""
        target = args.output_dir / f"glslangValidator{suffix}"
        shutil.copy2(binary, target)
        if not suffix:
            os.chmod(target, 0o755)
        shutil.copy2(source / "LICENSE.txt", args.output_dir / "LICENSE.txt")
        version = subprocess.run([str(target), "--version"], capture_output=True, text=True, check=True).stdout
        print(version.strip().splitlines()[0])
        if f"{args.tag}" not in version:
            raise SystemExit(f"build_glslang: {target} does not report version {args.tag}")
        print(f"build_glslang: {target} (glslang {args.tag}, {args.commit[:12]})")
    finally:
        if args.work_dir is None:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
