#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build the macOS libraries an openETQW app bundles, for macOS 11.0.

The role of openQ4's tools/build/prepare_macos_moltenvk.sh and its pinned
OpenAL Soft preparation (jm2/openQ4 b37b1456): a package must not bundle
Homebrew's libraries, which are built for the runner's macOS release (so the
app would need that release, whatever LSMinimumSystemVersion says). This
builds, from pinned sources, for arm64 (macOS is arm64 only: the owner dropped
macOS x64 on 2026-09-30) with a macOS 11.0 deployment target:
  SDL3             (libSDL3.0.dylib, sdl3.pc)
  Vulkan-Headers + Vulkan-Loader  (libvulkan.1.dylib, vulkan.pc)
and the Vulkan driver, Mesa's KosmicKrisp (libvulkan_kosmickrisp.dylib and its
ICD manifest in <prefix>/share/vulkan/icd.d), with the SPIRV-Tools it links.
KosmicKrisp replaced MoltenVK (#417: about 3x the frame rate on an M1 Max; owner,
2026-11-02). It needs Metal 4, so it and SPIRV-Tools are built for macOS 26.0;
on an older macOS the driver does not load and the engine starts on OpenGL.
The Mesa build is KisakCOD's recipe (jm2/kisakcod, ADR-0011, #971/#974): the
macOS platform for the Metal WSI, LLVM for its CLC shaders, and four patches for
leaked render-pass descriptors and drawables (a drawable kept across a swapchain
rebuild left the window black after a fullscreen round trip). Build-host tools,
not pins: LLVM (llvm-config, Homebrew's keg-only llvm is found), SPIRV-LLVM-
Translator, libclc, bison, glslang, and a python3 with packaging, mako and yaml
(--mesa-python).

The engine and the game are then configured against --prefix
(PKG_CONFIG_PATH=<prefix>/lib/pkgconfig, appended to $GITHUB_ENV when set),
and tools/build/package_release.py bundles the same files. Licence texts land
in <prefix>/share/licenses/{SDL3,Vulkan-Loader,SPIRV-Tools,Mesa}/.

Usage: prepare_macos_deps.py --arch arm64 --prefix DIR [--work-dir DIR] [--mesa-python PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

MACOS_DEPLOYMENT_TARGET = "11.0"
# KosmicKrisp uses Metal 4 (MTL4RenderPassDescriptor), which macOS 26 introduced
DRIVER_DEPLOYMENT_TARGET = "26.0"
# Bump deliberately: tag and commit together. SDL3 matches the CI's SDL3_TAG.
SOURCES = {
    "SDL3": ("https://github.com/libsdl-org/SDL.git", "release-3.4.16", "fa2c02bb6e21974a89ea9824bc53c9932abe5f9c"),
    "Vulkan-Headers": ("https://github.com/KhronosGroup/Vulkan-Headers.git", "v1.4.364", "b0c3dd6851e22621f194306d511b9253e4c1577f"),
    "Vulkan-Loader": ("https://github.com/KhronosGroup/Vulkan-Loader.git", "v1.4.364", "3270cf8a1349306f0ddee1b500ee2b0d552e1c82"),
    "SPIRV-Headers": ("https://github.com/KhronosGroup/SPIRV-Headers.git", "vulkan-sdk-1.4.363.0", "496543121ce6419f23d6fa5d7194ba66c36212d2"),
    "SPIRV-Tools": ("https://github.com/KhronosGroup/SPIRV-Tools.git", "vulkan-sdk-1.4.363.0", "ef96ed763b43b59b33b31b362f09a02b729fa1c9"),
}
MESA_VERSION = "26.2.4"
MESA_URL = f"https://archive.mesa3d.org/mesa-{MESA_VERSION}.tar.xz"
MESA_SHA256 = "bce5f7fbebb934373b86c999a064d52fb5065878dc57f287f95346648ec832e9"
# KisakCOD's KosmicKrisp patches (its macos_runtime_mesa.cmake): (file, pattern, replacement,
# text that must then be present). Drop them once a Mesa release carries the fixes.
MESA_PATCHES = (
    # mtl_new_render_pass_descriptor inits a descriptor `new` already initialized: every
    # render pass leaked its first colour attachment array
    ("src/kosmickrisp/bridge/mtl_render_state.m", r"\[\[MTL4RenderPassDescriptor new\] init\]",
     "[MTL4RenderPassDescriptor new]", "return [MTL4RenderPassDescriptor new];"),
    # kk_queue_submit presented the retained drawable and never released it: one per frame
    ("src/kosmickrisp/vulkan/kk_queue.c", r"mtl_drawable_present\(cmd_buffer->drawable\);$",
     "mtl_drawable_present(cmd_buffer->drawable); mtl_release(cmd_buffer->drawable);",
     "mtl_drawable_present(cmd_buffer->drawable); mtl_release(cmd_buffer->drawable);"),
    # a command buffer freed or re-recorded before its submit kept its drawable, so each
    # swapchain rebuild held one of the layer's three; a fullscreen round trip ran out
    ("src/kosmickrisp/vulkan/kk_cmd_buffer.c", r"^   mtl_release\(cmd->argument_table\);$",
     "   if (cmd->drawable) mtl_release(cmd->drawable);    mtl_release(cmd->argument_table);",
     "if (cmd->drawable) mtl_release(cmd->drawable);    mtl_release(cmd->argument_table)"),
    ("src/kosmickrisp/vulkan/kk_wsi.c", r"^   mtl_retain\(drawable\);$",
     "   if (cmd->drawable) mtl_release(cmd->drawable);    mtl_retain(drawable);",
     "if (cmd->drawable) mtl_release(cmd->drawable);    mtl_retain(drawable)"),
)
ARCHITECTURES = {"arm64": "arm64"}  # macOS is arm64 only (owner, 2026-09-30)


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


def cmake_build(source: Path, build: Path, prefix: Path, arch: str, extra: list[str],
                target: str = MACOS_DEPLOYMENT_TARGET) -> None:
    run([
        "cmake", "-S", str(source), "-B", str(build), "-G", "Ninja",
        "-DCMAKE_BUILD_TYPE=Release",
        f"-DCMAKE_INSTALL_PREFIX={prefix}",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        f"-DCMAKE_OSX_ARCHITECTURES={ARCHITECTURES[arch]}",
        f"-DCMAKE_OSX_DEPLOYMENT_TARGET={target}",
        f"-DCMAKE_PREFIX_PATH={prefix}",
    ] + extra)
    run(["cmake", "--build", str(build), "--parallel"])
    run(["cmake", "--install", str(build)])


def install_license(source: Path, prefix: Path, name: str) -> None:
    target = prefix / "share" / "licenses" / name / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def build_kosmickrisp(prefix: Path, work: Path, python: Path) -> None:
    archive = work / f"mesa-{MESA_VERSION}.tar.xz"
    if not archive.is_file():
        print(f"downloading {MESA_URL}", flush=True)
        with urllib.request.urlopen(MESA_URL, timeout=300) as response:
            archive.write_bytes(response.read())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != MESA_SHA256:
        archive.unlink()
        raise SystemExit(f"prepare_macos_deps: Mesa {MESA_VERSION} has SHA-256 {digest}, expected {MESA_SHA256}")
    source = work / f"mesa-{MESA_VERSION}"
    shutil.rmtree(source, ignore_errors=True)
    with tarfile.open(archive) as tar:
        tar.extractall(work, filter="data")
    for name, pattern, replacement, expected in MESA_PATCHES:
        path = source / name
        text, count = re.subn(pattern, replacement, path.read_text(encoding="utf-8"), flags=re.M)
        if count != 1 or expected not in text:
            raise SystemExit(f"prepare_macos_deps: the KosmicKrisp patch for {name} no longer applies")
        path.write_text(text, encoding="utf-8")
    llvm_config = shutil.which("llvm-config") or next(
        (str(p) for p in (Path("/opt/homebrew/opt/llvm/bin/llvm-config"), Path("/usr/local/opt/llvm/bin/llvm-config")) if p.is_file()), None)
    if llvm_config is None:
        raise SystemExit("prepare_macos_deps: Mesa needs llvm-config (Homebrew: brew install llvm)")
    bison = next((str(p) for p in (Path("/opt/homebrew/opt/bison/bin"), Path("/usr/local/opt/bison/bin")) if p.is_dir()), "")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(filter(None, [str(python.parent), str(Path(llvm_config).parent), bison, env.get("PATH", "")]))
    # this prefix's SPIRV-Tools (macOS 26) before the build host's
    env["PKG_CONFIG_PATH"] = os.pathsep.join(filter(None, [str(prefix / "lib" / "pkgconfig"), env.get("PKG_CONFIG_PATH", "")]))
    env["MACOSX_DEPLOYMENT_TARGET"] = DRIVER_DEPLOYMENT_TARGET
    build = work / "build-mesa"
    shutil.rmtree(build, ignore_errors=True)
    command = ["meson", "setup", str(build), str(source), "--buildtype=release", f"--prefix={prefix}", "--libdir=lib",
               "-Dplatforms=macos", "-Dgallium-drivers=", "-Dvulkan-drivers=kosmickrisp", "-Dopengl=false",
               "-Degl=disabled", "-Dglx=disabled", "-Dgbm=disabled", "-Dllvm=enabled", "-Dshared-llvm=enabled",
               "-Dzstd=disabled", "-Dwerror=false"]
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, env=env, check=True)
    print(f"+ ninja -C {build} install", flush=True)
    subprocess.run(["ninja", "-C", str(build), "install"], env=env, check=True)
    install_license(source / "docs" / "license.rst", prefix, "Mesa")


def minimum_os(path: Path) -> list[str]:
    commands = subprocess.run(["otool", "-arch", "all", "-l", str(path)], capture_output=True, text=True, check=True).stdout
    return sorted({line.split()[1] for line in commands.splitlines() if line.strip().startswith("minos ")})


def check_output(prefix: Path, arch: str) -> None:
    expected = sorted(ARCHITECTURES[arch].split(";"))
    for library, target in (("libSDL3.0.dylib", MACOS_DEPLOYMENT_TARGET), ("libvulkan.1.dylib", MACOS_DEPLOYMENT_TARGET),
                            ("libSPIRV-Tools.dylib", DRIVER_DEPLOYMENT_TARGET),
                            ("libvulkan_kosmickrisp.dylib", DRIVER_DEPLOYMENT_TARGET)):
        path = prefix / "lib" / library
        archs = sorted(subprocess.run(["lipo", "-archs", str(path)], capture_output=True, text=True, check=True).stdout.split())
        if not set(expected) <= set(archs):
            raise SystemExit(f"prepare_macos_deps: {library} has {archs}, needs {expected}")
        minos = minimum_os(path)
        if not minos or any(tuple(map(int, v.split("."))) > tuple(map(int, target.split("."))) for v in minos):
            raise SystemExit(f"prepare_macos_deps: {library} needs macOS {minos}, above {target}")
    # the driver must link this prefix's SPIRV-Tools, never the build host's
    for line in subprocess.run(["otool", "-L", str(prefix / "lib" / "libvulkan_kosmickrisp.dylib")],
                               capture_output=True, text=True, check=True).stdout.splitlines()[1:]:
        reference = line.split()[0]
        if Path(reference).name == "libvulkan_kosmickrisp.dylib":
            continue  # its own install name
        if not reference.startswith(("/usr/lib/", "/System/", "@rpath/", str(prefix), str(prefix.resolve()))):
            raise SystemExit(f"prepare_macos_deps: KosmicKrisp links {reference}, outside {prefix}")
    if len(list((prefix / "share" / "vulkan" / "icd.d").glob("kosmickrisp_mesa_icd*.json"))) != 1:
        raise SystemExit(f"prepare_macos_deps: no KosmicKrisp ICD manifest in {prefix}/share/vulkan/icd.d")
    for pc in ("sdl3.pc", "vulkan.pc"):
        if not (prefix / "lib" / "pkgconfig" / pc).is_file():
            raise SystemExit(f"prepare_macos_deps: no {pc} in {prefix}/lib/pkgconfig")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--arch", choices=sorted(ARCHITECTURES), required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=None, help="sources and build trees (default: temporary, removed after)")
    parser.add_argument("--mesa-python", type=Path, default=Path(sys.executable),
                        help="a python3 with packaging, mako and yaml for Mesa's meson (default: this one)")
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
        spirv_headers = fetch("SPIRV-Headers", work)
        cmake_build(spirv_headers, work / "build-spirv-headers", prefix, args.arch, [], DRIVER_DEPLOYMENT_TARGET)
        spirv_tools = fetch("SPIRV-Tools", work)
        cmake_build(spirv_tools, work / "build-spirv-tools", prefix, args.arch,
                    [f"-DSPIRV-Headers_SOURCE_DIR={spirv_headers}", "-DSPIRV_SKIP_TESTS=ON", "-DSPIRV_SKIP_EXECUTABLES=ON",
                     "-DSPIRV_TOOLS_BUILD_STATIC=OFF", "-DBUILD_SHARED_LIBS=ON"], DRIVER_DEPLOYMENT_TARGET)
        install_license(spirv_tools / "LICENSE", prefix, "SPIRV-Tools")
        probe = subprocess.run([str(args.mesa_python), "-c", "import packaging, mako, yaml"], capture_output=True)
        if probe.returncode != 0:
            raise SystemExit(f"prepare_macos_deps: {args.mesa_python} lacks packaging, mako or yaml (Mesa's meson needs them)")
        build_kosmickrisp(prefix, work, args.mesa_python)
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
