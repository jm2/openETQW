#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build an openETQW Windows installer from a packaged release directory.

Adapted from openQ4's tools/build/build_windows_installer.py at jm2/openQ4
b37b1456 (GPL-3.0; docs/provenance.md): the same payload, symlink and output
checks, Inno Setup template rendering and ISCC discovery, for the openETQW
package layout. The template is openETQWInstaller.iss.in next to this script;
the setup icon is the ET:QW icon, assets/icons/etqw.ico (decision 0004), unless
--setup-icon names another, and the output file name is given
(--output-basename) instead of being derived from a version tag.

--render-only writes the .iss and stops, so the rendering can be tested
without Inno Setup (for example on Linux).
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


PRODUCT_NAME = "openETQW"
PRODUCT_PUBLISHER = "openETQW contributors"
TEMPLATE_PATH = Path(__file__).resolve().parent / "openETQWInstaller.iss.in"
SETUP_ICON_PATH = Path(__file__).resolve().parents[2] / "assets" / "icons" / "etqw.ico"
LICENSE_RELATIVE = Path("licenses") / "openETQW" / "LICENSE"
SUPPORTED_ARCHES = ("x64", "arm64")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){2,3}$")
FILENAME_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

ARCHITECTURE_DIRECTIVES = {
    "x64": (
        "ArchitecturesAllowed=x64compatible",
        "ArchitecturesInstallIn64BitMode=x64compatible",
    ),
    "arm64": (
        "ArchitecturesAllowed=arm64",
        "ArchitecturesInstallIn64BitMode=arm64",
    ),
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"Compile an Inno Setup installer from a packaged {PRODUCT_NAME} Windows release directory."
    )
    parser.add_argument("--package-dir", required=True, help="Packaged Windows release directory to embed.")
    parser.add_argument("--version", required=True, help="Dotted numeric release version, for example 0.1.0.")
    parser.add_argument("--arch", default="x64", choices=SUPPORTED_ARCHES, help="Target architecture (default: x64).")
    parser.add_argument("--output-dir", required=True, help="Directory for the installer and the generated .iss.")
    parser.add_argument(
        "--output-basename",
        required=True,
        help="Installer file name without .exe, for example openetqw-0.1.0-windows-x64-setup.",
    )
    parser.add_argument("--iscc", default=None, help="Optional explicit path to ISCC.exe.")
    parser.add_argument(
        "--setup-icon",
        default=None,
        help="The .ico for the installer executable (default: assets/icons/etqw.ico).",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="Only write the generated .iss file; do not run ISCC.exe.",
    )
    return parser.parse_args(argv[1:])


def inno_string_literal(value: Path | str) -> str:
    return '"' + inno_string_contents(value) + '"'


def inno_string_contents(value: Path | str) -> str:
    return str(value).replace('"', '""')


def validate_version(value: str) -> str:
    normalized = value.strip()
    if VERSION_RE.fullmatch(normalized) is None:
        raise ValueError("version must be a dotted numeric release version such as 0.1.0")
    return normalized


def validate_filename_token(value: str, label: str) -> str:
    normalized = value.strip()
    if FILENAME_TOKEN_RE.fullmatch(normalized) is None:
        raise ValueError(f"{label} must be a file-name-safe token without slashes, spaces, or quotes")
    return normalized


def require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise FileNotFoundError(f"{label} must not be a symlink: {path}")
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")


def require_directory_arg(path: Path, label: str, *, must_exist: bool) -> Path:
    if path.is_symlink():
        raise FileNotFoundError(f"{label} must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise FileNotFoundError(f"{label} exists but is not a directory: {path}")
    if must_exist and not path.is_dir():
        raise FileNotFoundError(f"{label} not found: {path}")
    return path.resolve()


def validate_output_paths(output_dir: Path, script_path: Path, installer_path: Path) -> None:
    if output_dir.is_symlink():
        raise RuntimeError(f"installer output directory must not be a symlink: {output_dir}")
    if output_dir.exists() and not output_dir.is_dir():
        raise RuntimeError(f"installer output path exists but is not a directory: {output_dir}")
    for path in (script_path, installer_path):
        if path.is_symlink():
            raise RuntimeError(f"refusing to write installer output through symlink: {path}")
        if path.exists() and not path.is_file():
            raise RuntimeError(f"installer output path exists but is not a file: {path}")


def validate_package_dir(package_dir: Path, arch: str) -> None:
    required_files = [
        package_dir / f"{PRODUCT_NAME}-client_{arch}.exe",
        package_dir / f"{PRODUCT_NAME}-ded_{arch}.exe",
        package_dir / f"game_{arch}.dll",
        package_dir / "SDL3.dll",
        package_dir / "z.dll",
        package_dir / "glslangValidator.exe",
        package_dir / "README.txt",
        package_dir / LICENSE_RELATIVE,
    ]
    missing = [path for path in required_files if not path.is_file() and not path.is_symlink()]
    vkprogs = package_dir / "base" / "vkprogs"
    if vkprogs.is_symlink() or not vkprogs.is_dir() or not any(p.is_file() for p in vkprogs.rglob("*")):
        missing.append(vkprogs)
    if missing:
        joined = "\n".join(f"  - {path}" for path in missing)
        raise FileNotFoundError(f"required package payload files are missing:\n{joined}")
    symlinks = [path for path in required_files if path.is_symlink()]
    if symlinks:
        joined = "\n".join(f"  - {path}" for path in symlinks)
        raise FileNotFoundError(f"required package payload files must not be symlinks:\n{joined}")


def resolve_iscc_path(explicit_path: str | None) -> Path:
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    env_path = os.environ.get("INNO_SETUP_ISCC")
    if env_path:
        candidates.append(Path(env_path))
    # machine-wide installs, then a per-user one ("Install for me only", or
    # winget --scope user), which Inno Setup puts in $env:LOCALAPPDATA\Programs;
    # both come before an ISCC found on PATH
    bases = [os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")]
    local_app_data = os.environ.get("LOCALAPPDATA")
    bases.append(str(Path(local_app_data) / "Programs") if local_app_data else None)
    for base in bases:
        if base:
            candidates.append(Path(base) / "Inno Setup 7" / "ISCC.exe")
            candidates.append(Path(base) / "Inno Setup 6" / "ISCC.exe")
    for program in ("ISCC.exe", "iscc"):
        resolved = shutil.which(program)
        if resolved:
            candidates.append(Path(resolved))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("ISCC.exe was not found. Install Inno Setup or pass --iscc explicitly.")


def render_installer_script(
    template_text: str,
    *,
    package_dir: Path,
    output_dir: Path,
    output_basename: str,
    version: str,
    arch: str,
    setup_icon_file: Path | None,
) -> str:
    arch_allowed, arch_install_mode = ARCHITECTURE_DIRECTIVES[arch]
    setup_icon_directive = f"SetupIconFile={inno_string_literal(setup_icon_file)}" if setup_icon_file else ""
    replacements = {
        "@@APP_ARCH@@": arch,
        "@@APP_VERSION@@": version,
        "@@PACKAGE_SOURCE@@": inno_string_contents(package_dir),
        "@@LICENSE_FILE@@": inno_string_literal(package_dir / LICENSE_RELATIVE),
        "@@OUTPUT_DIR@@": inno_string_literal(output_dir),
        "@@OUTPUT_BASENAME@@": output_basename,
        "@@SETUP_ICON_DIRECTIVE@@": setup_icon_directive,
        "@@ARCH_ALLOWED_DIRECTIVE@@": arch_allowed,
        "@@ARCH_INSTALL_MODE_DIRECTIVE@@": arch_install_mode,
    }
    rendered = template_text
    for token, value in replacements.items():
        rendered = rendered.replace(token, value)
    return rendered


def compile_installer(iscc_path: Path, script_path: Path) -> None:
    completed = subprocess.run([str(iscc_path), "/Qp", str(script_path)], check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"ISCC.exe failed with exit code {completed.returncode}")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        version = validate_version(args.version)
        output_basename = validate_filename_token(args.output_basename, "output basename")
        package_dir = require_directory_arg(Path(args.package_dir), "package directory", must_exist=True)
        output_dir = require_directory_arg(Path(args.output_dir), "installer output directory", must_exist=False)
        validate_package_dir(package_dir, args.arch)
        require_regular_file(TEMPLATE_PATH, "installer template")
        setup_icon_file = Path(args.setup_icon) if args.setup_icon else SETUP_ICON_PATH
        require_regular_file(setup_icon_file, "installer icon")
        setup_icon_file = setup_icon_file.resolve()

        script_path = output_dir / f"{output_basename}.iss"
        installer_path = output_dir / f"{output_basename}.exe"
        validate_output_paths(output_dir, script_path, installer_path)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    script_text = render_installer_script(
        TEMPLATE_PATH.read_text(encoding="utf-8"),
        package_dir=package_dir,
        output_dir=output_dir,
        output_basename=output_basename,
        version=version,
        arch=args.arch,
        setup_icon_file=setup_icon_file,
    )
    script_path.write_text(script_text, encoding="utf-8", newline="\n")

    if args.render_only:
        print(f"Generated installer script: {script_path}")
        print(f"Expected installer path: {installer_path}")
        return 0

    try:
        iscc_path = resolve_iscc_path(args.iscc)
        compile_installer(iscc_path, script_path)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if not installer_path.is_file():
        print(f"error: expected installer executable not found: {installer_path}", file=sys.stderr)
        return 1

    print(f"Built {PRODUCT_NAME} Windows installer {version} ({args.arch})")
    print(f"Installer: {installer_path}")
    print(f"Installer script: {script_path}")
    print(f"Compiler: {iscc_path}")
    print(f"Publisher: {PRODUCT_PUBLISHER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
