#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Asset-free unit test for the packaging and validation tools.

Exercises, in temporary directories only:
  tools/validation/openetqw_validate.py  the staged-payload layout, vkprogs,
                                         hygiene and Windows DLL checks; the
                                         profiles' dry run
  tools/build/package_release.py         reproducible tar/zip archives, the
                                         staged-tree precondition, the licence
                                         requirement for bundled binaries
  tools/build/package_linux_appimage.py  the AppRun it writes (shell syntax,
                                         the retail-data lookup)

Plain Python. Exit 0 when every check passes, 1 otherwise.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
FAILURES: list[str] = []
CHECKS = 0


def load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def check(condition: bool, label: str) -> None:
    global CHECKS
    CHECKS += 1
    if not condition:
        FAILURES.append(label)
        print(f"FAIL {label}")


def tracked_vkprogs() -> list[str]:
    return subprocess.run(["git", "-C", str(ROOT), "ls-files", "base/vkprogs"],
                          capture_output=True, text=True, check=True).stdout.split()


def make_staged_tree(root: Path, system: str, arch: str, validate: ModuleType) -> None:
    root.mkdir(parents=True)
    for name in validate.expected_executables(arch, system).values():
        (root / name).write_bytes(b"binary")
    (root / validate.module_name(arch, system)).write_bytes(b"module")
    for rel in tracked_vkprogs():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes((ROOT / rel).read_bytes())
    for rel in validate.REQUIRED_LICENSES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("licence\n", encoding="utf-8")
    if system == "windows":
        for dll in validate.WINDOWS_RUNTIME_DLLS:
            (root / dll).write_bytes(b"dll")


def failures_of(validate: ModuleType, root: Path, system: str, arch: str, client: bool = True) -> list[str]:
    failures: list[str] = []
    validate.check_layout(root, arch, system, client, failures)
    validate.check_vkprogs(root, failures)
    validate.check_tree_hygiene(root, failures)
    if system == "windows":
        validate.check_windows_payload(root, client, failures)
    return failures


def test_validator(tmp: Path) -> None:
    validate = load(ROOT / "tools" / "validation" / "openetqw_validate.py", "openetqw_validate")
    for system, arch in (("linux", "x64"), ("windows", "arm64"), ("darwin", "arm64")):
        tree = tmp / f"stage-{system}-{arch}"
        make_staged_tree(tree, system, arch, validate)
        check(failures_of(validate, tree, system, arch) == [], f"a complete {system} {arch} tree passes")

    tree = tmp / "stage-linux-x64"
    (tree / "game_arm64.so").write_bytes(b"other module")
    check(any("exactly one game module" in f for f in failures_of(validate, tree, "linux", "x64")),
          "a second game module is rejected")
    (tree / "game_arm64.so").unlink()

    cache = tree / "base" / "vkprogs" / "cache" / "x.spv"
    cache.parent.mkdir()
    cache.write_bytes(b"spirv")
    check(any("untracked" in f for f in failures_of(validate, tree, "linux", "x64")),
          "a shader cache in base/vkprogs is rejected")
    cache.unlink()
    cache.parent.rmdir()

    first = tracked_vkprogs()[0]
    saved = (tree / first).read_bytes()
    (tree / first).unlink()
    check(any("missing" in f and "tracked" in f for f in failures_of(validate, tree, "linux", "x64")),
          "a missing vkprogs file is rejected")
    (tree / first).write_bytes(saved)

    licence = tree / validate.REQUIRED_LICENSES[0]
    licence.unlink()
    check(any("licence" in f for f in failures_of(validate, tree, "linux", "x64")), "a missing licence text is rejected")
    licence.write_text("licence\n", encoding="utf-8")

    try:
        (tree / "link").symlink_to("openETQW-ded_x64")
    except OSError:
        pass  # Windows without the symlink privilege
    else:
        check(any("symlink" in f for f in failures_of(validate, tree, "linux", "x64")), "a symlink is rejected")
        (tree / "link").unlink()

    pe = tmp / "pe"
    pe.mkdir()
    for arch, machine in (("x64", 0x8664), ("arm64", 0xAA64)):
        header = bytearray(512)
        header[0:2] = b"MZ"
        header[0x3C:0x40] = (0x80).to_bytes(4, "little")
        header[0x80:0x84] = b"PE\0\0"
        header[0x84:0x86] = machine.to_bytes(2, "little")
        (pe / f"{arch}.dll").write_bytes(bytes(header))
    failures: list[str] = []
    validate.check_windows_binary(pe / "x64.dll", "x64", failures)
    check(failures == [], "an x64 PE passes the x64 check")
    validate.check_windows_binary(pe / "arm64.dll", "x64", failures)
    check(len(failures) == 1 and "0xaa64" in failures[0], "an ARM64 PE fails the x64 check")
    (pe / "text.dll").write_bytes(b"not a PE")
    failures = []
    validate.check_windows_binary(pe / "text.dll", "arm64", failures)
    check(len(failures) == 1, "a file that is not a PE fails")

    # macOS and Windows file systems are usually case-insensitive: two such names are one file
    (tree / "README.txt").write_text("a", encoding="utf-8")
    if not (tree / "readme.TXT").exists():
        (tree / "readme.TXT").write_text("b", encoding="utf-8")
        check(any("case" in f for f in failures_of(validate, tree, "linux", "x64")), "names differing only in case are rejected")
        (tree / "readme.TXT").unlink()
    (tree / "README.txt").unlink()

    (tree / "openETQW-client_x64").unlink()
    check(any("client" in f for f in failures_of(validate, tree, "linux", "x64")), "a missing client is rejected")
    check(failures_of(validate, tree, "linux", "x64", client=False) == [], "--no-client accepts a tree without the client")

    windows = tmp / "stage-windows-arm64"
    (windows / "SDL3.dll").unlink()
    check(any("SDL3.dll" in f for f in failures_of(validate, windows, "windows", "arm64")), "a missing SDL3.dll is rejected")
    (windows / "openETQW-client_arm64.exe").unlink()
    check(failures_of(validate, windows, "windows", "arm64", client=False) == [],
          "a dedicated-only Windows tree needs no SDL3.dll")

    fake_game = tmp / "game"
    fake_game.mkdir()
    (fake_game / "meson.build").write_text("", encoding="utf-8")
    for profile in ("push", "pr"):
        completed = subprocess.run([sys.executable, str(ROOT / "tools" / "validation" / "openetqw_validate.py"), profile,
                                    "--dry-run", "--game-repo", str(fake_game), "--build-dir", str(tmp / "b")],
                                   capture_output=True, text=True, env={k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"})
        check(completed.returncode == 0 and "PASSED" in completed.stdout, f"the {profile} profile dry run passes")
        check("-Dinstall_root=" in completed.stdout and "dedicated_server_smoke.py" in completed.stdout,
              f"the {profile} dry run installs and smokes the staged tree")
    completed = subprocess.run([sys.executable, str(ROOT / "tools" / "validation" / "openetqw_validate.py"), "push",
                                "--dry-run", "--game-repo", str(tmp / "nowhere")], capture_output=True, text=True)
    check(completed.returncode == 2, "a missing game checkout is a usage error")
    completed = subprocess.run([sys.executable, str(ROOT / "tools" / "validation" / "openetqw_validate.py"), "push",
                                "--dry-run", "--game-repo", str(fake_game), "--cross-file", "x.ini"],
                               capture_output=True, text=True)
    check(completed.returncode == 2, "--cross-file without --arch is a usage error")


def test_package_release(tmp: Path) -> None:
    package = load(ROOT / "tools" / "build" / "package_release.py", "package_release")
    tree = tmp / "pkg"
    (tree / "base" / "vkprogs").mkdir(parents=True)
    (tree / "base" / "vkprogs" / "a.glsl").write_text("void main() {}\n", encoding="utf-8")
    (tree / "run").write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(tree / "run", 0o755)
    (tree / "notes.txt").write_text("x\n", encoding="utf-8")
    os.chmod(tree / "notes.txt", 0o600)

    first, second = tmp / "a.tar.xz", tmp / "b.tar.xz"
    package.write_tar(tree, "top", first, 1700000000, "xz")
    os.utime(tree / "notes.txt", (1, 1))
    package.write_tar(tree, "top", second, 1700000000, "xz")
    check(first.read_bytes() == second.read_bytes(), "tar.xz archives are reproducible")
    with tarfile.open(first) as archive:
        members = {m.name: m for m in archive.getmembers()}
    check(sorted(members) == ["top", "top/base", "top/base/vkprogs", "top/base/vkprogs/a.glsl", "top/notes.txt", "top/run"],
          "tar members sit under one top directory")
    if os.name != "nt":  # Windows files carry no executable bit
        check(members["top/run"].mode == 0o755 and members["top/notes.txt"].mode == 0o644,
              "tar keeps the executable bit, normalizes the rest")
    check(all(m.uid == 0 and m.gid == 0 and m.mtime == 1700000000 for m in members.values()), "tar owners and times are normalized")

    gz = tmp / "c.tar.gz"
    package.write_tar(tree, "top", gz, 1700000000, "gz")
    check(tarfile.open(gz).getnames()[0] == "top", "tar.gz archives open")

    zip_a, zip_b = tmp / "a.zip", tmp / "b.zip"
    count = package.write_zip(tree, "top", zip_a, 1700000000, select=lambda rel: rel.suffix != ".txt")
    package.write_zip(tree, "top", zip_b, 1700000000, select=lambda rel: rel.suffix != ".txt")
    check(zip_a.read_bytes() == zip_b.read_bytes(), "zip archives are reproducible")
    check(count == 2 and sorted(zipfile.ZipFile(zip_a).namelist()) == ["top/base/vkprogs/a.glsl", "top/run"],
          "zip selection filters members")

    staged = tmp / "staged"
    staged.mkdir()
    try:
        package.check_staged(staged, "linux", "x64")
        check(False, "an empty staged tree is rejected")
    except package.PackageError:
        check(True, "an empty staged tree is rejected")
    for name in ("openETQW-client_x64", "openETQW-ded_x64", "game_x64.so"):
        (staged / name).write_bytes(b"x")
    (staged / "base" / "vkprogs" / "cache").mkdir(parents=True)
    for rel in ("licenses/openETQW/LICENSE", "licenses/ETQW-SDK/EULA_SDK_ETQW.txt"):
        (staged / rel).parent.mkdir(parents=True, exist_ok=True)
        (staged / rel).write_text("l", encoding="utf-8")
    try:
        package.check_staged(staged, "linux", "x64")
        check(False, "a staged shader cache is rejected")
    except package.PackageError as error:
        check("cache" in str(error), "a staged shader cache is rejected")
    (staged / "base" / "vkprogs" / "cache").rmdir()
    try:
        package.check_staged(staged, "linux", "x64")
        check(True, "a complete staged tree is accepted")
    except package.PackageError:
        check(False, "a complete staged tree is accepted")

    try:
        package.add_licenses(tmp / "out", tmp / "out" / "licenses", "windows", {"glslang": tree / "notes.txt"})
        check(False, "bundling without every licence text fails")
    except package.PackageError as error:
        check("SDL3" in str(error) and "zlib" in str(error), "bundling without every licence text fails")
    completed = subprocess.run([sys.executable, str(ROOT / "tools" / "build" / "package_release.py"), "--platform", "linux",
                                "--arch", "x64", "--install-dir", str(staged), "--version-tag", "1.2", "--output-dir",
                                str(tmp / "o"), "--glslang-dir", str(tmp)], capture_output=True, text=True)
    check(completed.returncode == 2, "a malformed version tag is a usage error")


def test_apprun(tmp: Path) -> None:
    appimage = load(ROOT / "tools" / "build" / "package_linux_appimage.py", "package_linux_appimage")
    appdir = tmp / "AppDir"
    root = appdir / "usr" / "share" / "openetqw"
    (root / "base").mkdir(parents=True)
    client = root / "openETQW-client_x64"
    client.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n', encoding="utf-8")
    os.chmod(client, 0o755)
    apprun = appdir / "AppRun"
    apprun.write_text(appimage.render_apprun("x64"), encoding="utf-8")
    os.chmod(apprun, 0o755)
    check(subprocess.run(["sh", "-n", str(apprun)]).returncode == 0, "AppRun is valid shell")

    data = tmp / "install"
    (data / appimage.RETAIL_MARKER).parent.mkdir(parents=True)
    (data / appimage.RETAIL_MARKER).write_bytes(b"pak")
    env = {"PATH": os.environ.get("PATH", ""), "APPDIR": str(appdir)}
    found = subprocess.run([str(apprun), "+map", "valley"], capture_output=True, text=True, env=dict(env, OWD=str(data)))
    check(found.stdout.split() == ["+set", "fs_basepath", str(data), "+map", "valley"],
          "AppRun passes the retail data it finds in the start directory")
    given = subprocess.run([str(apprun), "+set", "fs_basepath", "/x"], capture_output=True, text=True, env=dict(env, OWD=str(data)))
    check(given.stdout.split() == ["+set", "fs_basepath", "/x"], "AppRun keeps an explicit fs_basepath")
    lookalike = subprocess.run([str(apprun), "+map", "my_fs_basepath_map"], capture_output=True, text=True,
                               env=dict(env, OWD=str(data)))
    check(lookalike.stdout.split() == ["+set", "fs_basepath", str(data), "+map", "my_fs_basepath_map"],
          "AppRun still finds the data when an argument only contains fs_basepath")
    none = subprocess.run([str(apprun), "+quit"], capture_output=True, text=True, env=dict(env, OWD=str(tmp), APPIMAGE=str(tmp / "a")))
    check(none.stdout.split() == ["+quit"], "AppRun adds nothing without retail data")


def main() -> int:
    if subprocess.run(["git", "-C", str(ROOT), "rev-parse"], capture_output=True).returncode != 0:
        print("SKIP: not a git checkout")
        return 77
    with tempfile.TemporaryDirectory(prefix="openetqw-packaging-test-") as temporary:
        tmp = Path(temporary)
        test_validator(tmp)
        test_package_release(tmp)
        if os.name != "nt":  # the AppImage's AppRun is a POSIX shell script
            test_apprun(tmp)
    if FAILURES:
        print(f"packaging tools: {len(FAILURES)} of {CHECKS} checks failed")
        return 1
    print(f"packaging tools: all {CHECKS} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
