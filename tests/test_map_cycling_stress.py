#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Milestone 1 Empirical Stress Test: Rapid Map Cycling & In-Process Transition Stability.

Challenger M1-1 Empirical Adversarial Harness for openETQW-ded:
1. Dynamic Map Switching / Desync Probe:
   Empirically verifies that calling `spawnServer <map>` on an active server
   incorrectly triggers `serverMapRestart` (restarting the old map) while mutating
   the ROM cvar `si_map` to the new map, desynchronizing advertised vs active map state.
2. In-Process Rapid Map Cycling & State Pollution Stress Test:
   Executes sequential map transitions (quarry -> slipgate -> area22 -> quarry)
   within a single dedicated server process without restarts.
   Tracks resident set size (RSS) memory consumption and unmasks state pollution,
   script thread leakage, and fatal crash during shutdown:
   `FATAL ERROR: idClip::DeleteClipModel called from a thread other than the main thread`.
3. Repeated In-Place Server Map Restart Stability:
   Executes 5x rapid `serverMapRestart` calls to evaluate single-map restart stability.
4. Malformed Input Fuzzing:
   Tests empty map names, traversal paths, and non-existent map identifiers.
5. Interrupted Loading Quick-Quit Lifecycle:
   Evaluates dedicated server graceful shutdown behavior under early quit signals.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import etqw_harness as h

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = h.env_data_path()

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


def _find_dedicated_binary() -> Path:
    candidates = [
        REPO_ROOT / "build" / "openETQW-ded_arm64",
        REPO_ROOT / "build" / "openETQW-ded_x64",
        REPO_ROOT / "build" / "openETQW-ded_x64.exe",
        REPO_ROOT / "build" / "openETQW-ded_arm64.exe",
        REPO_ROOT / "build" / "openETQW-ded",
        REPO_ROOT / "build" / "openETQW-ded.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


DEDICATED_BIN = _find_dedicated_binary()


def stream_reader(pipe, q: queue.Queue[str], history: list[str] | None = None) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            s = line.strip()
            q.put(s)
            if history is not None:
                history.append(s)
    except Exception as e:
        msg = f"STREAM_READER_ERROR: {e}"
        q.put(msg)
        if history is not None:
            history.append(msg)
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def get_process_rss_mb(pid: int) -> int:
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()
        return int(out) // 1024
    except Exception:
        return 0


def wait_for_pattern(out_q: queue.Queue[str], proc: subprocess.Popen, pattern: str, timeout: float = 15.0) -> bool:
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


def drain_queue(out_q: queue.Queue[str]) -> list[str]:
    lines = []
    while not out_q.empty():
        try:
            lines.append(out_q.get_nowait())
        except queue.Empty:
            break
    return lines


# =========================================================================
# TEST 1: spawnServer State Desync / Map Switch Failure
# =========================================================================
def test_spawn_server_desync() -> dict:
    print("\n" + "=" * 75)
    print(" TEST 1: spawnServer In-Process Map Switch & CVar Desync Verification")
    print("=" * 75)

    port = 27771
    cmd = [
        str(DEDICATED_BIN),
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

    result = {
        "name": "spawn_server_desync",
        "status": "FAIL",
        "details": {},
    }

    try:
        if not wait_for_pattern(out_q, proc, "...414 entities spawned", timeout=15.0):
            result["details"]["error"] = "Initial map 'quarry' failed to spawn"
            return result

        print("[OK] Server active running 'quarry' (414 entities spawned).")
        cmd_index = len(history)
        print("[ACTION] Issuing command 'spawnServer slipgate'...")

        proc.stdin.write("spawnServer slipgate\n")
        proc.stdin.flush()
        time.sleep(2.0)

        proc.stdin.write("si_map\n")
        proc.stdin.write("quit\n")
        proc.stdin.flush()

        proc.wait(timeout=5.0)

        # Inspect if slipgate was actually initialized vs quarry restarted
        post_cmd_lines = history[cmd_index:]
        quarry_restarts = [l for l in post_cmd_lines if "SetupMapMetaData: 'maps/quarry.world'" in l]
        slipgate_inits = [l for l in post_cmd_lines if "SetupMapMetaData: 'maps/slipgate.world'" in l]
        si_map_lines = [l for l in post_cmd_lines if '"si_map" is:' in l]

        reported_cvar = si_map_lines[-1] if si_map_lines else "UNKNOWN"
        print(f"[OBSERVATION] si_map cvar reported: {reported_cvar}")
        print(f"[OBSERVATION] slipgate loaded: {len(slipgate_inits) > 0}, quarry restarts: {len(quarry_restarts)}")

        # A defect exists when si_map is 'slipgate' but quarry was restarted instead of loading slipgate
        is_desynced = ('"si_map" is:"slipgate"' in reported_cvar) and (len(slipgate_inits) == 0)
        result["details"] = {
            "cvar_si_map": reported_cvar,
            "slipgate_loaded": len(slipgate_inits) > 0,
            "quarry_restarted": len(quarry_restarts) >= 1,
            "desynchronization_confirmed": is_desynced,
        }

        if is_desynced:
            print("[UNMASKED DEFECT 1] `spawnServer <map>` on active server buffers `serverMapRestart`!")
            print("                   `si_map` mutated to 'slipgate', but world remains on 'quarry'!")
            result["status"] = "UNMASKED_DEFECT"
        else:
            result["status"] = "PASS"

    except Exception as e:
        result["details"]["error"] = str(e)
    finally:
        if proc.poll() is None:
            proc.kill()

    return result


# =========================================================================
# TEST 2: In-Process Rapid Map Cycling & State Pollution Crash
# =========================================================================
def test_inprocess_map_cycling() -> dict:
    print("\n" + "=" * 75)
    print(" TEST 2: In-Process 4-Map Cycling Across Stock Maps (quarry->slipgate->area22->quarry)")
    print("=" * 75)

    port = 27772
    cmd = [
        str(DEDICATED_BIN),
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

    result = {
        "name": "inprocess_map_cycling",
        "status": "FAIL",
        "rss_progression_mb": {},
        "stages": {},
        "crash_detected": False,
        "exit_code": None,
    }

    try:
        # Stage 1: quarry
        print("[STAGE 1] Loading initial map 'quarry'...")
        s1 = wait_for_pattern(out_q, proc, "...414 entities spawned", timeout=15.0)
        rss1 = get_process_rss_mb(proc.pid)
        result["stages"]["1_quarry"] = s1
        result["rss_progression_mb"]["1_quarry"] = rss1
        print(f"         Status: {'SUCCESS' if s1 else 'FAIL'}, RSS: {rss1} MB")

        # Stage 2: slipgate
        print("[STAGE 2] Transitioning in-process to 'slipgate' via nextMap...")
        proc.stdin.write("spawnServer slipgate\nnextMap\n")
        proc.stdin.flush()
        s2 = wait_for_pattern(out_q, proc, "...1252 entities spawned", timeout=15.0)
        rss2 = get_process_rss_mb(proc.pid)
        result["stages"]["2_slipgate"] = s2
        result["rss_progression_mb"]["2_slipgate"] = rss2
        print(f"         Status: {'SUCCESS' if s2 else 'FAIL'}, RSS: {rss2} MB")

        # Stage 3: area22
        print("[STAGE 3] Transitioning in-process to 'area22' via nextMap...")
        proc.stdin.write("spawnServer area22\nnextMap\n")
        proc.stdin.flush()
        s3 = wait_for_pattern(out_q, proc, "...1063 entities spawned", timeout=15.0)
        rss3 = get_process_rss_mb(proc.pid)
        result["stages"]["3_area22"] = s3
        result["rss_progression_mb"]["3_area22"] = rss3
        print(f"         Status: {'SUCCESS' if s3 else 'FAIL'}, RSS: {rss3} MB")

        # Stage 4: quarry (re-entry)
        print("[STAGE 4] Transitioning in-process back to 'quarry'...")
        proc.stdin.write("spawnServer quarry\nnextMap\n")
        proc.stdin.flush()
        s4 = wait_for_pattern(out_q, proc, "...414 entities spawned", timeout=15.0)
        rss4 = get_process_rss_mb(proc.pid)
        result["stages"]["4_quarry_reload"] = s4
        result["rss_progression_mb"]["4_quarry_reload"] = rss4
        print(f"         Status: {'SUCCESS' if s4 else 'FAIL'}, RSS: {rss4} MB")

        print("[ACTION] Requesting server shutdown ('quit')...")
        proc.stdin.write("quit\n")
        proc.stdin.flush()

        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()

        result["exit_code"] = proc.returncode
        lines = history

        clip_fatal = any("FATAL ERROR: idClip::DeleteClipModel called from a thread other than the main thread" in l for l in lines)
        script_error = any("sdSysCallThread::Event_Spawn 'spawn' called during map start" in l for l in lines)

        result["clip_fatal"] = clip_fatal
        result["script_pollution_error"] = script_error

        print(f"[OBSERVATION] Process Exit Code: {proc.returncode}")
        print(f"[OBSERVATION] Script Thread Spawn Error: {script_error}")
        print(f"[OBSERVATION] idClip Fatal Thread Error: {clip_fatal}")

        if proc.returncode != 0 or clip_fatal or not s4:
            print("[UNMASKED DEFECT 2] In-process map cycling crashed with exit code 1 / DeleteClipModel fatal error!")
            result["status"] = "UNMASKED_DEFECT"
            result["crash_detected"] = True
        else:
            result["status"] = "PASS"

    except Exception as e:
        result["error"] = str(e)
    finally:
        if proc.poll() is None:
            proc.kill()

    return result


# =========================================================================
# TEST 3: Repeated In-Place serverMapRestart
# =========================================================================
def test_repeated_server_map_restart() -> dict:
    print("\n" + "=" * 75)
    print(" TEST 3: Repeated In-Place serverMapRestart Stability (5 iterations)")
    print("=" * 75)

    port = 27773
    cmd = [
        str(DEDICATED_BIN),
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
    reader = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
    reader.start()

    result = {
        "name": "repeated_server_map_restart",
        "iterations_completed": 0,
        "total_iterations": 5,
        "status": "FAIL",
    }

    try:
        if not wait_for_pattern(out_q, proc, "...414 entities spawned", timeout=15.0):
            result["error"] = "Initial map spawn timed out"
            return result

        for i in range(5):
            proc.stdin.write("serverMapRestart\n")
            proc.stdin.flush()
            if wait_for_pattern(out_q, proc, "...414 entities spawned", timeout=10.0):
                result["iterations_completed"] += 1
                print(f"         Iteration {i + 1}/5: SUCCESS")
            else:
                print(f"         Iteration {i + 1}/5: FAILED")
                break

        proc.stdin.write("quit\n")
        proc.stdin.flush()
        proc.wait(timeout=5.0)

        result["exit_code"] = proc.returncode
        if result["iterations_completed"] == 5 and proc.returncode == 0:
            result["status"] = "PASS"
            print("[PASS] In-place serverMapRestart survived 5 sequential restarts cleanly.")
        else:
            result["status"] = "FAIL"

    except Exception as e:
        result["error"] = str(e)
    finally:
        if proc.poll() is None:
            proc.kill()

    return result


# =========================================================================
# TEST 4: Malformed Input Fuzzing
# =========================================================================
def test_malformed_input_fuzzing() -> dict:
    print("\n" + "=" * 75)
    print(" TEST 4: Malformed Input & Fuzzing (Empty, Traversal, Nonexistent Maps)")
    print("=" * 75)

    cases = [
        {"desc": "Empty map string", "arg": ""},
        {"desc": "Explicit empty quotes", "arg": '""'},
        {"desc": "Path traversal string", "arg": "../../../etc/passwd"},
        {"desc": "Nonexistent map", "arg": "non_existent_map_99999"},
    ]

    result = {
        "name": "malformed_input_fuzzing",
        "total_cases": len(cases),
        "passed_cases": 0,
        "cases": [],
        "status": "FAIL",
    }

    base_port = 27780
    for idx, c in enumerate(cases):
        port = base_port + idx
        cmd = [
            str(DEDICATED_BIN),
            "+set", "dedicated", "1",
            "+set", "fs_basepath", str(DATA_PATH),
            "+set", "fs_game", "base",
            "+set", "net_port", str(port),
            "+set", "si_rules", "sdGameRulesObjective",
            "+spawnServer", c["arg"],
            "+quit",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            out, _ = proc.communicate(timeout=10.0)
            fatal = ("FATAL" in out) or ("SIGSEGV" in out) or ("Segmentation fault" in out)
            case_passed = (proc.returncode == 0) and not fatal
            if case_passed:
                result["passed_cases"] += 1
            print(f"         Case {idx + 1} ({c['desc']}): {'PASS' if case_passed else 'FAIL'} (exit: {proc.returncode})")
            result["cases"].append({
                "desc": c["desc"],
                "arg": c["arg"],
                "exit_code": proc.returncode,
                "fatal": fatal,
                "passed": case_passed,
            })
        except Exception as e:
            result["cases"].append({"desc": c["desc"], "error": str(e), "passed": False})
            if proc.poll() is None:
                proc.kill()

    if result["passed_cases"] == len(cases):
        result["status"] = "PASS"
        print("[PASS] Dedicated server cleanly handled all malformed map spawn inputs.")

    return result


# =========================================================================
# TEST 5: Interrupted Loading / Early Quick-Quit
# =========================================================================
def test_interrupted_loading_quick_quit() -> dict:
    print("\n" + "=" * 75)
    print(" TEST 5: Interrupted Map Loading Early Quick-Quit Sweep")
    print("=" * 75)

    delays = [0.05, 0.1, 0.25, 0.5, 0.75]
    result = {
        "name": "interrupted_loading_quick_quit",
        "total_sweeps": len(delays),
        "passed_sweeps": 0,
        "sweeps": [],
        "status": "FAIL",
    }

    base_port = 27790
    for idx, delay in enumerate(delays):
        port = base_port + idx
        cmd = [
            str(DEDICATED_BIN),
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
        )
        time.sleep(delay)
        try:
            proc.stdin.write("quit\n")
            proc.stdin.flush()
        except Exception:
            pass

        try:
            out, _ = proc.communicate(timeout=8.0)
            passed = (proc.returncode == 0)
            if passed:
                result["passed_sweeps"] += 1
            print(f"         Delay {delay:.2f}s: {'PASS' if passed else 'FAIL'} (exit: {proc.returncode})")
            result["sweeps"].append({
                "delay": delay,
                "exit_code": proc.returncode,
                "passed": passed,
            })
        except Exception as e:
            result["sweeps"].append({"delay": delay, "error": str(e), "passed": False})
            if proc.poll() is None:
                proc.kill()

    if result["passed_sweeps"] == len(delays):
        result["status"] = "PASS"
        print("[PASS] All interrupted loading quick-quits exited cleanly with code 0.")

    return result


def main() -> int:
    print("===========================================================================")
    print(" OPENETQW EMPIRICAL CHALLENGER: RAPID MAP CYCLING & STRESS TEST HARNESS")
    print(f" Dedicated Binary: {DEDICATED_BIN}")
    print(f" Data Path:        {DATA_PATH}")
    print("===========================================================================")

    t0 = time.time()
    t1 = test_spawn_server_desync()
    t2 = test_inprocess_map_cycling()
    t3 = test_repeated_server_map_restart()
    t4 = test_malformed_input_fuzzing()
    t5 = test_interrupted_loading_quick_quit()
    elapsed = time.time() - t0

    suite_summary = {
        "duration_seconds": round(elapsed, 2),
        "tests": [t1, t2, t3, t4, t5],
        "defects_unmasked": [
            t for t in [t1, t2] if t["status"] == "UNMASKED_DEFECT"
        ],
    }

    output_json_path = REPO_ROOT / ".agents" / "challenger_m1_1" / "stress_results.json"
    try:
        output_json_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_json_path, "w", encoding="utf-8") as f:
            json.dump(suite_summary, f, indent=2)
        print(f"\n[METRICS] Saved empirical test metrics to: {output_json_path}")
    except Exception as e:
        print(f"\n[WARNING] Could not save stress_results.json: {e}")

    print("\n" + "=" * 75)
    print(" EMPIRICAL ADVERSARIAL STRESS SUITE SUMMARY")
    print("=" * 75)
    print(f" Total Duration: {elapsed:.2f}s")
    for t in [t1, t2, t3, t4, t5]:
        print(f" - {t['name']:<35}: {t['status']}")
    print("=" * 75)

    # Return non-zero if defects unmasked or tests failed
    if suite_summary["defects_unmasked"]:
        print(f"\n[CRITICAL CHALLENGE RESULT] {len(suite_summary['defects_unmasked'])} defects unmasked!")
        print("Verdict: REJECT (Dynamic in-process cycling and map switching crash/desync)")
        return 1
    failed = [t["name"] for t in (t1, t2, t3, t4, t5) if t["status"] != "PASS"]
    if failed:
        print(f"\nVerdict: REJECT ({', '.join(failed)} did not pass)")
        return 1

    print("\nVerdict: APPROVE")
    return 0


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, DEDICATED_BIN)
    sys.exit(main())
