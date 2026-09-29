#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the macOS libraries an openETQW app bundles, for macOS 11.0.

The role of openQ4's tools/build/prepare_macos_moltenvk.sh and its pinned
OpenAL Soft preparation (jm2/openQ4 b37b1456): a package must not bundle
Homebrew's libraries, which are built for the runner's macOS release (so the
app would need that release, whatever LSMinimumSystemVersion says) and for
one architecture only (so a universal2 app could not load them). This builds,
from pinned sources and for the requested architectures with a macOS 11.0
deployment target:
  SDL3             (libSDL3.0.dylib, sdl3.pc)
  Vulkan-Headers + Vulkan-Loader  (libvulkan.1.dylib, vulkan.pc)
and fetches the pinned, checksum-verified MoltenVK release (libMoltenVK.dylib,
a universal binary Khronos builds for macOS 10.15+).

The engine and the game are then configured against --prefix
(PKG_CONFIG_PATH=<prefix>/lib/pkgconfig, appended to $GITHUB_ENV when set),
and tools/build/package_release.py bundles the same files. Licence texts land
in <prefix>/share/licenses/{SDL3,Vulkan-Loader,MoltenVK}/.

Usage: prepare_macos_deps.py --arch {arm64,x64,universal2} --prefix DIR [--work-dir DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

MACOS_DEPLOYMENT_TARGET = "11.0"
# Bump deliberately: tag and commit together. SDL3 matches the CI's SDL3_TAG.
SOURCES = {
    "SDL3": ("https://github.com/libsdl-org/SDL.git", "release-3.4.16", "fa2c02bb6e21974a89ea9824bc53c9932abe5f9c"),
    "Vulkan-Headers": ("https://github.com/KhronosGroup/Vulkan-Headers.git", "v1.4.364", "b0c3dd6851e22621f194306d511b9253e4c1577f"),
    "Vulkan-Loader": ("https://github.com/KhronosGroup/Vulkan-Loader.git", "v1.4.364", "3270cf8a1349306f0ddee1b500ee2b0d552e1c82"),
}
MOLTENVK_VERSION = "v1.4.2"
MOLTENVK_URL = f"https://github.com/KhronosGroup/MoltenVK/releases/download/{MOLTENVK_VERSION}/MoltenVK-macos.tar"
MOLTENVK_SHA256 = "f95765a6229cb7b915990a2890ce12ebe36a730b021545d3d52ae69ce4c4024e"
ARCHITECTURES = {"arm64": "arm64", "x64": "x86_64", "universal2": "arm64;x86_64"}


def run(command: list[str], cwd: Path | None = None) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=str(cwd) if cwd else None, check=True)


def fetch(name: str, destination: Path) -> Path:
    url, tag, commit = SOURCES[name]
    source = destination / name
    if (source / ".git").is_dir():
        return source
    run(["git", "init", "--quiet", str(source)])
    run(["git", "-C", str(source), "fetch", "--quiet", "--depth", "1", url, f"refs/tags/{tag}"])
    actual = subprocess.run(["git", "-C", str(source), "rev-parse", "FETCH_HEAD^{commit}"],
                            capture_output=True, text=True, check=True).stdout.strip()
    if actual != commit:
        raise SystemExit(f"prepare_macos_deps: {name} {tag} is {actual}, expected {commit}; refusing to build")
    run(["git", "-C", str(source), "checkout", "--quiet", "--detach", commit])
    return source


def cmake_build(source: Path, build: Path, prefix: Path, arch: str, extra: list[str]) -> None:
    run([
        "cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        f"-DCMAKE_OSX_ARCHITECTURES={ARCHITECTURES[arch]}",
        f"-DCMAKE_OSX_DEPLOYMENT_TARGET={MACOS_DEPLOYMENT_TARGET}",
        f"-DCMAKE_PREFIX_PATH={prefix}",
    ] + extra)
    run(["cmake", "--build", str(build), "--parallel"])
    run(["cmake", "--install", str(build)])


def install_license(source: Path, prefix: Path, name: str) -> None:
    target = prefix / "share" / "licenses" / name / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def fetch_moltenvk(prefix: Path, work: Path) -> None:
    archive = work / "MoltenVK-macos.tar"
    if not archive.is_file():
        print(f"downloading {MOLTENVK_URL}", flush=True)
        with urllib.request.urlopen(MOLTENVK_URL, timeout=300) as response:
            archive.write_bytes(response.read())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != MOLTENVK_SHA256:
        archive.unlink()
        raise SystemExit(f"prepare_macos_deps: MoltenVK {MOLTENVK_VERSION} has SHA-256 {digest}, expected {MOLTENVK_SHA256}")
    wanted = {
        "MoltenVK/MoltenVK/dynamic/dylib/macOS/libMoltenVK.dylib": prefix / "lib" / "libMoltenVK.dylib",
        "MoltenVK/LICENSE": prefix / "share" / "licenses" / "MoltenVK" / "LICENSE",
    }
    with tarfile.open(archive) as tar:
        for member, target in wanted.items():
            info = tar.getmember(member)
            if not info.isfile():
                raise SystemExit(f"prepare_macos_deps: {member} is not a regular file in the MoltenVK archive")
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(info) as source, open(target, "wb") as destination:
                shutil.copyfileobj(source, destination)
    os.chmod(prefix / "lib" / "libMoltenVK.dylib", 0o755)


def check_output(prefix: Path, arch: str) -> None:
    expected = sorted(ARCHITECTURES[arch].split(";"))
    for library in ("libSDL3.0.dylib", "libvulkan.1.dylib", "libMoltenVK.dylib"):
        path = prefix / "lib" / library
        archs = sorted(subprocess.run(["lipo", "-archs", str(path)], capture_output=True, text=True, check=True).stdout.split())
        if not set(expected) <= set(archs):
            raise SystemExit(f"prepare_macos_deps: {library} has {archs}, needs {expected}")
    for pc in ("sdl3.pc", "vulkan.pc"):
        if not (prefix / "lib" / "pkgconfig" / pc).is_file():
            raise SystemExit(f"prepare_macos_deps: no {pc} in {prefix}/lib/pkgconfig")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arch", choices=sorted(ARCHITECTURES), required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=None, help="sources and build trees (default: temporary, removed after)")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("this builds macOS libraries on a macOS host")
    prefix = args.prefix.resolve()
    work = args.work_dir.resolve() if args.work_dir else Path(tempfile.mkdtemp(prefix="openetqw-macos-deps-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        sdl = fetch("SDL3", work)
        cmake_build(sdl, work / "build-sdl3", prefix, args.arch,
                    ["-DSDL_SHARED=ON", "-DSDL_STATIC=OFF", "-DSDL_TESTS=OFF", "-DSDL_EXAMPLES=OFF"])
        headers = fetch("Vulkan-Headers", work)
        cmake_build(headers, work / "build-vulkan-headers", prefix, args.arch, [])
        loader = fetch("Vulkan-Loader", work)
        cmake_build(loader, work / "build-vulkan-loader", prefix, args.arch,
                    [f"-DVULKAN_HEADERS_INSTALL_DIR={prefix}", "-DBUILD_TESTS=OFF", "-DUPDATE_DEPS=OFF"])
        install_license(loader / "LICENSE.txt", prefix, "Vulkan-Loader")
        if not (prefix / "share" / "licenses" / "SDL3" / "LICENSE.txt").is_file():
            install_license(sdl / "LICENSE.txt", prefix, "SDL3")
        fetch_moltenvk(prefix, work)
        check_output(prefix, args.arch)
    finally:
        if args.work_dir is None:
            shutil.rmtree(work, ignore_errors=True)
    pkg_config_path = f"{prefix}/lib/pkgconfig"
    print(f"prepare_macos_deps: {args.arch} libraries in {prefix}; PKG_CONFIG_PATH={pkg_config_path}")
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as handle:
            handle.write(f"PKG_CONFIG_PATH={pkg_config_path}\nOPENETQW_MACOS_DEPS={prefix}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
