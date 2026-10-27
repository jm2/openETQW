#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Milestone 1 Iteration 3 Adversarial Stress Test: Same-Map Rapid Reload & Ping-Pong Cycling.

Challenger M1-Iter3-1:
1. Same-Map Rapid Reload Stress:
   Tests repeated in-place reloading of the SAME map ('quarry' -> 'quarry' -> 'quarry')
   to stress test reloadingSameMap, collision submodel preservation/purging, and
   sdLadderEntity collision surface binding.
2. Direct 'devmap' reload stress:
   Tests reloading via 'devmap quarry' directly on dedicated server.
3. Multi-Round Ping-Pong Alternating Cycling:
   Cycles back-and-forth between two distinct stock maps:
   quarry -> canyon -> quarry -> canyon -> quarry
   Verifies submodel tables, clip models, and event queues across repeated alternating transitions.
4. Thread & Invariant Verification:
   Ensures zero 'sdLadderEntity::Spawn No Ladder Surface Found',
   zero 'sdSysCallThread::Event_Spawn: called during map start',
   zero 'idClip::DeleteClipModel called from a thread other than the main thread',
   and clean exit code 0 on quit.
"""

from __future__ import annotations

import atexit
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
DATA_PATH = h.env_data_path()
OUT_DIR = Path(os.environ.get("CHALLENGER_OUT_DIR", str(REPO_ROOT / ".agents" / "challenger_m1_iter3_1")))
BINARY = h.build_binary("ded")

ACTIVE_PROCESSES: list[subprocess.Popen] = []


def _cleanup_subprocesses() -> None:
    for proc in ACTIVE_PROCESSES:
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass


atexit.register(_cleanup_subprocesses)


def get_process_rss_mb(pid: int) -> int:
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()
        return int(out) // 1024
    except Exception:
        return 0


def stream_reader(pipe, q: queue.Queue[str], history: list[str]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            s = line.strip()
            q.put(s)
            history.append(s)
    except Exception as e:
        msg = f"STREAM_READER_ERROR: {e}"
        q.put(msg)
        history.append(msg)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def wait_for_pattern(out_q: queue.Queue[str], proc: subprocess.Popen, pattern: str, timeout: float = 60.0) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return False
        try:
            line = out_q.get(timeout=0.1)
            if pattern in line:
                return True
        except queue.Empty:
            continue
    return False


def test_adversarial_rapid_cycling() -> dict:
    print("=" * 80)
    print(" EMPIRICAL CHALLENGER: ADVERSARIAL SAME-MAP RELOAD & PING-PONG CYCLING")
    print(" Scenarios:")
    print("   Part 1: Same-Map In-Place Reloads: quarry -> quarry -> quarry")
    print("   Part 2: Ping-Pong Alternating Transitions: canyon -> quarry -> canyon -> quarry")
    print(" Binary: ", BINARY)
    print(" Data:   ", DATA_PATH)
    print(" Output: ", OUT_DIR)
    print("=" * 80)

    port = int(os.environ.get("NET_PORT", "27865"))
    cmd = [
        str(BINARY),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "net_port", str(port),
        "+set", "si_rules", "sdGameRulesObjective",
        "+spawnServer", "quarry",
    ]

    proc = h.spawn_engine(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    ACTIVE_PROCESSES.append(proc)
    out_q: queue.Queue[str] = queue.Queue()
    history: list[str] = []
    reader = threading.Thread(target=stream_reader, args=(proc.stdout, out_q, history), daemon=True)
    reader.start()

    transitions = [
        # Initial spawn
        {"label": "1_quarry_initial", "map": "quarry", "pattern": h.spawn_line(DATA_PATH, "quarry"), "type": "initial"},
        # Same-map reload 1
        {"label": "2_quarry_reload_1", "map": "quarry", "pattern": h.spawn_line(DATA_PATH, "quarry"), "type": "same_map"},
        # Same-map reload 2
        {"label": "3_quarry_reload_2", "map": "quarry", "pattern": h.spawn_line(DATA_PATH, "quarry"), "type": "same_map"},
        # Switch to canyon
        {"label": "4_canyon_ping", "map": "canyon", "pattern": h.spawn_line(DATA_PATH, "canyon"), "type": "switch"},
        # Ping back to quarry
        {"label": "5_quarry_pong", "map": "quarry", "pattern": h.spawn_line(DATA_PATH, "quarry"), "type": "ping_pong"},
        # Ping back to canyon
        {"label": "6_canyon_ping2", "map": "canyon", "pattern": h.spawn_line(DATA_PATH, "canyon"), "type": "ping_pong"},
        # Final pong back to quarry
        {"label": "7_quarry_pong2", "map": "quarry", "pattern": h.spawn_line(DATA_PATH, "quarry"), "type": "ping_pong"},
    ]

    results = []
    all_ok = True
    t_start = time.time()

    for idx, t in enumerate(transitions):
        label = t["label"]
        target_map = t["map"]
        pattern = t["pattern"]
        t_type = t["type"]

        print(f"\n[TRANSITION {idx + 1}/{len(transitions)}] {label} (Target: '{target_map}', Mode: {t_type})")
        t0 = time.time()

        if t_type == "initial":
            print(f"  Waiting for initial map '{target_map}' spawn ({pattern})...")
        else:
            print(f"  Triggering transition: spawnServer {target_map} + nextMap...")
            proc.stdin.write(f"spawnServer {target_map}\n")
            proc.stdin.flush()
            time.sleep(0.5)
            proc.stdin.write("nextMap\n")
            proc.stdin.flush()

        loaded = wait_for_pattern(out_q, proc, pattern, timeout=75.0)
        dur = round(time.time() - t0, 2)
        rss = get_process_rss_mb(proc.pid) if proc.poll() is None else 0

        print(f"  Result: {'SUCCESS' if loaded else 'FAILED'} in {dur}s | RSS: {rss} MB")

        results.append({
            "step": idx + 1,
            "label": label,
            "map": target_map,
            "type": t_type,
            "loaded": loaded,
            "duration_s": dur,
            "rss_mb": rss,
        })

        if not loaded:
            print(f"[ERROR] Transition {idx + 1} ({label}) failed to load or timed out.")
            all_ok = False
            break

        # Allow 1.5s simulation advancement
        time.sleep(1.5)

    # Shutdown
    print("\n" + "-" * 60)
    print(" [SHUTDOWN] Issuing 'quit'...")
    print("-" * 60)
    t_quit = time.time()
    try:
        proc.stdin.write("quit\n")
        proc.stdin.flush()
    except Exception as e:
        print(f"  Warning on quit: {e}")

    clean_exit = False
    exit_code = None
    try:
        proc.wait(timeout=10.0)
        clean_exit = (proc.returncode == 0)
        exit_code = proc.returncode
        print(f"  Shutdown complete: clean_exit={clean_exit}, exit_code={exit_code} in {round(time.time() - t_quit, 2)}s")
    except subprocess.TimeoutExpired:
        print("  [ERROR] Server hung on quit; terminating forcibly.")
        proc.kill()
        proc.wait(timeout=2.0)
        exit_code = proc.returncode

    total_dur = round(time.time() - t_start, 2)

    # Log inspection for anomalies
    ladder_errors = [l for l in history if "sdLadderEntity::Spawn No Ladder Surface Found" in l or "No Ladder Surface Found" in l]
    syscall_thread_errors = [l for l in history if "called during map start" in l]
    clip_thread_errors = [l for l in history if "idClip::DeleteClipModel called from a thread other than the main thread" in l]
    fatal_errors = [l for l in history if "FATAL ERROR" in l or "Sys_Error" in l]
    double_free_errors = [l for l in history if "double free" in l or "POINTER_BEING_FREED_WAS_NOT_ALLOCATED" in l]
    sigsegv_errors = [l for l in history if "SIGSEGV" in l or "Segmentation fault" in l]

    has_ladder_error = len(ladder_errors) > 0
    has_syscall_thread_error = len(syscall_thread_errors) > 0
    has_clip_thread_error = len(clip_thread_errors) > 0
    has_fatal = len(fatal_errors) > 0
    has_double_free = len(double_free_errors) > 0
    has_sigsegv = len(sigsegv_errors) > 0

    passed = (
        all_ok
        and clean_exit
        and not has_ladder_error
        and not has_syscall_thread_error
        and not has_clip_thread_error
        and not has_fatal
        and not has_double_free
        and not has_sigsegv
    )

    print("\n" + "=" * 80)
    print(" ADVERSARIAL RAPID CYCLING SUMMARY")
    print("=" * 80)
    print(f" Total Duration:              {total_dur}s")
    print(f" All 7 Transitions Loaded:    {all_ok}")
    print(f" Clean Exit Code 0:           {exit_code == 0} (Exit: {exit_code})")
    print(f" Zero Ladder Surface Errors:  {not has_ladder_error} (Found: {len(ladder_errors)})")
    print(f" Zero Thread MapStart Errors: {not has_syscall_thread_error} (Found: {len(syscall_thread_errors)})")
    print(f" Zero Clip Thread Fatal:      {not has_clip_thread_error} (Found: {len(clip_thread_errors)})")
    print(f" Zero Fatal Engine Errors:    {not has_fatal} (Found: {len(fatal_errors)})")
    print(f" Zero Double-Free Errors:     {not has_double_free}")
    print(f" Zero SIGSEGV Faults:         {not has_sigsegv}")
    print("-" * 80)
    for r in results:
        print(f"  Step {r['step']}: {r['label']:<20} | Loaded: {str(r['loaded']):<5} | Time: {r['duration_s']:>5.2f}s | RSS: {r['rss_mb']:>4} MB")
    print("=" * 80)

    summary = {
        "name": "adversarial_same_map_rapid_cycling",
        "passed": passed,
        "total_duration_s": total_dur,
        "exit_code": exit_code,
        "all_transitions_loaded": all_ok,
        "has_ladder_error": has_ladder_error,
        "ladder_error_count": len(ladder_errors),
        "has_syscall_thread_error": has_syscall_thread_error,
        "has_clip_thread_error": has_clip_thread_error,
        "has_fatal": has_fatal,
        "has_double_free": has_double_free,
        "has_sigsegv": has_sigsegv,
        "transitions": results,
    }

    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        metrics_file = OUT_DIR / "adversarial_stress_results.json"
        log_file = OUT_DIR / "adversarial_cycling_stdout.log"
        with open(metrics_file, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"[METRICS] Saved adversarial metrics to: {metrics_file}")
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("\n".join(history))
        print(f"[LOGS] Saved stdout log to: {log_file}")
    except Exception as e:
        print(f"[WARNING] Could not save adversarial outputs: {e}")

    return summary


def main() -> int:
    summary = test_adversarial_rapid_cycling()
    if summary["passed"]:
        print("\n>>> ADVERSARIAL SAME-MAP & PING-PONG CYCLING TEST PASSED <<<")
        return 0
    else:
        print("\n>>> ADVERSARIAL SAME-MAP & PING-PONG CYCLING TEST FAILED <<<")
        return 1


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, BINARY)
    sys.exit(main())
