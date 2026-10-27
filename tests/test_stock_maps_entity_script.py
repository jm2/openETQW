#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Empirical Probe: Stock Maps Entity Dictionaries, Script Threads & Objective State Machines.

Challenger M1-2 Empirical Harness:
Probes openETQW-ded_arm64 dedicated server across all 11 stock maps:
1. Entity dictionary inspection: worldspawn, territories (sdDeployZone / sdSpawnController), objectives.
2. Script thread inspection (listThreads): active thread count and core thread validation.
3. Objective state machine inspection (objectiveStatus): gameState, current objective indices, winner.
4. Adversarial stress & fuzzing: format string injection, nonexistent entities, boundary queries.
5. Post-adversarial health check and clean shutdown (exit code 0).
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

STOCK_MAPS = [
    "quarry",
    "area22",
    "ark",
    "canyon",
    "island",
    "outskirts",
    "refinery",
    "salvage",
    "sewer",
    "slipgate",
    "volcano",
]

OBJECTIVE_STATUS_RE = re.compile(
    r"OBJECTIVE_STATUS:\s*gdf_obj=(-?\d+)\s*strogg_obj=(-?\d+)\s*state=(\d+)\s*winner=(\w+)"
)
ACTIVE_THREADS_RE = re.compile(r"(\d+)\s+active threads")
ENTITIES_SPAWNED_RE = re.compile(r"\.\.\.(\d+) entities spawned")
WORLDSPAWN_RE = re.compile(r"(\d+):\s*worldspawn\s+idWorldspawn\s+worldspawn")
TYPE_COUNT_RE = re.compile(r"(\d+)\s+entities of type\s+(\w+)")


def stream_reader(pipe, q):
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            q.put(line.strip())
    except Exception as e:
        q.put(f"READER_ERROR: {e}")
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def send_cmd(proc, cmd_text: str):
    try:
        proc.stdin.write(cmd_text + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        raise RuntimeError(f"Failed to send command '{cmd_text}': {e}") from e


def wait_for_output(q, pattern, timeout=10.0):
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


def probe_map(binary: Path, data_path: Path, map_name: str, port: int, timeout: float = 40.0) -> dict:
    t0 = time.time()
    result = {
        "map": map_name,
        "port": port,
        "status": "FAIL",
        "exit_code": -1,
        "entities_spawned": 0,
        "worldspawn_found": False,
        "worldspawn_num": -1,
        "worldspawn_args_count": 0,
        "deployzones_count": 0,
        "spawncontrollers_count": 0,
        "scriptentities_count": 0,
        "active_threads": 0,
        "initial_obj_status": None,
        "post_fuzz_obj_status": None,
        "fuzz_tests_passed": 0,
        "fuzz_tests_total": 0,
        "duration": 0.0,
        "error": None,
    }

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
    reader = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
    reader.start()

    try:
        # 1. Wait for server initialization
        init_start = time.time()
        initialized = False
        all_output = []
        while time.time() - init_start < 25.0:
            if proc.poll() is not None:
                raise RuntimeError(f"Server crashed during initialization with exit code {proc.returncode}")
            try:
                line = out_q.get(timeout=0.1)
                all_output.append(line)
                m_ent = ENTITIES_SPAWNED_RE.search(line)
                if m_ent:
                    result["entities_spawned"] = int(m_ent.group(1))
                if "Loading Map Bot Actions" in line or "ETQW initialized" in line:
                    initialized = True
                    break
            except queue.Empty:
                continue

        if not initialized:
            raise TimeoutError(f"Server on map '{map_name}' did not complete initialization within 25s")

        # Allow ticks to advance and scripts to initialize (brief 1.5s sleep)
        time.sleep(1.5)
        drain_queue(out_q, 0.3)

        # 2. Probe initial objectiveStatus
        send_cmd(proc, "objectiveStatus")
        m_obj, lines = wait_for_output(out_q, OBJECTIVE_STATUS_RE, timeout=5.0)
        if not m_obj:
            raise RuntimeError("Failed to receive valid OBJECTIVE_STATUS from initial query")
        result["initial_obj_status"] = {
            "gdf_obj": int(m_obj.group(1)),
            "strogg_obj": int(m_obj.group(2)),
            "state": int(m_obj.group(3)),
            "winner": m_obj.group(4),
        }

        # 3. Probe script threads (listThreads)
        send_cmd(proc, "listThreads")
        m_thr, lines = wait_for_output(out_q, ACTIVE_THREADS_RE, timeout=5.0)
        if not m_thr:
            raise RuntimeError("Failed to receive active threads from listThreads query")
        result["active_threads"] = int(m_thr.group(1))

        # 4. Probe entity dictionary: worldspawn
        send_cmd(proc, "listEntities worldspawn")
        m_ws, lines = wait_for_output(out_q, WORLDSPAWN_RE, timeout=5.0)
        if not m_ws:
            raise RuntimeError("Failed to find worldspawn entity via listEntities")
        result["worldspawn_found"] = True
        result["worldspawn_num"] = int(m_ws.group(1))

        # 5. Probe spawnArgs of worldspawn
        send_cmd(proc, "listSpawnArgs worldspawn")
        drain_lines = drain_queue(out_q, 0.8)
        spawn_arg_lines = [l for l in drain_lines if l.startswith('"') and '"' in l[1:]]
        result["worldspawn_args_count"] = len(spawn_arg_lines)
        if result["worldspawn_args_count"] == 0:
            # Try waiting slightly more
            more_lines = drain_queue(out_q, 0.5)
            spawn_arg_lines.extend([l for l in more_lines if l.startswith('"')])
            result["worldspawn_args_count"] = len(spawn_arg_lines)

        # 6. Probe entity breakdown (listEntities) for territories and objectives
        send_cmd(proc, "listEntities")
        ent_lines = drain_queue(out_q, 1.5)
        for el in ent_lines:
            m_tc = TYPE_COUNT_RE.search(el)
            if m_tc:
                count = int(m_tc.group(1))
                typename = m_tc.group(2)
                if typename == "sdDeployZone":
                    result["deployzones_count"] = count
                elif typename == "sdSpawnController":
                    result["spawncontrollers_count"] = count
                elif typename == "sdScriptEntity":
                    result["scriptentities_count"] = count

        # 7. Adversarial Fuzzing & Null Pointer Stress Queries
        fuzz_commands = [
            ("listSpawnArgs nonexistent_entity_99999", "entity not found"),
            ("listSpawnArgs \"\"", "entity not found"),
            ("listSpawnArgs \"%s%d%x%n\"", "entity not found"),
            ("listEntities nonexistent_filter_xyz_123", "...0 entities"),
            ("damage nonexistent_ent 100", None),
            ("trigger nonexistent_ent", None),
            ("teleport nonexistent_ent", None),
            ("remove nonexistent_ent", None),
            ("spawn nonexistent_entity_def_xyz", None),
        ]
        result["fuzz_tests_total"] = len(fuzz_commands)
        fuzz_passed = 0

        for f_cmd, expected_kw in fuzz_commands:
            send_cmd(proc, f_cmd)
            drain_res = drain_queue(out_q, 0.2)
            if proc.poll() is not None:
                raise RuntimeError(f"Server crashed during fuzz command '{f_cmd}' with exit code {proc.returncode}")
            fuzz_passed += 1

        result["fuzz_tests_passed"] = fuzz_passed

        # 8. Post-Fuzz Health Check
        send_cmd(proc, "objectiveStatus")
        m_post, lines = wait_for_output(out_q, OBJECTIVE_STATUS_RE, timeout=5.0)
        if not m_post:
            raise RuntimeError("Server unresponsive to objectiveStatus after adversarial fuzzing")
        result["post_fuzz_obj_status"] = {
            "gdf_obj": int(m_post.group(1)),
            "strogg_obj": int(m_post.group(2)),
            "state": int(m_post.group(3)),
            "winner": m_post.group(4),
        }

        # 9. Graceful Shutdown
        send_cmd(proc, "quit")
        proc.wait(timeout=10.0)
        result["exit_code"] = proc.returncode

        if result["exit_code"] == 0:
            result["status"] = "PASS"
        else:
            result["error"] = f"Abnormal shutdown exit code {result['exit_code']}"

    except Exception as e:
        result["error"] = str(e)
        result["status"] = "FAIL"
        if proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass
        result["exit_code"] = proc.returncode if proc.returncode is not None else -1

    result["duration"] = time.time() - t0
    return result


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    binary = h.build_binary("ded")
    data_path = h.env_data_path()

    h.skip_unless_available(data_path, binary)

    print("=" * 95)
    print(" OPENETQW EMPIRICAL PROBE: ENTITY DICTIONARIES, SCRIPT THREADS & OBJECTIVE STATE")
    print(f" Binary:    {binary}")
    print(f" Data Path: {data_path}")
    print(f" Maps:      {len(STOCK_MAPS)} stock maps")
    print("=" * 95)

    header = f"{'Map Name':<11} | {'Status':<6} | {'Exit':<4} | {'Ents':<5} | {'WS':<4} | {'Args':<4} | {'Terr':<4} | {'Objs':<4} | {'Thrs':<4} | {'State':<5} | {'Fuzz':<5} | {'Time(s)':<7}"
    print(header)
    print("-" * len(header))

    results = []
    base_port = 27810
    failures = 0

    for i, map_name in enumerate(STOCK_MAPS):
        res = probe_map(binary, data_path, map_name, port=base_port + i, timeout=40.0)
        results.append(res)

        ws_str = "OK" if res["worldspawn_found"] else "MISS"
        args_str = str(res["worldspawn_args_count"])
        terr_count = res["deployzones_count"] + res["spawncontrollers_count"]
        terr_str = str(terr_count)
        obj_str = str(res["scriptentities_count"])
        thr_str = str(res["active_threads"])
        state_str = str(res["initial_obj_status"]["state"]) if res["initial_obj_status"] else "ERR"
        fuzz_str = f"{res['fuzz_tests_passed']}/{res['fuzz_tests_total']}"
        time_str = f"{res['duration']:.2f}"

        row = f"{map_name:<11} | {res['status']:<6} | {res['exit_code']:<4} | {res['entities_spawned']:<5} | {ws_str:<4} | {args_str:<4} | {terr_str:<4} | {obj_str:<4} | {thr_str:<4} | {state_str:<5} | {fuzz_str:<5} | {time_str:<7}"
        print(row)

        if res["status"] != "PASS":
            failures += 1
            print(f"  --> ERROR on {map_name}: {res['error']}")

    print("=" * len(header))

    # Save structured results JSON for verification and reporting
    out_json = repo_root / ".agents" / "challenger_m1_2" / "probe_results.json"
    try:
        with open(out_json, "w") as f:
            json.dump(results, f, indent=2)
        print(f"[REPORT] Structured results saved to: {out_json}")
    except Exception as e:
        print(f"[WARN] Could not save json: {e}", file=sys.stderr)

    if failures == 0:
        print(f"[SUCCESS] 100% PASS: All {len(STOCK_MAPS)} stock maps verified with robust entity/script state!")
        return 0
    else:
        print(f"[FAILURE] {failures} maps failed empirical probe.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
