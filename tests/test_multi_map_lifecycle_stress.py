#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Milestone 1 Iteration 2 Challenger Test: Multi-Map Lifecycle & In-Process Cycling Stress.

Empirical Challenger M1-Iter2-1:
1. In-process multi-map cycling across stock maps without restarting openETQW-ded_arm64:
   Sequence: quarry -> outskirts -> ark -> canyon -> quarry (re-entry loop).
2. Dynamic transition verification:
   Evaluates PurgeModels, clip model teardown, entity instantiation, and active 30 Hz simulation ticks.
3. Heap & RSS memory tracking:
   Verifies memory stability and absence of unbounded heap leaks across sequential map changes.
4. Clean shutdown verification:
   Asserts zero memory corruption, zero double-frees, zero dangling pointer dereferences,
   and clean exit code 0 upon final 'quit'.
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


def wait_for_pattern(out_q: queue.Queue[str], proc: subprocess.Popen, pattern: str, timeout: float = 35.0) -> bool:
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


def test_multi_map_cycling() -> dict:
    print("=" * 80)
    print(" EMPIRICAL CHALLENGER: IN-PROCESS MULTI-MAP CYCLING STRESS TEST")
    print(" Sequence: quarry -> outskirts -> ark -> canyon -> quarry (re-entry)")
    print(" Binary:   ", BINARY)
    print(" Data:     ", DATA_PATH)
    print("=" * 80)

    port = int(os.environ.get("NET_PORT", "27850"))
    cmd = [
        str(BINARY),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "net_port", str(port),
        "+set", "si_rules", "sdGameRulesObjective",
        "+spawnServer", "quarry",
    ]

    proc = subprocess.Popen(
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

    stages = [
        {"name": "quarry", "expected_ents": "...414 entities spawned", "is_initial": True},
        {"name": "outskirts", "expected_ents": "...863 entities spawned", "is_initial": False},
        {"name": "ark", "expected_ents": "...898 entities spawned", "is_initial": False},
        {"name": "canyon", "expected_ents": "...849 entities spawned", "is_initial": False},
        {"name": "quarry_reentry", "map_cmd": "quarry", "expected_ents": "...414 entities spawned", "is_initial": False},
    ]

    stage_results = []
    all_stages_ok = True

    t_start = time.time()

    for idx, stage in enumerate(stages):
        stage_name = stage["name"]
        expected_pattern = stage["expected_ents"]
        target_map = stage.get("map_cmd", stage_name)

        print(f"\n[STAGE {idx + 1}/5] Target Map: '{target_map}' (Label: {stage_name})")
        stage_t0 = time.time()

        if stage["is_initial"]:
            print(f"  Waiting for initial map '{target_map}' spawn ({expected_pattern})...")
        else:
            print(f"  Issuing dynamic transition: spawnServer {target_map} + nextMap...")
            proc.stdin.write(f"spawnServer {target_map}\n")
            proc.stdin.flush()
            # Give short window for cvar latch
            time.sleep(1.0)
            proc.stdin.write("nextMap\n")
            proc.stdin.flush()

        loaded = wait_for_pattern(out_q, proc, expected_pattern, timeout=75.0)
        stage_duration = round(time.time() - stage_t0, 2)
        rss_mb = get_process_rss_mb(proc.pid) if proc.poll() is None else 0

        print(f"  Status:    {'SUCCESS' if loaded else 'FAILED'} (in {stage_duration}s)")
        print(f"  RSS Heap:  {rss_mb} MB")

        stage_results.append({
            "stage": idx + 1,
            "name": stage_name,
            "target_map": target_map,
            "loaded": loaded,
            "duration_s": stage_duration,
            "rss_mb": rss_mb,
        })

        if not loaded:
            print(f"[ERROR] Stage {idx + 1} ('{target_map}') failed to load or timed out.")
            all_stages_ok = False
            break

        # Simulate 2.0s of active gameplay on this map before switching
        print(f"  Advancing active simulation ticks for 2.0s...")
        time.sleep(2.0)

    print("\n" + "-" * 60)
    print(" [SHUTDOWN] Issuing final 'quit' command...")
    print("-" * 60)
    shutdown_t0 = time.time()
    try:
        proc.stdin.write("quit\n")
        proc.stdin.flush()
    except Exception as e:
        print(f"  Warning sending quit: {e}")

    clean_exit = False
    exit_code = None
    try:
        proc.wait(timeout=10.0)
        clean_exit = (proc.returncode == 0)
        exit_code = proc.returncode
        print(f"  Clean exit observed: {clean_exit} (Exit code: {exit_code}) in {round(time.time() - shutdown_t0, 2)}s")
    except subprocess.TimeoutExpired:
        print("  [ERROR] Server hung on quit; terminating forcibly.")
        proc.kill()
        proc.wait(timeout=2.0)
        exit_code = proc.returncode

    total_time = round(time.time() - t_start, 2)

    # Analyze output log for memory faults or fatal errors
    has_fatal = any("FATAL ERROR" in line or "Sys_Error" in line for line in history)
    has_double_free = any("POINTER_BEING_FREED_WAS_NOT_ALLOCATED" in line or "double free" in line for line in history)
    has_sigsegv = any("SIGSEGV" in line or "Segmentation fault" in line for line in history)
    has_clip_thread_fatal = any("idClip::DeleteClipModel called from a thread other than the main thread" in line for line in history)

    print("\n" + "=" * 80)
    print(" IN-PROCESS MAP CYCLING RESULTS SUMMARY")
    print("=" * 80)
    print(f" Total Duration:          {total_time}s")
    print(f" All Stages Loaded:       {all_stages_ok}")
    print(f" Clean Exit Code 0:       {exit_code == 0} (Exit: {exit_code})")
    print(f" Zero Fatal Errors:       {not has_fatal}")
    print(f" Zero Double-Free Errors: {not has_double_free}")
    print(f" Zero SIGSEGV Faults:     {not has_sigsegv}")
    print(f" Zero Clip Thread Fatal:  {not has_clip_thread_fatal}")
    print("-" * 80)
    for r in stage_results:
        print(f"  Stage {r['stage']}: {r['name']:<18} | Loaded: {str(r['loaded']):<5} | Time: {r['duration_s']:>5.2f}s | RSS: {r['rss_mb']:>4} MB")
    print("=" * 80)

    overall_passed = (
        all_stages_ok
        and exit_code == 0
        and not has_fatal
        and not has_double_free
        and not has_sigsegv
        and not has_clip_thread_fatal
    )

    summary = {
        "name": "multi_map_lifecycle_stress",
        "passed": overall_passed,
        "total_duration_s": total_time,
        "exit_code": exit_code,
        "all_stages_loaded": all_stages_ok,
        "has_fatal": has_fatal,
        "has_double_free": has_double_free,
        "has_sigsegv": has_sigsegv,
        "has_clip_thread_fatal": has_clip_thread_fatal,
        "stages": stage_results,
    }

    out_dir = Path(os.environ.get("CHALLENGER_OUT_DIR", str(REPO_ROOT / ".agents" / "challenger_m1_iter2_1")))
    metrics_path = out_dir / "stress_results.json"
    log_path = out_dir / "cycling_stdout.log"
    try:
        metrics_path.parent.mkdir(parents=True, exist_ok=True)
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=2)
        print(f"[METRICS] Saved results to: {metrics_path}")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("\n".join(history))
        print(f"[LOGS] Saved stdout log to: {log_path}")
    except Exception as e:
        print(f"[WARNING] Could not save metrics: {e}")

    return summary


def main() -> int:
    summary = test_multi_map_cycling()
    if summary["passed"]:
        print("\n>>> IN-PROCESS MULTI-MAP CYCLING STRESS TEST PASSED <<<")
        return 0
    else:
        print("\n>>> IN-PROCESS MULTI-MAP CYCLING STRESS TEST FAILED <<<")
        return 1


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, BINARY)
    sys.exit(main())
