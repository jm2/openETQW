#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Package a staged openETQW install tree as a release asset.

Takes the tree `meson install` stages (the binaries, the game module,
base/vkprogs and licenses/; tools/validation/openetqw_validate.py checks it)
and makes the package users download. Plays the role of openQ4's
tools/build/package_nightly.py (jm2/openQ4 b37b1456), written for openETQW's
single Vulkan-first build: one package per platform and architecture, and one
macOS build (Vulkan on MoltenVK), not an OpenGL/Metal pair.

Every package carries:
  - the engine client and dedicated server and the game module;
  - base/vkprogs, the engine's GLSL (compiled to SPIR-V at run time);
  - glslangValidator next to the engine (tools/build/build_glslang.py): the
    renderer finds it through the executable's directory;
  - the libraries the engine links that a user's system may lack, next to the
    engine (Linux: SDL3; Windows: SDL3, zlib and, with --windows-crt-dir, the
    MSVC runtime; macOS: SDL3, the Vulkan loader and MoltenVK in the bundle);
  - licenses/: the texts staged by meson install, plus one per bundled
    third-party binary (--license NAME=FILE); a bundled binary without a
    licence text fails the package;
  - README.txt: version, layout, and where the retail data and saves go.

Outputs, in --output-dir (<tag> is --version-tag):
  linux    openetqw-<tag>-linux-<arch>.tar.xz and openetqw-<tag>-linux-<arch>-debug.tar.xz
           (the binaries are stripped; the symbols are split into .debug files
           linked with --add-gnu-debuglink), plus the unpacked tree for the AppImage
  windows  openetqw-<tag>-windows-<arch>.zip (PDBs go to openetqw-<tag>-windows-<arch>-pdb.zip,
           a workflow artifact), plus the unpacked tree for the installer
  macos    openETQW.app (self-contained-v1: Contents/Resources/base,
           Contents/Frameworks/game_<arch>.dylib, OpenETQWRuntimeLayout in Info.plist),
           signed ad hoc or with --macos-sign-identity, archived as
           openetqw-<tag>-macos-<arch>-unsigned.tar.gz, or with --macos-dmg as
           openetqw-<tag>-macos-<arch>.dmg (notarization is the workflow's step)

Archives are reproducible for a given tree: sorted members, mtime
SOURCE_DATE_EPOCH (or --source-date-epoch), owner 0:0, modes kept.
The paths of the results are printed as key=value lines and appended to
$GITHUB_OUTPUT when that is set.

Exit codes: 0 packaged, 1 a package check failed, 2 usage error.
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import lzma
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

PRODUCT = "openETQW"
ASSET_PREFIX = "openetqw"
APP_NAME = "openETQW.app"
BUNDLE_ID = "io.github.jm2.openetqw"
MACOS_MIN_OS = "11.0"
TAG_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.]+)?$")
# licences the package must carry for each bundled third-party binary (--license NAME=FILE)
BUNDLE_LICENSES = {
    "linux": ["glslang", "SDL3"],
    "windows": ["glslang", "SDL3", "zlib"],
    "macos": ["glslang", "SDL3", "Vulkan-Loader", "MoltenVK"],
}


class PackageError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"package_release: {message}", flush=True)


def run(command: list[str], *, capture: bool = False) -> str:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        raise PackageError(f"{' '.join(command)} failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout if capture else ""


def copy_tree(source: Path, target: Path, *, skip=lambda rel: False) -> None:
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(source)
        if path.is_symlink():
            raise PackageError(f"the staged tree has a symlink: {rel}")
        if skip(rel):
            continue
        destination = target / rel
        if path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


def copy_file(source: Path, target: Path, mode: int | None = None) -> None:
    if not source.is_file():
        raise PackageError(f"missing input {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source.resolve(), target)
    shutil.copystat(source.resolve(), target)
    if mode is not None:
        os.chmod(target, mode)


# ---------------------------------------------------------------- archives

def _normalized(info: tarfile.TarInfo, epoch: int) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = epoch
    if info.isdir():
        info.mode = 0o755
    else:
        info.mode = 0o755 if info.mode & 0o111 else 0o644
    return info


def write_tar(root: Path, top: str, output: Path, epoch: int, compression: str) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        archive.add(root, arcname=top, recursive=False, filter=lambda i: _normalized(i, epoch))
        for path in sorted(root.rglob("*")):
            archive.add(path, arcname=f"{top}/{path.relative_to(root).as_posix()}", recursive=False,
                        filter=lambda i: _normalized(i, epoch))
    data = buffer.getvalue()
    if compression == "xz":
        output.write_bytes(lzma.compress(data, preset=9 | lzma.PRESET_EXTREME))
    else:
        with open(output, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=9, mtime=epoch) as gz:
            gz.write(data)


def write_zip(root: Path, top: str, output: Path, epoch: int, *, select=lambda rel: True) -> int:
    import time
    stamp = time.gmtime(max(epoch, 315532800))[:6]  # zip cannot store dates before 1980
    count = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if path.is_dir() or not select(rel):
                continue
            info = zipfile.ZipInfo(f"{top}/{rel.as_posix()}", date_time=stamp)
            info.external_attr = ((0o755 if os.access(path, os.X_OK) else 0o644) | stat.S_IFREG) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
            count += 1
    return count


# ---------------------------------------------------------------- common payload

def add_licenses(package: Path, licenses_root: Path, platform: str, licenses: dict[str, Path]) -> None:
    missing = [name for name in BUNDLE_LICENSES[platform] if name not in licenses]
    if missing:
        raise PackageError(f"no licence text given for bundled {', '.join(missing)} (--license NAME=FILE)")
    for name, source in sorted(licenses.items()):
        copy_file(source, licenses_root / name / source.name, 0o644)


def write_readme(target: Path, platform: str, arch: str, tag: str, sources: dict[str, str]) -> None:
    saves = {
        "linux": "$XDG_DATA_HOME/openetqw (normally ~/.local/share/openetqw)",
        "windows": "%LOCALAPPDATA%\\openETQW",
        "macos": "~/Library/Application Support/openETQW",
    }[platform]
    start = {
        "linux": f"./openETQW-client_{arch} +set fs_basepath /path/to/ETQW",
        "windows": f"openETQW-client_{arch}.exe +set fs_basepath \"C:\\path\\to\\ETQW\"",
        "macos": "open openETQW.app --args +set fs_basepath \"/path/to/ETQW\"",
    }[platform]
    lines = [
        f"{PRODUCT} {tag} ({platform} {arch})",
        "",
        "An open-source engine for Enemy Territory: QUAKE Wars. It needs the retail game",
        "data: a full ET:QW install patched to 1.5, whose base/ holds pak000-pak008.pk4.",
        "The unpatched DVD data is not supported.",
        "",
        "Starting",
        f"  {start}",
    ] + ([] if platform == "macos" else [
        # the app takes its content from Contents/Resources first, so this does not apply to it
        "  or extract this package into the ET:QW install directory, next to its base/,",
        "  and start the client from there.",
    ]) + [
        "",
        f"Configs, logs and the shader cache go to {saves}.",
        "",
        "The engine renders with Vulkan and falls back to OpenGL when Vulkan cannot",
        "start. It compiles its shaders on first use with the bundled glslangValidator.",
        "",
        f"Licences: {'openETQW.app/Contents/Resources/licenses/' if platform == 'macos' else 'licenses/'}"
        " (the engine is GPL-3.0-or-later; the ET:QW SDK code it",
        "links is under the ET:QW SDK EULA, which accompanies every copy).",
        "",
        "Built from:",
    ] + [f"  {name} {value}" for name, value in sorted(sources.items())]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\r\n" if platform == "windows" else "\n")


def check_staged(install: Path, platform: str, arch: str) -> None:
    if arch == "universal2":
        if platform != "macos":
            raise PackageError("universal2 is a macOS architecture")
        required = ["openETQW-client_universal2", "openETQW-ded_universal2", "game_arm64.dylib", "game_x64.dylib"]
    else:
        exe = ".exe" if platform == "windows" else ""
        module = {"linux": ".so", "windows": ".dll", "macos": ".dylib"}[platform]
        required = [f"openETQW-client_{arch}{exe}", f"openETQW-ded_{arch}{exe}", f"game_{arch}{module}"]
    required += ["base/vkprogs", "licenses/openETQW/LICENSE", "licenses/ETQW-SDK/EULA_SDK_ETQW.txt"]
    missing = [r for r in required if not (install / r).exists()]
    if missing:
        raise PackageError(f"the staged tree {install} lacks {', '.join(missing)} (run meson install first)")
    if (install / "base" / "vkprogs" / "cache").exists():
        raise PackageError("the staged tree has a shader cache (base/vkprogs/cache); stage from a clean install")


# ---------------------------------------------------------------- Linux

def linux_needed_library(binary: Path, soname: str) -> Path:
    for line in run(["ldd", str(binary)], capture=True).splitlines():
        parts = line.split()
        if parts and parts[0] == soname and "=>" in parts and parts[2] != "not":
            return Path(parts[2])
    raise PackageError(f"{binary.name} does not resolve {soname} (is LD_LIBRARY_PATH set to the SDL3 prefix?)")


def split_debug(binary: Path, debug_root: Path, objcopy: str) -> None:
    debug_file = debug_root / f"{binary.name}.debug"
    run([objcopy, "--only-keep-debug", str(binary), str(debug_file)])
    run([objcopy, "--strip-debug", "--strip-unneeded", str(binary)])
    # the link names the file only; debuggers look beside the binary and in .debug/
    run([objcopy, f"--add-gnu-debuglink={debug_file}", str(binary)])
    os.chmod(debug_file, 0o644)


def package_linux(args, install: Path, work: Path, licenses: dict[str, Path], sources: dict[str, str]) -> dict[str, str]:
    arch = args.arch
    top = f"{ASSET_PREFIX}-{args.version_tag}-linux-{arch}"
    package = work / top
    debug = work / f"{top}-debug"
    debug.mkdir(parents=True)
    copy_tree(install, package)
    client = package / f"openETQW-client_{arch}"
    runpath = run(["readelf", "-dW", str(client)], capture=True)
    if "$ORIGIN" not in runpath:
        raise PackageError(f"{client.name} has no $ORIGIN RUNPATH, so it would not find the bundled SDL3 (meson.build install_rpath)")
    sdl3 = args.linux_sdl3 or linux_needed_library(install / client.name, "libSDL3.so.0")
    copy_file(sdl3, package / "libSDL3.so.0", 0o644)
    copy_file(args.glslang_dir / "glslangValidator", package / "glslangValidator", 0o755)
    add_licenses(package, package / "licenses", "linux", licenses)
    write_readme(package / "README.txt", "linux", arch, args.version_tag, sources)
    objcopy = args.objcopy or shutil.which("objcopy") or "objcopy"
    for binary in [client, package / f"openETQW-ded_{arch}", package / f"game_{arch}.so"]:
        split_debug(binary, debug, objcopy)
    archive = args.output_dir / f"{top}.tar.xz"
    debug_archive = args.output_dir / f"{top}-debug.tar.xz"
    write_tar(package, top, archive, args.source_date_epoch, "xz")
    write_tar(debug, f"{top}-debug", debug_archive, args.source_date_epoch, "xz")
    return {"archive": str(archive), "debug_archive": str(debug_archive), "package_dir": str(package)}


# ---------------------------------------------------------------- Windows

def package_windows(args, install: Path, work: Path, licenses: dict[str, Path], sources: dict[str, str]) -> dict[str, str]:
    arch = args.arch
    top = f"{ASSET_PREFIX}-{args.version_tag}-windows-{arch}"
    package = work / top
    copy_tree(install, package)
    for dll in ("SDL3.dll", "z.dll"):
        if not (package / dll).is_file():
            raise PackageError(f"the staged tree lacks {dll} (meson.build copies it from the dependency prefix)")
    copy_file(args.glslang_dir / "glslangValidator.exe", package / "glslangValidator.exe")
    if args.windows_crt_dir:
        crt = sorted(args.windows_crt_dir.glob("*.dll"))
        if not crt:
            raise PackageError(f"no DLLs in --windows-crt-dir {args.windows_crt_dir}")
        for dll in crt:
            copy_file(dll, package / dll.name)
        log(f"bundled the MSVC runtime app-locally: {', '.join(d.name for d in crt)}")
    add_licenses(package, package / "licenses", "windows", licenses)
    write_readme(package / "README.txt", "windows", arch, args.version_tag, sources)
    for pdb in args.extra_pdb:  # the game module's, which meson install does not stage
        copy_file(pdb, package / pdb.name)
    archive = args.output_dir / f"{top}.zip"
    pdb_archive = args.output_dir / f"{top}-pdb.zip"
    write_zip(package, top, archive, args.source_date_epoch, select=lambda rel: rel.suffix.lower() != ".pdb")
    pdbs = write_zip(package, f"{top}-pdb", pdb_archive, args.source_date_epoch, select=lambda rel: rel.suffix.lower() == ".pdb")
    for pdb in package.rglob("*.pdb"):
        pdb.unlink()  # the installer source is the zip's content
    outputs = {"archive": str(archive), "package_dir": str(package)}
    if pdbs:
        outputs["pdb_archive"] = str(pdb_archive)
    else:
        pdb_archive.unlink()
    return outputs


# ---------------------------------------------------------------- macOS

SYSTEM_PREFIXES = ("/usr/lib/", "/System/")


def _otool_entries(output: str) -> list[str]:
    # "<file>:" or, for a universal binary, "<file> (architecture arm64):" heads each slice
    return [line.strip() for line in output.splitlines() if line.strip() and not line.rstrip().endswith(":")]


def macho_dependencies(binary: Path) -> list[str]:
    deps = {line.split(" (compatibility")[0] for line in _otool_entries(run(["otool", "-L", str(binary)], capture=True))}
    # a dylib lists its own install name among them
    ident = set(_otool_entries(run(["otool", "-D", str(binary)], capture=True)))
    return sorted(deps - ident)


def macho_rpaths(binary: Path) -> list[str]:
    rpaths, lines = [], run(["otool", "-l", str(binary)], capture=True).splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "cmd LC_RPATH":
            for follow in lines[index + 1:index + 4]:
                follow = follow.strip()
                if follow.startswith("path "):
                    rpaths.append(follow.split()[1])
    return rpaths


def resolve_dependency(reference: str, referrer: Path, referrer_source: Path, lib_dirs: list[Path]) -> Path | None:
    if reference.startswith(SYSTEM_PREFIXES):
        return None
    if reference.startswith("@rpath/"):
        leaf = reference[len("@rpath/"):]
        for rpath in macho_rpaths(referrer_source):
            base = rpath.replace("@loader_path", str(referrer_source.parent)).replace("@executable_path", str(referrer_source.parent))
            if (Path(base) / leaf).is_file():
                return (Path(base) / leaf).resolve()
        # `meson install` drops the build tree's rpaths, so the staged engine
        # names @rpath libraries it has no LC_RPATH for: --macos-lib-dir
        for directory in lib_dirs:
            if (directory / leaf).is_file():
                return (directory / leaf).resolve()
        raise PackageError(f"{referrer.name}: cannot resolve {reference} through its rpaths or --macos-lib-dir")
    if reference.startswith(("@loader_path/", "@executable_path/")):
        return None  # already bundle-relative
    if not Path(reference).is_file():
        raise PackageError(f"{referrer.name} links {reference}, which does not exist on this host")
    return Path(reference).resolve()


def bundle_macos_dependencies(images: list[Path], frameworks: Path, lib_dirs: list[Path]) -> list[str]:
    """Copy every non-system library the images need into Frameworks and point them at @rpath."""
    bundled: dict[str, Path] = {}
    queue = [(image, image) for image in images]  # (bundled path, where its references resolve)
    while queue:
        image, source = queue.pop()
        for reference in macho_dependencies(image):
            if reference.startswith(SYSTEM_PREFIXES) or reference.startswith(("@loader_path/", "@executable_path/")):
                continue
            leaf = Path(reference).name
            target = frameworks / leaf
            if leaf not in bundled and not target.is_file():
                resolved = resolve_dependency(reference, image, source, lib_dirs)
                copy_file(resolved, target, 0o755)
                run(["install_name_tool", "-id", f"@rpath/{leaf}", str(target)])
                bundled[leaf] = resolved
                queue.append((target, resolved))
            if reference != f"@rpath/{leaf}":
                run(["install_name_tool", "-change", reference, f"@rpath/{leaf}", str(image)])
    return sorted(bundled)


def version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def check_bundle(app: Path) -> None:
    """Every Mach-O in the app resolves its libraries inside it and runs on MACOS_MIN_OS."""
    frameworks = app / "Contents" / "Frameworks"
    images = sorted(p for d in (app / "Contents" / "MacOS", frameworks) for p in d.iterdir() if p.is_file())
    problems = []
    for image in images:
        for reference in macho_dependencies(image):
            if reference.startswith(SYSTEM_PREFIXES):
                continue
            if reference.startswith("@rpath/"):
                if not (frameworks / reference[len("@rpath/"):]).is_file():
                    problems.append(f"{image.name} needs {reference}, which is not in Contents/Frameworks")
            elif reference.startswith(("@executable_path/", "@loader_path/")):
                base = app / "Contents" / "MacOS" if reference.startswith("@executable_path/") else image.parent
                if not (base / reference.split("/", 1)[1]).resolve().is_file():
                    problems.append(f"{image.name} needs {reference}, which is not in the app")
            else:
                problems.append(f"{image.name} needs {reference}, outside the app")
        commands = run(["otool", "-arch", "all", "-l", str(image)], capture=True)
        minos = {line.split()[1] for line in commands.splitlines() if line.strip().startswith("minos ")}
        if not minos or any(version_tuple(v) > version_tuple(MACOS_MIN_OS) for v in minos):
            problems.append(f"{image.name} needs macOS {sorted(minos) or 'unknown'}, above {MACOS_MIN_OS}")
    if problems:
        raise PackageError("the app bundle is not self-contained:\n  " + "\n  ".join(problems))


def add_rpath(binary: Path, rpath: str) -> None:
    if rpath not in macho_rpaths(binary):
        run(["install_name_tool", "-add_rpath", rpath, str(binary)])


def codesign(path: Path, identity: str, *, hardened: bool) -> None:
    command = ["codesign", "--force", "--sign", identity]
    if identity != "-":
        command += ["--timestamp"]
        if hardened:
            command += ["--options", "runtime"]
    else:
        command += ["--timestamp=none"]
    run(command + [str(path)])


def package_macos(args, install: Path, work: Path, licenses: dict[str, Path], sources: dict[str, str]) -> dict[str, str]:
    arch = args.arch
    top = f"{ASSET_PREFIX}-{args.version_tag}-macos-{arch}"
    package = work / top
    app = package / APP_NAME
    macos_dir, frameworks, resources = app / "Contents" / "MacOS", app / "Contents" / "Frameworks", app / "Contents" / "Resources"
    for directory in (macos_dir, frameworks, resources):
        directory.mkdir(parents=True)

    client = f"openETQW-client_{arch}"
    executables = [macos_dir / client, macos_dir / f"openETQW-ded_{arch}"]
    for exe in executables:
        copy_file(install / exe.name, exe, 0o755)
    modules = sorted(install.glob("game_*.dylib"))
    for module in modules:
        copy_file(module, frameworks / module.name, 0o755)
    copy_tree(install / "base", resources / "base")
    copy_tree(install / "licenses", resources / "licenses")
    copy_file(args.glslang_dir / "glslangValidator", macos_dir / "glslangValidator", 0o755)

    for exe in executables:
        add_rpath(exe, "@executable_path/../Frameworks")
    images = executables + [frameworks / m.name for m in modules]
    lib_dirs = [d.resolve() for d in args.macos_lib_dir]
    bundled = bundle_macos_dependencies(images, frameworks, lib_dirs)
    log(f"bundled libraries: {', '.join(bundled) or 'none'}")
    # the build machine's library directories must not be searched on a user's Mac
    for image in images + [frameworks / name for name in bundled]:
        for rpath in sorted(set(macho_rpaths(image))):  # a universal binary lists each slice's
            if not rpath.startswith(("@executable_path", "@loader_path")):
                run(["install_name_tool", "-delete_rpath", rpath, str(image)])
    if not any(name.startswith("libvulkan") for name in bundled):
        raise PackageError("the client does not link the Vulkan loader; nothing would load MoltenVK")

    # MoltenVK is a loader driver: the Vulkan loader finds it through the bundle's ICD manifest
    if not args.macos_moltenvk:
        raise PackageError("--macos-moltenvk is required (libMoltenVK.dylib)")
    copy_file(args.macos_moltenvk, frameworks / "libMoltenVK.dylib", 0o755)
    run(["install_name_tool", "-id", "@rpath/libMoltenVK.dylib", str(frameworks / "libMoltenVK.dylib")])
    icd = resources / "vulkan" / "icd.d" / "MoltenVK_icd.json"
    icd.parent.mkdir(parents=True)
    icd.write_text(json.dumps({
        "file_format_version": "1.0.0",
        "ICD": {
            "library_path": "../../../Frameworks/libMoltenVK.dylib",
            "api_version": "1.3.0",
            "is_portability_driver": True,
        },
    }, indent=4) + "\n", encoding="utf-8")
    for extra in bundle_macos_dependencies([frameworks / "libMoltenVK.dylib"], frameworks, lib_dirs):
        log(f"MoltenVK needs {extra}")
    check_bundle(app)

    with open(app / "Contents" / "Info.plist", "wb") as plist:
        plistlib.dump({
            "CFBundleDevelopmentRegion": "en",
            "CFBundleExecutable": client,
            "CFBundleIdentifier": BUNDLE_ID,
            "CFBundleInfoDictionaryVersion": "6.0",
            "CFBundleName": PRODUCT,
            "CFBundleDisplayName": PRODUCT,
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": args.version_tag.split("-")[0],
            "CFBundleVersion": args.version_tag,
            "LSMinimumSystemVersion": MACOS_MIN_OS,
            "LSApplicationCategoryType": "public.app-category.action-games",
            "NSHighResolutionCapable": True,
            "OpenETQWRuntimeLayout": "self-contained-v1",
        }, plist, sort_keys=True)
    (app / "Contents" / "PkgInfo").write_text("APPL????", encoding="ascii")

    add_licenses(package, resources / "licenses", "macos", licenses)
    write_readme(package / "README.txt", "macos", arch, args.version_tag, sources)

    # sign inside out: libraries, helper executables, then the bundle (whose main executable it signs)
    identity = args.macos_sign_identity
    for path in sorted(frameworks.iterdir()) + [macos_dir / "glslangValidator", macos_dir / f"openETQW-ded_{arch}"]:
        codesign(path, identity, hardened=True)
    codesign(app, identity, hardened=True)
    run(["codesign", "--verify", "--strict", "--deep", "--verbose=2", str(app)])

    outputs = {"package_dir": str(package), "app": str(app)}
    if args.macos_dmg:
        dmg = args.output_dir / f"{top}.dmg"
        staging = work / "dmg"
        shutil.copytree(package, staging, symlinks=True)
        os.symlink("/Applications", staging / "Applications")
        run(["hdiutil", "create", "-volname", f"{PRODUCT} {args.version_tag}", "-srcfolder", str(staging),
             "-fs", "HFS+", "-format", "UDZO", "-ov", str(dmg)])
        if identity != "-":
            codesign(dmg, identity, hardened=False)
        outputs["archive"] = str(dmg)
    else:
        archive = args.output_dir / f"{top}-unsigned.tar.gz"
        write_tar(package, top, archive, args.source_date_epoch, "gz")
        outputs["archive"] = str(archive)
    return outputs


# ---------------------------------------------------------------- main

def parse_license(value: str) -> tuple[str, Path]:
    name, sep, path = value.partition("=")
    if not sep or not name or not path:
        raise argparse.ArgumentTypeError(f"expected NAME=FILE, got {value!r}")
    return name, Path(path)


def git_head(path: Path) -> str:
    completed = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", choices=("linux", "windows", "macos"), required=True)
    parser.add_argument("--arch", choices=("x64", "arm64", "universal2"), required=True)
    parser.add_argument("--install-dir", type=Path, required=True, help="the tree meson install staged")
    parser.add_argument("--version-tag", required=True, help="release version without the v, e.g. 0.1.0")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, default=None, help="where the package tree is assembled (default <output-dir>/work)")
    parser.add_argument("--glslang-dir", type=Path, required=True, help="tools/build/build_glslang.py output")
    parser.add_argument("--license", action="append", type=parse_license, default=[], help="NAME=FILE for a bundled binary")
    parser.add_argument("--source-date-epoch", type=int, default=int(os.environ.get("SOURCE_DATE_EPOCH", "0") or 0))
    parser.add_argument("--game-repo", type=Path, default=None, help="for the README's source list")
    parser.add_argument("--linux-sdl3", type=Path, default=None, help="libSDL3.so.0 to bundle (default: what the client resolves)")
    parser.add_argument("--objcopy", default=None)
    parser.add_argument("--windows-crt-dir", type=Path, default=None, help="Microsoft.VC14x.CRT directory to bundle app-locally")
    parser.add_argument("--extra-pdb", type=Path, action="append", default=[], help="Windows: another PDB for the -pdb.zip")
    parser.add_argument("--macos-moltenvk", type=Path, default=None, help="libMoltenVK.dylib to bundle")
    parser.add_argument("--macos-lib-dir", type=Path, action="append", default=[],
                        help="where @rpath libraries the staged engine names are found (prepare_macos_deps.py's <prefix>/lib)")
    parser.add_argument("--macos-sign-identity", default="-", help="codesign identity (default '-': ad hoc)")
    parser.add_argument("--macos-dmg", action="store_true", help="make a DMG instead of the unsigned tar.gz")
    args = parser.parse_args(argv)
    if not TAG_RE.match(args.version_tag):
        parser.error(f"--version-tag must look like 1.2.3 or 1.2.3-rc.1, got {args.version_tag!r}")
    if args.arch == "universal2" and args.platform != "macos":
        parser.error("universal2 is a macOS architecture")
    host = {"linux": "linux", "darwin": "macos", "win32": "windows"}.get(sys.platform)
    if host != args.platform:
        parser.error(f"a {args.platform} package is made on a {args.platform} host (this is {sys.platform})")

    install = args.install_dir.resolve()
    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    work = (args.work_dir or args.output_dir / "work").resolve()
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    licenses = dict(args.license)
    root = Path(__file__).resolve().parents[2]
    sources = {PRODUCT: git_head(root)}
    game = args.game_repo or Path(os.environ.get("OPENETQW_GAME_REPO", "") or root.parent / "openETQW-game")
    sources["openETQW-game"] = git_head(game)

    try:
        check_staged(install, args.platform, args.arch)
        packager = {"linux": package_linux, "windows": package_windows, "macos": package_macos}[args.platform]
        outputs = packager(args, install, work, licenses, sources)
    except PackageError as error:
        print(f"package_release: FAILED: {error}", file=sys.stderr)
        return 1
    lines = [f"{key}={value}" for key, value in sorted(outputs.items())]
    print("\n".join(lines))
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
