#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""High-Refresh Simulation Determinism Replay Test for OpenETQW Milestone 5.

Verifies that decoupling Vulkan frame presentation (com_maxFPS 30 to 240 Hz and uncapped,
com_unlockFPS 0, and a cap changed during play) from authoritative 30 Hz simulation (USERCMD_HZ = 30, USERCMD_MSEC = 33) produces
zero simulation drift or physics divergence on stock map Valley (maps/valley.world).

Validates:
1. Fixed tick-indexed command stream replayed at caps 60, 120, 144 and 240 and uncapped
   (com_maxFPS 0, which also measures this host's frame-rate ceiling), at cap 30 (every
   frame runs a tick: no render-only frames, with the base-draw path of com_unlockFPS 1),
   and once more at cap 240 with com_unlockFPS 0 (M5 plan step 11, D8: the renderer does
   not go faster than the ticks). The com_unlockFPS 0 schedule is compared like the
   others, and instead of the cap rules it must draw no render-only frame: it logs
   every draw (g_logFramePoses), each must be a base draw of a new game time
   (test_frame_poses.locked_check), and no play window may draw more often than it
   ticks (FRAME_TIMING draws= <= ticks=).
   A further schedule changes the cap during play (M5 plan step 17): it starts at 60,
   sets com_unlock_maxFPS 90 once tick 10 is logged and com_maxFPS 45 once tick 80 is
   (over stdin, as a player would at the console), about 70 ticks at each so that a
   FRAME_TIMING window of about a second falls wholly inside each whatever the phase.
   It is compared like the others; its FRAME_TIMING windows must report the caps in
   that order (the alias carries com_unlock_maxFPS into com_maxFPS), and each new cap
   must be held in a full window at it, by the same rule as the fixed caps.
2. Bit-for-bit identical CRC32 simulation hashes (SIM_HASH) across all schedules, and
   SIM_VIEW identical to cap 30's on every tick in every schedule: state each tick
   produces that later ticks read back and that a render frame could write into (the view
   angles after UpdateViewAngles, the tick's logged view origin and weapon offset and
   orientation, the vehicle's last pushed pose and the axis its view last returned;
   docs/frame-path.md section 6.9). Cap 30 runs no render-only frame, so any difference
   is render-frame state leaking into a tick; the first 10 are printed. (Until M5 step 6
   the render frames of the tick that enters the vehicle wrote the weapon pose the
   vehicle ticks then logged, and the test allowed that wlog divergence as
   KNOWN_VIEW_LEAK.) Every schedule must print
   SIM_HASH and SIM_VIEW exactly once per tick, on the same ticks, each SIM_VIEW line with
   a view player, every field and finite values, and the replay must log a weapon pose, a
   vehicle pose and a vehicle view axis on some tick.
3. Identical player/vehicle coordinates, velocities, health, objective state, and serviced event counts.
   And the occupied vehicle's VEH_TRACE (M5 step 5a: its whole physics and control state
   in full precision; the titan, ticks 92-115) identical at every cap.
4. The frame rate each schedule reached during play, from the engine's once-a-second
   FRAME_TIMING lines (com_logFrameTiming 1; windows with >= 25 game ticks), taken from the
   median frame interval so one loading hitch does not decide it. It may not exceed the cap
   by more than 3%. A cap clearly below the uncapped rate (<= 90% of it) is set by the pacer
   and must be held to 98% (the pacer ran a 60 cap at 58 fps before 2026-09-25). Any other
   cap is set by the host, and per docs/m5-plan.md decision D1 the rate it reached is
   reported against the lower of the cap and the uncapped rate, not failed: no
   high-refresh hardware is part of testing. A cap above the uncapped rate is reported as
   unreachable on this host (on a 120 Hz display, presentation waits for the display, so
   144 and 240 run at about 120). The average rate and the longest frame are reported
   too, and every FRAME_TIMING window must report the schedule's own cap (the cap-change
   schedule's windows, the caps in order; the com_unlockFPS 0 schedule is checked by
   draws instead, item 1).
5. Clean shutdown with exit code 0.
6. Render-only frames are prepared only when they can be drawn (M5 step 7; with the client
   binary): FRAME_TIMING's cuv= counts the frames on which the engine called
   ClientUpdateView. The com_unlockFPS 0 schedule must have none, since it draws no frame
   that ran no tick, and every schedule above cap 30 some, in play.
7. Tick code never reads a render-only frame's view (M5 step 7, owner decision E3; with
   the client binary): at each tick's entry the game puts back the view player's renderView
   origin and axis as the last tick's pose log saved them, and prints TICK_VIEW (entry= the
   comparison with the saved view before that, think= the one as entity Think starts: 1
   differs, 0 the same, -1 nothing to compare with, after a change of view player, seat or
   remote camera). Every tick must have a TICK_VIEW line and none may print think=1; every
   schedule above cap 30 must print entry=1 on some tick (a render-only frame moved the
   view, and it was put back), and the com_unlockFPS 0 schedule none (nothing else moves
   the view between ticks).

--listen runs the same schedules on a listen host (M5 plan step 5's listen variant): the
Vulkan client serves the map itself (+spawnServer valley on --port, net_allowCheats 1) and
simulates its own player as the server, where M5 step 7 changes how the frame runs. Each
run must print the listen server's "Server spawned on ...:<port>" line, so a launch that
fell back to single player cannot pass. Every check above applies, so SIM_HASH, SIM_VIEW
and VEH_TRACE must be identical to cap 30's and to the com_unlockFPS 0 schedule's on every
tick. Every schedule also logs its draws (g_logFramePoses), and the exercise check takes
the host's render-only draws on the on-foot ticks 21-90 (a draw whose tick, FRAME_POSE fn,
is the previous draw's: its frame ran no tick). Each should be an unlocked draw (ud=1,
ClientUpdateView called on its frame) with a fraction in (0, 1) (the call went on to
present one). Before step 7 the host draws between ticks but never calls ClientUpdateView,
so none is, and the counts are reported; with LISTEN_HOST_UNLOCKS set (step 7) every cap
above 30 must have at least 20 render-only draws there, all but max(1, 1%) of them so.
With com_unlockFPS 0 a frame that ran no tick is not drawn (item 1), so no draw can show
whether ClientUpdateView ran there; item 6 checks that with a count of the calls
themselves. Needs the client binary (no fallback to the dedicated one).

--log-dir DIR writes each schedule's output to DIR/<sp|listen>-<schedule>.log.
"""

from __future__ import annotations

import argparse
import atexit
import collections
import dataclasses
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import etqw_harness as h
import test_frame_poses as frame_poses

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = h.env_data_path()
CLIENT_BIN = h.build_binary("client")
# archived cvars this test sets (com_maxFPS, ...) must not reach other runs
SAVEPATH = h.private_savepath("etqw-high-refresh-")
DED_BIN = h.build_binary("ded")

TEST_PROCS: list[subprocess.Popen] = []


def cleanup_procs() -> None:
    for p in TEST_PROCS:
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=2.0)
            except Exception:
                try:
                    p.kill()
                    p.wait(timeout=1.0)
                except Exception:
                    pass


atexit.register(cleanup_procs)


@dataclasses.dataclass(frozen=True)
class SimHashEntry:
    tick: int
    crc_hash: str
    pos: tuple[float, float, float]
    vel: tuple[float, float, float]
    hp: int
    obj: tuple[int, int, int]
    ev: int


SIM_HASH_RE = re.compile(
    r"SIM_HASH:\s+tick=(\d+)\s+hash=0x([0-9a-fA-F]+)\s+"
    r"p=\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)\s+"
    r"v=\(([-0-9.]+),([-0-9.]+),([-0-9.]+)\)\s+"
    r"hp=(\d+)\s+"
    r"obj=\(([-0-9]+),([-0-9]+),([-0-9]+)\)\s+"
    r"ev=(\d+)"
)


SIM_VIEW_RE = re.compile(r"SIM_VIEW: tick=(\d+) (.*)$")
TICK_VIEW_RE = re.compile(r"TICK_VIEW: tick=(\d+) entry=(-?\d+) think=(-?\d+)$")
VEH_TRACE_RE = re.compile(r"VEH_TRACE: (tick=\d+ .*)$")
# every field EmitSimView prints for a view player, so a field dropped from every
# schedule cannot pass as "identical". Without a view player EmitSimView prints only
# "view=-1", but the replay's local player is the view player from tick 1, so every
# replay tick must carry the full record.
# values are printed in full precision (%.9g), so no difference hides in rounding;
# nan and inf do not match
_NUM = r"-?(?:\d+\.?\d*|\.\d+)(?:e[-+]?\d+)?"
_TRIPLE = rf"\({_NUM},{_NUM},{_NUM}\)"
SIM_VIEW_FIELDS_RE = re.compile(
    rf"view=(\d+) ang={_TRIPLE} olog={_TRIPLE} wlog=(\({_NUM}(?:,{_NUM}){{5}}\)|-)"
    rf" vpush=({_TRIPLE}) vpang={_TRIPLE} vret=({_TRIPLE}|-)")

FRAME_TIMING_RE = re.compile(
    r"FRAME_TIMING: window_ms=([\d.]+) frames=(\d+) ticks=(\d+) cap=(-?\d+)"
    r" dt_p50=([\d.]+) dt_p95=([\d.]+) dt_p99=([\d.]+) dt_max=([\d.]+)"
    r"(?:.*? draws=(\d+))?"
    r"(?:.*? cuv=(\d+))?"   # the line's last field (M5 step 7)
)
LOCKED = -240           # the schedule at cap 240 with com_unlockFPS 0 (M5 plan step 11, D8)
CHANGED = -1            # the schedule whose cap changes during play (M5 plan step 17)
CAP_START = 60
CAP_CHANGES = ((10, "com_unlock_maxFPS", 90), (80, "com_maxFPS", 45))  # (after tick, CVar, value); starts at CAP_START
CAP_ORDER = [CAP_START] + [value for _, _, value in CAP_CHANGES]
# the full-window rule below relies on the cap never returning to an earlier value
assert len(set(CAP_ORDER)) == len(CAP_ORDER), "CAP_CHANGES must not repeat a cap"
FULL_WINDOW_MIN_FRAMES = 20  # enough intervals for the window's median to set its rate
PLAY_MIN_TICKS = 25     # a FRAME_TIMING window with fewer game ticks is loading or menus
CAP_OVERSHOOT = 1.03    # a schedule may run at most this much above its cap
PACED_FRACTION = 0.98   # a cap <= REACH_FRACTION x the uncapped rate must be held this closely
REACH_FRACTION = 0.9    # a cap above this fraction of the uncapped rate is the host's (D1: reported)
LISTEN_PORT = 29964     # --listen's default net_port (tests/meson.build passes it too)
# --listen's exercise check: the host's render-only draws (FRAME_POSE) on the ticks the
# replay is on foot. Since M5 step 7 idAsyncServer::RunFrame calls ClientUpdateView on
# render-only frames, and the check is asserted (at every cap above 30, unlocked draws
# with a fraction in (0, 1)); with False it is only reported, as before step 7, when the
# host drew between ticks but never made an unlocked draw.
LISTEN_HOST_UNLOCKS = True
ON_FOOT_TICKS = (21, 90)    # the replay walks, strafes and jumps, then fires; it enters the titan at 91


def play_timing(lines: list[str]) -> dict | None:
    """Frame rate and frame-interval spread over the FRAME_TIMING windows of play."""
    windows = [m for ln in lines if (m := FRAME_TIMING_RE.search(ln)) and int(m.group(3)) >= PLAY_MIN_TICKS]
    if not windows:
        return None
    ms = sum(float(m.group(1)) for m in windows)
    frames = sum(int(m.group(2)) for m in windows)
    medians = sorted(float(m.group(5)) for m in windows)
    draws = [int(m.group(9)) for m in windows if m.group(9) is not None]
    return {
        "draws_ps": sum(draws) * 1000.0 / ms if len(draws) == len(windows) else None,
        "caps": sorted({int(m.group(4)) for m in re.finditer(FRAME_TIMING_RE, "\n".join(lines))}),
        "windows": len(windows),
        "fps": 1000.0 / medians[len(medians) // 2],  # from the median frame interval
        "avg_fps": frames * 1000.0 / ms,
        "dt_p50": max(float(m.group(5)) for m in windows),
        "dt_p99": max(float(m.group(7)) for m in windows),
        "dt_max": max(float(m.group(8)) for m in windows),
    }


def start_cap(fps: int) -> int:
    """The com_maxFPS a schedule starts with."""
    return CAP_START if fps == CHANGED else abs(fps)


def cap_label(fps: int) -> str:
    if fps == CHANGED:
        return "cap changed in play (" + ", ".join(str(c) for c in CAP_ORDER) + ")"
    if fps < 0:
        return f"{-fps} Hz, com_unlockFPS 0"
    return "uncapped" if fps == 0 else f"{fps} Hz"


def stream_reader(pipe, q: queue.Queue[str]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            q.put(line.strip())
    except Exception as e:
        q.put(f"STREAM_READER_ERROR: {e}")
    finally:
        pipe.close()


def run_determinism_schedule(
    fps: int,
    bin_path: Path,
    expected_min_ticks: int = 150,
    timeout_sec: float = 45.0,
    listen_port: int | None = None,
) -> tuple[dict[int, SimHashEntry], int, list[str]]:
    """Runs a single determinism schedule at the given com_maxFPS: single player
    (+devmap), or on a listen host serving listen_port (+spawnServer)."""
    where = "single player" if listen_port is None else f"listen host on port {listen_port}"
    print(f"\n--- [SCHEDULE {cap_label(fps)}] Launching Valley determinism replay (com_maxFPS {start_cap(fps)}, {where}) ---")

    # every listen draw is logged, for the exercise check; single player logs the locked ones
    log_poses = "4" if fps == LOCKED else ("0" if listen_port is None else "1")
    args = [
        str(bin_path),
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_savepath", str(SAVEPATH),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "r_swapInterval", "0",
        "+set", "com_maxFPS", str(start_cap(fps)),
        "+set", "com_unlockFPS", "0" if fps == LOCKED else "1",
        "+set", "g_logFramePoses", log_poses,
        "+set", "com_logFrameTiming", "1",
        "+set", "g_determinismTest", "1",
        "+set", "g_simHash", "1",
    ]
    if listen_port is None:
        args += ["+devmap", "valley"]
    else:
        # g_logFramePoses is a cheat (it logs other players whatever their visibility)
        args += ["+set", "net_port", str(listen_port), "+set", "net_allowCheats", "1", "+spawnServer", "valley"]
    args += ["+clientTeam", "gdf", "+clientClass", "soldier", "+closeLimbo"]

    start_time = time.time()
    proc = subprocess.Popen(
        args,
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    TEST_PROCS.append(proc)

    output_lines: list[str] = []
    out_q: queue.Queue[str] = queue.Queue()
    t = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
    t.start()

    sim_hashes: dict[int, SimHashEntry] = {}
    vulkan_frames = 0
    clean_exit = False
    changes_sent = 0

    while time.time() - start_time < timeout_sec:
        ret = proc.poll()
        if ret is not None:
            clean_exit = True
            break

        try:
            line = out_q.get(timeout=0.2)
            output_lines.append(line)

            match = SIM_HASH_RE.search(line)
            if match:
                tick = int(match.group(1))
                entry = SimHashEntry(
                    tick=tick,
                    crc_hash=match.group(2).lower(),
                    pos=(float(match.group(3)), float(match.group(4)), float(match.group(5))),
                    vel=(float(match.group(6)), float(match.group(7)), float(match.group(8))),
                    hp=int(match.group(9)),
                    obj=(int(match.group(10)), int(match.group(11)), int(match.group(12))),
                    ev=int(match.group(13)),
                )
                sim_hashes[tick] = entry
                # the cap change during play (M5 plan step 17): each once its tick is logged
                while fps == CHANGED and changes_sent < len(CAP_CHANGES) and tick >= CAP_CHANGES[changes_sent][0]:
                    _, cvar, value = CAP_CHANGES[changes_sent]
                    changes_sent += 1
                    try:
                        proc.stdin.write(f"set {cvar} {value}\n")
                        proc.stdin.flush()
                    except (BrokenPipeError, OSError) as e:
                        # the client died: the schedule's missing ticks and exit code report it
                        print(f"  [CAP] tick {tick}: could not send set {cvar} {value}: {e}")
                        break
                    print(f"  [CAP] tick {tick}: set {cvar} {value}")

        except queue.Empty:
            continue

    # let the reader reach EOF first: the SIM_VIEW check needs every line of the run
    if proc.poll() is not None:
        t.join(timeout=10.0)
    # Drain remaining output
    while True:
        try:
            line = out_q.get_nowait()
            output_lines.append(line)
            match = SIM_HASH_RE.search(line)
            if match:
                tick = int(match.group(1))
                entry = SimHashEntry(
                    tick=tick,
                    crc_hash=match.group(2).lower(),
                    pos=(float(match.group(3)), float(match.group(4)), float(match.group(5))),
                    vel=(float(match.group(6)), float(match.group(7)), float(match.group(8))),
                    hp=int(match.group(9)),
                    obj=(int(match.group(10)), int(match.group(11)), int(match.group(12))),
                    ev=int(match.group(13)),
                )
                sim_hashes[tick] = entry
        except queue.Empty:
            break

    if proc.poll() is None:
        print(f"  [ERROR] Schedule {fps} Hz timed out after {timeout_sec}s! Terminating...")
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise TimeoutError(f"Process timed out at {fps} Hz after {timeout_sec}s")

    exit_code = proc.poll()
    full_output = "\n".join(output_lines)
    # a tick printed twice would be collapsed into one entry above, hiding the first
    counts = collections.Counter(int(m.group(1)) for ln in output_lines if (m := SIM_HASH_RE.search(ln)))
    repeated = sorted(t for t, c in counts.items() if c > 1)
    if repeated:
        raise RuntimeError(f"Schedule {cap_label(fps)} printed SIM_HASH more than once for ticks {repeated[:10]}")

    frames_match = re.search(r"Vulkan frames presented:\s*(\d+)", full_output)
    if frames_match:
        vulkan_frames = int(frames_match.group(1))

    duration = time.time() - start_time
    print(f"  [METRICS {cap_label(fps)}] Duration: {duration:.2f}s | Exit code: {exit_code}")
    print(f"  [METRICS {cap_label(fps)}] Vulkan frames presented: {vulkan_frames}")
    print(f"  [METRICS {cap_label(fps)}] Authoritative SIM_HASH ticks recorded: {len(sim_hashes)}")

    if exit_code != 0:
        print("\n--- Recent output before abnormal exit ---")
        print("\n".join(output_lines[-40:]))
        raise RuntimeError(f"Schedule {fps} Hz exited with non-zero exit code {exit_code}")

    if len(sim_hashes) < expected_min_ticks:
        print("\n--- Recent output before premature end ---")
        print("\n".join(output_lines[-40:]))
        raise RuntimeError(
            f"Schedule {fps} Hz captured only {len(sim_hashes)} ticks, expected >= {expected_min_ticks}"
        )

    # the replay must have run on the listen server, not in a single-player session
    if listen_port is not None and not re.search(rf"^Server spawned on \S+:{listen_port}$", full_output, re.MULTILINE):
        raise RuntimeError(f"Schedule {cap_label(fps)}: no 'Server spawned on ...:{listen_port}' line: not a listen host")

    return sim_hashes, vulkan_frames, output_lines


def render_only_draws(lines: list[str]) -> dict[str, int]:
    """--listen's exercise check, over the draws FRAME_POSE logged on the on-foot ticks:
    all of them ("drawn"), those with canUnlockFrames ("cu": the local player's
    IsFPSUnlock()), and the render-only ones ("n": a draw whose tick, fn, is the previous
    draw's, so its frame ran no tick), of which "not_unlocked" have ud=0 and "bad_fraction"
    have ud=1 with a fraction outside (0, 1). ud=1 says only that ClientUpdateView was
    called on the draw's frame (the game sets it before the function's gates); a fraction
    in (0, 1) says the call went on to present one."""
    lo, hi = ON_FOOT_TICKS
    poses = [p for ln in lines if (p := frame_poses.parse_pose(ln))]
    on_foot = [p for p in poses if lo <= p["fn"] <= hi]
    render_only = [b for a, b in zip(poses, poses[1:]) if lo <= b["fn"] <= hi and b["fn"] == a["fn"]]
    return {"drawn": len(on_foot), "cu": sum(1 for p in on_foot if p["cu"] == 1), "n": len(render_only),
            "not_unlocked": sum(1 for p in render_only if p["ud"] != 1),
            "bad_fraction": sum(1 for p in render_only if p["ud"] == 1 and not 0.0 < p["fr"] < 1.0)}


def tick_views(lines: list[str], label: str) -> dict[int, tuple[int, int]]:
    """tick -> (entry, think) from TICK_VIEW (game builds from M5 step 7); a tick printed
    twice fails."""
    records = [(int(m.group(1)), int(m.group(2)), int(m.group(3))) for ln in lines if (m := TICK_VIEW_RE.search(ln))]
    counts = collections.Counter(t for t, _, _ in records)
    repeated = sorted(t for t, c in counts.items() if c > 1)
    if repeated:
        raise AssertionError(f"{label}: TICK_VIEW printed more than once for ticks {repeated[:10]}")
    return {t: (entry, think) for t, entry, think in records}


def update_views(lines: list[str]) -> tuple[int | None, int | None]:
    """(ClientUpdateView calls over every FRAME_TIMING window, over the windows of play),
    from cuv= (engine builds from M5 step 7); None when a window lacks it."""
    windows = [m for ln in lines if (m := FRAME_TIMING_RE.search(ln))]
    if not windows or any(m.group(10) is None for m in windows):
        return None, None
    return (sum(int(m.group(10)) for m in windows),
            sum(int(m.group(10)) for m in windows if int(m.group(3)) >= PLAY_MIN_TICKS))


def sim_views(lines: list[str], label: str) -> dict[int, str]:
    """tick -> the SIM_VIEW fields (game builds from 2026-09-25); a tick printed twice fails."""
    records = [(int(m.group(1)), m.group(2).strip()) for ln in lines if (m := SIM_VIEW_RE.search(ln))]
    counts = collections.Counter(t for t, _ in records)
    repeated = sorted(t for t, c in counts.items() if c > 1)
    if repeated:
        raise AssertionError(f"{label}: SIM_VIEW printed more than once for ticks {repeated[:10]}")
    return dict(records)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", action="store_true", help="replay on a listen host (+spawnServer) instead of single player")
    ap.add_argument("--port", type=int, default=LISTEN_PORT, help="the listen host's net_port (with --listen)")
    ap.add_argument("--log-dir", type=Path, help="write each schedule's output to <dir>/<sp|listen>-<schedule>.log")
    opts = ap.parse_args(argv)
    listen_port = opts.port if opts.listen else None

    print("=================================================================")
    print(" OpenETQW High-Refresh Simulation Determinism Replay Test"
          + (f" (listen host, port {listen_port})" if opts.listen else " (single player)"))
    print(f" Working Directory: {REPO_ROOT}")
    print(f" Retail Basepath:   {DATA_PATH}")
    print(" Render Schedules:   60 Hz, 120 Hz, 144 Hz, 240 Hz, uncapped")
    print(" Target Binary:     " + (str(CLIENT_BIN) if CLIENT_BIN.is_file() or opts.listen else str(DED_BIN)))
    print("=================================================================")

    bin_path = CLIENT_BIN
    if not bin_path.is_file():
        if opts.listen:
            # the listen host draws; the dedicated binary cannot stand in for it
            print(f"[SKIP] {CLIENT_BIN} does not exist (--listen needs the client binary). Skipping test.")
            return 77  # Meson skip code
        if DED_BIN.is_file():
            print(f"[WARN] Client binary not found, falling back to dedicated: {DED_BIN}")
            bin_path = DED_BIN
        else:
            print(f"[SKIP] Neither {CLIENT_BIN} nor {DED_BIN} exists. Skipping test.")
            return 77  # Meson skip code

    missing = h.missing_required_paks(DATA_PATH)
    if missing:
        print(f"[SKIP] Retail 1.5 data incomplete at {DATA_PATH / 'base'} (missing: {', '.join(missing)}). Skipping test.")
        return 77  # Meson skip code

    rates = [60, 120, 144, 240, 0, 30, LOCKED, CHANGED]  # 0 = uncapped: this host's ceiling
    results: dict[int, dict[int, SimHashEntry]] = {}
    frames: dict[int, int] = {}
    timing: dict[int, dict | None] = {}
    schedule_lines: dict[int, list[str]] = {}

    test_start = time.time()

    for fps in rates:
        sim_hashes, presented_frames, lines = run_determinism_schedule(
            fps=fps,
            bin_path=bin_path,
            expected_min_ticks=150,
            timeout_sec=50.0,
            listen_port=listen_port,
        )
        results[fps] = sim_hashes
        frames[fps] = presented_frames
        timing[fps] = play_timing(lines)
        schedule_lines[fps] = lines
        if opts.log_dir is not None:
            opts.log_dir.mkdir(parents=True, exist_ok=True)
            name = {LOCKED: "locked240", CHANGED: "changed"}.get(fps, "uncapped" if fps == 0 else str(fps))
            (opts.log_dir / f"{'listen' if opts.listen else 'sp'}-{name}.log").write_text("\n".join(lines) + "\n")

    print("\n=================================================================")
    print(" Cross-Schedule Determinism & Bit-for-Bit Identity Verification")
    print("=================================================================")

    base_fps = rates[0]
    base_schedule = results[base_fps]
    common_ticks = sorted(set(base_schedule.keys()))

    # Verify all schedules have identical tick coverage
    for fps in rates[1:]:
        fps_ticks = sorted(set(results[fps].keys()))
        if fps_ticks != common_ticks:
            diff = set(common_ticks).symmetric_difference(set(fps_ticks))
            raise AssertionError(f"Tick set mismatch between {base_fps} Hz and {fps} Hz! Discrepant ticks: {diff}")

    print(f"  Verified identical tick count: {len(common_ticks)} ticks across all schedules.")

    mismatches = 0
    sample_ticks = [1, 20, 50, 70, 91, 105, 115, 121, 140, 150]

    print("\n--- Telemetry Sample Comparison across every schedule (SIM_HASH) ---")
    header = f"{'Tick':>5} | " + " | ".join(f"{cap_label(r)[:10]:>10}" for r in rates) + f" | {'Identical?':>10}"
    print(header)
    print("-" * len(header))

    for tick in common_ticks:
        base_entry = base_schedule[tick]
        tick_mismatch = False

        for fps in rates[1:]:
            cmp_entry = results[fps][tick]

            # 1. CRC32 bit-for-bit hash equality
            if base_entry.crc_hash != cmp_entry.crc_hash:
                print(f"[FAIL] HASH MISMATCH at tick {tick}: {base_fps}Hz=0x{base_entry.crc_hash} vs {fps}Hz=0x{cmp_entry.crc_hash}")
                tick_mismatch = True

            # 2. Position coordinates equality
            if base_entry.pos != cmp_entry.pos:
                print(f"[FAIL] POSITION MISMATCH at tick {tick}: {base_fps}Hz={base_entry.pos} vs {fps}Hz={cmp_entry.pos}")
                tick_mismatch = True

            # 3. Velocity coordinates equality
            if base_entry.vel != cmp_entry.vel:
                print(f"[FAIL] VELOCITY MISMATCH at tick {tick}: {base_fps}Hz={base_entry.vel} vs {fps}Hz={cmp_entry.vel}")
                tick_mismatch = True

            # 4. Health equality
            if base_entry.hp != cmp_entry.hp:
                print(f"[FAIL] HEALTH MISMATCH at tick {tick}: {base_fps}Hz={base_entry.hp} vs {fps}Hz={cmp_entry.hp}")
                tick_mismatch = True

            # 5. Objective state equality
            if base_entry.obj != cmp_entry.obj:
                print(f"[FAIL] OBJECTIVE MISMATCH at tick {tick}: {base_fps}Hz={base_entry.obj} vs {fps}Hz={cmp_entry.obj}")
                tick_mismatch = True

            # 6. Serviced events equality
            if base_entry.ev != cmp_entry.ev:
                print(f"[FAIL] EVENT COUNT MISMATCH at tick {tick}: {base_fps}Hz={base_entry.ev} vs {fps}Hz={cmp_entry.ev}")
                tick_mismatch = True

        if tick_mismatch:
            mismatches += 1

        if tick in sample_ticks:
            status = "MATCH (OK)" if not tick_mismatch else "MISMATCH"
            print(f"{tick:5d} | " + " | ".join(f"0x{results[r][tick].crc_hash}" for r in rates) + f" | {status:>10}")

    print("\n--- Frame rate during play (FRAME_TIMING) ---")
    rate_failures = []
    for fps in rates:
        t = timing[fps]
        if t is None:
            rate_failures.append(f"{cap_label(fps)}: no FRAME_TIMING window with >= {PLAY_MIN_TICKS} ticks")
            continue
        print(f"  {cap_label(fps):>8}: {t['fps']:6.1f} fps (median frame interval; average {t['avg_fps']:.1f})"
              f" over {t['windows']} s of play; per-second p50 <= {t['dt_p50']:.2f} ms, p99 <= {t['dt_p99']:.2f} ms,"
              f" longest frame {t['dt_max']:.2f} ms; {frames[fps]} frames presented in the whole run")
    ceiling = timing[0]["fps"] if timing.get(0) else None
    for fps in (r for r in rates if r > 0):
        t = timing[fps]
        if t is None or ceiling is None:
            continue
        if t["fps"] > CAP_OVERSHOOT * fps:
            rate_failures.append(f"{cap_label(fps)}: {t['fps']:.1f} fps is above the cap (> {CAP_OVERSHOOT:.2f} x {fps})")
        if fps <= REACH_FRACTION * ceiling:
            # the pacer, not the host, sets this rate: hold the cap
            need = PACED_FRACTION * fps
            if t["fps"] < need:
                rate_failures.append(f"{cap_label(fps)}: {t['fps']:.1f} fps is below {need:.1f}"
                                     f" ({PACED_FRACTION:.0%} of the cap, which is well below uncapped {ceiling:.1f})")
        else:
            # the host sets this rate (D1): report how close it came, do not fail
            target = min(fps, ceiling)
            print(f"  [REPORT] {cap_label(fps)}: {t['fps']:.1f} fps, {t['fps'] / target:.0%} of min(cap,"
                  f" uncapped {ceiling:.1f}) (the host's rate, reported, not failed: D1)")
        if ceiling < fps:
            print(f"  [NOTE] {cap_label(fps)} is not reachable on this host: uncapped play runs at {ceiling:.1f} fps")
    # com_unlockFPS 0: no render-only draw. Every logged draw is a base draw of a new game
    # time (frame_poses.locked_check, over the draws of play), and no play window draws more
    # often than it ticks; the draw rate is reported (a slow host may not reach 30, D1)
    poses = [p for ln in schedule_lines[LOCKED] if (p := frame_poses.parse_pose(ln))]
    play = [p for p in poses if p["gt"] > 0]
    lk = frame_poses.locked_check(play[5:], 240)  # the first draws of play settle the view
    print(f"  {cap_label(LOCKED)}: {lk['detail']}")
    if not lk["ok"]:
        rate_failures.append(f"{cap_label(LOCKED)}: not one base draw per tick: {lk['detail']}")
    windows = [m for ln in schedule_lines[LOCKED] if (m := FRAME_TIMING_RE.search(ln)) and int(m.group(3)) >= PLAY_MIN_TICKS]
    if not windows or any(m.group(9) is None for m in windows):
        rate_failures.append(f"{cap_label(LOCKED)}: FRAME_TIMING carries no draws= count (an engine before M5 step 11)")
    else:
        over = [(int(m.group(9)), int(m.group(3))) for m in windows if int(m.group(9)) > int(m.group(3))]
        print(f"  {cap_label(LOCKED)}: draws/ticks per play window {[(int(m.group(9)), int(m.group(3))) for m in windows]}")
        if over:
            rate_failures.append(f"{cap_label(LOCKED)}: play windows drew more often than they ticked (draws, ticks): {over}")
    # the cap changed during play (M5 plan step 17): the windows report the caps in order,
    # and each new cap is held in a window that starts and ends at it
    all_windows = [m for ln in schedule_lines[CHANGED] if (m := FRAME_TIMING_RE.search(ln))]
    windows = [m for m in all_windows if int(m.group(3)) >= PLAY_MIN_TICKS]
    order = CAP_ORDER
    seen = [int(m.group(4)) for m in windows]
    # (play windows need PLAY_MIN_TICKS ticks, so the short stretch at the starting cap may have
    # none; the fixed 60 Hz schedule covers that cap)
    if [c for i, c in enumerate(seen) if i == 0 or c != seen[i - 1]] not in (order, order[1:]):
        rate_failures.append(f"{cap_label(CHANGED)}: the play windows reported caps {seen}, not {order} in order")
    for cap in order[1:]:
        # a window reports the cap of its last frame, so (the caps never repeat, CAP_ORDER)
        # a window ran wholly at a cap when the window just before it already ended at that
        # cap. Both are taken from every window, not only play windows: right after spawn a
        # first-use stall can leave a window with too few ticks to count as play, whether it
        # is the one the change landed in or the next. Every printed window spans at least a
        # second, and its median frame interval is not moved by one long frame, so a window
        # with ticks and FULL_WINDOW_MIN_FRAMES frames still gives the rate.
        full = [1000.0 / float(m.group(5)) for i, m in enumerate(all_windows)
                if int(m.group(3)) > 0 and int(m.group(2)) >= FULL_WINDOW_MIN_FRAMES and int(m.group(4)) == cap
                and i > 0 and int(all_windows[i - 1].group(4)) == cap]
        if not full:
            rate_failures.append(f"{cap_label(CHANGED)}: no full play window at cap {cap}")
            continue
        r = full[0]
        print(f"  {cap_label(CHANGED)}: {r:.1f} fps in the first full window at cap {cap}")
        if r > CAP_OVERSHOOT * cap:
            rate_failures.append(f"{cap_label(CHANGED)}: {r:.1f} fps at cap {cap} is above the cap")
        if ceiling is not None and cap <= REACH_FRACTION * ceiling:
            if r < PACED_FRACTION * cap:
                rate_failures.append(f"{cap_label(CHANGED)}: {r:.1f} fps at cap {cap} is below {PACED_FRACTION * cap:.1f}"
                                     f" ({PACED_FRACTION:.0%} of a cap well below uncapped {ceiling:.1f})")
        elif ceiling is not None:
            print(f"  [REPORT] {cap_label(CHANGED)}: {r:.1f} fps at cap {cap}, the host's rate (D1)")
    for fps in rates:
        t = timing[fps]
        if fps != CHANGED and t is not None and t["caps"] != [abs(fps)]:
            rate_failures.append(f"{cap_label(fps)}: FRAME_TIMING reported caps {t['caps']}, expected [{abs(fps)}]")
    for f in rate_failures:
        print(f"  [FAIL] {f}")

    total_duration = time.time() - test_start
    print(f"\n[SUMMARY] Total runtime for all {len(rates)} schedules: {total_duration:.2f}s")
    print(f"[SUMMARY] Total simulation ticks verified per schedule: {len(common_ticks)}")
    print(f"[SUMMARY] Total state comparisons across {len(rates)} schedules: {len(common_ticks) * (len(rates) - 1)}")
    print(f"[SUMMARY] Total mismatches detected: {mismatches}")

    assert mismatches == 0, f"Determinism failure: {mismatches} state mismatches detected across render schedules!"

    print("\n--- Tick-produced view state (SIM_VIEW) across schedules ---")
    views = {fps: sim_views(schedule_lines[fps], cap_label(fps)) for fps in rates}
    if not views[rates[0]]:
        raise AssertionError("no SIM_VIEW lines: the game build predates them")
    # every simulated tick must have its SIM_VIEW line, in every schedule, or the
    # comparison below would skip the missing ones
    for fps in rates:
        missing = sorted(set(common_ticks) - set(views[fps]))
        extra = sorted(set(views[fps]) - set(common_ticks))
        if missing or extra:
            raise AssertionError(f"{cap_label(fps)}: SIM_VIEW ticks do not match the SIM_HASH ticks"
                                 f" (missing {missing[:10]}{'...' if len(missing) > 10 else ''},"
                                 f" extra {extra[:10]}{'...' if len(extra) > 10 else ''})")
    # and every line must carry every field with a finite value (a view player on every
    # tick; "view=-1" fails too), or a field missing from all schedules, or a no-view
    # record on the same ticks in all of them, would compare equal
    for fps in rates:
        bad = [t for t, v in sorted(views[fps].items()) if not SIM_VIEW_FIELDS_RE.fullmatch(v)]
        if bad:
            raise AssertionError(f"{cap_label(fps)}: {len(bad)} SIM_VIEW line(s) lack a field, a finite value or"
                                 f" a view player (first, tick {bad[0]}: {views[fps][bad[0]]})")
    # the replay must exercise each part: a logged weapon pose, a vehicle pose and a
    # vehicle view axis (it rides a vehicle on ticks 92-114)
    parsed = [SIM_VIEW_FIELDS_RE.fullmatch(v) for v in views[rates[0]].values()]
    if not any(m.group(2) != "-" for m in parsed):
        raise AssertionError("SIM_VIEW never logged a weapon pose")
    if not any(float(x) != 0.0 for m in parsed for x in m.group(3)[1:-1].split(",")):
        raise AssertionError("SIM_VIEW never logged a vehicle pose")
    if not any(m.group(4) != "-" for m in parsed):
        raise AssertionError("SIM_VIEW never logged a vehicle view axis")

    def fields_of(line):
        return dict(re.findall(r"(\w+)=(\([^)]*\)|\S+)", line or ""))

    # every schedule's SIM_VIEW equal to cap 30's on every tick (cap 30 runs no render-only
    # frame, with the base-draw path on; M5 step 6 removed the last known difference)
    view_mismatches = []
    for fps in (r for r in rates if r != 30):
        diff = sorted(t for t in views[30] if views[fps].get(t) != views[30][t])
        print(f"  SIM_VIEW against cap 30: {cap_label(fps)}: "
              + ("identical" if not diff else f"differs on {len(diff)} ticks ({diff[0]}-{diff[-1]})"))
        for tick in diff:
            a, b = fields_of(views[30][tick]), fields_of(views[fps].get(tick))
            differing = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            view_mismatches.append(f"tick {tick} ({', '.join(differing)}): {cap_label(30)} {views[30][tick]}"
                                   f" | {cap_label(fps)} {views[fps].get(tick)}")
    print(f"  {len(views[30])} ticks per schedule; {len(view_mismatches)} differences from cap 30")
    for m in view_mismatches[:10]:
        print(f"  [FAIL] {m}")
    assert not view_mismatches, f"render-frame state leaked into {len(view_mismatches)} tick(s) (SIM_VIEW against cap 30)"

    # M5 step 5a: the occupied vehicle's full physics and control state (the titan,
    # ticks 92-115), every field in full precision, must not depend on the cap either
    print("\n--- Vehicle physics (VEH_TRACE) across schedules ---")
    veh = {fps: [m.group(1) for ln in schedule_lines[fps] if (m := VEH_TRACE_RE.search(ln))] for fps in rates}
    if not veh[rates[0]]:
        raise AssertionError("no VEH_TRACE lines: the game build predates them (M5 step 5a), or the titan"
                             " was never entered (look for ENTER_VEHICLE in the schedule's output)")
    for fps in rates[1:]:
        if veh[fps] != veh[rates[0]]:
            diff = next((i for i, (a, b) in enumerate(zip(veh[rates[0]], veh[fps])) if a != b),
                        min(len(veh[rates[0]]), len(veh[fps])))
            base = veh[rates[0]][diff] if diff < len(veh[rates[0]]) else "(none)"
            other = veh[fps][diff] if diff < len(veh[fps]) else "(none)"
            raise AssertionError(f"VEH_TRACE differs between {cap_label(rates[0])} and {cap_label(fps)} at line {diff + 1}"
                                 f" ({len(veh[rates[0]])} and {len(veh[fps])} lines):\n"
                                 f"    {cap_label(rates[0])}: {base}\n    {cap_label(fps)}: {other}")
    print(f"  {len(veh[rates[0]])} VEH_TRACE lines, identical at every cap")
    # each comparison above holds every schedule to one reference, so all are equal
    print(f"  SIM_HASH, SIM_VIEW and VEH_TRACE: every schedule identical to {cap_label(30)} and to"
          f" {cap_label(LOCKED)} on all {len(common_ticks)} ticks")

    exercise_failures = []
    exercised = bin_path == CLIENT_BIN  # the dedicated fallback draws nothing, so prepares nothing
    # M5 step 7: render-only frames are prepared (ClientUpdateView) only when they can be
    # drawn; FRAME_TIMING cuv= counts them whether or not anything is drawn
    print("\n--- Render-only frames prepared for drawing (FRAME_TIMING cuv=) ---")
    for fps in rates:
        total, in_play = update_views(schedule_lines[fps])
        if total is None:
            exercise_failures.append(f"{cap_label(fps)}: FRAME_TIMING carries no cuv= count (an engine before M5 step 7)")
            continue
        print(f"  {cap_label(fps)}: {total} ClientUpdateView calls ({in_play} in play windows)")
        if fps == LOCKED and total:
            exercise_failures.append(f"{cap_label(fps)}: {total} ClientUpdateView calls with com_unlockFPS 0")
        elif fps not in (30, LOCKED) and exercised and not in_play:
            exercise_failures.append(f"{cap_label(fps)}: no ClientUpdateView call in play (render-only frames not prepared)")

    # M5 step 7 (owner decision E3): tick code never reads a render-only frame's view. The
    # game puts back the view player's renderView at each tick's entry; TICK_VIEW shows the
    # view before that (entry) and as entity Think starts (think)
    print("\n--- The view player's renderView at tick entry (TICK_VIEW) ---")
    lo, hi = ON_FOOT_TICKS
    for fps in rates:
        tv = tick_views(schedule_lines[fps], cap_label(fps))
        if not tv:
            exercise_failures.append(f"{cap_label(fps)}: no TICK_VIEW lines: the game build predates them (M5 step 7)")
            continue
        if set(tv) != set(common_ticks):
            missing = sorted(set(common_ticks) - set(tv))
            exercise_failures.append(f"{cap_label(fps)}: TICK_VIEW ticks do not match the SIM_HASH ticks"
                                     f" (missing {missing[:10]}, extra {sorted(set(tv) - set(common_ticks))[:10]})")
        moved = sorted(t for t, (entry, _) in tv.items() if entry == 1)
        leaked = sorted(t for t, (_, think) in tv.items() if think == 1)
        unmatched = sorted(t for t, (entry, _) in tv.items() if entry == -1)
        # think=-1 after entry=1: the tick changed the seat before Think (the replay's enterVehicle)
        print(f"  {cap_label(fps)}: a render-only frame's view at the tick's entry on {len(moved)} of {len(tv)} ticks"
              f" ({sum(1 for t in moved if lo <= t <= hi)} of them on the on-foot ticks {lo}-{hi}); Think started on the"
              f" tick's view on {sum(1 for t in moved if tv[t][1] == 0)} of them, after a seat change on"
              f" {sum(1 for t in moved if tv[t][1] == -1)}; Think started on another view on {len(leaked)} ticks; nothing"
              f" to compare with at the entry of {len(unmatched)} {unmatched[:12]}")
        if leaked:
            exercise_failures.append(f"{cap_label(fps)}: entity Think started on a view other than the tick's"
                                     f" (TICK_VIEW think=1) on {len(leaked)} ticks, first {leaked[:10]}")
        if fps == LOCKED and moved:
            exercise_failures.append(f"{cap_label(fps)}: the view moved between ticks with com_unlockFPS 0"
                                     f" (TICK_VIEW entry=1) on ticks {moved[:10]}")
        elif fps not in (30, LOCKED) and exercised and not moved:
            exercise_failures.append(f"{cap_label(fps)}: no render-only frame's view reached a tick (TICK_VIEW entry=1):"
                                     " the put-back is not exercised")

    if opts.listen:
        # whether the host's draws between ticks at the caps above 30 are unlocked ones (M5
        # step 7). With com_unlockFPS 0 a frame that ran no tick is not drawn (locked_check),
        # so no draw shows whether ClientUpdateView ran there: cuv= above counts the calls
        print(f"\n--- The host's render-only draws on the on-foot ticks {lo}-{hi} (FRAME_POSE fn repeated) ---")
        for fps in rates:
            ro = render_only_draws(schedule_lines[fps])
            misses = ro["not_unlocked"] + ro["bad_fraction"]
            print(f"  {cap_label(fps)}: {ro['n']} render-only of {ro['drawn']} draws ({ro['cu']} with cu=1):"
                  f" {ro['not_unlocked']} not unlocked (ud=0), {ro['bad_fraction']} unlocked with a fraction outside (0, 1)")
            if ro["drawn"] == 0:
                exercise_failures.append(f"{cap_label(fps)}: no FRAME_POSE draw on ticks {lo}-{hi} (g_logFramePoses refused?)")
            elif LISTEN_HOST_UNLOCKS and fps not in (30, LOCKED):
                if ro["n"] < frame_poses.MIN_PAIRS:
                    exercise_failures.append(f"{cap_label(fps)}: only {ro['n']} render-only draws on ticks {lo}-{hi}"
                                             f" (need >= {frame_poses.MIN_PAIRS})")
                elif misses > frame_poses.allowed_misses(ro["n"]):
                    exercise_failures.append(f"{cap_label(fps)}: {misses} of {ro['n']} render-only draws are not unlocked"
                                             f" draws with a fraction in (0, 1) (ClientUpdateView; allowed"
                                             f" {frame_poses.allowed_misses(ro['n'])})")
        if not LISTEN_HOST_UNLOCKS:
            print("  [REPORT] counted, not asserted: until M5 step 7 the listen host never calls ClientUpdateView"
                  " (LISTEN_HOST_UNLOCKS)")
    for f in exercise_failures:
        print(f"  [FAIL] {f}")
    assert not rate_failures, f"Frame rate failures: {rate_failures}"
    assert not exercise_failures, f"Exercise check failures: {exercise_failures}"

    print("\n[PASSED] 100% BIT-FOR-BIT IDENTICAL SIMULATION AT CAPS 60, 120, 144, 240, UNCAPPED AND 30, AT 240 WITH"
          " com_unlockFPS 0, AND WITH THE CAP CHANGED IN PLAY; SIM_VIEW IDENTICAL TO CAP 30 ON EVERY TICK;"
          " FRAME RATES WITHIN BOUNDS" + (" (LISTEN HOST)." if opts.listen else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
