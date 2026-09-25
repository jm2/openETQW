#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared helpers for OpenETQW dedicated-server integration tests.

This module is imported by test scripts in tests/ (it is not a test itself).
It provides:

* binary discovery for the dedicated server (build/openETQW-ded_<arch>[.exe],
  overridable with --ded-binary or OPENETQW_DED_BINARY);
* retail-data resolution from ETQW_DATA_PATH only (there is deliberately no
  per-user default: unset or missing <path>/base/pak000.pk4 means SKIP, 77);
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

STOCK_MAPS = (
    "area22", "ark", "canyon", "island", "outskirts", "quarry",
    "refinery", "salvage", "sewer", "slipgate", "volcano",
)

READY_MARKER = "------------- ETQW initialized -------------"


class SkipTest(Exception):
    """Raised when a prerequisite (binary, retail data) is missing -> exit 77."""


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
    """The first existing build/openETQW-<kind>_<arch>[.exe], else the host
    architecture's name (so a missing-binary message names what was expected).
    For standalone tests that build their command lines at import time; pair
    with skip_unless_available()."""
    candidates = binary_candidates(kind)
    return next((c for c in candidates if c.is_file()), candidates[0])


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


def resolve_data_path() -> Path:
    """Return the retail data root (directory containing base/) from
    ETQW_DATA_PATH. Raises SkipTest when unset or incomplete."""
    raw = os.environ.get("ETQW_DATA_PATH", "").strip()
    if not raw:
        raise SkipTest("ETQW_DATA_PATH is not set (point it at the directory containing base/pak000.pk4)")
    p = Path(raw).expanduser()
    if not (p / "base" / "pak000.pk4").is_file():
        raise SkipTest(f"ETQW_DATA_PATH={raw}: {p / 'base' / 'pak000.pk4'} not found")
    return p.resolve()


def env_data_path() -> Path:
    """$ETQW_DATA_PATH as a Path ('.' when unset), for standalone tests that
    build their command lines at import time; pair with skip_unless_available()."""
    return Path(os.environ.get("ETQW_DATA_PATH", "").strip()).expanduser()


def skip_unless_available(data_path: Path, *binaries: Path) -> None:
    """Exit 77 (the meson skip code) unless <data_path>/base/pak000.pk4 and
    every binary exist."""
    reasons = []
    if not os.environ.get("ETQW_DATA_PATH", "").strip():
        reasons.append("ETQW_DATA_PATH is not set (point it at the directory containing base/pak000.pk4)")
    elif not (Path(data_path) / "base" / "pak000.pk4").is_file():
        reasons.append(f"{Path(data_path) / 'base' / 'pak000.pk4'} not found")
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
    r"\s*\)",
    re.IGNORECASE,
)


def parse_player_pos(line: str) -> Optional[dict]:
    """Parse a PLAYER_POS line. The optional ' hp=%d dead=%d area=%d',
    ' yaw=%.2f' and ' vehicle=%d' suffixes (before the closing ')') yield
    hp/dead/area/yaw/vehicle; they are None when the build does not print them."""
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
    "Waiting on BotThread",
    "Couldn't load AAS file",
    "No valid AAS navigation profiles",
    "bind() failed",
    "called from a thread other than the main thread",
    # a retail script event declaration its native handler cannot take (game ReconcileEventsWithScript)
    "does not match its native definition",
)


def scan_failures(lines: Iterable[str]) -> list[dict]:
    """Return failure markers found in a server log.

    Besides the fixed substrings above, an 'ERROR:' line directly following a
    '********************' banner line is a common->Error drop (the process
    keeps running and still exits 0, so the exit code alone proves nothing).
    Script runtime errors (idInterpreter::Error) arrive through that banner
    and are tagged kind='script' when the message names a script thread."""
    found: list[dict] = []
    prev = ""
    for i, line in enumerate(lines):
        s = line.rstrip("\r\n")
        for marker in FAILURE_MARKERS:
            if marker in s:
                found.append({"line": i + 1, "marker": marker, "kind": "marker", "text": s})
                break
        if s.startswith("ERROR:") and prev.startswith("********************"):
            kind = "script" if ("Thread '" in s or ".script(" in s) else "error"
            found.append({"line": i + 1, "marker": "ERROR banner", "kind": kind, "text": s})
        prev = s
    return found


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
        """Send 'quit' and wait for exit. Returns the exit code, or None if
        the process had to be killed."""
        if self.proc is None:
            return None
        if self.proc.poll() is None:
            self.send("quit")
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.kill()
                return None
        if self._reader:
            self._reader.join(timeout=5.0)
        return self.proc.returncode

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5.0)
            except Exception:
                pass
        if self._reader:
            self._reader.join(timeout=5.0)

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
