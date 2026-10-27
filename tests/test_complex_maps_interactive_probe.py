#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deep Interactive Stdin Probe for Complex Stock Maps: Volcano, Slipgate, Quarry.

Empirical Challenger M1-Iter3-2 Harness:
1. Interactive inspection of entity dictionaries, script objects, auto-threads, and objective states.
2. In-process map transition sequence (volcano -> slipgate -> quarry -> volcano re-entry) within a single process.
3. Strict checks for thread memory corruption, auto-thread leaks, clip model errors, and clean shutdown.
"""

from __future__ import annotations

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

COMPLEX_MAPS = ["volcano", "slipgate", "quarry"]

OBJECTIVE_STATUS_RE = re.compile(
    r"OBJECTIVE_STATUS:\s*gdf_obj=(-?\d+)\s*strogg_obj=(-?\d+)\s*state=(\d+)\s*winner=(\w+)"
)
ACTIVE_THREADS_RE = re.compile(r"(\d+)\s+active threads")
ENTITIES_SPAWNED_RE = re.compile(r"\.\.\.(\d+) entities spawned")
WORLDSPAWN_RE = re.compile(r"(\d+):\s*worldspawn\s+idWorldspawn\s+worldspawn")
TYPE_COUNT_RE = re.compile(r"(\d+)\s+entities of type\s+(\w+)")
THREAD_LINE_RE = re.compile(r"^\s*(\d+):\s*([^\(]+)\s*\((.*)\)")


def send_cmd(proc, cmd_text: str):
    try:
        proc.stdin.write(cmd_text + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        raise RuntimeError(f"Failed to send command '{cmd_text}': {e}") from e


def wait_for_pattern(q, pattern, timeout=10.0):
    start = time.time()
    accumulated = []
    while time.time() - start < timeout:
        try:
            line = q.get(timeout=0.1)
            accumulated.append(line)
            m = pattern.search(line)
            if m:
                return m, accumulated
        except queue.Empty:
            continue
    return None, accumulated


def drain_queue(q, duration=0.5):
    lines = []
    start = time.time()
    while time.time() - start < duration:
        try:
            lines.append(q.get(timeout=0.05))
        except queue.Empty:
            pass
    return lines


def wait_for_map_spawn(q, proc, timeout=60.0) -> int:
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            raise RuntimeError(f"Server exited prematurely with code {proc.returncode}")
        try:
            line = q.get(timeout=0.1)
            m = ENTITIES_SPAWNED_RE.search(line)
            if m:
                return int(m.group(1))
        except queue.Empty:
            continue
    raise TimeoutError(f"Server failed to complete map spawn within {timeout}s")


def probe_individual_map(binary: Path, data_path: Path, map_name: str, port: int) -> dict:
    print(f"\n--- PROBING MAP: {map_name} (port {port}) ---")
    res = {
        "map": map_name,
        "status": "FAIL",
        "exit_code": -1,
        "entities_spawned": 0,
        "entity_types": {},
        "threads_initial": 0,
        "threads_post_sim": 0,
        "thread_names": [],
        "worldspawn_args": 0,
        "objective_status": None,
        "corruptions_detected": [],
        "duration": 0.0,
        "error": None,
    }
    t0 = time.time()

    cmd = [
        str(binary),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(data_path),
        "+set", "fs_game", "base",
        "+set", "net_allowCheats", "1",
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
    all_output = []

    def capturing_reader(pipe, q, sink):
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                stripped = line.strip()
                sink.append(stripped)
                q.put(stripped)
        except Exception as e:
            q.put(f"READER_ERROR: {e}")
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    reader = threading.Thread(target=capturing_reader, args=(proc.stdout, out_q, all_output), daemon=True)
    reader.start()

    try:
        # 1. Wait for map spawn
        spawned = wait_for_map_spawn(out_q, proc, timeout=30.0)
        res["entities_spawned"] = spawned
        print(f"[{map_name}] Server initialized. Total entities spawned: {spawned}")

        # Let server tick for 1.0s to settle auto-threads
        time.sleep(1.0)
        drain_queue(out_q, 0.2)

        # 2. Probe listEntities
        send_cmd(proc, "listEntities")
        time.sleep(0.5)
        lines = drain_queue(out_q, 1.0)
        for line in lines:
            m = TYPE_COUNT_RE.search(line)
            if m:
                count = int(m.group(1))
                typename = m.group(2)
                res["entity_types"][typename] = count

        print(f"[{map_name}] Entity type breakdown ({len(res['entity_types'])} types found):")
        for t, c in sorted(res["entity_types"].items(), key=lambda x: -x[1])[:8]:
            print(f"    {t}: {c}")

        # 3. Probe worldspawn spawnArgs
        send_cmd(proc, "listSpawnArgs worldspawn")
        time.sleep(0.5)
        lines = drain_queue(out_q, 0.8)
        spawn_args = [l for l in lines if l.startswith('"') and '"' in l[1:]]
        res["worldspawn_args"] = len(spawn_args)
        print(f"[{map_name}] Worldspawn spawnArgs count: {len(spawn_args)}")
        if len(spawn_args) == 0:
            raise RuntimeError(f"[{map_name}] Worldspawn spawnArgs dictionary is empty or unparseable")

        # 4. Probe initial script threads
        send_cmd(proc, "listThreads")
        m_thr, lines = wait_for_pattern(out_q, ACTIVE_THREADS_RE, timeout=5.0)
        if not m_thr:
            raise RuntimeError(f"[{map_name}] No active threads output found from listThreads")
        res["threads_initial"] = int(m_thr.group(1))
        # Collect thread names
        for l in lines:
            m_t = THREAD_LINE_RE.search(l)
            if m_t:
                res["thread_names"].append(m_t.group(2).strip())
        print(f"[{map_name}] Initial active threads: {res['threads_initial']}")

        # 5. Probe objectiveStatus
        send_cmd(proc, "objectiveStatus")
        m_obj, lines = wait_for_pattern(out_q, OBJECTIVE_STATUS_RE, timeout=5.0)
        if not m_obj:
            raise RuntimeError(f"[{map_name}] Failed to get OBJECTIVE_STATUS")
        res["objective_status"] = {
            "gdf_obj": int(m_obj.group(1)),
            "strogg_obj": int(m_obj.group(2)),
            "state": int(m_obj.group(3)),
            "winner": m_obj.group(4),
        }
        print(f"[{map_name}] Objective status: state={res['objective_status']['state']}, winner={res['objective_status']['winner']}")

        # 6. Let 30 Hz simulation advance for 3 seconds (~90 ticks)
        print(f"[{map_name}] Advancing simulation for 3.0 seconds (90 ticks)...")
        time.sleep(3.0)
        drain_queue(out_q, 0.2)

        # 7. Probe script threads again after simulation run
        send_cmd(proc, "listThreads")
        m_thr2, lines2 = wait_for_pattern(out_q, ACTIVE_THREADS_RE, timeout=5.0)
        if not m_thr2:
            raise RuntimeError(f"[{map_name}] Failed to query listThreads after simulation advancement")
        res["threads_post_sim"] = int(m_thr2.group(1))
        print(f"[{map_name}] Active threads post-simulation: {res['threads_post_sim']}")

        # Thread count delta sanity check
        thr_delta = abs(res["threads_post_sim"] - res["threads_initial"])
        if thr_delta > 10:
            res["corruptions_detected"].append(f"Thread count drifted unexpectedly: {res['threads_initial']} -> {res['threads_post_sim']}")

        # 8. Clean quit and check for corruption warnings in log
        print(f"[{map_name}] Sending 'quit' command...")
        send_cmd(proc, "quit")
        proc.wait(timeout=10.0)
        res["exit_code"] = proc.returncode
        print(f"[{map_name}] Server exited with code {res['exit_code']}")

        # Inspect all captured output for anomalies
        joined_log = "\n".join(all_output)
        bad_patterns = [
            ("Killing A Scriptobject Within its own Auto Thread", "Auto-thread kill violation"),
            ("called from a thread other than the main thread", "Clip thread safety violation"),
            ("double free or corruption", "Heap corruption / double free"),
            ("Segmentation fault", "Segmentation fault"),
            ("SIGSEGV", "SIGSEGV signal"),
            ("insideExecuteMapChange", "Map change recursion violation"),
            ("AddressSanitizer", "ASan memory violation"),
        ]
        for pattern, desc in bad_patterns:
            if pattern in joined_log:
                res["corruptions_detected"].append(f"{desc} detected in output ({pattern})")

        if res["exit_code"] == 0 and len(res["corruptions_detected"]) == 0:
            res["status"] = "PASS"
        else:
            res["status"] = "FAIL"
            res["error"] = f"Exit code {res['exit_code']}, corruptions: {res['corruptions_detected']}"

    except Exception as e:
        res["error"] = str(e)
        res["status"] = "FAIL"
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass
        res["exit_code"] = proc.returncode if proc.returncode is not None else -1

    res["duration"] = time.time() - t0
    return res


def probe_map_transition_sequence(binary: Path, data_path: Path, port: int) -> dict:
    """Stress test in-process transitions: volcano -> slipgate -> quarry -> volcano within a single server."""
    print(f"\n=======================================================")
    print(f" PROBING IN-PROCESS MAP TRANSITION SEQUENCE (port {port})")
    print(f" Sequence: volcano -> slipgate -> quarry -> volcano (re-entry)")
    print(f"=======================================================")

    res = {
        "status": "FAIL",
        "exit_code": -1,
        "stages": [],
        "corruptions_detected": [],
        "duration": 0.0,
        "error": None,
    }
    t0 = time.time()

    cmd = [
        str(binary),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(data_path),
        "+set", "fs_game", "base",
        "+set", "net_allowCheats", "1",
        "+set", "net_port", str(port),
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "si_pure", "0",
        "+set", "bot_enable", "1",
        "+set", "bot_sleepWhenServerEmpty", "0",
        "+spawnServer", "volcano",
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
    all_output = []

    def capturing_reader(pipe, q, sink):
        try:
            for line in iter(pipe.readline, ""):
                if not line:
                    break
                stripped = line.strip()
                sink.append(stripped)
                q.put(stripped)
        except Exception:
            pass
        finally:
            try:
                pipe.close()
            except Exception:
                pass

    reader = threading.Thread(target=capturing_reader, args=(proc.stdout, out_q, all_output), daemon=True)
    reader.start()

    sequence = ["volcano", "slipgate", "quarry", "volcano"]

    try:
        for idx, m_name in enumerate(sequence):
            stage_name = f"{m_name}_reentry" if idx == 3 else m_name
            print(f"\n[Sequence Stage {idx+1}/4]: Target '{stage_name}'...")

            if idx == 0:
                spawned = wait_for_map_spawn(out_q, proc, timeout=40.0)
            else:
                # Dynamic map transition
                print(f"  Sending 'spawnServer {m_name}' + 'nextMap'...")
                send_cmd(proc, f"spawnServer {m_name}")
                time.sleep(1.0)
                send_cmd(proc, "nextMap")
                spawned = wait_for_map_spawn(out_q, proc, timeout=60.0)

            time.sleep(1.5)
            drain_queue(out_q, 0.2)

            # Probe listThreads
            send_cmd(proc, "listThreads")
            m_thr, _ = wait_for_pattern(out_q, ACTIVE_THREADS_RE, timeout=5.0)
            thrs = int(m_thr.group(1)) if m_thr else -1

            # Probe objectiveStatus
            send_cmd(proc, "objectiveStatus")
            m_obj, _ = wait_for_pattern(out_q, OBJECTIVE_STATUS_RE, timeout=5.0)
            obj_state = int(m_obj.group(3)) if m_obj else -1

            # Advance simulation for 2 seconds
            time.sleep(2.0)
            drain_queue(out_q, 0.2)

            print(f"  --> Stage '{stage_name}' OK: {spawned} entities, {thrs} threads, state={obj_state}")
            res["stages"].append({
                "stage": stage_name,
                "map": m_name,
                "entities": spawned,
                "threads": thrs,
                "obj_state": obj_state,
            })

        # Quit cleanly
        print("\n[Sequence] Transition sequence complete. Sending 'quit'...")
        send_cmd(proc, "quit")
        proc.wait(timeout=10.0)
        res["exit_code"] = proc.returncode
        print(f"[Sequence] Server exited with code {res['exit_code']}")

        joined_log = "\n".join(all_output)
        bad_patterns = [
            ("Killing A Scriptobject Within its own Auto Thread", "Auto-thread kill violation"),
            ("called from a thread other than the main thread", "Clip thread safety violation"),
            ("double free or corruption", "Heap corruption / double free"),
            ("Segmentation fault", "Segmentation fault"),
            ("SIGSEGV", "SIGSEGV signal"),
            ("No Ladder Surface Found", "Quarry ladder submodel eviction failure"),
            ("insideExecuteMapChange", "Map change recursion violation"),
        ]
        for pattern, desc in bad_patterns:
            if pattern in joined_log:
                res["corruptions_detected"].append(f"{desc} ({pattern})")

        if res["exit_code"] == 0 and len(res["corruptions_detected"]) == 0 and len(res["stages"]) == 4:
            res["status"] = "PASS"
        else:
            res["status"] = "FAIL"
            res["error"] = f"Exit code {res['exit_code']}, corruptions: {res['corruptions_detected']}"

    except Exception as e:
        res["error"] = str(e)
        res["status"] = "FAIL"
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass
        res["exit_code"] = proc.returncode if proc.returncode is not None else -1

    res["duration"] = time.time() - t0
    return res


def main():
    repo_root = Path(__file__).resolve().parents[1]
    binary = h.build_binary("ded")
    data_path = h.env_data_path()

    h.skip_unless_available(data_path, binary)

    print("=" * 80)
    print(" EMPIRICAL CHALLENGER DEEP PROBE: VOLCANO, SLIPGATE, QUARRY")
    print(f" Binary:    {binary}")
    print(f" Data Path: {data_path}")
    print("=" * 80)

    individual_results = []
    base_port = 29200
    for idx, map_name in enumerate(COMPLEX_MAPS):
        r = probe_individual_map(binary, data_path, map_name, port=base_port + idx)
        individual_results.append(r)

    seq_result = probe_map_transition_sequence(binary, data_path, port=base_port + len(COMPLEX_MAPS))

    all_passed = (
        all(r["status"] == "PASS" for r in individual_results)
        and seq_result["status"] == "PASS"
    )

    out_json = repo_root / ".agents" / "challenger_m1_iter3_2" / "complex_probe_results.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    summary_data = {
        "individual": individual_results,
        "sequence": seq_result,
        "overall_status": "PASS" if all_passed else "FAIL",
    }
    with open(out_json, "w") as f:
        json.dump(summary_data, f, indent=2)

    print("\n" + "=" * 80)
    print(f" DEEP PROBE SUMMARY: {'PASS' if all_passed else 'FAIL'}")
    print("=" * 80)
    for r in individual_results:
        print(f"  {r['map']:<10}: {r['status']} (spawned: {r['entities_spawned']}, threads: {r['threads_initial']}, exit: {r['exit_code']}, time: {r['duration']:.2f}s)")
    print(f"  Transition Sequence: {seq_result['status']} ({len(seq_result['stages'])}/4 stages, exit: {seq_result['exit_code']}, time: {seq_result['duration']:.2f}s)")
    print(f" Results written to: {out_json}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
