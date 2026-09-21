#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Milestone 4 Challenger 2: Valley Objective Progression & Win Condition Adversarial Harness.

Adversarial stress-testing suite for OpenETQW dedicated server on Valley:
- Sub-test 1: Invalid & boundary objective indices (-1, -999, INT_MIN, 4, 9999, INT_MAX, no arg).
- Sub-test 2: Sequential objective progression (0 -> 1 -> 2 -> 3) and EndGame state machine.
- Sub-test 3: Duplicate trigger storm: 5x successive completeObjective 3 calls before and after state 4.
- Sub-test 4: Out-of-order execution: direct completeObjective 3 skipping 0, 1, and 2.
- Sub-test 5: Rapid command storm (50 commands) and immediate shutdown under active script threads.
"""

import atexit
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

TEST_PROCS = []


def cleanup_procs():
    for p in TEST_PROCS:
        if p and p.poll() is None:
            try:
                p.kill()
                p.wait(timeout=2.0)
            except Exception:
                pass


atexit.register(cleanup_procs)

REPO_ROOT = Path(__file__).resolve().parents[1]
BINARY_PATH = REPO_ROOT / "build" / "openETQW-ded_arm64"
DATA_PATH = Path("/Users/jmulesa/etqw_data")

OBJECTIVE_STATUS_PATTERN = re.compile(
    r"OBJECTIVE_STATUS:\s*gdf_obj=(\d+)\s*strogg_obj=(\d+)\s*state=(\d+)\s*winner=(\w+)"
)
PLAYER_POS_PATTERN = re.compile(
    r"PLAYER_POS:.*?\(client:\s*(\d+)\s*team:\s*(\w+)\s*spectating\s*(\d+)"
)


def stream_reader(pipe, name, q):
    try:
        for line in iter(pipe.readline, ''):
            if not line:
                break
            q.put((name, line.strip()))
    except Exception as e:
        q.put((name, f"READER_ERROR: {e}"))
    finally:
        pipe.close()


def send_cmd(proc, cmd):
    try:
        proc.stdin.write(cmd + "\n")
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        print(f"\nERROR: stdin write '{cmd}' failed: {e}")
        raise


def wait_for_line(output_queue, marker, timeout=10.0):
    start = time.time()
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            if marker in line:
                return line
        except queue.Empty:
            continue
    return None


def query_objective_status(proc, output_queue, timeout=10.0):
    send_cmd(proc, "objectiveStatus")
    start = time.time()
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            m = OBJECTIVE_STATUS_PATTERN.search(line)
            if m:
                return {
                    "gdf_obj": int(m.group(1)),
                    "strogg_obj": int(m.group(2)),
                    "state": int(m.group(3)),
                    "winner": m.group(4)
                }
        except queue.Empty:
            continue
    raise TimeoutError("Timed out waiting for OBJECTIVE_STATUS response")


def launch_dedicated_server(port, desc=""):
    print(f"--> Spawning dedicated server for {desc} on port {port}...")
    server_args = [
        str(BINARY_PATH),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "net_port", str(port),
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "si_pure", "0",
        "+set", "bot_enable", "1",
        "+set", "bot_sleepWhenServerEmpty", "0",
        "+set", "g_teamSwitchDelay", "0",
        "+set", "g_skipDeployChecks", "1",
        "+spawnServer", "valley"
    ]

    proc = subprocess.Popen(
        server_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    TEST_PROCS.append(proc)

    out_q = queue.Queue()
    reader_thread = threading.Thread(
        target=stream_reader,
        args=(proc.stdout, f"ded:{port}", out_q),
        daemon=True
    )
    reader_thread.start()

    # Wait for map to initialize (specifically wait for bot actions loading which signals end of map spawn)
    start = time.time()
    ready = False
    while time.time() - start < 45.0:
        if proc.poll() is not None:
            raise RuntimeError(f"Server died during startup with returncode {proc.returncode}")
        try:
            source, line = out_q.get(timeout=0.1)
            if "Loading Map Bot Actions" in line:
                ready = True
                break
        except queue.Empty:
            continue

    if not ready:
        proc.kill()
        raise TimeoutError(f"Server on port {port} failed to initialize Valley within 45s")

    # Brief stabilization delay after entity spawning completes
    time.sleep(1.0)
    print(f"    Server online and Valley map ready on port {port}.")
    return proc, out_q


def test_suite_invalid_indices_and_sequential():
    """Sub-test 1, 2, & 3: Invalid indices, sequential progression, duplicate EndGame triggers."""
    print("\n" + "=" * 70)
    print(" [ADVERSARIAL SUITE A] Invalid Indices, Progression & Duplicate Triggers")
    print("=" * 70)

    port = 27850
    proc, out_q = launch_dedicated_server(port, "Suite A")

    try:
        # Spawn bots
        send_cmd(proc, "addbot gdf soldier")
        time.sleep(0.5)
        send_cmd(proc, "addbot strogg soldier")
        time.sleep(1.0)

        # -------------------------------------------------------------
        # 1. Invalid & Boundary Indices Stress
        # -------------------------------------------------------------
        print("\n--- 1. Testing Boundary & Invalid Indices ---")
        initial_status = query_objective_status(proc, out_q)
        print(f"  Initial status: gdf_obj={initial_status['gdf_obj']} state={initial_status['state']}")
        assert initial_status["gdf_obj"] == 0, f"Expected initial gdf_obj=0, got {initial_status['gdf_obj']}"

        invalid_calls = [
            ("completeObjective", "usage: completeObjective <index>"),
            ("completeObjective -1", "COMPLETE_OBJECTIVE: -1 triggered"),
            ("completeObjective -999", "COMPLETE_OBJECTIVE: -999 triggered"),
            ("completeObjective -2147483648", "COMPLETE_OBJECTIVE: -2147483648 triggered"),
            ("completeObjective 4", "COMPLETE_OBJECTIVE: 4 triggered"),
            ("completeObjective 5", "COMPLETE_OBJECTIVE: 5 triggered"),
            ("completeObjective 9999", "COMPLETE_OBJECTIVE: 9999 triggered"),
            ("completeObjective 2147483647", "COMPLETE_OBJECTIVE: 2147483647 triggered"),
        ]

        for cmd, expected_marker in invalid_calls:
            print(f"  Sending: '{cmd}'")
            send_cmd(proc, cmd)
            matched = wait_for_line(out_q, expected_marker, timeout=3.0)
            if not matched:
                raise AssertionError(f"Expected marker '{expected_marker}' for command '{cmd}'")

            # Check server stability and ensure gdf_obj has NOT mutated
            status = query_objective_status(proc, out_q)
            assert status["gdf_obj"] == 0, (
                f"gdf_obj mutated on invalid call '{cmd}'! Value: {status['gdf_obj']}"
            )
            assert proc.poll() is None, f"Server crashed on invalid call '{cmd}'"

        print("  -> Passed: Server gracefully handled all negative and out-of-bound indices without crash or corruption.\n")

        # -------------------------------------------------------------
        # 2. Sequential Objective Progression
        # -------------------------------------------------------------
        print("--- 2. Testing Sequential Objective Progression (0 -> 1 -> 2) ---")
        for obj_idx in (0, 1, 2):
            print(f"  Triggering objective {obj_idx}...")
            send_cmd(proc, f"completeObjective {obj_idx}")
            wait_for_line(out_q, f"COMPLETE_OBJECTIVE: {obj_idx} triggered", timeout=5.0)
            time.sleep(1.0)
            status = query_objective_status(proc, out_q)
            print(f"  After obj {obj_idx}: gdf_obj={status['gdf_obj']} state={status['state']}")
            assert status["gdf_obj"] == obj_idx + 1, (
                f"Expected gdf_obj={obj_idx + 1}, got {status['gdf_obj']}"
            )

        print("  -> Passed: Sequential progression (Bridge -> Outpost -> Shield) verified.\n")

        # -------------------------------------------------------------
        # 3. Duplicate Trigger Storm on Final Objective (Objective 3)
        # -------------------------------------------------------------
        print("--- 3. Testing Duplicate Trigger Storm on Objective 3 (5x Burst) ---")
        # Blast completeObjective 3 five times back-to-back
        for i in range(5):
            send_cmd(proc, "completeObjective 3")

        # Wait for trigger lines
        for i in range(5):
            line = wait_for_line(out_q, "COMPLETE_OBJECTIVE: 3 triggered", timeout=5.0)
            if not line:
                raise AssertionError(f"Missing trigger line for duplicate {i+1}")

        print("  All 5 duplicate triggers received. Waiting 2.5s for EndGame delay...")
        time.sleep(2.5)

        end_status = query_objective_status(proc, out_q)
        print(f"  Post-EndGame Status: gdf_obj={end_status['gdf_obj']} state={end_status['state']} winner={end_status['winner']}")
        assert end_status["winner"] == "gdf", f"Expected winner 'gdf', got '{end_status['winner']}'"
        assert end_status["state"] == 4, f"Expected state=4 (GS_GAMEREVIEW), got {end_status['state']}"
        assert proc.poll() is None, "Server crashed during duplicate trigger EndGame"

        # Now hammer completeObjective 3 AGAIN while ALREADY in GS_GAMEREVIEW (state 4)!
        print("  Hammering completeObjective 3 while ALREADY in GS_GAMEREVIEW (state 4)...")
        send_cmd(proc, "completeObjective 3")
        wait_for_line(out_q, "COMPLETE_OBJECTIVE: 3 triggered", timeout=3.0)
        time.sleep(1.5)

        post_review_status = query_objective_status(proc, out_q)
        print(f"  Status after post-review trigger: state={post_review_status['state']} winner={post_review_status['winner']}")
        assert post_review_status["state"] == 4, f"State corrupted after post-review trigger: {post_review_status['state']}"
        assert post_review_status["winner"] == "gdf", f"Winner corrupted: {post_review_status['winner']}"
        assert proc.poll() is None, "Server crashed on post-review objective trigger!"

        print("  -> Passed: Duplicate EndGame triggers handled safely without double-free or assertion crash.\n")

    finally:
        print("  Cleaning up Suite A server via quit...")
        send_cmd(proc, "quit")
        try:
            proc.wait(timeout=5.0)
            assert proc.returncode == 0, f"Server exited with non-zero code {proc.returncode}"
            print(f"  Suite A server exited cleanly with code {proc.returncode}.")
        except subprocess.TimeoutExpired:
            proc.kill()
            raise TimeoutError("Suite A server hung on quit")


def test_suite_out_of_order_endgame():
    """Sub-test 4: Direct EndGame skipping (completeObjective 3 from initial state)."""
    print("\n" + "=" * 70)
    print(" [ADVERSARIAL SUITE B] Out-Of-Order / Direct EndGame Skip Stress")
    print("=" * 70)

    port = 27851
    proc, out_q = launch_dedicated_server(port, "Suite B (Out-Of-Order)")

    try:
        # Spawn a bot to establish active player context and advance game frames
        send_cmd(proc, "addbot gdf soldier")
        time.sleep(1.0)

        # Check initial state: objectives 0, 1, 2 have NOT been touched
        init_stat = query_objective_status(proc, out_q)
        print(f"  Initial status: gdf_obj={init_stat['gdf_obj']} state={init_stat['state']}")
        assert init_stat["gdf_obj"] == 0

        # Jump directly to final objective (Objective 3: Contaminator destruction)
        print("  Jumping directly to completeObjective 3 without completing 0, 1, or 2...")
        send_cmd(proc, "completeObjective 3")
        line = wait_for_line(out_q, "COMPLETE_OBJECTIVE: 3 triggered", timeout=5.0)
        assert line is not None, "Failed to trigger objective 3 out-of-order"

        # Wait for EndGame delay (1000ms + margin)
        print("  Waiting 2.5s for EndGame transition...")
        time.sleep(2.5)

        end_stat = query_objective_status(proc, out_q)
        print(f"  Out-of-order EndGame status: gdf_obj={end_stat['gdf_obj']} state={end_stat['state']} winner={end_stat['winner']}")
        assert end_stat["winner"] == "gdf", f"Expected winner 'gdf', got '{end_stat['winner']}'"
        assert end_stat["state"] == 4, f"Expected state=4 (GS_GAMEREVIEW), got {end_stat['state']}"
        assert proc.poll() is None, "Server crashed during out-of-order EndGame"

        print("  -> Passed: Out-of-order EndGame executed deterministically without crash or NULL fault.\n")

    finally:
        print("  Cleaning up Suite B server via quit...")
        send_cmd(proc, "quit")
        try:
            proc.wait(timeout=5.0)
            assert proc.returncode == 0, f"Server exited with non-zero code {proc.returncode}"
            print(f"  Suite B server exited cleanly with code {proc.returncode}.")
        except subprocess.TimeoutExpired:
            proc.kill()
            raise TimeoutError("Suite B server hung on quit")


def test_suite_rapid_command_storm_and_shutdown():
    """Sub-test 5: Rapid command storm (50 commands) followed immediately by quit."""
    print("\n" + "=" * 70)
    print(" [ADVERSARIAL SUITE C] Rapid Command Storm & Rapid Teardown Stress")
    print("=" * 70)

    port = 27852
    proc, out_q = launch_dedicated_server(port, "Suite C (Rapid Storm)")

    try:
        # Spawn bots to create active pawn entities and thinking threads
        send_cmd(proc, "addbot gdf soldier")
        send_cmd(proc, "addbot strogg soldier")
        time.sleep(1.0)

        # Blast 50 mixed commands in a single tight loop
        print("  Firing 50 rapid interleaved commands...")
        storm_commands = [
            "objectiveStatus",
            "completeObjective 0",
            "playerPos 0",
            "vehicleStatus 0",
            "deployStatus",
            "completeObjective 1",
            "objectiveStatus",
            "playerPos 1",
            "completeObjective 2",
            "completeObjective 3",
            "status",
            "serverInfo",
            "completeObjective -1",
            "completeObjective 999",
            "completeObjective 3",
        ]

        burst = storm_commands * 4  # 60 commands total
        for cmd in burst:
            send_cmd(proc, cmd)

        print("  Burst sent. Immediately issuing 'quit' during active script threads...")
        send_cmd(proc, "quit")

        # Wait for clean termination
        t0 = time.time()
        terminated = False
        while time.time() - t0 < 6.0:
            ret = proc.poll()
            if ret is not None:
                terminated = True
                print(f"  Server terminated in {time.time() - t0:.2f}s with code {ret}")
                assert ret == 0, f"Server crashed on rapid shutdown with code {ret}"
                break
            time.sleep(0.1)

        if not terminated:
            proc.kill()
            raise TimeoutError("Server hung on rapid storm shutdown")

        print("  -> Passed: Server survived rapid command storm and shut down cleanly with code 0.\n")

    finally:
        if proc.poll() is None:
            proc.kill()


def main():
    print("=" * 70)
    print(" OpenETQW Milestone 4: Objectives & Win Condition Adversarial Harness")
    print(" Binary:", BINARY_PATH)
    print(" Map: Valley | GameRules: sdGameRulesObjective")
    print("=" * 70)

    start_time = time.time()
    try:
        test_suite_invalid_indices_and_sequential()
        test_suite_out_of_order_endgame()
        test_suite_rapid_command_storm_and_shutdown()

        elapsed = time.time() - start_time
        print("=" * 70)
        print(f" ALL ADVERSARIAL CHALLENGES PASSED CLEANLY IN {elapsed:.2f}s!")
        print(" VERDICT: APPROVE")
        print("=" * 70)
        sys.exit(0)
    except Exception as e:
        print(f"\n[ADVERSARIAL CHALLENGE FAILURE]: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        print("\nVERDICT: REJECT")
        sys.exit(1)


if __name__ == "__main__":
    main()
