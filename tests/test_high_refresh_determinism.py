#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""High-Refresh Simulation Determinism Replay Test for OpenETQW Milestone 5.

Verifies that decoupling Vulkan frame presentation (com_maxFPS 60, 120, 144, 240 Hz)
from authoritative 30 Hz simulation (USERCMD_HZ = 30, USERCMD_MSEC = 33) produces
zero simulation drift or physics divergence on stock map Valley (maps/valley.world).

Validates:
1. Fixed tick-indexed command stream replayed across 60, 120, 144, and 240 Hz render schedules.
2. Bit-for-bit identical CRC32 simulation hashes (SIM_HASH) across all 4 refresh rates.
3. Identical player/vehicle coordinates, velocities, health, objective state, and serviced event counts.
4. Smooth presentation scaling with com_maxFPS and clean shutdown with exit code 0.
"""

from __future__ import annotations

import atexit
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

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = h.env_data_path()
CLIENT_BIN = h.build_binary("client")
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
    print(f"\n--- [SCHEDULE {fps} Hz] Launching Valley determinism replay (com_maxFPS {fps}) ---")

    args = [
        str(bin_path),
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "r_swapInterval", "0",
        "+set", "com_maxFPS", str(fps),
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

        except queue.Empty:
            continue

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

    frames_match = re.search(r"Vulkan frames presented:\s*(\d+)", full_output)
    if frames_match:
        vulkan_frames = int(frames_match.group(1))

    duration = time.time() - start_time
    print(f"  [METRICS {fps} Hz] Duration: {duration:.2f}s | Exit code: {exit_code}")
    print(f"  [METRICS {fps} Hz] Vulkan frames presented: {vulkan_frames}")
    print(f"  [METRICS {fps} Hz] Authoritative SIM_HASH ticks recorded: {len(sim_hashes)}")

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


def main() -> int:
    print("=================================================================")
    print(" OpenETQW High-Refresh Simulation Determinism Replay Test")
    print(f" Working Directory: {REPO_ROOT}")
    print(f" Retail Basepath:   {DATA_PATH}")
    print(" Render Schedules:   60 Hz, 120 Hz, 144 Hz, 240 Hz")
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

    if not (DATA_PATH / "base" / "pak000.pk4").is_file():
        print(f"[SKIP] Retail assets not found at {DATA_PATH / 'base' / 'pak000.pk4'}. Skipping test.")
        return 77  # Meson skip code

    rates = [60, 120, 144, 240]
    results: dict[int, dict[int, SimHashEntry]] = {}
    frames: dict[int, int] = {}

    test_start = time.time()

    for fps in rates:
        sim_hashes, presented_frames, _ = run_determinism_schedule(
            fps=fps,
            bin_path=bin_path,
            expected_min_ticks=150,
            timeout_sec=50.0,
        )
        results[fps] = sim_hashes
        frames[fps] = presented_frames

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

    print("\n--- Telemetry Sample Comparison across 60, 120, 144, 240 Hz ---")
    header = f"{'Tick':>5} | {'60 Hz Hash':>10} | {'120 Hz Hash':>10} | {'144 Hz Hash':>10} | {'240 Hz Hash':>10} | {'Identical?':>10}"
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
            h60 = results[60][tick].crc_hash
            h120 = results[120][tick].crc_hash
            h144 = results[144][tick].crc_hash
            h240 = results[240][tick].crc_hash
            status = "MATCH (OK)" if not tick_mismatch else "MISMATCH"
            print(f"{tick:5d} | 0x{h60} | 0x{h120} | 0x{h144} | 0x{h240} | {status:>10}")

    print("\n--- Presentation Cadence Summary ---")
    for fps in rates:
        print(f"  {fps:3d} Hz schedule presented: {frames[fps]:4d} Vulkan frames ({frames[fps]/150:.2f} frames/tick)")

    assert frames[60] > 0, "Expected non-zero Vulkan frames at 60 Hz"
    assert frames[120] > frames[60], f"Expected 120 Hz frames ({frames[120]}) > 60 Hz frames ({frames[60]})"

    total_duration = time.time() - test_start
    print(f"\n[SUMMARY] Total runtime for all 4 schedules: {total_duration:.2f}s")
    print(f"[SUMMARY] Total simulation ticks verified per schedule: {len(common_ticks)}")
    print(f"[SUMMARY] Total state comparisons across 4 schedules: {len(common_ticks) * 3}")
    print(f"[SUMMARY] Total mismatches detected: {mismatches}")

    assert mismatches == 0, f"Determinism failure: {mismatches} state mismatches detected across render schedules!"

    print("\n[PASSED] 100% BIT-FOR-BIT IDENTICAL SIMULATION ACROSS 60, 120, 144, AND 240 HZ RENDER SCHEDULES.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
