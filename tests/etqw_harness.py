#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for OpenETQW dedicated-server integration tests.

This module is imported by test scripts in tests/ (it is not a test itself).
It provides:

* binary discovery for the dedicated server (build/openETQW-ded_<arch>[.exe],
  overridable with --ded-binary or OPENETQW_DED_BINARY);
* retail-data resolution from ETQW_DATA_PATH only (there is deliberately no
  per-user default: unset, or any of <path>/base/pak000-pak008.pk4 missing,
  means SKIP, 77);
* a retail pak index (pak000..pakNNN, later paks override earlier ones);
* a dedicated-server launcher with a private fs_savepath, a stdout reader
  thread that timestamps every line with time.monotonic(), a stdin writer and
  'echo SYNC_<n>' command synchronisation;
* parsers for the diagnostic lines the engine/game print (PLAYER_POS,
  BOT_THREAD_STATS, spawn lines, OBJECTIVE_STATUS, AAS/nav load lines, ...);
* a log scanner for failure markers.
"""

from __future__ import annotations

import atexit
import getpass
import math
import os
import platform
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_SKIP = 77  # meson 'exitcode' protocol: 77 == SKIP


# Coverage: this decode covers every process started through this module, because
# quit() and kill() both return an ExitStatus.  A test that owns a bare
# subprocess.Popen still formats the raw returncode itself; converting those is
# issue #290, and this module does not claim them.
#
# Windows reports a crash as an NTSTATUS value above 0x7fffffff (Python keeps it
# either signed or unsigned, depending on where the object was built).  Naming
# the common ones keeps a failure message readable without changing the code's
# value: the int subclass below still compares equal to the raw number.
NTSTATUS_NAMES = {
    0xC0000005: "access violation",
    0xC00000FD: "stack overflow",
    0xC0000135: "DLL not found",
    0xC0000409: "stack buffer overrun (fast fail)",
}


def exit_status_text(code: Optional[int]) -> str:
    """Readable text for a process exit code.

    Windows: a status above 0x7fffffff is an NTSTATUS; name the common ones and
    print the code in both forms, e.g. ``-1073741819 (NTSTATUS 0xC0000005:
    access violation)``.  POSIX: a negative code is -signal, as before, with the
    signal name added.  A normal (0 or positive) code is unchanged.
    """
    if code is None:
        return "no exit code (the process never started, or has not exited)"
    value = int(code)
    if value == 0:
        return "0"
    if os.name == "nt":
        unsigned = value & 0xFFFFFFFF
        if unsigned >= 0x80000000:
            detail = f"NTSTATUS 0x{unsigned:08X}"
            name = NTSTATUS_NAMES.get(unsigned)
            if name:
                detail += f": {name}"
            return f"{value} ({detail})"
    elif value < 0:
        sig = -value
        try:
            name = signal.Signals(sig).name
        except ValueError:
            name = None
        detail = f"killed by signal {sig}"
        if name:
            detail += f" ({name})"
        return f"{value} ({detail})"
    return str(value)


class ExitStatus(int):
    """An int exit code whose formatted form explains a Windows crash.

    Tests interpolate ``proc.quit()`` into failure messages.  Formatting this
    subclass uses exit_status_text(), while equality, ordering and arithmetic
    still compare the raw int, so no verdict changes.
    """

    def __new__(cls, code: int, text: Optional[str] = None):
        obj = int.__new__(cls, code)
        obj.text = text if text is not None else exit_status_text(code)
        return obj

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return self.text

    def __format__(self, spec: str) -> str:
        return format(int(self), spec) if spec else self.text


# An engine started without a fs_savepath of its own saves under the per-user
# directory: $XDG_DATA_HOME/openetqw on Linux, $env:LOCALAPPDATA\openETQW on Windows.
# Every process a test starts gets a test directory instead, so no test reads or
# writes the developer's own configs, logs or downloaded paks: under Meson the
# build directory's (OPENETQW_TEST_HOME, set by the default test setup), when a
# script runs by hand one per user in the temporary directory (a shared name
# could be pre-created by another user). Windows always sets LOCALAPPDATA, so
# both are overridden rather than defaulted. macOS saves under
# ~/Library/Application Support, as it always has.
def _user_tag() -> str:
    try:
        return re.sub(r"[^A-Za-z0-9_.-]", "_", getpass.getuser()) or "user"
    except (KeyError, OSError, ImportError):
        return "user"


TEST_USER_HOME = Path(os.environ.get("OPENETQW_TEST_HOME")
                      or Path(tempfile.gettempdir()) / f"openetqw-test-home-{_user_tag()}")
os.environ["XDG_DATA_HOME"] = str(TEST_USER_HOME / "xdg-data")
os.environ["LOCALAPPDATA"] = str(TEST_USER_HOME / "localappdata")

STOCK_MAPS = (
    "area22", "ark", "canyon", "island", "outskirts", "quarry",
    "refinery", "salvage", "sewer", "slipgate", "volcano",
)

READY_MARKER = "------------- ETQW initialized -------------"


class SkipTest(Exception):
    """Raised when a prerequisite (binary, retail data) is missing -> exit 77."""


class PreflightError(RuntimeError):
    """The host is not clean enough to start a fixed-port test (not a skip)."""


# ---------------------------------------------------------------------------
# Process cleanup: every server started through this module is killed on exit,
# including when the test runner terminates us with SIGTERM (meson timeout).
# ---------------------------------------------------------------------------

_LIVE_PROCS: list[subprocess.Popen] = []
_LIVE_PROCS_LOCK = threading.Lock()


def _kill_live_procs() -> None:
    with _LIVE_PROCS_LOCK:
        procs = list(_LIVE_PROCS)
    for p in procs:
        if p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=5.0)
            except Exception:
                pass


atexit.register(_kill_live_procs)


def install_signal_handlers() -> None:
    """Turn SIGTERM/SIGHUP into SystemExit so atexit cleanup runs."""
    def _handler(signum, _frame):
        _kill_live_procs()
        raise SystemExit(128 + signum)

    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                pass


def _udp_port_error(port: int) -> Optional[OSError]:
    """The bind error for UDP port, or None when it is free.

    The engine's net_port is a UDP bind on 0.0.0.0; this is the same probe
    PortAllocator uses, but it returns the error so the message can say why.
    """
    if port <= 0:
        return None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", port))
        return None
    except OSError as exc:
        return exc
    finally:
        sock.close()


def _windows_udp_port_owner(port: int) -> Optional[int]:
    """PID from the IP Helper API's UDP owner table (Windows only)."""
    import ctypes
    from ctypes import wintypes

    class MIB_UDPROW_OWNER_PID(ctypes.Structure):
        _fields_ = [
            ("dwLocalAddr", wintypes.DWORD),
            ("dwLocalPort", wintypes.DWORD),
            ("dwOwningPid", wintypes.DWORD),
        ]

    iphlpapi = ctypes.WinDLL("iphlpapi", use_last_error=True)
    iphlpapi.GetExtendedUdpTable.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
        wintypes.DWORD, ctypes.c_int, wintypes.DWORD,
    ]
    iphlpapi.GetExtendedUdpTable.restype = wintypes.DWORD
    AF_INET, UDP_TABLE_OWNER_PID = 2, 1
    size = wintypes.DWORD(0)
    iphlpapi.GetExtendedUdpTable(None, ctypes.byref(size), False, AF_INET,
                                 UDP_TABLE_OWNER_PID, 0)
    if size.value == 0:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if iphlpapi.GetExtendedUdpTable(buffer, ctypes.byref(size), False, AF_INET,
                                    UDP_TABLE_OWNER_PID, 0) != 0:
        return None
    base = ctypes.addressof(buffer)
    count = ctypes.c_uint32.from_address(base).value
    rows = (MIB_UDPROW_OWNER_PID * count).from_address(base + 4)
    for row in rows:
        # dwLocalPort holds the port in network byte order
        local_port = ((row.dwLocalPort & 0xFF) << 8) | ((row.dwLocalPort >> 8) & 0xFF)
        if local_port == port:
            return int(row.dwOwningPid)
    return None


def _linux_udp_port_owner(port: int) -> Optional[int]:
    """PID from /proc: the UDP socket inodes, then the fds that hold them."""
    inodes: set[str] = set()
    for table in ("/proc/net/udp", "/proc/net/udp6"):
        try:
            with open(table, encoding="ascii", errors="replace") as fh:
                next(fh, None)
                for line in fh:
                    fields = line.split()
                    if len(fields) < 10 or ":" not in fields[1]:
                        continue
                    try:
                        local_port = int(fields[1].rsplit(":", 1)[1], 16)
                    except ValueError:
                        continue
                    if local_port == port:
                        inodes.add(fields[9])
        except OSError:
            continue
    if not inodes:
        return None
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        fd_dir = f"/proc/{name}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(f"{fd_dir}/{fd}")
            except OSError:
                continue
            if target.startswith("socket:[") and target[8:-1] in inodes:
                return int(name)
    return None


def _lsof_udp_port_owner(port: int) -> Optional[int]:
    """PID from lsof, for platforms without /proc (macOS)."""
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iUDP:{port}", "-F", "p"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            return int(line[1:])
    return None


def _udp_port_owner(port: int) -> Optional[int]:
    """PID holding a bound UDP port, or None when the platform cannot say.

    None never switches the check off: check_startup_ready still refuses an
    unattributable port, so this only decides whether the holder is this run.
    """
    if os.name == "nt":
        return _windows_udp_port_owner(port)
    if sys.platform.startswith("linux"):
        return _linux_udp_port_owner(port)
    return _lsof_udp_port_owner(port)


def _live_own_pids() -> set[int]:
    """PIDs of processes this test run started through this module."""
    with _LIVE_PROCS_LOCK:
        procs = list(_LIVE_PROCS)
    pids: set[int] = set()
    for proc in procs:
        try:
            if proc.poll() is None and proc.pid:
                pids.add(proc.pid)
        except Exception:
            pass
    return pids


def _windows_server_processes() -> list[tuple[int, str]]:
    """(pid, image name) of live openETQW-ded processes.

    tasklist is Access denied in the Windows sandbox, so use the kernel's
    toolhelp snapshot.  Returns [] when the snapshot cannot be taken; the port
    check still runs, so a startup is never blocked by a failed listing alone.
    """
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value or not snapshot:
        return []
    found: list[tuple[int, str]] = []
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            name = entry.szExeFile
            if name.lower().startswith("openetqw-ded"):
                found.append((int(entry.th32ProcessID), name))
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def check_startup_ready(port: int) -> None:
    """Refuse to start when a fixed test port or engine process is left over.

    Meson's TerminateProcess on timeout skips the harness cleanup, so a stale
    dedicated server can hold net_port; the engine then cannot bind, but its
    stdin still answers SYNC and the run stalls until a 180 s snapshot timeout.
    Check before spawning instead, and fail with a message that names the port.
    """
    if os.name == "nt":
        own = _live_own_pids()
        leftover = [(pid, name) for pid, name in _windows_server_processes() if pid not in own]
        if leftover:
            listing = ", ".join(f"{name} (pid {pid})" for pid, name in leftover)
            raise PreflightError(
                f"refusing to start on net_port {port}: leftover openETQW-ded "
                f"process is alive: {listing}. Stop it before running the test "
                f"(Meson's TerminateProcess on timeout skips harness cleanup)."
            )
    error = _udp_port_error(port)
    if error is not None:
        # mirror the process check above: a socket this run holds is not a squatter
        owner = _udp_port_owner(port)
        if owner is not None and owner in _live_own_pids():
            return
        holder = (f"pid {owner}" if owner is not None
                  else "a process this platform cannot attribute")
        raise PreflightError(
            f"refusing to start on net_port {port}: UDP port {port} is already "
            f"bound ({error}); it is held by {holder}. A leftover test process "
            f"or another program holds it; free the port before running the test."
        )


# Popen creationflags for a dedicated server that graceful_terminate() should stop
# with CTRL_BREAK_EVENT on Windows (a console control event needs its own process
# group); 0 on POSIX.
CONSOLE_SIGNAL_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def graceful_terminate(proc: subprocess.Popen, console_group: bool = False) -> str:
    """Ask an engine process to shut down cleanly (it must exit 0).

    POSIX sends SIGTERM, which the engine turns into a clean shutdown.
    On Windows there is no SIGTERM (send_signal would TerminateProcess and
    the exit code would not be 0).  A dedicated server started with
    creationflags=CONSOLE_SIGNAL_GROUP (console_group=True) gets
    CTRL_BREAK_EVENT, which it handles like SIGTERM; otherwise, as for the
    GUI client, which has no console, the request goes over stdin (the
    engine reads console input from stdin on every platform).  Returns the
    request that was used."""
    if os.name == "nt" and console_group:
        proc.send_signal(signal.CTRL_BREAK_EVENT)
        return "CTRL_BREAK_EVENT"
    if os.name == "nt":
        try:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            proc.terminate()
            return "terminate (stdin unavailable)"
        return "quit over stdin"
    proc.send_signal(signal.SIGTERM)
    return "SIGTERM"


# The Vulkan client's device-memory allocations, from its shutdown metrics.  Many
# Windows drivers (NVIDIA, Intel) report maxMemoryAllocationCount 4096, so the
# client must stay under that on every host, whatever the local driver allows.
VK_ALLOCATIONS_RE = re.compile(r"Vulkan memory allocations:\s*(\d+) live,\s*(\d+) peak")
PORTABLE_VK_ALLOCATION_LIMIT = 4096


def vk_allocation_peak(output: str) -> Optional[int]:
    """The peak device-memory allocation count the client printed, or None."""
    match = VK_ALLOCATIONS_RE.search(output)
    return int(match.group(2)) if match else None


# ---------------------------------------------------------------------------
# Binary discovery and retail data
# ---------------------------------------------------------------------------

def _arch_suffixes() -> list[str]:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return ["arm64", "x64"]
    if machine in ("x86_64", "amd64", "x64"):
        return ["x64", "arm64"]
    return ["arm64", "x64"]


def binary_candidates(kind: str, build_dir: Optional[Path] = None) -> list[Path]:
    """build/openETQW-<kind>_<arch>[.exe] names for kind 'ded' or 'client',
    host architecture first."""
    build = Path(build_dir) if build_dir else REPO_ROOT / "build"
    names: list[str] = []
    for arch in _arch_suffixes():
        names += [f"openETQW-{kind}_{arch}", f"openETQW-{kind}_{arch}.exe"]
    names += [f"openETQW-{kind}", f"openETQW-{kind}.exe"]
    return [build / n for n in names]


def ded_binary_candidates(build_dir: Optional[Path] = None) -> list[Path]:
    return binary_candidates("ded", build_dir)


def build_binary(kind: str) -> Path:
    """$OPENETQW_CLIENT_BINARY / $OPENETQW_DED_BINARY when set (tests/meson.build
    passes the binaries of the build directory it runs from, which need not be
    build/), else the first existing build/openETQW-<kind>_<arch>[.exe], else the
    host architecture's name (so a missing-binary message names what was expected).
    For standalone tests that build their command lines at import time; pair
    with skip_unless_available()."""
    requested = os.environ.get("OPENETQW_DED_BINARY" if kind == "ded" else "OPENETQW_CLIENT_BINARY")
    if requested:
        return Path(requested).expanduser()
    candidates = binary_candidates(kind)
    return next((c for c in candidates if c.is_file()), candidates[0])


def private_savepath(prefix: str) -> Path:
    """An empty fs_savepath for this test run, removed at exit.  The engine writes
    the archived cvars a test sets with +set (r_rayTracing, r_vkValidation,
    com_maxFPS, ...) to <savepath>/base/etqwconfig.cfg; with the shared default
    (the repository's base/) they carry into later runs and other tests."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, path, True)
    return path


def find_ded_binary(explicit: Optional[str] = None) -> Path:
    """Resolve the dedicated server binary.

    Order: explicit argument (--ded-binary), $OPENETQW_DED_BINARY, then the
    build/openETQW-ded_<arch>[.exe] variants. Raises SkipTest when nothing
    usable exists (an explicitly requested but missing binary also skips, with
    the path in the message, so a typo is visible)."""
    requested = explicit or os.environ.get("OPENETQW_DED_BINARY")
    if requested:
        p = Path(requested).expanduser()
        if p.is_file():
            return p.resolve()
        raise SkipTest(f"dedicated server binary not found: {p}")
    for c in ded_binary_candidates():
        if c.is_file():
            return c
    raise SkipTest("dedicated server binary not found (looked for "
                   + ", ".join(str(c) for c in ded_binary_candidates()) + ")")


# the base paks the engine's retail-install check requires (the retail data patched to 1.5)
REQUIRED_PAKS = [f"pak{n:03d}.pk4" for n in range(9)]


def missing_required_paks(data_path: Path) -> list[str]:
    """The required base paks (pak000-pak008) missing under data_path/base."""
    return [p for p in REQUIRED_PAKS if not (Path(data_path) / "base" / p).is_file()]


# On synthetic data (fs_validateOfficialPaks 0) game->Init fails at
# idWeapon::GetAmmoType("stroyent") unless an ammoDef of that name exists, and a
# failed game->Init stops the engine (idCommonLocal::LoadGameDLL) before any
# + command runs. tools/ci/dedicated_server_smoke.py's stub-decls variant carries
# the same decl, in a pk4.
SYNTHETIC_STUB_DECL = "def/openetqw_test_stub.def"
SYNTHETIC_STUB_DECL_TEXT = (
    "// Generated by tests/etqw_harness.py for tests on synthetic data; no retail ETQW data.\n"
    "// sdPlayerProperties::Init requires an ammo type named 'stroyent'.\n"
    "ammoDef stroyent {\n"
    "}\n"
)


def write_synthetic_stub_decls(game_dir: Path) -> Path:
    """Write the decls game->Init needs on synthetic data into game_dir (base/) as a loose file."""
    path = Path(game_dir) / SYNTHETIC_STUB_DECL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SYNTHETIC_STUB_DECL_TEXT, encoding="utf-8")
    return path


def resolve_data_path() -> Path:
    """Return the retail data root (directory containing base/) from
    ETQW_DATA_PATH. Raises SkipTest when unset or incomplete (the engine
    refuses to start without pak000-pak008, the retail data patched to 1.5)."""
    raw = os.environ.get("ETQW_DATA_PATH", "").strip()
    if not raw:
        raise SkipTest("ETQW_DATA_PATH is not set (point it at the directory containing base/pak000.pk4)")
    p = Path(raw).expanduser()
    missing = missing_required_paks(p)
    if missing:
        raise SkipTest(f"ETQW_DATA_PATH={raw}: base/{', base/'.join(missing)} not found (the data must be patched to 1.5)")
    return p.resolve()


def env_data_path() -> Path:
    """$ETQW_DATA_PATH as a Path ('.' when unset), for standalone tests that
    build their command lines at import time; pair with skip_unless_available()."""
    return Path(os.environ.get("ETQW_DATA_PATH", "").strip()).expanduser()


def skip_unless_available(data_path: Path, *binaries: Path) -> None:
    """Exit 77 (the meson skip code) unless <data_path>/base/pak000-pak008.pk4
    and every binary exist."""
    reasons = []
    if not os.environ.get("ETQW_DATA_PATH", "").strip():
        reasons.append("ETQW_DATA_PATH is not set (point it at the directory containing base/pak000.pk4)")
    elif missing_required_paks(data_path):
        reasons.append(f"{Path(data_path) / 'base'}: {', '.join(missing_required_paks(data_path))} not found"
                       " (the data must be patched to 1.5)")
    reasons += [f"binary not found: {b}" for b in binaries if not Path(b).is_file()]
    if reasons:
        for r in reasons:
            print(f"[SKIP] {r}")
        sys.exit(EXIT_SKIP)


class PakIndex:
    """Read-only index of the retail base/pak*.pk4 archives.

    Archives are searched pak000..pakNNN; a file present in several paks is
    resolved to the highest-numbered one (the idTech override order)."""

    def __init__(self, data_path: Path):
        self.base = Path(data_path) / "base"
        self.paks = sorted(self.base.glob("pak*.pk4"))
        if not self.paks:
            raise SkipTest(f"no pak*.pk4 archives under {self.base}")
        self._where: dict[str, Path] = {}
        self._names: dict[str, str] = {}
        for pak in self.paks:
            with zipfile.ZipFile(pak) as z:
                for name in z.namelist():
                    key = name.lower()
                    self._where[key] = pak      # later paks override
                    self._names[key] = name

    def which(self, name: str) -> Optional[Path]:
        return self._where.get(name.lower())

    def exists(self, name: str) -> bool:
        return name.lower() in self._where

    def read(self, name: str) -> Optional[bytes]:
        pak = self.which(name)
        if pak is None:
            return None
        with zipfile.ZipFile(pak) as z:
            return z.read(self._names[name.lower()])


def retail_entity_count(paks: PakIndex, map_name: str) -> Optional[int]:
    """Number of '"classname"' lines in the retail maps/<map>.entities."""
    data = paks.read(f"maps/{map_name}.entities")
    if data is None:
        return None
    text = data.decode("latin-1")
    return sum(1 for line in text.splitlines() if '"classname"' in line)


_PAK_INDEXES: dict[str, "PakIndex"] = {}


def spawn_line(data_path: Path, map_name: str) -> str:
    """The server's '...N entities spawned' marker for a stock map on this data. N is
    the retail entity count: on the 1.5 data every map entity spawns (the DVD data left
    a few path corners unspawned)."""
    key = str(Path(data_path).resolve())
    if key not in _PAK_INDEXES:
        _PAK_INDEXES[key] = PakIndex(data_path)
    n = retail_entity_count(_PAK_INDEXES[key], map_name)
    return f"...{n} entities spawned" if n is not None else " entities spawned"


def retail_aas_counts(paks: PakIndex, map_name: str, ext: str) -> Optional[tuple[int, int]]:
    """(areas, reachabilities) parsed straight from the retail AAS file, using
    the binary parser in test_aas_stress.py. The engine's 'Loaded AAS file'
    line reports the same raw file counts (area 0 included)."""
    from test_aas_stress import parse_aas  # lazy: test_aas_stress imports us
    name = f"maps/{map_name}.{ext}"
    data = paks.read(name)
    if data is None:
        return None
    info = parse_aas(data, name, counts_only=True)
    return info["num_areas"], info["num_reach"]


def retail_nav_header(paks: PakIndex, map_name: str) -> Optional[tuple[int, int]]:
    """(version, numNodes) from the header of the retail maps/<map>.nav, or
    None when no pak ships one."""
    data = paks.read(f"maps/{map_name}.nav")
    if data is None:
        return None
    if len(data) < 8:
        return (-1, -1)
    return struct.unpack_from("<ii", data, 0)


# ---------------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------------

class PortAllocator:
    """Hands out unique UDP ports from [base, base+span) and verifies each one
    can actually be bound right now (another process may hold it)."""

    def __init__(self, base: int, span: int = 100):
        self.base = base
        self.span = span
        self._used: set[int] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _udp_free(port: int) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def acquire(self, preferred: Optional[int] = None) -> int:
        with self._lock:
            order = list(range(self.base, self.base + self.span))
            if preferred is not None and preferred in order:
                order.remove(preferred)
                order.insert(0, preferred)
            for port in order:
                if port in self._used:
                    continue
                if self._udp_free(port):
                    self._used.add(port)
                    return port
        raise RuntimeError(f"no free UDP port in [{self.base}, {self.base + self.span})")

    def release(self, port: int) -> None:
        with self._lock:
            self._used.discard(port)


# ---------------------------------------------------------------------------
# Line parsers
# ---------------------------------------------------------------------------

_F = r"(-?(?:nan|inf|[\d.]+(?:e[-+]?\d+)?))"  # printf %.2f incl. nan/inf

PLAYER_POS_RE = re.compile(
    r"PLAYER_POS:\s*\(" + _F + r",\s*" + _F + r",\s*" + _F + r"\)\s*"
    r"vel:\s*\(" + _F + r",\s*" + _F + r",\s*" + _F + r"\)\s*"
    r"ground:\s*(\d+)\s*ducked:\s*(\d+)\s*prone:\s*(\d+)\s*jumped:\s*(\d+)\s*"
    r"\(client:\s*(\d+)\s*team:\s*(\S+)\s*spectating\s*(\d+)\s*atk:\s*(\d+)\s*"
    r"cmd:\s*fwd=(-?\d+)\s*side=(-?\d+)\s*up=(-?\d+)\s*atk_cmd=(\d+)"
    r"(?:\s*lagged=(\d+)\s*frozen=(\d+)\s*inh=(\d+))?"
    r"(?:\s*hp=(-?\d+)\s*dead=(\d+)\s*area=(-?\d+))?"
    r"(?:\s*yaw=" + _F + r")?"
    r"(?:\s*vehicle=(-?\d+))?"
    r"(?:\s*tele=(\d+))?"
    # fields a newer game prints that this harness doesn't know yet (the game PR of a
    # cross-repo pair merges first, so the line can be ahead of the parser)
    r"(?:\s+\w+=[^\s)]*)*"
    r"\s*\)",
    re.IGNORECASE,
)


def parse_player_pos(line: str) -> Optional[dict]:
    """Parse a PLAYER_POS line. The optional ' hp=%d dead=%d area=%d',
    ' yaw=%.2f', ' vehicle=%d' and ' tele=%d' suffixes (before the closing ')') yield
    hp/dead/area/yaw/vehicle/tele; they are None when the build does not print them."""
    m = PLAYER_POS_RE.search(line)
    if not m:
        return None
    g = m.groups()
    fl = [float(v) for v in g[0:6]]
    return {
        "org": (fl[0], fl[1], fl[2]),
        "vel": (fl[3], fl[4], fl[5]),
        "ground": int(g[6]), "ducked": int(g[7]), "prone": int(g[8]), "jumped": int(g[9]),
        "client": int(g[10]), "team": g[11], "spectating": int(g[12]), "atk": int(g[13]),
        "fwd": int(g[14]), "side": int(g[15]), "up": int(g[16]), "atk_cmd": int(g[17]),
        "lagged": int(g[18]) if g[18] is not None else None,
        "frozen": int(g[19]) if g[19] is not None else None,
        "inh": int(g[20]) if g[20] is not None else None,
        "hp": int(g[21]) if g[21] is not None else None,
        "dead": int(g[22]) if g[22] is not None else None,
        "area": int(g[23]) if g[23] is not None else None,
        # the yaw of the player's latest usercmd in degrees (game builds from 2026-09-24)
        "yaw": float(g[24]) if g[24] is not None else None,
        # the entity number of the vehicle (or other proxy) the player rides, -1 on foot
        # (game builds from 2026-09-25)
        "vehicle": int(g[25]) if g[25] is not None else None,
        # 1 while the player is in a teleporter's transit, held at its storage location
        # (game builds from 2026-10-20)
        "tele": int(g[26]) if g[26] is not None else None,
    }


BOT_THREAD_STATS_RE = re.compile(
    r"BOT_THREAD_STATS:\s*threading=(-?\d+)\s+running=(-?\d+)\s+gameFrame=(-?\d+)\s+"
    r"gameTimeMs=(-?\d+)\s+botFrames=(-?\d+)\s+minDelay=(-?\d+)\s+maxDelay=(-?\d+)\s+waits=(-?\d+)"
)
_BTS_KEYS = ("threading", "running", "gameFrame", "gameTimeMs", "botFrames",
             "minDelay", "maxDelay", "waits")


def parse_bot_thread_stats(line: str) -> Optional[dict]:
    m = BOT_THREAD_STATS_RE.search(line)
    if not m:
        return None
    return {k: int(v) for k, v in zip(_BTS_KEYS, m.groups())}


SPAWN_RE = re.compile(
    r"Player (\d+) \('([^']*)'\) spawned at \(" + _F + r", " + _F + r", " + _F + r"\) "
    r"angles \([^)]*\) team (\S+) spectating (\d+)",
    re.IGNORECASE,
)


def parse_spawn(line: str) -> Optional[dict]:
    m = SPAWN_RE.search(line)
    if not m:
        return None
    return {
        "client": int(m.group(1)), "name": m.group(2),
        "org": (float(m.group(3)), float(m.group(4)), float(m.group(5))),
        "team": m.group(6), "spectating": int(m.group(7)),
    }


OBJECTIVE_STATUS_RE = re.compile(
    r"OBJECTIVE_STATUS:\s*gdf_obj=(-?\d+)\s+strogg_obj=(-?\d+)\s+state=(-?\d+)\s+winner=(\S*)"
)


def parse_objective_status(line: str) -> Optional[dict]:
    m = OBJECTIVE_STATUS_RE.search(line)
    if not m:
        return None
    return {"gdf_obj": int(m.group(1)), "strogg_obj": int(m.group(2)),
            "state": int(m.group(3)), "winner": m.group(4)}


ENTITIES_SPAWNED_RE = re.compile(r"^\.\.\.(\d+) entities spawned, (\d+) inhibited")
MAP_LINE_RE = re.compile(r"^Map: (\S+)")
AAS_LOADED_RE = re.compile(
    r"Loaded AAS file: 'maps/([\w-]+)\.(aas_\w+)' \((\d+) areas, (\d+) reachabilities\)")
AAS_INIT_RE = re.compile(
    r"AAS initialization complete: (\d+) profiles loaded \(player AAS: (\w+), vehicle AAS: (\w+)\)")
NAV_LOADED_RE = re.compile(r"Loaded vehicle navigation graph 'maps/([\w-]+)\.nav' \((\d+) nodes\)")
SIM_HASH_TICK_RE = re.compile(r"SIM_HASH:\s+tick=(\d+)")
SCRIPT_DEBUG_RE = re.compile(r"^\*{5,}\s*(.*?)\s*\*{5,}\s*$")
UNKNOWN_COMMAND_RE = re.compile(r"^Unknown command '([^']*)'")


# ---------------------------------------------------------------------------
# Failure-marker scanner
# ---------------------------------------------------------------------------

FAILURE_MARKERS = (
    "FATAL ERROR",
    "FATAL: recursive error",
    "FATAL SIGNAL CAUGHT",
    "Couldn't load AAS file",
    "No valid AAS navigation profiles",
    "bind() failed",
    "called from a thread other than the main thread",
    # a retail script event declaration its native handler cannot take (game ReconcileEventsWithScript)
    "does not match its native definition",
)

# The game's own per-frame pacing warning (game repo src/game/botai/BotThreadData.cpp:
# the game thread waited because the bot thread was more than
# bot_threadMaxFrameDelay frames behind). It says the host was slow to schedule the
# bot thread, which is not a fault on its own: on 2026-10-27 the owner decided
# (issue #294) that it counts as a failure only when the same run also fails a
# gameplay check. A wedged bot thread shows up as the gameplay checks it breaks.
PACING_MARKERS = (
    "Waiting on BotThread",
)


def scan_failures(lines: Iterable[str], *, run_failed: bool = False) -> list[dict]:
    """Return failure markers found in a server log.

    Besides the fixed substrings above, an 'ERROR:' line directly following a
    '********************' banner line is a common->Error drop (the process
    keeps running and still exits 0, so the exit code alone proves nothing).
    Script runtime errors (idInterpreter::Error) arrive through that banner
    and are tagged kind='script' when the message names a script thread.

    Pacing warnings (PACING_MARKERS) are tagged kind='pacing' and are returned
    only when run_failed is true, that is, when the caller's own gameplay checks
    failed (#294). Pass the run's outcome, not a wait count: the three sightings
    behind the decision had 2, 4 and 30 waits and all finished normally."""
    found: list[dict] = []
    prev = ""
    for i, line in enumerate(lines):
        s = line.rstrip("\r\n")
        for marker in FAILURE_MARKERS:
            if marker in s:
                found.append({"line": i + 1, "marker": marker, "kind": "marker", "text": s})
                break
        else:
            for marker in PACING_MARKERS:
                if marker in s:
                    found.append({"line": i + 1, "marker": marker, "kind": "pacing", "text": s})
                    break
        if s.startswith("ERROR:") and prev.startswith("********************"):
            kind = "script" if ("Thread '" in s or ".script(" in s) else "error"
            found.append({"line": i + 1, "marker": "ERROR banner", "kind": kind, "text": s})
        prev = s
    if not run_failed:
        found = [f for f in found if f["kind"] != "pacing"]
    return found


def scan_pacing(lines: Iterable[str]) -> list[dict]:
    """The 'Waiting on BotThread' pacing warnings in a log, recorded but not gating.

    Use this to report how often a run waited on its bot thread; whether that
    counts as a failure is decided by scan_failures(run_failed=...) (#294)."""
    return [f for f in scan_failures(lines, run_failed=True) if f["kind"] == "pacing"]


# ---------------------------------------------------------------------------
# Dedicated server wrapper
# ---------------------------------------------------------------------------

@dataclass
class LogLine:
    t: float    # time.monotonic() when the reader thread received the line
    text: str


class DedicatedServer:
    """A dedicated server process driven over stdin/stdout.

    Every stdout line is stored with a monotonic timestamp (self.lines) and
    appended to log_path. The engine executes one stdin line per frame, in
    order, so 'echo SYNC_<n>' marks the point after which all earlier commands
    have run."""

    def __init__(self, binary: Path, data_path: Path, map_name: str, port: int,
                 log_path: Optional[Path] = None, cvars: Optional[dict] = None,
                 savepath: Optional[Path] = None, extra_args: Optional[list[str]] = None,
                 cwd: Optional[Path] = None):
        self.binary = Path(binary)
        self.data_path = Path(data_path)
        self.map_name = map_name
        self.port = port
        self.log_path = Path(log_path) if log_path else None
        self.cvars = dict(cvars or {})
        self.extra_args = list(extra_args or [])
        self._own_savepath = savepath is None
        self.savepath = Path(savepath) if savepath else Path(tempfile.mkdtemp(prefix=f"etqw-{map_name}-"))
        # the working directory (default: the save path); the Vulkan client finds
        # vkprogs/ relative to it, so it runs from the repository root
        self.cwd = Path(cwd) if cwd else None
        self.lines: list[LogLine] = []
        self._cond = threading.Condition()
        self._sync_n = 0
        self._eof = False
        self.proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self.start_time: Optional[float] = None

    # -- lifecycle ---------------------------------------------------------
    def command_line(self) -> list[str]:
        args = [
            str(self.binary),
            "+set", "dedicated", "1",
            "+set", "fs_basepath", str(self.data_path),
            "+set", "fs_game", "base",
            "+set", "fs_savepath", str(self.savepath),
            "+set", "sys_skipThreadPrio", "1",
            "+set", "net_port", str(self.port),
        ]
        for k, v in self.cvars.items():
            args += ["+set", str(k), str(v)]
        args += self.extra_args
        args += ["+spawnServer", self.map_name]
        return args

    def start(self) -> None:
        self.preflight()
        self.savepath.mkdir(parents=True, exist_ok=True)
        log_fh = open(self.log_path, "w", encoding="utf-8", errors="replace") if self.log_path else None
        if log_fh:
            log_fh.write("# " + " ".join(self.command_line()) + "\n")
            log_fh.flush()
        self.start_time = time.monotonic()
        self.proc = subprocess.Popen(
            self.command_line(),
            cwd=str(self.cwd or self.savepath),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with _LIVE_PROCS_LOCK:
            _LIVE_PROCS.append(self.proc)

        def _read():
            try:
                for raw in iter(self.proc.stdout.readline, ""):
                    t = time.monotonic()
                    text = raw.rstrip("\r\n")
                    if log_fh:
                        log_fh.write(text + "\n")
                        log_fh.flush()
                    with self._cond:
                        self.lines.append(LogLine(t, text))
                        self._cond.notify_all()
            finally:
                if log_fh:
                    log_fh.close()
                with self._cond:
                    self._eof = True
                    self._cond.notify_all()

        self._reader = threading.Thread(target=_read, name=f"reader-{self.map_name}", daemon=True)
        self._reader.start()

    def preflight(self) -> None:
        try:
            check_startup_ready(self.port)
        except PreflightError as exc:
            # one line a log scan can find before the traceback names the test
            print(f"PREFLIGHT: {exc}", file=sys.stderr, flush=True)
            raise

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def send(self, cmd: str) -> bool:
        if not self.alive():
            return False
        try:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def wait_for(self, predicate: Callable[[str], object], timeout: float,
                 start: int = 0) -> Optional[tuple[int, LogLine, object]]:
        """Wait until a line at index >= start satisfies predicate (returns a
        truthy value). Returns (index, LogLine, value) or None on timeout /
        process exit."""
        deadline = time.monotonic() + timeout
        idx = start
        with self._cond:
            while True:
                while idx < len(self.lines):
                    v = predicate(self.lines[idx].text)
                    if v:
                        return idx, self.lines[idx], v
                    idx += 1
                if self._eof:
                    return None
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._cond.wait(min(remaining, 0.25))

    def wait_for_text(self, text: str, timeout: float, start: int = 0):
        return self.wait_for(lambda s: text in s, timeout, start)

    def mark(self) -> int:
        with self._cond:
            return len(self.lines)

    def sync(self, timeout: float = 15.0) -> Optional[int]:
        """Send 'echo SYNC_<n>' and wait for it. Returns the index of the
        SYNC line, or None on timeout."""
        self._sync_n += 1
        token = f"SYNC_{self._sync_n}"
        start = self.mark()
        if not self.send(f"echo {token}"):
            return None
        r = self.wait_for(lambda s: s.strip() == token, timeout, start)
        return r[0] if r else None

    def command(self, cmd: str, timeout: float = 15.0) -> Optional[list[LogLine]]:
        """Run one console command and return the lines printed up to the
        following SYNC echo (None when the SYNC never arrived)."""
        start = self.mark()
        if not self.send(cmd):
            return None
        end = self.sync(timeout)
        if end is None:
            return None
        with self._cond:
            return list(self.lines[start:end])

    def snapshot(self, start: int = 0, end: Optional[int] = None) -> list[LogLine]:
        with self._cond:
            return list(self.lines[start:end])

    def quit(self, timeout: float = 45.0) -> Optional[int]:
        """Send 'quit' and wait for exit. Returns the exit code, or None when
        the process was never started. The code is an ExitStatus: an int that
        formats its NTSTATUS/signal meaning in failure messages. A process
        that had to be killed after the timeout returns its status with the
        reason in the text, so it no longer reads like a server that never
        started."""
        if self.proc is None:
            return None
        if self.proc.poll() is None:
            self.send("quit")
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return self.kill(f"killed after the 'quit' timeout of {timeout:.0f} s")
        if self._reader:
            self._reader.join(timeout=5.0)
        return self._exit_status()

    def _exit_status(self) -> Optional[ExitStatus]:
        """The process status as an ExitStatus, or None when there is none."""
        code = self.proc.returncode if self.proc is not None else None
        if code is None:
            return None
        if not isinstance(code, ExitStatus):
            code = ExitStatus(code)
            self.proc.returncode = code
        return code

    def kill(self, reason: str = "killed") -> Optional[ExitStatus]:
        """Kill the process and return its exit status (None if never started).

        The reason goes into the status text when this call did the killing,
        so a timeout kill says why it happened."""
        if self.proc is None:
            return None
        killed = self.proc.poll() is None
        if killed:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5.0)
            except Exception:
                pass
        if self._reader:
            self._reader.join(timeout=5.0)
        status = self._exit_status()
        if status is not None and killed:
            status = ExitStatus(int(status), f"{exit_status_text(int(status))}; {reason}")
            self.proc.returncode = status
        return status

    def cleanup(self) -> None:
        self.kill()
        with _LIVE_PROCS_LOCK:
            if self.proc in _LIVE_PROCS:
                _LIVE_PROCS.remove(self.proc)
        if self._own_savepath:
            shutil.rmtree(self.savepath, ignore_errors=True)


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------

def finite(*vals: float) -> bool:
    return all(math.isfinite(v) for v in vals)


def dist2d(a: tuple, b: tuple) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])
