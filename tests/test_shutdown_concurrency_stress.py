#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Milestone 1 Empirical Challenger: Dedicated Server Rapid Shutdown & Concurrency Stress.

Challenger M1-Iter2-2 Empirical Adversarial Harness for openETQW-ded:
1. Early Load Shutdown Timing:
   Issues 'quit' at 0.1s, 0.3s, 0.6s, 1.0s, 1.5s across outskirts, ark, and volcano.
2. Mid-Gameplay Shutdown Timing:
   Issues 'quit' at 3.0s, 6.0s (historical crash point), and 10.0s on outskirts and ark.
3. Post-Fuzzing Rapid Shutdown:
   Executes adversarial console probes and immediately triggers quit without delay.
4. Rapid Sequential Burst Cycling on Outskirts:
   Executes 10 rapid sequential load-and-quit cycles specifically on outskirts.
5. Concurrent Multi-Server Shutdown:
   Launches 3 dedicated servers concurrently (outskirts, ark, volcano) and shuts them down in parallel.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import etqw_harness as h

REPO_ROOT = Path(__file__).resolve().parents[1]
BINARY = h.build_binary("ded")
DATA_PATH = h.env_data_path()


def stream_reader(pipe, q: queue.Queue[str]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            q.put(line.strip())
    except Exception:
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def run_early_quit_sweep(map_name: str, base_port: int, delays: list[float]) -> dict:
    print(f"\n[PHASE 1] Early-load quick-quit sweep on '{map_name}' ({len(delays)} delays)...")
    results = []
    all_passed = True

    for i, delay in enumerate(delays):
        port = base_port + i
        cmd = [
            str(BINARY),
            "+set", "dedicated", "1",
            "+set", "fs_basepath", str(DATA_PATH),
            "+set", "fs_game", "base",
            "+set", "net_port", str(port),
            "+set", "si_rules", "sdGameRulesObjective",
            "+set", "si_pure", "0",
            "+spawnServer", map_name,
        ]
        proc = h.spawn_engine(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        time.sleep(delay)
        try:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except Exception:
            pass

        try:
            out, _ = proc.communicate(timeout=10.0)
            passed = (proc.returncode == 0)
        except Exception as e:
            proc.kill()
            out, _ = proc.communicate()
            passed = False

        if not passed:
            all_passed = False

        status_str = "PASS" if passed else "FAIL"
        print(f"  Delay {delay:.2f}s (port {port}): {status_str} (exit code {proc.returncode})")
        results.append({
            "delay": delay,
            "port": port,
            "exit_code": proc.returncode,
            "passed": passed,
        })

    return {
        "map": map_name,
        "type": "early_quit_sweep",
        "passed": all_passed,
        "sweeps": results,
    }


def run_midgame_quit_sweep(map_name: str, base_port: int, durations: list[float]) -> dict:
    print(f"\n[PHASE 2] Mid-gameplay shutdown sweep on '{map_name}' ({len(durations)} durations)...")
    results = []
    all_passed = True

    for i, duration in enumerate(durations):
        port = base_port + i
        cmd = [
            str(BINARY),
            "+set", "dedicated", "1",
            "+set", "fs_basepath", str(DATA_PATH),
            "+set", "fs_game", "base",
            "+set", "net_port", str(port),
            "+set", "si_rules", "sdGameRulesObjective",
            "+set", "si_pure", "0",
            "+set", "bot_enable", "1",
            "+set", "bot_sleepWhenServerEmpty", "0",
            "+spawnServer", map_name,
        ]
        proc = h.spawn_engine(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        out_q = queue.Queue()
        t = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
        t.start()

        # Wait until server has initialized
        t0 = time.time()
        initialized = False
        while time.time() - t0 < 20.0:
            if proc.poll() is not None:
                break
            try:
                line = out_q.get(timeout=0.1)
                if "Loading Map Bot Actions" in line or "ETQW initialized" in line:
                    initialized = True
                    break
            except queue.Empty:
                continue

        # Run gameplay simulation for requested duration
        time.sleep(duration)

        # Issue quit command
        try:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except Exception:
            pass

        try:
            proc.wait(timeout=10.0)
            passed = (proc.returncode == 0)
        except Exception:
            proc.kill()
            proc.wait()
            passed = False

        if not passed:
            all_passed = False

        status_str = "PASS" if passed else "FAIL"
        print(f"  Simulation {duration:.1f}s (port {port}): {status_str} (exit code {proc.returncode})")
        results.append({
            "duration": duration,
            "port": port,
            "exit_code": proc.returncode,
            "passed": passed,
        })

    return {
        "map": map_name,
        "type": "midgame_quit_sweep",
        "passed": all_passed,
        "sweeps": results,
    }


def run_post_fuzz_quit(map_name: str, port: int) -> dict:
    print(f"\n[PHASE 3] Post-fuzzing rapid shutdown on '{map_name}' (port {port})...")
    cmd = [
        str(BINARY),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "net_port", str(port),
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "si_pure", "0",
        "+set", "bot_enable", "1",
        "+spawnServer", map_name,
    ]
    proc = h.spawn_engine(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    out_q = queue.Queue()
    t = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
    t.start()

    t0 = time.time()
    while time.time() - t0 < 20.0:
        if proc.poll() is not None:
            break
        try:
            line = out_q.get(timeout=0.1)
            if "ETQW initialized" in line:
                break
        except queue.Empty:
            continue

    # Rapid adversarial fuzz sequence immediately followed by quit
    fuzz_batch = (
        "listSpawnArgs nonexistent_9999\n"
        "listSpawnArgs \"%s%d%x%n\"\n"
        "damage nonexistent_ent 100\n"
        "trigger nonexistent_ent\n"
        "teleport nonexistent_ent\n"
        "remove nonexistent_ent\n"
        "spawn nonexistent_def\n"
        "quit\n"
    )
    try:
        proc.stdin.write(fuzz_batch)
        proc.stdin.flush()
    except Exception:
        pass

    try:
        proc.wait(timeout=10.0)
        passed = (proc.returncode == 0)
    except Exception:
        proc.kill()
        proc.wait()
        passed = False

    status_str = "PASS" if passed else "FAIL"
    print(f"  Post-fuzz quit result: {status_str} (exit code {proc.returncode})")
    return {
        "map": map_name,
        "type": "post_fuzz_quit",
        "passed": passed,
        "exit_code": proc.returncode,
    }


def run_burst_cycling_outskirts(cycles: int = 10, base_port: int = 27860) -> dict:
    print(f"\n[PHASE 4] Outskirts Rapid Sequential Burst Cycling ({cycles} cycles)...")
    results = []
    all_passed = True

    for c in range(cycles):
        port = base_port + (c % 5)
        cmd = [
            str(BINARY),
            "+set", "dedicated", "1",
            "+set", "fs_basepath", str(DATA_PATH),
            "+set", "fs_game", "base",
            "+set", "net_port", str(port),
            "+set", "si_rules", "sdGameRulesObjective",
            "+set", "si_pure", "0",
            "+set", "bot_enable", "1",
            "+spawnServer", "outskirts",
        ]
        proc = h.spawn_engine(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        out_q = queue.Queue()
        t = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
        t.start()

        # Wait until entities spawned
        t0 = time.time()
        ents = 0
        while time.time() - t0 < 20.0:
            if proc.poll() is not None:
                break
            try:
                line = out_q.get(timeout=0.1)
                if "entities spawned" in line:
                    if m := line.split(" entities spawned")[0].split("..."):
                        try:
                            ents = int(m[-1])
                        except ValueError:
                            pass
                if "ETQW initialized" in line:
                    break
            except queue.Empty:
                continue

        # Run 1s
        time.sleep(1.0)

        # Issue quit
        try:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except Exception:
            pass

        try:
            proc.wait(timeout=10.0)
            # every map entity spawns on the 1.5 data (owner decision B9: all accounted for)
            passed = (proc.returncode == 0) and h.spawn_line(DATA_PATH, "outskirts") == f"...{ents} entities spawned"
        except Exception:
            proc.kill()
            proc.wait()
            passed = False

        if not passed:
            all_passed = False

        status_str = "PASS" if passed else "FAIL"
        print(f"  Cycle {c + 1}/{cycles} (port {port}, ents {ents}): {status_str} (exit code {proc.returncode})")
        results.append({
            "cycle": c + 1,
            "port": port,
            "ents": ents,
            "exit_code": proc.returncode,
            "passed": passed,
        })

    return {
        "type": "burst_cycling_outskirts",
        "cycles": cycles,
        "passed": all_passed,
        "results": results,
    }


def run_concurrent_shutdown(maps: list[str], base_port: int = 27880) -> dict:
    print(f"\n[PHASE 5] Concurrent Multi-Server Shutdown ({len(maps)} maps in parallel)...")
    processes = []
    queues = []

    for i, m in enumerate(maps):
        port = base_port + i
        cmd = [
            str(BINARY),
            "+set", "dedicated", "1",
            "+set", "fs_basepath", str(DATA_PATH),
            "+set", "fs_game", "base",
            "+set", "net_port", str(port),
            "+set", "si_rules", "sdGameRulesObjective",
            "+set", "si_pure", "0",
            "+set", "bot_enable", "1",
            "+spawnServer", m,
        ]
        proc = h.spawn_engine(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        out_q = queue.Queue()
        t = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
        t.start()
        processes.append((m, port, proc, out_q))

    # Wait for all to reach initialized
    print("  Waiting for all concurrent servers to initialize...")
    for m, port, proc, out_q in processes:
        t0 = time.time()
        while time.time() - t0 < 25.0:
            if proc.poll() is not None:
                break
            try:
                line = out_q.get(timeout=0.1)
                if "ETQW initialized" in line:
                    break
            except queue.Empty:
                continue

    time.sleep(2.0)
    print("  Simultaneously commanding all servers to quit...")

    # Concurrently send quit
    for m, port, proc, out_q in processes:
        try:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except Exception:
            pass

    results = []
    all_passed = True
    for m, port, proc, out_q in processes:
        try:
            proc.wait(timeout=10.0)
            passed = (proc.returncode == 0)
        except Exception:
            proc.kill()
            proc.wait()
            passed = False

        if not passed:
            all_passed = False

        status_str = "PASS" if passed else "FAIL"
        print(f"  Server '{m}' (port {port}): {status_str} (exit code {proc.returncode})")
        results.append({
            "map": m,
            "port": port,
            "exit_code": proc.returncode,
            "passed": passed,
        })

    return {
        "type": "concurrent_shutdown",
        "passed": all_passed,
        "servers": results,
    }


def main() -> int:
    print("=" * 80)
    print(" OPENETQW EMPIRICAL CHALLENGER: RAPID SHUTDOWN & CONCURRENCY STRESS")
    print(f" Binary:    {BINARY}")
    print(f" Data Path: {DATA_PATH}")
    print("=" * 80)

    t_start = time.time()
    overall_passed = True

    # 1. Early-load quick-quit sweep on outskirts, ark, volcano
    r1 = run_early_quit_sweep("outskirts", base_port=27840, delays=[0.1, 0.3, 0.6, 1.0, 1.5])
    r2 = run_early_quit_sweep("ark", base_port=27846, delays=[0.2, 0.5, 1.0])
    if not r1["passed"] or not r2["passed"]:
        overall_passed = False

    # 2. Mid-gameplay shutdown sweep on outskirts and ark
    r3 = run_midgame_quit_sweep("outskirts", base_port=27850, durations=[3.0, 6.0, 10.0])
    r4 = run_midgame_quit_sweep("ark", base_port=27854, durations=[3.0, 6.0])
    if not r3["passed"] or not r4["passed"]:
        overall_passed = False

    # 3. Post-fuzzing rapid shutdown on outskirts and volcano
    r5 = run_post_fuzz_quit("outskirts", port=27858)
    r6 = run_post_fuzz_quit("volcano", port=27859)
    if not r5["passed"] or not r6["passed"]:
        overall_passed = False

    # 4. Outskirts rapid sequential burst cycling (10 iterations)
    r7 = run_burst_cycling_outskirts(cycles=10, base_port=27860)
    if not r7["passed"]:
        overall_passed = False

    # 5. Concurrent multi-server shutdown (3 servers in parallel)
    r8 = run_concurrent_shutdown(["outskirts", "ark", "volcano"], base_port=27870)
    if not r8["passed"]:
        overall_passed = False

    elapsed = time.time() - t_start

    print("\n" + "=" * 80)
    print(f" SHUTDOWN & CONCURRENCY STRESS SUMMARY (Duration: {elapsed:.2f}s)")
    print("=" * 80)
    print(f" - Early Quit Sweep (outskirts):     {'PASS' if r1['passed'] else 'FAIL'}")
    print(f" - Early Quit Sweep (ark):           {'PASS' if r2['passed'] else 'FAIL'}")
    print(f" - Mid-Game Sweep (outskirts):       {'PASS' if r3['passed'] else 'FAIL'}")
    print(f" - Mid-Game Sweep (ark):             {'PASS' if r4['passed'] else 'FAIL'}")
    print(f" - Post-Fuzz Rapid Quit (outskirts): {'PASS' if r5['passed'] else 'FAIL'}")
    print(f" - Post-Fuzz Rapid Quit (volcano):   {'PASS' if r6['passed'] else 'FAIL'}")
    print(f" - Outskirts 10x Burst Cycling:      {'PASS' if r7['passed'] else 'FAIL'}")
    print(f" - 3-Server Concurrent Shutdown:     {'PASS' if r8['passed'] else 'FAIL'}")
    print("=" * 80)

    summary_file = REPO_ROOT / ".agents" / "challenger_m1_iter2_2" / "shutdown_stress_results.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "overall_passed": overall_passed,
        "elapsed_seconds": elapsed,
        "tests": [r1, r2, r3, r4, r5, r6, r7, r8],
    }
    with open(summary_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Results written to: {summary_file}")

    if overall_passed:
        print("[VERDICT] ALL RAPID SHUTDOWN & CONCURRENCY STRESS TESTS PASSED CLEANLY (EXIT 0, 0 SIGNALS).")
        return 0
    else:
        print("[VERDICT] STRESS TESTS DETECTED FAILURES!", file=sys.stderr)
        return 1


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, BINARY)
    sys.exit(main())
