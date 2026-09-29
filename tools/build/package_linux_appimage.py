#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build and check the openETQW Linux AppImage from a packaged Linux tree.

Adapted from openQ4's tools/build/package_linux_appimage.py (jm2/openQ4
b37b1456): a type-2 AppImage made with a pinned, checksum-verified
appimagetool, whose own runtime prefix is carved out and passed back with
--runtime-file so nothing is fetched at build time. openETQW needs no
linuxdeploy: the package tree tools/build/package_release.py makes already
bundles the one library a desktop may lack (SDL3) behind a $ORIGIN RUNPATH;
Vulkan, OpenGL, zlib and the C/C++ runtimes are the system's.

AppDir:
  AppRun                       starts the client; when no fs_basepath is given
                               and the directory it was started from, or the one
                               holding the AppImage, has the retail data
                               (base/pak000.pk4), it passes +set fs_basepath there
  openetqw.desktop, openetqw.svg, .DirIcon
  usr/share/openetqw/          the package tree, unchanged

Checks, before and after packing (the image is extracted again): the AppDir
layout and desktop entry; the client and dedicated server resolve every
library and symbol (ldd -r) with the loader environment cleared; the packed
file is a type-2 AppImage of the right ELF machine.

Usage:
  package_linux_appimage.py --package-dir DIR --arch {x64,arm64} --version-tag TAG
      --output FILE (--appimagetool FILE | --download-appimagetool DIR)
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "tools" / "build" / "linux"
APPIMAGETOOL_VERSION = "1.9.1"
APPIMAGETOOL_SHA256 = {
    "x86_64": "ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0",
    "aarch64": "f0837e7448a0c1e4e650a93bb3e85802546e60654ef287576f46c71c126a9158",
}
APPIMAGE_ARCH = {"x64": "x86_64", "arm64": "aarch64"}
ELF_MACHINE = {"x64": "X86-64", "arm64": "AArch64"}
TYPE2_MAGIC = b"AI\x02"
# a directory holding this is an ET:QW install (the first retail pak)
RETAIL_MARKER = "base/pak000.pk4"


class AppImageError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(command, cwd=str(cwd), env=env, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AppImageError(f"{command[0]} failed ({completed.returncode}):\n{completed.stdout}{completed.stderr}")
    return completed.stdout


def render_apprun(arch: str) -> str:
    return f"""#!/bin/sh
# openETQW AppImage entry point (tools/build/package_linux_appimage.py)
set -eu
appdir="${{APPDIR:-$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)}}"
package_root="${{appdir}}/usr/share/openetqw"
client="${{package_root}}/openETQW-client_{arch}"
if [ ! -x "${{client}}" ] || [ ! -d "${{package_root}}/base" ]; then
    echo "openETQW AppImage payload is incomplete" >&2
    exit 127
fi

# the retail data: the directory this was started from, or the one holding the AppImage
case " $* " in
    *fs_basepath*) exec "${{client}}" "$@" ;;
esac
for candidate in "${{OWD:-$PWD}}" "$(dirname -- "${{APPIMAGE:-$0}}")"; do
    if [ -f "${{candidate}}/{RETAIL_MARKER}" ]; then
        exec "${{client}}" +set fs_basepath "${{candidate}}" "$@"
    fi
done
exec "${{client}}" "$@"
"""


def sanitized_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LD_", "APPIMAGE", "APPDIR", "OWD"))}
    env["LC_ALL"] = "C"
    return env


def check_resolution(package_root: Path, arch: str) -> None:
    for name in (f"openETQW-client_{arch}", f"openETQW-ded_{arch}"):
        completed = subprocess.run(["ldd", "-r", str(package_root / name)], capture_output=True, text=True,
                                   env=sanitized_env(), cwd=str(package_root))
        output = completed.stdout + completed.stderr
        if completed.returncode != 0 or re.search(r"not found|undefined symbol|symbol lookup error", output):
            raise AppImageError(f"{name} does not resolve its libraries inside the AppDir:\n{output}")
        if name.startswith("openETQW-client") and str(package_root / "libSDL3.so.0") not in output:
            raise AppImageError(f"{name} resolves SDL3 outside the AppDir:\n{output}")


def check_appdir(appdir: Path, arch: str) -> None:
    for required in ("AppRun", "openetqw.desktop", "openetqw.svg", ".DirIcon",
                     f"usr/share/openetqw/openETQW-client_{arch}", "usr/share/openetqw/base/vkprogs",
                     "usr/share/openetqw/glslangValidator", "usr/share/openetqw/libSDL3.so.0",
                     "usr/share/openetqw/licenses/openETQW/LICENSE"):
        if not (appdir / required).exists():
            raise AppImageError(f"the AppDir lacks {required}")
    if not os.access(appdir / "AppRun", os.X_OK):
        raise AppImageError("AppRun is not executable")
    for path in appdir.rglob("*"):
        if path.is_symlink() and path.relative_to(appdir).as_posix() != ".DirIcon":
            raise AppImageError(f"unexpected symlink in the AppDir: {path.relative_to(appdir)}")
    check_resolution(appdir / "usr" / "share" / "openetqw", arch)


def carve_runtime(appimagetool: Path, target: Path, work: Path) -> None:
    """appimagetool is itself a type-2 AppImage: its runtime is the ELF before its payload."""
    env = dict(sanitized_env(), APPIMAGE_EXTRACT_AND_RUN="1")
    offset = int(run([str(appimagetool), "--appimage-offset"], cwd=work, env=env).strip())
    data = appimagetool.read_bytes()
    if not 0 < offset < len(data) or data[:4] != b"\x7fELF" or data[8:11] != TYPE2_MAGIC:
        raise AppImageError(f"{appimagetool} does not start with a type-2 AppImage runtime (offset {offset})")
    target.write_bytes(data[:offset])


def download_appimagetool(directory: Path, arch: str) -> Path:
    name = f"appimagetool-{APPIMAGE_ARCH[arch]}.AppImage"
    target = directory / name
    if not target.is_file():
        directory.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/AppImage/appimagetool/releases/download/{APPIMAGETOOL_VERSION}/{name}"
        print(f"downloading {url}", flush=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            target.write_bytes(response.read())
    if sha256(target) != APPIMAGETOOL_SHA256[APPIMAGE_ARCH[arch]]:
        target.unlink()
        raise AppImageError(f"{name} does not match its pinned SHA-256")
    os.chmod(target, 0o755)
    return target


def build(args: argparse.Namespace) -> Path:
    package = args.package_dir.resolve()
    arch = args.arch
    if not (package / f"openETQW-client_{arch}").is_file():
        raise AppImageError(f"{package} is not a packaged Linux {arch} tree")
    appimagetool = args.appimagetool or download_appimagetool(args.download_appimagetool, arch)
    if sha256(appimagetool) != APPIMAGETOOL_SHA256[APPIMAGE_ARCH[arch]]:
        raise AppImageError(f"{appimagetool} is not the pinned appimagetool {APPIMAGETOOL_VERSION} for {arch}")
    output = args.output.resolve()
    with tempfile.TemporaryDirectory(prefix="openetqw-appimage-") as temporary:
        work = Path(temporary)
        appdir = work / "AppDir"
        shutil.copytree(package, appdir / "usr" / "share" / "openetqw", symlinks=True)
        (appdir / "AppRun").write_text(render_apprun(arch), encoding="utf-8")
        os.chmod(appdir / "AppRun", 0o755)
        shutil.copy2(ASSETS / "openetqw.desktop", appdir / "openetqw.desktop")
        shutil.copy2(ASSETS / "openetqw.svg", appdir / "openetqw.svg")
        (appdir / ".DirIcon").symlink_to("openetqw.svg")
        if shutil.which("desktop-file-validate"):
            run(["desktop-file-validate", str(appdir / "openetqw.desktop")], cwd=work)
        check_appdir(appdir, arch)

        runtime = work / "runtime"
        carve_runtime(appimagetool, runtime, work)
        env = dict(sanitized_env(), APPIMAGE_EXTRACT_AND_RUN="1", ARCH=APPIMAGE_ARCH[arch],
                   SOURCE_DATE_EPOCH=str(args.source_date_epoch))
        output.parent.mkdir(parents=True, exist_ok=True)
        run([str(appimagetool), "--no-appstream", "--comp", "zstd", "--runtime-file", str(runtime),
             str(appdir), str(output)], cwd=work, env=env)
        os.chmod(output, 0o755)

        header = output.read_bytes()[:16]
        if header[:4] != b"\x7fELF" or header[8:11] != TYPE2_MAGIC:
            raise AppImageError(f"{output} is not a type-2 AppImage")
        machine = run(["readelf", "-h", str(output)], cwd=work)
        if ELF_MACHINE[arch] not in machine:
            raise AppImageError(f"{output} runtime is not {ELF_MACHINE[arch]}")
        extract = work / "extract"
        extract.mkdir()
        run([str(output), "--appimage-extract"], cwd=extract, env=sanitized_env())
        check_appdir(extract / "squashfs-root", arch)
    return output


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--package-dir", type=Path, required=True, help="package_release.py's Linux package tree")
    parser.add_argument("--arch", choices=sorted(APPIMAGE_ARCH), required=True)
    parser.add_argument("--version-tag", required=True)
    parser.add_argument("--output", type=Path, default=None, help="default <cwd>/openetqw-<tag>-<appimage arch>.AppImage")
    tool = parser.add_mutually_exclusive_group(required=True)
    tool.add_argument("--appimagetool", type=Path, help=f"pinned appimagetool {APPIMAGETOOL_VERSION} AppImage")
    tool.add_argument("--download-appimagetool", type=Path, metavar="DIR", help="download and verify it into DIR")
    parser.add_argument("--source-date-epoch", type=int, default=int(os.environ.get("SOURCE_DATE_EPOCH", "0") or 0))
    args = parser.parse_args(argv)
    if args.output is None:
        args.output = Path.cwd() / f"openetqw-{args.version_tag}-{APPIMAGE_ARCH[args.arch]}.AppImage"
    try:
        output = build(args)
    except (AppImageError, OSError, ValueError) as error:
        print(f"package_linux_appimage: FAILED: {error}", file=sys.stderr)
        return 1
    print(f"appimage={output}")
    print(f"sha256={sha256(output)}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"appimage={output}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
