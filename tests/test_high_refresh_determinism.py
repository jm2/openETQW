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
   ticks (FRAME_TIMING draws= <= ticks=). Which schedules produce the same SIM_VIEW as
   cap 30 is reported (M5 step 6 is to make every cap match it).
   A further schedule changes the cap during play (M5 plan step 17): it starts at 60,
   sets com_unlock_maxFPS 90 once tick 10 is logged and com_maxFPS 45 once tick 80 is
   (over stdin, as a player would at the console), about 70 ticks at each so that a
   FRAME_TIMING window of about a second falls wholly inside each whatever the phase.
   It is compared like the others; its FRAME_TIMING windows must report the caps in
   that order (the alias carries com_unlock_maxFPS into com_maxFPS), and each new cap
   must be held in a full window at it, by the same rule as the fixed caps. (At cap 45
   the entry tick has zero or one render-only frame, so its wlog leak, if any, has the
   shape of cap 60's, which KNOWN_VIEW_LEAK's bounds were measured on.)
2. Bit-for-bit identical CRC32 simulation hashes (SIM_HASH) across all schedules, and
   identical SIM_VIEW lines: state each tick produces that later ticks read back and that
   a render frame could write into (the view angles after UpdateViewAngles, the tick's
   logged view origin and weapon offset and orientation, the vehicle's last pushed pose and
   the axis its view last returned; docs/frame-path.md section 6.9). One divergence is
   known and allowed in a bounded
   shape (KNOWN_VIEW_LEAK): after the replay enters the vehicle, the logged weapon pose
   (wlog) can differ between schedules, because the render frames of the entering tick
   still run the on-foot unlock path and write the weapon's render entity, which the
   vehicle ticks then log. Any other SIM_VIEW difference fails. Every schedule must print
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
"""

from __future__ import annotations

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
# found 2026-09-25 (docs/frame-path.md section 9): in some runs wlog differs from the
# tick after the replay's enterVehicle (91) to the end of the replay, between one
# schedule and the 60 Hz one. At full precision (game PR #10) it covers 55-56 of the 59
# ticks 92-150 when it appears (7 of 10 runs, DVD and 1.5 data); two decimals had hidden
# the smaller differences (31-40 ticks visible). Allowed only in that shape: the wlog
# field alone, a weapon pose on both sides, starting at tick 92, on 45-59 ticks per
# schedule. Nothing else may differ.
# 2026-09-26 (game 78ec05e): the stream now enters the map's only vehicle_titan instead
# of the nearest vehicle. From where the replay is at tick 90 the two nearest are
# icarus_78 (2210.6 u) and desecrator_77 (2225.9 u), so the arm64/x86_64 difference in
# the path decided it: Linux took the icarus, whose view logs no axis (the test needs
# one), and macOS the desecrator, the 55-56-tick shape above. With the titan, on antec
# (5 runs, 17 comparisons) the leak starts at tick 92 and covers 43 or 53-55 ticks; the
# bounds cover both vehicles. Not yet measured with the titan on macOS.
KNOWN_VIEW_LEAK = {"field": "wlog", "from_tick": 92, "to_tick": 150, "min_ticks": 40, "max_ticks": 59}

FRAME_TIMING_RE = re.compile(
    r"FRAME_TIMING: window_ms=([\d.]+) frames=(\d+) ticks=(\d+) cap=(-?\d+)"
    r" dt_p50=([\d.]+) dt_p95=([\d.]+) dt_p99=([\d.]+) dt_max=([\d.]+)"
    r"(?:.*? draws=(\d+))?"
)
LOCKED = -240           # the schedule at cap 240 with com_unlockFPS 0 (M5 plan step 11, D8)
CHANGED = -1            # the schedule whose cap changes during play (M5 plan step 17)
CAP_START = 60
CAP_CHANGES = ((10, "com_unlock_maxFPS", 90), (80, "com_maxFPS", 45))  # (after tick, CVar, value); starts at CAP_START
CAP_ORDER = [CAP_START] + [value for _, _, value in CAP_CHANGES]
PLAY_MIN_TICKS = 25     # a FRAME_TIMING window with fewer game ticks is loading or menus
CAP_OVERSHOOT = 1.03    # a schedule may run at most this much above its cap
PACED_FRACTION = 0.98   # a cap <= REACH_FRACTION x the uncapped rate must be held this closely
REACH_FRACTION = 0.9    # a cap above this fraction of the uncapped rate is the host's (D1: reported)


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
) -> tuple[dict[int, SimHashEntry], int, list[str]]:
    """Runs a single determinism schedule at the given com_maxFPS."""
    print(f"\n--- [SCHEDULE {cap_label(fps)}] Launching Valley determinism replay (com_maxFPS {start_cap(fps)}) ---")

    args = [
        str(bin_path),
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_savepath", str(SAVEPATH),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "r_swapInterval", "0",
        "+set", "com_maxFPS", str(start_cap(fps)),
        "+set", "com_unlockFPS", "0" if fps == LOCKED else "1",
        "+set", "g_logFramePoses", "4" if fps == LOCKED else "0",
        "+set", "com_logFrameTiming", "1",
        "+set", "g_determinismTest", "1",
        "+set", "g_simHash", "1",
        "+devmap", "valley",
        "+clientTeam", "gdf",
        "+clientClass", "soldier",
        "+closeLimbo",
    ]

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

    return sim_hashes, vulkan_frames, output_lines


def sim_views(lines: list[str], label: str) -> dict[int, str]:
    """tick -> the SIM_VIEW fields (game builds from 2026-09-25); a tick printed twice fails."""
    records = [(int(m.group(1)), m.group(2).strip()) for ln in lines if (m := SIM_VIEW_RE.search(ln))]
    counts = collections.Counter(t for t, _ in records)
    repeated = sorted(t for t, c in counts.items() if c > 1)
    if repeated:
        raise AssertionError(f"{label}: SIM_VIEW printed more than once for ticks {repeated[:10]}")
    return dict(records)


def main() -> int:
    print("=================================================================")
    print(" OpenETQW High-Refresh Simulation Determinism Replay Test")
    print(f" Working Directory: {REPO_ROOT}")
    print(f" Retail Basepath:   {DATA_PATH}")
    print(" Render Schedules:   60 Hz, 120 Hz, 144 Hz, 240 Hz, uncapped")
    print(" Target Binary:     " + (str(CLIENT_BIN) if CLIENT_BIN.is_file() else str(DED_BIN)))
    print("=================================================================")

    bin_path = CLIENT_BIN
    if not bin_path.is_file():
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
        )
        results[fps] = sim_hashes
        frames[fps] = presented_frames
        timing[fps] = play_timing(lines)
        schedule_lines[fps] = lines

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
    windows = [m for ln in schedule_lines[CHANGED] if (m := FRAME_TIMING_RE.search(ln)) and int(m.group(3)) >= PLAY_MIN_TICKS]
    order = CAP_ORDER
    seen = [int(m.group(4)) for m in windows]
    # (play windows need PLAY_MIN_TICKS ticks, so the short stretch at the starting cap may have
    # none; the fixed 60 Hz schedule covers that cap)
    if [c for i, c in enumerate(seen) if i == 0 or c != seen[i - 1]] not in (order, order[1:]):
        rate_failures.append(f"{cap_label(CHANGED)}: the play windows reported caps {seen}, not {order} in order")
    for cap in order[1:]:
        full = [1000.0 / float(m.group(5)) for i, m in enumerate(windows)
                if int(m.group(4)) == cap and i > 0 and int(windows[i - 1].group(4)) == cap]
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

    view_mismatches, known = [], 0
    known_span: dict[int, list[int]] = {}
    for fps in rates[1:]:
        for tick, line in views[rates[0]].items():
            other = views[fps].get(tick)
            if other == line:
                continue
            a, b = fields_of(line), fields_of(other)
            differing = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            field = KNOWN_VIEW_LEAK["field"]
            if (differing == [field] and KNOWN_VIEW_LEAK["from_tick"] <= tick <= KNOWN_VIEW_LEAK["to_tick"]
                    and a.get(field, "-").startswith("(") and b.get(field, "-").startswith("(")):
                known += 1
                known_span.setdefault(fps, []).append(tick)
                continue
            view_mismatches.append(f"tick {tick} ({', '.join(differing)}): {cap_label(rates[0])} {line} | {cap_label(fps)} {other}")
    print(f"  {len(views[rates[0]])} ticks per schedule; {len(view_mismatches)} unexpected differences,"
          f" {known} known (the weapon log after entering the vehicle)")
    for fps, ticks in sorted(known_span.items()):
        print(f"    known: {cap_label(fps)} against {cap_label(rates[0])}, ticks {min(ticks)}-{max(ticks)} ({len(ticks)})")
        # the observed shape: it starts on the tick after entering the vehicle and covers
        # 43-56 ticks at full precision (see KNOWN_VIEW_LEAK); a divergence that starts
        # later or is much smaller is new
        if (min(ticks) != KNOWN_VIEW_LEAK["from_tick"]
                or not KNOWN_VIEW_LEAK["min_ticks"] <= len(ticks) <= KNOWN_VIEW_LEAK["max_ticks"]):
            view_mismatches.append(f"{cap_label(fps)}: a weapon-log divergence on ticks {min(ticks)}-{max(ticks)}"
                                   f" ({len(ticks)} ticks) does not have the known shape (from tick"
                                   f" {KNOWN_VIEW_LEAK['from_tick']}, {KNOWN_VIEW_LEAK['min_ticks']}-{KNOWN_VIEW_LEAK['max_ticks']} ticks)")
    # M5 step 6's target: every schedule's SIM_VIEW equal to cap 30's (no render-only frames,
    # the base-draw path on); reported until step 6 lands
    for fps in (r for r in rates if r != 30):
        diff = sorted(t for t in views[30] if views[fps].get(t) != views[30][t])
        print(f"  SIM_VIEW against cap 30: {cap_label(fps)}: "
              + ("identical" if not diff else f"differs on {len(diff)} ticks ({diff[0]}-{diff[-1]})"))
    for m in view_mismatches[:10]:
        print(f"  [FAIL] {m}")
    assert not view_mismatches, f"render-frame state leaked into {len(view_mismatches)} tick(s) (SIM_VIEW)"

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
    assert not rate_failures, f"Frame rate failures: {rate_failures}"

    print("\n[PASSED] 100% BIT-FOR-BIT IDENTICAL SIMULATION AT CAPS 60, 120, 144, 240, UNCAPPED AND 30, AT 240 WITH"
          " com_unlockFPS 0, AND WITH THE CAP CHANGED IN PLAY; FRAME RATES WITHIN BOUNDS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
