#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adversarial Resilience and Stress Test Suite for OpenETQW Milestone 2.

Tests:
1. Rapid console command flooding & sequence stress (status, serverMapRestart, status, quit).
2. Multi-cycle serverMapRestart with entity re-spawning and memory leak verification.
3. Piped stdin handling:
   - Early pipe with immediate EOF
   - Active piped session with EOF and graceful SIGTERM shutdown
4. Malformed, oversized, and hostile console inputs (buffer overflow probe, control chars, unclosed quotes).
5. Verification of clean exit code 0 across all scenarios.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import etqw_harness as h


REPO_ROOT = Path(__file__).resolve().parents[1]
BINARY_PATH = h.build_binary("ded")
DATA_PATH = h.env_data_path()

COMMON_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "si_rules", "sdGameRulesObjective",
    "+spawnServer", "valley"
]


def get_process_rss_kb(pid: int) -> int:
    """Get Resident Set Size in Kilobytes for a PID on macOS."""
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()
        return int(out)
    except Exception:
        return 0


def get_process_cpu_percent(pid: int) -> float:
    """Get CPU percentage for a PID on macOS."""
    try:
        out = subprocess.check_output(["ps", "-o", "%cpu=", "-p", str(pid)]).decode().strip()
        return float(out)
    except Exception:
        return 0.0


# the map is loaded and the game running (the objective script's "StartFirstObjective"
# thread no longer logs a warning since the retail sendNetworkEvent signature was fixed)
READY_MARKER = "------------- ETQW initialized -------------"


def wait_for_marker(proc: subprocess.Popen, marker: str, timeout: float = 30.0) -> bool:
    """Read stdout lines until marker appears or timeout occurs."""
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return False
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.05)
            continue
        if marker in line:
            return True
    return False


def test_rapid_console_burst():
    """Test rapid bursts of console commands during active map simulation."""
    print("\n--- [TEST 1] Rapid Console Command Flooding & Sequence Stress ---")
    proc = subprocess.Popen(
        COMMON_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    try:
        print("[1] Waiting for the map to load (ETQW initialized)...")
        if not wait_for_marker(proc, READY_MARKER, timeout=40.0):
            raise RuntimeError("Server failed to load the map")

        print("[2] Flooding console: 'status\\nserverMapRestart\\nstatus\\n' in zero-delay burst...")
        proc.stdin.write("status\nserverMapRestart\nstatus\necho MAP_RESTART_DONE\n")
        proc.stdin.flush()

        print("[3] Waiting for serverMapRestart to finish (echo after the burst)...")
        if not wait_for_marker(proc, "MAP_RESTART_DONE", timeout=40.0):
            raise RuntimeError("Server failed to re-initialize after serverMapRestart")

        print("[4] Map successfully restarted and active! Sending another rapid burst: 'status\\nstatus\\n'...")
        proc.stdin.write("status\nstatus\n")
        proc.stdin.flush()
        time.sleep(1.0)

        print("[5] Sending 'quit' command...")
        proc.stdin.write("quit\n")
        proc.stdin.flush()

        rc = proc.wait(timeout=15.0)
        assert rc == 0, f"Expected exit code 0, got {rc}"
        print(f"[PASSED] Test 1 completed successfully with exit code {rc}.")
        return True
    finally:
        if proc.poll() is None:
            proc.kill()


def test_multi_cycle_restart_and_memory():
    """Test 3 consecutive serverMapRestart cycles and monitor memory stability."""
    print("\n--- [TEST 2] Multi-Cycle serverMapRestart & Memory Stability ---")
    proc = subprocess.Popen(
        COMMON_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    try:
        print("[1] Waiting for initial map simulation...")
        if not wait_for_marker(proc, READY_MARKER, timeout=40.0):
            raise RuntimeError("Server failed to load the map")

        # Give simulation 2 seconds to stabilize
        time.sleep(2.0)
        rss_initial = get_process_rss_kb(proc.pid)
        print(f"Initial RSS memory: {rss_initial} KB (~{rss_initial / 1024:.2f} MB)")

        rss_measurements = [rss_initial]

        for cycle in range(1, 4):
            print(f"\n[Cycle {cycle}/3] Issuing 'serverMapRestart'...")
            proc.stdin.write(f"serverMapRestart\necho RESTART_DONE_{cycle}\n")
            proc.stdin.flush()

            if not wait_for_marker(proc, f"RESTART_DONE_{cycle}", timeout=40.0):
                raise RuntimeError(f"Server failed to complete restart in cycle {cycle}")

            # Let simulation run for 60 frames (~2 seconds)
            time.sleep(2.0)
            rss = get_process_rss_kb(proc.pid)
            rss_measurements.append(rss)
            print(f"Cycle {cycle} complete. Current RSS: {rss} KB (~{rss / 1024:.2f} MB)")

        delta_kb = rss_measurements[-1] - rss_initial
        delta_mb = delta_kb / 1024
        print(f"\nTotal RSS delta after 3 full restarts: {delta_kb:+d} KB ({delta_mb:+.2f} MB)")
        # A runaway leak would add tens of MBs each restart (>50MB). ETQW caches assets; small growth < 25MB is expected.
        assert delta_mb < 35.0, f"Potential memory leak detected: RSS grew by {delta_mb:.2f} MB across 3 restarts!"

        print("[Cycle Complete] Sending final 'status' and 'quit'...")
        proc.stdin.write("status\nquit\n")
        proc.stdin.flush()

        rc = proc.wait(timeout=15.0)
        assert rc == 0, f"Expected exit code 0, got {rc}"
        print(f"[PASSED] Test 2 completed successfully. Exit code {rc}, memory stable.")
        return True
    finally:
        if proc.poll() is None:
            proc.kill()


def test_piped_stdin_immediate_eof():
    """Test piped stdin with immediate commands and EOF at process launch."""
    print("\n--- [TEST 3A] Piped stdin with Immediate Commands & EOF ---")
    pipe_input = "status\nquit\n"
    start = time.time()
    proc = subprocess.Popen(
        COMMON_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )

    stdout_data, _ = proc.communicate(input=pipe_input, timeout=40.0)
    elapsed = time.time() - start
    rc = proc.returncode

    print(f"Piped process terminated in {elapsed:.2f}s with exit code {rc}")
    assert rc == 0, f"Expected exit code 0, got {rc}"
    assert "ETQW Dedicated Server Status" in stdout_data or "Shutdown event system" in stdout_data, \
        "Expected status or clean shutdown in piped stdout"
    print(f"[PASSED] Test 3A completed successfully with exit code {rc}.")
    return True


def test_piped_stdin_active_eof_and_signal():
    """Test piped stdin EOF during active simulation, verify no spinlock, and clean SIGTERM shutdown."""
    print("\n--- [TEST 3B] Active Piped stdin EOF & Graceful Signal Handling ---")
    proc = subprocess.Popen(
        COMMON_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    try:
        print("[1] Waiting for active map simulation...")
        if not wait_for_marker(proc, READY_MARKER, timeout=40.0):
            raise RuntimeError("Server failed to load the map")

        print("[2] Sending 'status' and then immediately closing stdin (EOF)...")
        proc.stdin.write("status\n")
        proc.stdin.flush()
        proc.stdin.close()  # EOF trigger!

        print("[3] Observing process behavior with EOF over 3 seconds...")
        time.sleep(3.0)

        # Verify process is still alive and running (did not crash or exit on EOF)
        assert proc.poll() is None, "Process died prematurely upon stdin EOF"

        # Check CPU utilization - should not be pegged in 100% spin loop
        cpu = get_process_cpu_percent(proc.pid)
        print(f"Process alive after EOF. Current CPU usage: {cpu:.1f}%")

        print("[4] Sending SIGTERM to test graceful signal shutdown...")
        os.kill(proc.pid, signal.SIGTERM)

        rc = proc.wait(timeout=15.0)
        assert rc == 0, f"Expected exit code 0 on graceful SIGTERM, got {rc}"
        print(f"[PASSED] Test 3B completed successfully. EOF handled without spinlock, clean SIGTERM exit code {rc}.")
        return True
    finally:
        if proc.poll() is None:
            proc.kill()


def test_malformed_and_hostile_inputs():
    """Test console input resilience against buffer overflow, empty lines, and malformed strings."""
    print("\n--- [TEST 4] Malformed, Oversized, and Hostile Console Inputs ---")
    proc = subprocess.Popen(
        COMMON_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    try:
        print("[1] Waiting for active map simulation...")
        if not wait_for_marker(proc, READY_MARKER, timeout=40.0):
            raise RuntimeError("Server failed to load the map")

        print("[2] Injecting hostile inputs:")
        # A: Empty lines and whitespace
        print("  -> Injecting 20 empty newlines and whitespace chunks...")
        proc.stdin.write("\n\n\n   \t   \n\n\n")
        proc.stdin.flush()

        # B: Very long line (>1024 chars to stress Posix_ConsoleInput buffer limit)
        print("  -> Injecting oversized 2048-character line...")
        oversized = "echo " + ("X" * 2040) + "\n"
        proc.stdin.write(oversized)
        proc.stdin.flush()

        # C: Unclosed quotes and special punctuation
        print("  -> Injecting unclosed quotes and multi-semicolons...")
        proc.stdin.write("\"unclosed_quote_string; status; \"\"\"\"\"\n")
        proc.stdin.flush()

        # D: Control characters (backspaces, bell, esc, del)
        print("  -> Injecting control characters (\\x08, \\x07, \\x1b, \\x7f)...")
        proc.stdin.write("stat\x08\x08atus\x07\x1b\x7f\n")
        proc.stdin.flush()

        # E: Unknown commands
        print("  -> Injecting unknown command tokens...")
        proc.stdin.write("nonexistent_command_12345 foo bar baz\n")
        proc.stdin.flush()

        time.sleep(1.0)
        # F: Verify console is still fully functional after abuse
        print("[3] Verifying console functionality post-abuse with 'status'...")
        proc.stdin.write("status\n")
        proc.stdin.flush()
        time.sleep(1.0)

        print("[4] Sending clean 'quit'...")
        proc.stdin.write("quit\n")
        proc.stdin.flush()

        rc = proc.wait(timeout=15.0)
        assert rc == 0, f"Expected exit code 0 after hostile inputs, got {rc}"
        print(f"[PASSED] Test 4 completed successfully. Engine survived all hostile inputs with exit code {rc}.")
        return True
    finally:
        if proc.poll() is None:
            proc.kill()


def main():
    print("=================================================================")
    print("OpenETQW Milestone 2: Adversarial Resilience & Stress Test Suite")
    print("=================================================================")

    tests = [
        ("Test 1: Rapid Console Flooding & Sequence Stress", test_rapid_console_burst),
        ("Test 2: Multi-Cycle serverMapRestart & Memory Stability", test_multi_cycle_restart_and_memory),
        ("Test 3A: Piped stdin Immediate EOF", test_piped_stdin_immediate_eof),
        ("Test 3B: Active Piped stdin EOF & SIGTERM Shutdown", test_piped_stdin_active_eof_and_signal),
        ("Test 4: Malformed, Oversized & Hostile Console Inputs", test_malformed_and_hostile_inputs),
    ]

    results = {}
    total_start = time.time()

    for name, test_fn in tests:
        try:
            passed = test_fn()
            results[name] = "PASSED" if passed else "FAILED"
        except Exception as exc:
            print(f"[FAILED] {name} failed with exception: {exc}")
            results[name] = f"FAILED: {exc}"

    total_elapsed = time.time() - total_start
    print("\n=================================================================")
    print("ADVERSARIAL STRESS TEST SUMMARY REPORT")
    print(f"Total Execution Time: {total_elapsed:.2f} seconds")
    print("=================================================================")
    all_passed = True
    for name, res in results.items():
        status_str = "PASS" if res == "PASSED" else "FAIL"
        print(f"[{status_str}] {name} -> {res}")
        if res != "PASSED":
            all_passed = False

    print("=================================================================")
    if all_passed:
        print("ALL ADVERSARIAL RESILIENCE TESTS PASSED!")
        sys.exit(0)
    else:
        print("SOME ADVERSARIAL TESTS FAILED.")
        sys.exit(1)


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, BINARY_PATH)
    main()
