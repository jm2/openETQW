#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assetless dedicated-server startup smoke for hosted CI.

Hosted runners never have retail Enemy Territory: Quake Wars data, so this
smoke drives build/openETQW-ded_<arch> against a synthetic base directory that
this script generates itself. No retail content is read, copied or required:
the only files the server can see are the ones written here, and the run
fails if the engine's file-system search path reaches anywhere else.

Each variant runs one server with private fs_basepath / fs_savepath / HOME
directories and a staged copy of the executable (plus the game module, which
the engine's FindDLL looks up next to the executable first, then in the
search path, and on Windows the runtime DLLs beside the executable), drives
the console over stdin, and asserts a ladder of phases:

  engine_banner      version banner printed
  filesystem         "file system initialized."
  synthetic_pak      our generated pk4 was mounted (n/a for 'empty')
  decls              decl manager initialized
  session            session initialized
  game_dll_path      "Loading game DLL: '<the staged module>'"
  game_dll_loaded    GetGameAPI succeeded and game->Init started
  game_initialized   game->Init completed ("game initialized.")
  console_ready      "------------- ETQW initialized -------------"
  stdin_roundtrip    an 'echo' sent on stdin came back
  status             'status' answered
  missing_map_survived  'spawnServer <map that does not exist>' was rejected
                     and the server kept running
  quit_shutdown      'quit' ran the game shutdown
  clean_exit         the process exited with status 0

A variant that expects game->Init to fail (Variant.expect_init_failure) climbs
the ladder up to game_dll_loaded and then asserts a clean fatal init failure:

  game_init_error    the expected game error ("ERROR: ..."), after game_dll_loaded
  fatal_init_error   then the engine's "Error during initialization" (Sys_Error)
  fatal_exit         the process exited with status 1, not by a signal

game_initialized, console_ready and the runtime phases are not applicable
there, and reaching any of them fails the run: the engine went on instead of
stopping.

Variants and what each reaches today:

  stub-decls  generated pk4 holding a marker file and generated decls,
              'ammoDef stroyent {}' and a 'guiGlobals' stringMap with a
              'handle' and a 'float' entry: every phase of the ladder.
              sdPlayerProperties::Init calls idWeapon::GetAmmoType("stroyent"),
              which raises a game error when no such decl exists; with it,
              game->Init completes. sdGlobalProperties::Init registers the
              guiGlobals entries; an optimized game built before
              openETQW-game #17 rejects 'handle' there and fails
              game_initialized (2026-09-30). Measured on macOS arm64 and,
              under Rosetta, x86_64, debug and debugoptimized, openETQW
              2fdb8aa + openETQW-game 09a900f.
  bare        generated pk4 holding only a marker file: game->Init fails
              without the 'stroyent' decl (STROYENT_INIT_FAILURE), so every
              phase up to game_dll_loaded, then the fatal init failure.
  empty       no pk4 at all, an empty base directory: same as 'bare'.

A failed game->Init stops the engine (owner decision, 2026-09-30):
idCommonLocal::LoadGameDLL prints the failure and rethrows, and
idCommonLocal::Init exits through Sys_Error("Error during initialization")
with status 1. Until then the engine logged a warning and carried on without
an initialized game, and 'bare' and 'empty' reached the console. Measured on
Linux x86_64, debug, openETQW-game aa8988c. Beyond these ladders nothing can
be asserted without retail data: loading any map needs .entities, .cm, .aas
and a campaign decl from the retail paks.

With --drive args (the Windows default, where the console may not read
stdin) the commands are passed as +echo/+status/+spawnServer/+quit. The
engine runs them inside idCommonLocal::Init, before its "ETQW initialized"
banner, so console_ready is not applicable in that mode and quit_shutdown is
anchored on the +echo having run.

The run fails on a crash or signal, a hang (no progress before a timeout),
an exit status other than the expected one (0; 1 where game->Init is to
fail), a fatal marker (FATAL ERROR, Sys_Error, sanitizer reports, assertion
failures, ...; only the expected fatal init error is exempt, and only where
game->Init is to fail), any 'ERROR:' line that is not an expected one, or a
search-path entry outside the variant's private directories.

Artifacts per variant (under --output-root/<variant>/): server.log,
report.json and the synthetic base/save directories; a combined summary.json
is written next to them, and a Markdown table goes to $GITHUB_STEP_SUMMARY
when it is set. Exit status: 0 all variants passed, 1 a variant failed,
2 usage or setup error (binary or module missing).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import random
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
READY_MARKER = "------------- ETQW initialized -------------"
SYNC_TOKEN = "OPENETQW_CI_SMOKE_SYNC_"
MISSING_MAP = "openetqw_ci_no_such_map"
SMOKE_PAK = "zz_openetqw_ci_smoke.pk4"
PORT_RANGE = (30400, 30499)

# Generated, test-only content. Nothing here is derived from retail data.
PAK_MARKER_NAME = "openetqw_ci_smoke.txt"
PAK_MARKER_TEXT = "Generated by tools/ci/dedicated_server_smoke.py; contains no retail ETQW data.\n"
STUB_DECL_NAME = "def/openetqw_ci_smoke.def"
STUB_DECL_TEXT = (
    "// Generated by tools/ci/dedicated_server_smoke.py for the assetless CI smoke.\n"
    "// sdPlayerProperties::Init requires an ammo type named 'stroyent'.\n"
    "ammoDef stroyent {\n"
    "}\n"
    "// sdGlobalProperties::Init registers one property per entry. The 'handle'\n"
    "// branch was once removed by clang -O2 (an uninitialized read in the game's\n"
    "// sdPropertyValue), which failed game->Init on the retail data only.\n"
    "stringMap guiGlobals {\n"
    "\t\"ciSmokeHandle\"\t\"handle\"\n"
    "\t\"ciSmokeFloat\"\t\"float\"\n"
    "}\n"
)

STARTUP_PHASES: tuple[tuple[str, str], ...] = (
    ("engine_banner", "engine version banner"),
    ("filesystem", "file system initialized"),
    ("synthetic_pak", "synthetic pk4 mounted"),
    ("decls", "decl manager initialized"),
    ("session", "session initialized"),
    ("game_dll_path", "staged game module loaded"),
    ("game_dll_loaded", "GetGameAPI ok, game->Init entered"),
    ("game_initialized", "game->Init completed"),
    ("console_ready", "common init complete, console ready"),
)
RUNTIME_PHASES: tuple[tuple[str, str], ...] = (
    ("stdin_roundtrip", "stdin echo round trip"),
    ("status", "'status' answered"),
    ("missing_map_survived", "missing map rejected, server alive"),
    ("quit_shutdown", "game shutdown on quit"),
    ("clean_exit", "exit status 0"),
)
ALL_PHASES = tuple(name for name, _ in STARTUP_PHASES + RUNTIME_PHASES)
# Where game->Init is to fail: these replace everything after game_dll_loaded.
INIT_FAILURE_PHASES: tuple[tuple[str, str], ...] = (
    ("game_init_error", "game->Init raised the expected game error"),
    ("fatal_init_error", "then the engine's fatal init error"),
    ("fatal_exit", "exit status 1, not a signal"),
)
# idCommonLocal::Init exits before any of these when game->Init fails.
AFTER_GAME_INIT = ALL_PHASES[ALL_PHASES.index("game_initialized"):]
PHASE_LABELS = dict(STARTUP_PHASES + RUNTIME_PHASES + INIT_FAILURE_PHASES)

LINE_PHASES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("engine_banner", re.compile(r"^ETQW \d+\.\d+")),
    ("filesystem", re.compile(r"^file system initialized\.")),
    ("synthetic_pak", re.compile(r"Loaded pk4 .*" + re.escape(SMOKE_PAK))),
    ("decls", re.compile(r"^----- Initializing Decls -----")),
    ("session", re.compile(r"^session initialized")),
    ("game_dll_loaded", re.compile(r"^-+ Initializing Game -+$")),
    ("game_initialized", re.compile(r"^game initialized\.")),
    ("console_ready", re.compile(r"^" + re.escape(READY_MARKER))),
)
GAME_DLL_LINE = re.compile(r"Loading game DLL: '(?P<path>[^']+)'")
SHUTDOWN_LINE = re.compile(r"^-+ Game Shutdown -+$")
# idCommonLocal::Init's catch: Sys_Error( "Error during initialization" ), which
# prints "Sys_Error: <text>" on POSIX (posix_main.cpp) and the bare text on
# Windows (SystemBootstrap.cpp), both to stderr.
FATAL_INIT_LINE = re.compile(r"^(?:Sys_Error: )?Error during initialization$")

FATAL_MARKERS = (
    "FATAL ERROR",
    "Sys_Error:",
    "FATAL SIGNAL CAUGHT",
    "Error during initialization",
    "couldn't find game dynamic library",
    "couldn't load game dynamic library",
    "couldn't find game DLL API",
    "Wrong Game DLL API version",
    "game DLL returned a NULL game interface",
    "ASSERTION FAILED",
    "Assertion failed",
    "Assertion `",
    "AddressSanitizer",
    "UndefinedBehaviorSanitizer",
    "LeakSanitizer",
    "ThreadSanitizer",
    "MemorySanitizer",
    "runtime error:",
    "Segmentation fault",
    "Bus error",
    "terminate called",
    "libc++abi: terminating",
    "Unhandled exception",
)

WINDOWS_NTSTATUS = {
    0xC0000005: "STATUS_ACCESS_VIOLATION",
    0xC000001D: "STATUS_ILLEGAL_INSTRUCTION",
    0xC000007B: "STATUS_INVALID_IMAGE_FORMAT",
    0xC00000FD: "STATUS_STACK_OVERFLOW",
    0xC0000135: "STATUS_DLL_NOT_FOUND",
    0xC0000139: "STATUS_ENTRYPOINT_NOT_FOUND",
    0xC0000142: "STATUS_DLL_INIT_FAILED",
    0xC0000409: "STATUS_STACK_BUFFER_OVERRUN",
    0x80000003: "STATUS_BREAKPOINT",
}


@dataclass(frozen=True)
class InitFailure:
    """game->Init must fail with a game error matching `error`, and the engine must stop."""
    error: str  # regex searched in the game's "ERROR: ..." line
    why: str


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    pak: str  # "stub-decls" (marker + one decl), "marker" (marker only) or "none" (no pk4)
    module_location: str  # "exe-dir" or "basepath"
    # Set: assert a clean fatal init failure (INIT_FAILURE_PHASES) instead of
    # game_initialized, the console and the runtime phases.
    expect_init_failure: Optional[InitFailure] = None
    allowed_errors: tuple[str, ...] = ()

    def ladder(self) -> tuple[str, ...]:
        """Every phase reported for this variant, in order."""
        if self.expect_init_failure is None:
            return ALL_PHASES
        cut = ALL_PHASES.index("game_initialized")
        return ALL_PHASES[:cut] + tuple(name for name, _ in INIT_FAILURE_PHASES) + ALL_PHASES[cut:]

    def not_applicable(self, drive: str, skip_missing_map: bool = False) -> tuple[str, ...]:
        phases = []
        if self.pak == "none":
            phases.append("synthetic_pak")
        if self.expect_init_failure is not None:
            # idCommonLocal::Init exits before the console, and +commands too.
            phases.extend(AFTER_GAME_INIT)
            return tuple(phases)
        if drive == "args":
            # +commands, +quit included, run inside idCommonLocal::Init before
            # the "ETQW initialized" banner, so that banner never appears.
            phases.append("console_ready")
        if skip_missing_map:
            phases.append("missing_map_survived")
        return tuple(phases)

    def forbidden(self) -> tuple[str, ...]:
        """Phases whose presence fails the run: the engine went on after a failed game->Init."""
        return AFTER_GAME_INIT if self.expect_init_failure is not None else ()

    def required(self, drive: str, skip_missing_map: bool = False) -> tuple[str, ...]:
        skipped = set(self.not_applicable(drive, skip_missing_map)) | set(self.forbidden())
        return tuple(phase for phase in self.ladder() if phase not in skipped)

    def expected_exit(self) -> int:
        return 1 if self.expect_init_failure is not None else 0


STROYENT_ERROR = r"idWeapon::GetAmmoType Invalid Ammo Type stroyent"
STROYENT_INIT_FAILURE = InitFailure(
    error=STROYENT_ERROR,
    why=(
        "game->Init stops at idWeapon::GetAmmoType(\"stroyent\") when no ammoDef decl exists "
        "(openETQW-game src/game/PlayerProperties.cpp sdPlayerProperties::Init -> src/game/Weapon.cpp "
        "idWeapon::GetAmmoType -> gameLocal.Error); idCommonLocal::LoadGameDLL rethrows the error and "
        "idCommonLocal::Init stops the engine with Sys_Error(\"Error during initialization\")"
    ),
)

VARIANTS: dict[str, Variant] = {
    "stub-decls": Variant(
        name="stub-decls",
        description="generated pk4 with stub decls (ammoDef stroyent {}, a guiGlobals stringMap with a handle entry); game module next to the executable",
        pak="stub-decls",
        module_location="exe-dir",
        allowed_errors=(re.escape(MISSING_MAP),),
    ),
    "bare": Variant(
        name="bare",
        description="generated pk4 without any decls; game module found through fs_basepath/base; game->Init fails and the engine stops",
        pak="marker",
        module_location="basepath",
        expect_init_failure=STROYENT_INIT_FAILURE,
    ),
    "empty": Variant(
        name="empty",
        description="no pk4 at all (empty base directory); game module next to the executable; game->Init fails and the engine stops",
        pak="none",
        module_location="exe-dir",
        expect_init_failure=STROYENT_INIT_FAILURE,
    ),
}


class SetupError(Exception):
    pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def host_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64", "x64"):
        return "x64"
    if machine in ("arm64", "aarch64"):
        return "arm64"
    raise SetupError(f"unsupported host architecture: {machine or '<empty>'}")


def module_file_name(arch: str) -> str:
    if sys.platform == "win32":
        return f"game_{arch}.dll"
    if sys.platform == "darwin":
        return f"game_{arch}.dylib"
    return f"game_{arch}.so"


def exe_file_name(arch: str) -> str:
    return f"openETQW-ded_{arch}" + (".exe" if sys.platform == "win32" else "")


def default_game_repo() -> Path:
    raw = os.environ.get("OPENETQW_GAME_REPO", "").strip()
    return Path(raw).expanduser() if raw else ROOT.parent / "openETQW-game"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def udp_port_free(port: int) -> bool:
    for family, host in ((socket.AF_INET, "0.0.0.0"),):
        with socket.socket(family, socket.SOCK_DGRAM) as sock:
            try:
                sock.bind((host, port))
            except OSError:
                return False
    return True


def pick_port(requested: Optional[int]) -> int:
    if requested:
        return requested
    ports = list(range(PORT_RANGE[0], PORT_RANGE[1] + 1))
    random.shuffle(ports)
    for port in ports:
        if udp_port_free(port):
            return port
    raise SetupError(f"no free UDP port in {PORT_RANGE[0]}-{PORT_RANGE[1]}")


def describe_exit(code: Optional[int]) -> str:
    if code is None:
        return "none (killed)"
    if code < 0:
        try:
            return f"killed by signal {signal.Signals(-code).name} ({-code})"
        except ValueError:
            return f"killed by signal {-code}"
    if sys.platform != "win32" and 128 < code < 160:
        try:
            return f"{code} (crash handler: {signal.Signals(code - 128).name})"
        except ValueError:
            return f"{code} (crash handler: signal {code - 128})"
    if sys.platform == "win32" and code & 0xFFFFFFFF >= 0x80000000:
        status = code & 0xFFFFFFFF
        name = WINDOWS_NTSTATUS.get(status)
        return f"0x{status:08X}" + (f" ({name})" if name else "")
    return str(code)


def init_failure_report(variant: Variant) -> Optional[dict]:
    failure = variant.expect_init_failure
    if failure is None:
        return None
    return {"gameError": failure.error, "fatalInitError": FATAL_INIT_LINE.pattern, "exitCode": 1, "why": failure.why}


def same_path(a: str, b: Path) -> bool:
    try:
        left = os.path.normcase(os.path.realpath(a))
        right = os.path.normcase(os.path.realpath(str(b)))
    except OSError:
        return False
    return left == right


def make_synthetic_base(base_root: Path, pak_kind: str) -> Optional[Path]:
    game_dir = base_root / "base"
    game_dir.mkdir(parents=True, exist_ok=False)
    if pak_kind == "none":
        return None
    pak = game_dir / SMOKE_PAK
    with zipfile.ZipFile(pak, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(PAK_MARKER_NAME, PAK_MARKER_TEXT)
        if pak_kind == "stub-decls":
            archive.writestr(STUB_DECL_NAME, STUB_DECL_TEXT)
    return pak


SEARCH_PATH_HEADER = "Current search path:"
SEARCH_PATH_END = re.compile(r"^(game DLL:|Addon pk4s:|file system initialized)")
SEARCH_PATH_SUFFIX = re.compile(r"\s+\(\d+ files\b.*\)$")


def foreign_search_paths(lines: list[tuple[float, str]], allowed_root: Path) -> list[str]:
    """Search-path entries the engine printed that lie outside allowed_root.

    The engine prints its search path after every file-system (re)start. Any
    entry outside the variant's private directories means the server could
    read files this smoke did not generate (for example retail data found
    through a default path), which would make the run unrepresentative."""
    root = os.path.normcase(os.path.realpath(str(allowed_root)))
    foreign: list[str] = []
    inside = False
    for _, text in lines:
        stripped = text.strip()
        if stripped == SEARCH_PATH_HEADER:
            inside = True
            continue
        if not inside:
            continue
        if not stripped or SEARCH_PATH_END.match(stripped):
            inside = False
            continue
        entry = SEARCH_PATH_SUFFIX.sub("", stripped)
        resolved = os.path.normcase(os.path.realpath(entry))
        if resolved != root and not resolved.startswith(root + os.sep):
            foreign.append(entry)
    return foreign


def gha_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def gha(command: str, message: str) -> None:
    if os.environ.get("GITHUB_ACTIONS", "").lower() == "true":
        print(f"::{command}::{gha_escape(message)}", flush=True)
    else:
        print(f"{command.upper()}: {message}", flush=True)


# ---------------------------------------------------------------------------
# server driver
# ---------------------------------------------------------------------------

class Server:
    def __init__(self, command: list[str], cwd: Path, env: dict[str, str], log_path: Path):
        self.command = command
        self.lines: list[tuple[float, str]] = []
        self._cond = threading.Condition()
        self._eof = False
        self._log = log_path.open("w", encoding="utf-8", errors="replace")
        self._log.write("# " + " ".join(command) + "\n")
        self.started = time.monotonic()
        # Own process group/session, so a hang can be killed together with any
        # children that would otherwise keep the output pipe open.
        extra: dict = {}
        if sys.platform == "win32":
            extra["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            extra["start_new_session"] = True
        try:
            self.proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **extra,
            )
        except OSError as exc:
            self._log.write(f"# could not start: {exc}\n")
            self._log.close()
            raise
        self._reader = threading.Thread(target=self._read, name="smoke-reader", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        try:
            for raw in iter(self.proc.stdout.readline, ""):
                stamp = time.monotonic() - self.started
                text = raw.rstrip("\r\n")
                self._log.write(text + "\n")
                self._log.flush()
                with self._cond:
                    self.lines.append((stamp, text))
                    self._cond.notify_all()
        finally:
            with self._cond:
                self._eof = True
                self._cond.notify_all()

    def alive(self) -> bool:
        return self.proc.poll() is None

    def output_closed(self) -> bool:
        with self._cond:
            return self._eof

    def send(self, text: str) -> bool:
        if not self.alive() or self.proc.stdin is None:
            return False
        try:
            self.proc.stdin.write(text + "\n")
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def mark(self) -> int:
        with self._cond:
            return len(self.lines)

    def wait_line(self, predicate, timeout: float, start: int = 0) -> Optional[int]:
        deadline = time.monotonic() + timeout
        index = start
        with self._cond:
            while True:
                while index < len(self.lines):
                    if predicate(self.lines[index][1]):
                        return index
                    index += 1
                if self._eof:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(min(remaining, 0.25))

    def wait_exit(self, timeout: float) -> Optional[int]:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def kill(self) -> None:
        if sys.platform != "win32":
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)  # the whole session
            except (ProcessLookupError, PermissionError, OSError):
                pass
        if self.proc.poll() is None:
            try:
                self.proc.kill()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    def finish(self) -> None:
        self._reader.join(timeout=10)
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except OSError:
            pass
        self._log.close()

    def snapshot(self) -> list[tuple[float, str]]:
        with self._cond:
            return list(self.lines)


# ---------------------------------------------------------------------------
# one variant
# ---------------------------------------------------------------------------

def run_variant(variant: Variant, args: argparse.Namespace, executable: Path, module: Path, port: int) -> dict:
    run_dir = args.output_root / variant.name
    if run_dir.exists():
        shutil.rmtree(run_dir)
    install_dir = run_dir / "install"
    base_root = run_dir / "basepath"
    save_root = run_dir / "savepath"
    home = run_dir / "home"
    for directory in (install_dir, save_root, home):
        directory.mkdir(parents=True)
    pak = make_synthetic_base(base_root, variant.pak)

    staged_exe = install_dir / executable.name
    shutil.copy2(executable, staged_exe)
    staged_dlls = []
    if sys.platform == "win32":
        # Windows loads a DLL from the executable's directory before PATH, and a
        # build or package keeps zlib and SDL3 there: copy them, so the smoke does
        # not depend on vcpkg's bin being on PATH. Never the game module, whose
        # place is the variant's.
        for dll in sorted(executable.parent.glob("*.dll")):
            if dll.resolve() != module.resolve() and not dll.name.lower().startswith("game_"):
                staged_dlls.append(install_dir / dll.name)
                shutil.copy2(dll, staged_dlls[-1])
    if variant.module_location == "exe-dir":
        staged_module = install_dir / module.name
    else:
        staged_module = base_root / "base" / module.name
    shutil.copy2(module, staged_module)

    def remove_staged() -> None:
        # the output root is uploaded as a CI log artifact: no binaries in it
        if args.keep_binaries:
            return
        for path in [staged_exe, staged_module] + staged_dlls:
            try:
                path.unlink()
            except OSError:
                pass

    command = [
        str(staged_exe),
        "+set", "dedicated", "1",
        "+set", "developer", "1",
        "+set", "fs_basepath", str(base_root),
        "+set", "fs_savepath", str(save_root),
        "+set", "fs_game", "base",
        # synthetic data: turn off the retail-install check (tests/test_retail_install_check.py covers it)
        "+set", "fs_validateOfficialPaks", "0",
        "+set", "sys_skipThreadPrio", "1",
        "+set", "net_port", str(port),
    ]
    if args.drive == "args":
        command += ["+echo", f"{SYNC_TOKEN}1", "+status", "+echo", f"{SYNC_TOKEN}2"]
        if not args.skip_missing_map:
            command += ["+spawnServer", MISSING_MAP, "+echo", f"{SYNC_TOKEN}3"]
        command += ["+quit"]

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    if sys.platform == "win32":
        env["USERPROFILE"] = str(home)
        env["APPDATA"] = str(home / "AppData" / "Roaming")
        env["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    env.pop("ETQW_DATA_PATH", None)

    log_path = run_dir / "server.log"
    try:
        server = Server(command, run_dir, env, log_path)
    except OSError as exc:
        # Not startable at all (wrong architecture, missing loader, ...).
        remove_staged()
        return {
            "variant": variant.name,
            "description": variant.description,
            "status": "fail",
            "furthestPhase": "",
            "phases": {phase: None for phase in variant.ladder()},
            "requiredMissing": list(variant.required(args.drive, args.skip_missing_map)),
            "notApplicable": list(variant.not_applicable(args.drive, args.skip_missing_map)),
            "expectInitFailure": init_failure_report(variant),
            "unexpectedPhases": [],
            "expectedExitCode": variant.expected_exit(),
            "exitCode": None,
            "exitStatus": f"did not start: {exc}",
            "hangAt": "",
            "fatalMarkers": [],
            "expectedFatal": [],
            "unexpectedErrors": [],
            "expectedErrors": [],
            "foreignSearchPaths": [],
            "warningLines": 0,
            "gameDllPaths": [],
            "notes": [f"could not start {staged_exe}: {exc}"],
            "elapsedSeconds": 0.0,
            "port": port,
            "drive": args.drive,
            "executable": {"path": str(executable), "sha256": sha256(executable)},
            "gameModule": {"path": str(module), "sha256": sha256(module), "stagedAt": variant.module_location},
            "syntheticPak": str(pak) if pak else None,
            "log": str(log_path),
            "command": command,
        }
    reached: dict[str, float] = {}
    notes: list[str] = []
    timed_out_at = ""

    def sync(step: int, timeout: float) -> Optional[int]:
        token = f"{SYNC_TOKEN}{step}"
        start = server.mark()
        if args.drive == "stdin" and not server.send(f"echo {token}"):
            return None
        return server.wait_line(lambda s: s.strip() == token, timeout, start if args.drive == "stdin" else 0)

    try:
        ready = server.wait_line(lambda s: s.startswith(READY_MARKER), args.startup_timeout)
        if ready is None:
            # A process that is exiting ('+quit' during Init with --drive args, or a
            # fatal init error, never prints the ready marker) closes its output a
            # moment before it can be reaped.
            if server.output_closed():
                server.wait_exit(5.0)
            if server.alive():
                timed_out_at = (
                    "startup (the process neither stopped on a fatal init error nor reached the console)"
                    if variant.expect_init_failure is not None
                    else "startup (console never became ready)"
                )
        elif args.drive == "stdin":
            first = sync(1, args.command_timeout)
            if first is None:
                timed_out_at = timed_out_at or ("" if not server.alive() else "stdin echo")
            else:
                reached["stdin_roundtrip"] = server.snapshot()[first][0]
                start = server.mark()
                server.send("status")
                second = sync(2, args.command_timeout)
                if second is not None:
                    window = [text for _, text in server.snapshot()[start:second]]
                    if any("Dedicated Server Status" in text or text.startswith("ETQW Dedicated") for text in window):
                        reached["status"] = server.snapshot()[second][0]
                    else:
                        notes.append("'status' printed nothing recognisable")
                elif server.alive():
                    timed_out_at = "status"
                if second is not None and not args.skip_missing_map:
                    server.send(f"spawnServer {MISSING_MAP}")
                    third = sync(3, args.command_timeout)
                    if third is not None and server.alive():
                        reached["missing_map_survived"] = server.snapshot()[third][0]
                    elif server.alive():
                        timed_out_at = "spawnServer of a missing map"
                elif args.skip_missing_map:
                    notes.append("missing-map probe skipped (--skip-missing-map)")
            if server.alive():
                server.send("quit")
        exit_code = server.wait_exit(args.quit_timeout if ready is not None else 1.0)
        if exit_code is None:
            if not timed_out_at:
                timed_out_at = "quit (process did not exit)" if ready is not None else "startup"
            server.kill()
            exit_code = None
    finally:
        # Also reaps stray children left in the server's process group.
        server.kill()
        server.finish()
    elapsed = time.monotonic() - server.started
    lines = server.snapshot()

    # args-driven runs: derive runtime phases from the log.
    if args.drive == "args":
        tokens = {text.strip(): stamp for stamp, text in lines}
        if f"{SYNC_TOKEN}1" in tokens:
            reached["stdin_roundtrip"] = tokens[f"{SYNC_TOKEN}1"]
            notes.append("driven by command-line arguments; 'stdin_roundtrip' means the +echo ran")
        if any("Dedicated Server Status" in text for _, text in lines):
            reached["status"] = tokens.get(f"{SYNC_TOKEN}2", 0.0)
        if f"{SYNC_TOKEN}3" in tokens:
            reached["missing_map_survived"] = tokens[f"{SYNC_TOKEN}3"]

    failure = variant.expect_init_failure
    failure_error = re.compile(failure.error) if failure is not None else None
    dll_paths: list[str] = []
    for stamp, text in lines:
        stripped = text.strip()
        for phase, pattern in LINE_PHASES:
            if phase not in reached and pattern.search(stripped):
                reached[phase] = stamp
        match = GAME_DLL_LINE.search(text)
        if match:
            dll_paths.append(match.group("path"))
            if "game_dll_path" not in reached and same_path(match.group("path"), staged_module):
                reached["game_dll_path"] = stamp
        # Only a shutdown after the console answered counts as the one 'quit'
        # caused (a failed init also shuts the game down).
        answered = "console_ready" in reached or (args.drive == "args" and "stdin_roundtrip" in reached)
        if SHUTDOWN_LINE.match(stripped) and "quit_shutdown" not in reached and answered:
            reached["quit_shutdown"] = stamp
        # A failed init, in order: the game's error once game->Init has started,
        # then the engine's fatal init error.
        if failure_error is not None:
            if "game_init_error" not in reached:
                if "game_dll_loaded" in reached and stripped.startswith("ERROR:") and failure_error.search(stripped):
                    reached["game_init_error"] = stamp
            elif "fatal_init_error" not in reached and FATAL_INIT_LINE.match(stripped):
                reached["fatal_init_error"] = stamp
    if dll_paths and "game_dll_path" not in reached:
        notes.append(f"engine loaded a different game module: {dll_paths[0]} (expected {staged_module})")
    if exit_code == 0 and not timed_out_at:
        reached["clean_exit"] = round(elapsed, 3)
    if failure is not None and exit_code == 1 and not timed_out_at:
        reached["fatal_exit"] = round(elapsed, 3)

    allowed = [re.compile(pattern) for pattern in variant.allowed_errors]
    if failure is not None:
        allowed.append(re.compile(failure.error))
    fatal: list[str] = []
    expected_fatal: list[str] = []
    unexpected_errors: list[str] = []
    expected_errors: list[str] = []
    warnings = 0
    for _, text in lines:
        stripped = text.strip()
        if stripped.startswith("WARNING:"):
            warnings += 1
        if failure is not None and FATAL_INIT_LINE.match(stripped):
            # the one fatal line a failed game->Init must produce (fatal_init_error)
            expected_fatal.append(stripped)
        else:
            for marker in FATAL_MARKERS:
                if marker in text:
                    fatal.append(stripped)
                    break
        if stripped.startswith("ERROR:"):
            (expected_errors if any(p.search(stripped) for p in allowed) else unexpected_errors).append(stripped)

    foreign = foreign_search_paths(lines, run_dir)
    missing = [phase for phase in variant.required(args.drive, args.skip_missing_map) if phase not in reached]
    unexpected_phases = [phase for phase in variant.forbidden() if phase in reached]
    if failure is not None and "game_initialized" in reached:
        notes.append(
            "game->Init completed, but this variant expects it to fail with "
            f"'{failure.error}': if the game no longer fails here, update the variant's expect_init_failure"
        )
    elif unexpected_phases:
        notes.append(
            f"the engine went on after the failed game->Init instead of stopping: reached {', '.join(unexpected_phases)}"
        )
    furthest = ""
    for phase in variant.ladder():
        if phase in reached:
            furthest = phase
    passed = (
        not missing
        and not unexpected_phases
        and not fatal
        and not unexpected_errors
        and not foreign
        and not timed_out_at
        and exit_code == variant.expected_exit()
    )

    remove_staged()

    return {
        "variant": variant.name,
        "description": variant.description,
        "status": "pass" if passed else "fail",
        "furthestPhase": furthest,
        "phases": {phase: (round(reached[phase], 3) if phase in reached else None) for phase in variant.ladder()},
        "requiredMissing": missing,
        "notApplicable": list(variant.not_applicable(args.drive, args.skip_missing_map)),
        "expectInitFailure": init_failure_report(variant),
        "unexpectedPhases": unexpected_phases,
        "expectedExitCode": variant.expected_exit(),
        "exitCode": exit_code,
        "exitStatus": describe_exit(exit_code),
        "hangAt": timed_out_at,
        "fatalMarkers": fatal[:20],
        "expectedFatal": expected_fatal[:20],
        "unexpectedErrors": unexpected_errors[:20],
        "expectedErrors": expected_errors[:20],
        "foreignSearchPaths": foreign,
        "warningLines": warnings,
        "gameDllPaths": dll_paths,
        "notes": notes,
        "elapsedSeconds": round(elapsed, 3),
        "port": port,
        "drive": args.drive,
        "executable": {"path": str(executable), "sha256": sha256(executable)},
        "gameModule": {"path": str(module), "sha256": sha256(module), "stagedAt": variant.module_location},
        "syntheticPak": str(pak) if pak else None,
        "log": str(log_path),
        "command": command,
    }


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

def print_report(result: dict) -> None:
    name = result["variant"]
    failure = result["expectInitFailure"]
    print(f"dedicated_server_smoke[{name}]: {result['status'].upper()} "
          f"(furthest phase: {result['furthestPhase'] or '<none>'}, exit {result['exitStatus']}, "
          f"{result['elapsedSeconds']:.1f}s{'; expects game->Init to fail' if failure else ''})")
    for phase, stamp in result["phases"].items():
        if stamp is not None and phase in result["unexpectedPhases"]:
            state = f"UNEXPECTED {stamp:.3f}s"
        elif stamp is not None:
            state = f"ok   {stamp:8.3f}s"
        elif phase in result["notApplicable"]:
            state = "n/a"
        else:
            state = "MISSING"
        print(f"    {phase:22s} {state}  {PHASE_LABELS[phase]}")
    if failure:
        print(f"  expected init failure: {failure['why']}")
    for key, label in (
        ("fatalMarkers", "fatal markers"),
        ("unexpectedErrors", "unexpected ERROR lines"),
        ("foreignSearchPaths", "search-path entries outside the private directories"),
        ("notes", "notes"),
    ):
        if result[key]:
            print(f"  {label}:")
            for item in result[key]:
                print(f"    - {item}")
    if result["hangAt"]:
        print(f"  hang: no progress during {result['hangAt']}")


def tail(path: Path, count: int = 60) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:])
    except OSError:
        return ""


def write_step_summary(results: list[dict]) -> None:
    target = os.environ.get("GITHUB_STEP_SUMMARY", "").strip()
    if not target:
        return
    rows = [
        "### Assetless dedicated-server smoke",
        "",
        "| variant | status | furthest phase | exit | expected |",
        "| --- | --- | --- | --- | --- |",
    ]
    for result in results:
        expected = "fatal game->Init failure, exit 1" if result["expectInitFailure"] else "console and quit, exit 0"
        rows.append(
            f"| {result['variant']} | {result['status'].upper()} | {result['furthestPhase'] or '-'} "
            f"| {result['exitStatus']} | {expected} |"
        )
    with open(target, "a", encoding="utf-8") as handle:
        handle.write("\n".join(rows) + "\n\n")


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build", help="engine build directory")
    parser.add_argument("--executable", type=Path, default=None, help="dedicated server (default <build-dir>/openETQW-ded_<arch>)")
    parser.add_argument("--game-repo", type=Path, default=None, help="openETQW-game checkout (default $OPENETQW_GAME_REPO or ../openETQW-game)")
    parser.add_argument("--game-module", type=Path, default=None, help="game module (default <game-repo>/build/game_<arch>.<ext>)")
    parser.add_argument("--arch", choices=("x64", "arm64"), default=None, help="binary architecture tag (default: host)")
    parser.add_argument("--output-root", type=Path, default=ROOT / ".tmp" / "ci" / "dedicated-smoke")
    parser.add_argument(
        "--variants",
        default=",".join(VARIANTS),
        help=f"comma-separated variants to run (default: {','.join(VARIANTS)})",
    )
    parser.add_argument("--port", type=int, default=None, help=f"UDP port (default: a free one in {PORT_RANGE[0]}-{PORT_RANGE[1]})")
    parser.add_argument(
        "--drive",
        choices=("stdin", "args"),
        default="args" if sys.platform == "win32" else "stdin",
        help="send console commands over stdin (default) or as +command arguments (Windows default)",
    )
    parser.add_argument("--startup-timeout", type=float, default=90.0, help="seconds to reach the console")
    parser.add_argument("--command-timeout", type=float, default=30.0, help="seconds per console command")
    parser.add_argument("--quit-timeout", type=float, default=45.0, help="seconds to exit after 'quit'")
    parser.add_argument("--skip-missing-map", action="store_true", help="do not probe spawnServer of a missing map")
    parser.add_argument("--keep-binaries", action="store_true", help="keep the staged executable, module and (Windows) DLL copies")
    args = parser.parse_args(argv)
    names = [name.strip() for name in args.variants.split(",") if name.strip()]
    unknown = [name for name in names if name not in VARIANTS]
    if unknown or not names:
        parser.error(f"unknown variant(s) {unknown}; choose from {', '.join(VARIANTS)}")
    args.variant_list = [VARIANTS[name] for name in names]
    for option in ("startup_timeout", "command_timeout", "quit_timeout"):
        if getattr(args, option) <= 0:
            parser.error(f"--{option.replace('_', '-')} must be > 0")
    return args


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        arch = args.arch or host_arch()
        executable = (args.executable or args.build_dir / exe_file_name(arch)).resolve()
        game_repo = (args.game_repo or default_game_repo()).resolve()
        module = (args.game_module or game_repo / "build" / module_file_name(arch)).resolve()
        for path, label in ((executable, "dedicated-server executable"), (module, "game module")):
            if not path.is_file():
                raise SetupError(f"{label} not found: {path}")
        if not os.access(executable, os.X_OK):
            raise SetupError(f"dedicated-server executable is not executable: {executable}")
        args.output_root = args.output_root.resolve()
        args.output_root.mkdir(parents=True, exist_ok=True)
    except SetupError as exc:
        gha("error", f"dedicated_server_smoke: {exc}")
        return 2

    print(f"dedicated_server_smoke: {executable} + {module} ({arch}, drive={args.drive})", flush=True)
    results: list[dict] = []
    for variant in args.variant_list:
        try:
            port = pick_port(args.port)
        except SetupError as exc:
            gha("error", f"dedicated_server_smoke: {exc}")
            return 2
        result = run_variant(variant, args, executable, module, port)
        (args.output_root / variant.name / "report.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print_report(result)
        if result["status"] != "pass":
            log_tail = tail(Path(result["log"]))
            if log_tail:
                print(f"  server.log tail ({result['log']}):")
                for line in log_tail.splitlines():
                    print(f"    {line}")
            unexpected = f", reached {result['unexpectedPhases']} after the failed game->Init" if result["unexpectedPhases"] else ""
            gha(
                "error",
                f"dedicated smoke '{variant.name}' failed: furthest phase {result['furthestPhase'] or '<none>'}, "
                f"missing {result['requiredMissing']}{unexpected}, exit {result['exitStatus']} "
                f"(expected {result['expectedExitCode']})",
            )
        results.append(result)

    summary = {
        "status": "pass" if all(result["status"] == "pass" for result in results) else "fail",
        "architecture": arch,
        "platform": sys.platform,
        "executable": str(executable),
        "gameModule": str(module),
        "variants": {result["variant"]: result["status"] for result in results},
    }
    (args.output_root / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    write_step_summary(results)
    print(f"dedicated_server_smoke: {summary['status'].upper()} -> {args.output_root / 'summary.json'}")
    return 0 if summary["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
