#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build, test, stage and check openETQW the way CI and the release do.

The workflows call this; developers run the same thing locally. Modelled on
openQ4's tools/validation/openq4_validate.py (jm2/openQ4 b37b1456; profiles,
staged-payload checks), written for openETQW's two-repository build.

Profiles:
  push  build the game and the engine, unit tests, the integration suite must
        skip without retail data, meson install into <build-dir>/install, the
        staged-payload checks, and the assetless dedicated-server smoke on the
        staged binary
  pr    the same, plus --fail-on-dirty by default

Steps, in order (each can be skipped where it makes sense):
  1. game: meson setup + compile in <game-repo>/build, where the engine looks
     for idLib and the game module, unless --skip-game-build
  2. engine: meson setup (which stages the game headers) + compile of the
     client, the dedicated server and the tests
  3. unit: meson test --suite unit
  4. skips: meson test --suite integration without ETQW_DATA_PATH, every test
     must SKIP (tools/ci/check_meson_testlog.py)
  5. install: meson install into <build-dir>/install, then the staged-payload
     checks (below); the installed game module is then linked (copied on
     Windows) beside the build's binaries, where the retail suite loads it
  6. smoke: tools/ci/dedicated_server_smoke.py on the staged dedicated server
     and game module

Staged-payload checks: the client and dedicated binaries and exactly one game
module for this architecture; base/vkprogs matching the tracked sources; the
licence texts every package carries; no symlinks and no names that differ only
in case; the binaries' architecture; Linux: full RELRO, BIND_NOW, a
non-executable stack and a $ORIGIN RUNPATH on the engine (a package bundles
SDL3 next to it); macOS: the architecture and minimum OS of every Mach-O;
Windows: the runtime DLLs the engine needs next to it, and the PE machine of
every executable and DLL there.

Exit codes: 0 pass, 1 a check failed, 2 usage or environment error.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ci"))
from check_install_licenses import REQUIRED as REQUIRED_LICENSES  # noqa: E402

PROFILES = {
    "push": {"buildtype": "debugoptimized", "fail_on_dirty": False},
    "pr": {"buildtype": "debugoptimized", "fail_on_dirty": True},
}

WINDOWS_RUNTIME_DLLS = ["SDL3.dll", "z.dll"]
MACOS_MIN_OS = "11.0"


class CheckFailed(RuntimeError):
    pass


def host_system() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def host_arch() -> str:
    machine = platform.machine().lower()
    return "arm64" if machine in ("arm64", "aarch64") else "x64"


STEPS: list[str] = []


def section(title: str) -> None:
    STEPS.append(title)
    print(f"\n=== {title} ===", flush=True)


def write_step_summary(heading: str, failure: str | None) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    lines = [f"### {heading}: {'FAILED' if failure else 'passed'}", ""]
    for index, step in enumerate(STEPS):
        failed = failure is not None and index == len(STEPS) - 1
        lines.append(f"- {'FAIL' if failed else 'ok'}: {step}")
    if failure:
        lines += ["", f"`{failure}`"]
    with open(path, "a", encoding="utf-8") as summary:
        summary.write("\n".join(lines) + "\n\n")


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None, dry_run: bool = False) -> None:
    print("+ " + " ".join(f'"{c}"' if " " in c else c for c in command), flush=True)
    if dry_run:
        return
    completed = subprocess.run(command, cwd=str(cwd), env=env)
    if completed.returncode != 0:
        raise CheckFailed(f"command failed with exit {completed.returncode}: {command[0]} ...")


def capture(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(command, cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if completed.returncode != 0:
        raise CheckFailed(f"{command[0]} failed: {completed.stderr.strip() or completed.stdout.strip()}")
    return completed.stdout


def meson_command() -> list[str]:
    meson = shutil.which("meson")
    return [meson] if meson else [sys.executable, "-m", "mesonbuild.mesonmain"]


def is_meson_build_dir(path: Path) -> bool:
    return (path / "meson-private" / "coredata.dat").is_file()


def setup(source: Path, build: Path, options: list[str], *, clean: bool, vsenv: bool, dry_run: bool,
          env: dict[str, str]) -> None:
    command = meson_command() + ["setup", str(build), str(source)] + options
    if vsenv:
        command.append("--vsenv")
    if is_meson_build_dir(build):
        command.append("--wipe" if clean else "--reconfigure")
    # the engine's configure finds the game through OPENETQW_GAME_REPO
    run(command, cwd=source, env=env, dry_run=dry_run)


def game_repo_default() -> Path:
    env = os.environ.get("OPENETQW_GAME_REPO", "").strip()
    return Path(env) if env else ROOT.parent / "openETQW-game"


def expected_executables(arch: str, system: str) -> dict[str, str]:
    suffix = ".exe" if system == "windows" else ""
    return {
        "client": f"openETQW-client_{arch}{suffix}",
        "dedicated": f"openETQW-ded_{arch}{suffix}",
    }


def module_name(arch: str, system: str) -> str:
    return f"game_{arch}" + {"windows": ".dll", "darwin": ".dylib"}.get(system, ".so")


# ---------------------------------------------------------------- payload checks

def check_layout(install: Path, arch: str, system: str, client_expected: bool, failures: list[str]) -> list[Path]:
    binaries = []
    for kind, name in expected_executables(arch, system).items():
        path = install / name
        if path.is_file():
            binaries.append(path)
        elif kind == "dedicated" or client_expected:
            failures.append(f"missing {kind} binary {name}")
    modules = sorted(p.name for p in install.glob("game_*") if p.is_file())
    if modules != [module_name(arch, system)]:
        failures.append(f"expected exactly one game module {module_name(arch, system)}, found {modules or 'none'}")
    else:
        binaries.append(install / modules[0])
    for licence in REQUIRED_LICENSES:
        if not (install / licence).is_file():
            failures.append(f"missing licence text {licence}")
    return binaries


def check_vkprogs(install: Path, failures: list[str]) -> None:
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "base/vkprogs"],
                             capture_output=True, text=True).stdout.split()
    if not tracked:
        failures.append("git ls-files base/vkprogs returned nothing (not a git checkout?)")
        return
    missing = [p for p in tracked if not (install / p).is_file()]
    staged = {p.relative_to(install).as_posix() for p in (install / "base" / "vkprogs").rglob("*") if p.is_file()}
    extra = sorted(staged - set(tracked))
    if missing:
        failures.append(f"base/vkprogs is missing {len(missing)} tracked file(s), e.g. {missing[0]}")
    if extra:
        failures.append(f"base/vkprogs has {len(extra)} untracked file(s), e.g. {extra[0]} (a shader cache?)")


def check_tree_hygiene(install: Path, failures: list[str]) -> None:
    seen: dict[str, str] = {}
    for path in install.rglob("*"):
        rel = path.relative_to(install).as_posix()
        if path.is_symlink():
            failures.append(f"symlink in the staged tree: {rel}")
        key = rel.casefold()
        if key in seen and seen[key] != rel:
            failures.append(f"names differ only in case: {seen[key]} and {rel}")
        seen.setdefault(key, rel)


def check_linux_binary(path: Path, arch: str, failures: list[str]) -> None:
    header = capture(["readelf", "-hW", str(path)])
    machine = "AArch64" if arch == "arm64" else "X86-64"
    if machine not in header:
        failures.append(f"{path.name}: not an {machine} ELF")
    segments = capture(["readelf", "-lW", str(path)])
    dynamic = capture(["readelf", "-dW", str(path)])
    if "GNU_RELRO" not in segments:
        failures.append(f"{path.name}: no GNU_RELRO segment")
    if "BIND_NOW" not in dynamic and "Flags: NOW" not in dynamic:
        failures.append(f"{path.name}: not linked with -z now (partial RELRO)")
    stack = [line for line in segments.splitlines() if "GNU_STACK" in line]
    if not stack or "RWE" in stack[0]:
        failures.append(f"{path.name}: executable stack")
    if path.name.startswith("openETQW-") and "$ORIGIN" not in dynamic:
        failures.append(f"{path.name}: no $ORIGIN RUNPATH (a package bundles SDL3 next to it)")


def check_macos_binary(path: Path, arch: str, failures: list[str]) -> None:
    archs = capture(["lipo", "-archs", str(path)]).split()
    expected = "arm64" if arch == "arm64" else "x86_64"
    if archs != [expected]:
        failures.append(f"{path.name}: architectures {archs}, expected [{expected}]")
    load_commands = capture(["otool", "-l", str(path)])
    minos = [line.split()[-1] for line in load_commands.splitlines() if line.strip().startswith("minos")]
    if not minos or any(m != MACOS_MIN_OS for m in minos):
        failures.append(f"{path.name}: minimum macOS {minos or 'unknown'}, expected {MACOS_MIN_OS}")


PE_MACHINE = {"x64": 0x8664, "arm64": 0xAA64}


def pe_machine(path: Path) -> int | None:
    with open(path, "rb") as handle:
        header = handle.read(4096)
    if header[:2] != b"MZ" or len(header) < 0x40:
        return None
    offset = int.from_bytes(header[0x3C:0x40], "little")
    if offset + 6 > len(header) or header[offset:offset + 4] != b"PE\0\0":
        return None
    return int.from_bytes(header[offset + 4:offset + 6], "little")


def check_windows_binary(path: Path, arch: str, failures: list[str]) -> None:
    machine = pe_machine(path)
    if machine != PE_MACHINE[arch]:
        failures.append(f"{path.name}: PE machine {machine if machine is None else hex(machine)}, expected {hex(PE_MACHINE[arch])} ({arch})")


def check_windows_payload(install: Path, client_expected: bool, failures: list[str]) -> None:
    for dll in WINDOWS_RUNTIME_DLLS:
        if dll == "SDL3.dll" and not client_expected:
            continue
        if not (install / dll).is_file():
            failures.append(f"missing runtime DLL {dll} next to the binaries")


def validate_payload(install: Path, arch: str, system: str, client_expected: bool) -> None:
    failures: list[str] = []
    if not install.is_dir():
        raise CheckFailed(f"no staged tree at {install}")
    binaries = check_layout(install, arch, system, client_expected, failures)
    check_vkprogs(install, failures)
    check_tree_hygiene(install, failures)
    for binary in binaries:
        if system == "linux":
            check_linux_binary(binary, arch, failures)
        elif system == "darwin":
            check_macos_binary(binary, arch, failures)
    if system == "windows":
        check_windows_payload(install, client_expected, failures)
        # every executable and DLL beside the engine, the runtime DLLs included
        for binary in sorted(install.glob("*.exe")) + sorted(install.glob("*.dll")):
            check_windows_binary(binary, arch, failures)
    for failure in failures:
        print(f"  FAIL {failure}")
    if failures:
        raise CheckFailed(f"{len(failures)} staged-payload check(s) failed")
    print(f"  staged payload OK: {len(binaries)} binaries, vkprogs, {len(REQUIRED_LICENSES)} licence texts")


# ---------------------------------------------------------------- main

def stage_game_module(build: Path, install: Path, arch: str, system: str) -> None:
    """put the installed game module beside the build's binaries.

    The retail suite runs the client and server from the build directory, and
    FindDLL tries the executable's directory before the module root and the
    search paths (<fs_basepath>/base among them).  Without a module here a test
    loads whatever module the data directory holds, an older game than the
    PR's (#188).  On Windows the copy is refreshed only by the next validate run.
    """
    name = module_name(arch, system)
    source = install / name
    target = build / name
    try:
        if target.is_symlink() or target.exists():
            target.unlink()
        if system == "windows":
            # symlinks need a privilege on Windows
            shutil.copy2(source, target)
        else:
            target.symlink_to(Path("install") / name)
    except OSError as error:
        raise CheckFailed(f"could not stage the game module at {target}: {error}") from error
    print(f"staged {target} -> {source}")


def check_dirty(root: Path) -> None:
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True, text=True).stdout.strip()
    if status:
        print(status)
        raise CheckFailed("the validation left changes or untracked files in the source tree")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("profile", choices=sorted(PROFILES))
    parser.add_argument("--game-repo", type=Path, default=None, help="openETQW-game checkout (default $OPENETQW_GAME_REPO or ../openETQW-game)")
    parser.add_argument("--build-dir", type=Path, default=None, help="engine build directory (default builddir-validate-<profile>)")
    parser.add_argument("--buildtype", default=None, help="Meson buildtype (profile default: debugoptimized)")
    parser.add_argument("--sanitize", default="", help="Meson b_sanitize for both repositories, e.g. address,undefined")
    parser.add_argument("--cross-file", type=Path, default=None,
                        help="Meson cross file for both repositories; the binaries cannot run, so no tests or smoke")
    parser.add_argument("--arch", choices=("x64", "arm64"), default=None, help="target architecture tag (default: host)")
    parser.add_argument("--meson-option", action="append", default=[], help="extra -D option for the engine (repeatable)")
    parser.add_argument("--clean", action="store_true", help="wipe existing build directories")
    parser.add_argument("--skip-game-build", action="store_true", help="use the game build as it is")
    parser.add_argument("--no-client", action="store_true", help="build the dedicated server only (-Dbuild_engine=false)")
    parser.add_argument("--no-tests", action="store_true", help="skip the unit and integration-skip steps")
    parser.add_argument("--no-install", action="store_true", help="skip meson install and the staged-payload checks")
    parser.add_argument("--no-smoke", action="store_true", help="skip the assetless dedicated-server smoke")
    parser.add_argument("--fail-on-dirty", dest="fail_on_dirty", action="store_true", default=None)
    parser.add_argument("--no-fail-on-dirty", dest="fail_on_dirty", action="store_false")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    args = parser.parse_args(argv)
    defaults = PROFILES[args.profile]
    args.buildtype = args.buildtype or defaults["buildtype"]
    if args.fail_on_dirty is None:
        args.fail_on_dirty = defaults["fail_on_dirty"]
    if args.cross_file:
        if not args.arch:
            parser.error("--cross-file needs --arch")
        args.no_tests = args.no_smoke = True
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    system = host_system()
    arch = args.arch or host_arch()
    game_repo = (args.game_repo or game_repo_default()).resolve()
    if not (game_repo / "meson.build").is_file():
        print(f"openetqw_validate: no openETQW-game checkout at {game_repo} (--game-repo or $OPENETQW_GAME_REPO)", file=sys.stderr)
        return 2
    game_build = game_repo / "build"
    build = (args.build_dir or ROOT / f"builddir-validate-{args.profile}").resolve()
    install = build / "install"
    vsenv = system == "windows"
    common = [f"--buildtype={args.buildtype}"]
    if args.sanitize:
        common += [f"-Db_sanitize={args.sanitize}", "-Db_lundef=false"]
    if args.cross_file:
        common.append(f"--cross-file={args.cross_file.resolve()}")
    env = dict(os.environ, OPENETQW_GAME_REPO=str(game_repo))
    heading = f"openetqw_validate {args.profile} ({system} {arch}, {args.buildtype}{', ' + args.sanitize if args.sanitize else ''})"
    print(f"{heading}: engine {build}; game {game_build}")

    try:
        if not args.skip_game_build:
            section("game: setup and compile")
            setup(game_repo, game_build, common, clean=args.clean, vsenv=vsenv, dry_run=args.dry_run, env=env)
            run(meson_command() + ["compile", "-C", str(game_build)], cwd=game_repo, dry_run=args.dry_run)

        section("engine: setup and compile")
        options = common + [
            f"-Dbuild_engine={'false' if args.no_client else 'true'}",
            "-Dbuild_dedicated=true",
            f"-Dbuild_tests={'false' if args.no_tests else 'true'}",
            f"-Dinstall_root={install}",
        ] + [f"-D{o}" for o in args.meson_option]
        setup(ROOT, build, options, clean=args.clean, vsenv=vsenv, dry_run=args.dry_run, env=env)
        run(meson_command() + ["compile", "-C", str(build)], cwd=ROOT, env=env, dry_run=args.dry_run)

        if not args.no_tests:
            section("unit tests")
            run(meson_command() + ["test", "-C", str(build), "--suite", "unit", "--no-rebuild", "--print-errorlogs"],
                cwd=ROOT, env=env, dry_run=args.dry_run)
            section("the integration suite must skip without retail data")
            skip_env = {k: v for k, v in env.items() if k != "ETQW_DATA_PATH"}
            integration = subprocess.run(
                meson_command() + ["test", "-C", str(build), "--suite", "integration", "--no-rebuild",
                                   "--timeout-multiplier", "0.1", "--logbase", "testlog-integration"],
                cwd=str(ROOT), env=skip_env) if not args.dry_run else None
            run([sys.executable, str(ROOT / "tools" / "ci" / "check_meson_testlog.py"),
                 str(build / "meson-logs" / "testlog-integration.json"), "--expect", "SKIP",
                 "--title", f"Integration suite without retail data ({system} {arch})"],
                cwd=ROOT, dry_run=args.dry_run)
            if integration is not None and integration.returncode != 0:
                raise CheckFailed(f"meson test --suite integration returned {integration.returncode}")

        if not args.no_install:
            section("install and staged-payload checks")
            if install.exists() and not args.dry_run:
                shutil.rmtree(install)
            run(meson_command() + ["install", "-C", str(build), "--no-rebuild", "--quiet"], cwd=ROOT, dry_run=args.dry_run)
            if not args.dry_run:
                validate_payload(install, arch, system, client_expected=not args.no_client)
                stage_game_module(build, install, arch, system)

            if not args.no_smoke:
                section("assetless dedicated-server smoke on the staged tree")
                names = expected_executables(arch, system)
                run([sys.executable, str(ROOT / "tools" / "ci" / "dedicated_server_smoke.py"),
                     "--build-dir", str(build), "--executable", str(install / names["dedicated"]),
                     "--game-repo", str(game_repo), "--game-module", str(install / module_name(arch, system)),
                     "--arch", arch, "--output-root", str(ROOT / ".tmp" / "ci" / "dedicated-smoke")]
                    # an instrumented server starts and quits several times slower
                    + (["--startup-timeout", "240", "--command-timeout", "90", "--quit-timeout", "120"] if args.sanitize else []),
                    cwd=ROOT, env=env, dry_run=args.dry_run)

        if args.fail_on_dirty and not args.dry_run:
            section("source tree clean")
            check_dirty(ROOT)
    except CheckFailed as failure:
        print(f"\n{heading}: FAILED: {failure}", file=sys.stderr)
        write_step_summary(heading, str(failure))
        return 1
    print(f"\n{heading}: PASSED")
    write_step_summary(heading, None)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
