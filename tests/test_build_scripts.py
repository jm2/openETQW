#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The build helpers (scripts/build-*.sh, scripts/build-windows.ps1) keep their contract.

Each helper runs with --dry-run and the dependency check off, against a stand-in
game repository, so nothing is built or fetched. Checked: conflicting options are
usage errors (exit 2); the game builds before the engine, both with one build type;
an existing build directory is reconfigured and --clean wipes it instead; the engine
gets its -D options; POSIX hosts link the game module; --server and --run pass the
map and ETQW_DATA_PATH; a missing OPENETQW_GAME_REPO fails. docs/build-scripts.md
describes the helpers. On POSIX this runs the host's shell helper, and the Windows
helper too when pwsh is installed; on Windows, the Windows helper. The Windows helper
runs in ConstrainedLanguage mode (as AppLocker or WDAC would run it), so a .NET call
that mode refuses fails here.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures: list[str] = []


def expect(ok: bool, what: str) -> None:
    if not ok:
        failures.append(what)
        print(f"FAIL: {what}")


class Helper:
    def __init__(self, name: str, command: list[str], style: str) -> None:
        self.name = name
        self.command = command
        self.style = style  # "posix" (--flag) or "ps" (-Flag)

    def flag(self, posix: str) -> str:
        if self.style == "posix":
            return posix
        return {
            "--dry-run": "-DryRun", "--no-deps": "-NoDeps", "--test": "-Test", "--run": "-Run",
            "--server": "-Server", "--map": "-Map", "--clean": "-Clean", "--validate": "-Validate",
            "--release": "-BuildType", "--build-dir": "-BuildDir", "--bogus": "-Bogus",
        }[posix]

    def args(self, *posix: str) -> list[str]:
        out: list[str] = []
        for a in posix:
            out.append(self.flag(a) if a.startswith("--") else a)
            if self.style == "ps" and a == "--release":
                out.append("release")
        return out

    def run(self, *posix: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        if self.style == "ps":
            # switches stay bare (a quoted -Flag is a positional string); values are quoted
            quoted = " ".join(a if a.startswith("-") else "'" + a.replace("'", "''") + "'"
                              for a in self.args(*posix))
            script = str(ROOT / "scripts" / "build-windows.ps1").replace("'", "''")
            command = self.command + [
                "$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'; "
                f"& '{script}' {quoted}; exit $LASTEXITCODE"]
        else:
            command = self.command + self.args(*posix)
        return subprocess.run(command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=60)


def steps(output: str) -> list[str]:
    return [line[2:] for line in output.splitlines() if line.startswith("+ ")]


def index_of(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines):
        if needle in line:
            return i
    return -1


def check(helper: Helper, game: Path) -> None:
    tag = helper.name
    env = dict(os.environ, OPENETQW_GAME_REPO=str(game))
    env.pop("ETQW_DATA_PATH", None)
    base = ("--dry-run", "--no-deps")

    for bad in [("--test", "--run"), ("--run", "--server"), ("--map", "outskirts"),
                ("--validate", "--clean"), ("--bogus",)]:
        r = helper.run(*base, *bad, env=env)
        expect(r.returncode == 2, f"{tag}: {' '.join(bad)} is a usage error (exit {r.returncode})")
    if helper.style == "posix":
        r = helper.run(*base, "--debug", "--release", env=env)
        expect(r.returncode == 2, f"{tag}: --debug --release is a usage error")

    r = helper.run(*base, env=env)
    expect(r.returncode == 0, f"{tag}: a dry run succeeds (exit {r.returncode}: {r.stderr.strip()[-300:]})")
    s = steps(r.stdout)
    game_setup = index_of(s, f"setup {game / 'build'}")
    if game_setup < 0:
        game_setup = index_of(s, str(game / "build") + " " + str(game))
    game_compile = index_of(s, f"compile -C {game / 'build'}")
    engine_setup = index_of(s, "-Dbuild_engine=true")
    engine_compile = max((i for i, line in enumerate(s) if "compile -C" in line), default=-1)
    expect(0 <= game_setup < game_compile < engine_setup < engine_compile,
           f"{tag}: game setup, game compile, engine setup, engine compile in order ({s})")
    if engine_setup >= 0:
        e = s[engine_setup]
        expect("-Dbuild_dedicated=true" in e and "-Dbuild_tests=true" in e, f"{tag}: engine gets its -D options")
    expect(sum("--buildtype=debugoptimized" in line for line in s) == 2, f"{tag}: both builds default to debugoptimized")
    if helper.style == "posix":
        link = index_of(s, "ln -sfn")
        expect(link > engine_compile and f"{game / 'build'}/game_" in s[link], f"{tag}: the game module is linked after the build")

    r = helper.run(*base, "--release", env=env)
    expect(sum("--buildtype=release" in line for line in steps(r.stdout)) == 2, f"{tag}: --release reaches both builds")

    with tempfile.TemporaryDirectory() as tmp:
        bd = Path(tmp) / "eb"
        (bd / "meson-private").mkdir(parents=True)
        (bd / "meson-private" / "coredata.dat").write_bytes(b"")
        r = helper.run(*base, "--build-dir", str(bd), env=env)
        s = steps(r.stdout)
        expect(any("--reconfigure" in line and str(bd) in line for line in s), f"{tag}: an existing build directory is reconfigured")
        r = helper.run(*base, "--clean", "--build-dir", str(bd), env=env)
        s = steps(r.stdout)
        wipe = index_of(s, str(bd))
        expect(wipe >= 0 and ("rm -rf" in s[wipe] or "Remove-Item" in s[wipe]), f"{tag}: --clean wipes the build directory first")
        expect(not any("--reconfigure" in line for line in s), f"{tag}: --clean sets up afresh")

    data = str(game / "etqw")
    r = helper.run(*base, "--server", "--map", "outskirts", env=dict(env, ETQW_DATA_PATH=data))
    last = steps(r.stdout)[-1] if steps(r.stdout) else ""
    expect("openETQW-ded_" in last and "+spawnServer outskirts" in last and f"fs_basepath {data}" in last,
           f"{tag}: --server --map starts the dedicated server with the map and data path ({last})")
    r = helper.run(*base, "--server", env=env)
    last = steps(r.stdout)[-1] if steps(r.stdout) else ""
    expect("+spawnServer valley" in last and "fs_basepath" not in last, f"{tag}: --server defaults to valley; no data path without ETQW_DATA_PATH")
    r = helper.run(*base, "--run", "--map", "canyon", env=env)
    last = steps(r.stdout)[-1] if steps(r.stdout) else ""
    expect("openETQW-client_" in last and "+devmap canyon" in last, f"{tag}: --run --map starts a devmap session")

    r = helper.run(*base, env=dict(env, OPENETQW_GAME_REPO=str(game / "missing")))
    expect(r.returncode == 1, f"{tag}: a missing OPENETQW_GAME_REPO fails (exit {r.returncode})")


def main() -> int:
    helpers: list[Helper] = []
    system = platform.system()
    if system == "Windows":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell:
            helpers.append(Helper("build-windows.ps1", [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"], "ps"))
    else:
        script = "build-macos.sh" if system == "Darwin" else "build-linux.sh"
        helpers.append(Helper(script, ["bash", str(ROOT / "scripts" / script)], "posix"))
        pwsh = shutil.which("pwsh")
        if pwsh:
            helpers.append(Helper("build-windows.ps1", [pwsh, "-NoProfile", "-Command"], "ps"))
    if not helpers:
        print("SKIP: no shell to run the helpers")
        return 77

    with tempfile.TemporaryDirectory() as tmp:
        game = Path(tmp).resolve() / "game"
        game.mkdir()
        (game / "meson.build").write_text("project('stand-in')\n")
        for helper in helpers:
            check(helper, game)
            print(f"{helper.name}: checked")

    if failures:
        return 1
    print("build-scripts: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
