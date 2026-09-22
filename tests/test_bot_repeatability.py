#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Repeatability check for Bot AI Gameplay (Milestone 4).

Runs tests/test_bot_gameplay.py across multiple consecutive executions and verifies:
1. 100% pass rate (exit code 0 for all runs).
2. Pawn spawning & ground touchdown on Valley on every run.
3. Bot displacement >= 50 units on every single run.
4. Combat engagement detection (atk_cmd=1 / atk=1) on every run.
5. Clean process termination without hanging or socket bind collisions.

Runs are not compared with each other (bot behaviour is not deterministic);
each run must pass on its own. If the child test skips (exit 77: no retail
data / no binary) this script skips too (exit 77) instead of failing.
"""

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_SCRIPT = REPO_ROOT / "tests" / "test_bot_gameplay.py"
NUM_RUNS = 5


def main():
    print("===============================================================")
    print(f" OpenETQW Milestone 4: Bot Gameplay Repeatability ({NUM_RUNS} Runs)")
    print("===============================================================\n")

    results = []
    total_start = time.time()

    for run_idx in range(1, NUM_RUNS + 1):
        print(f"\n--- [Execution {run_idx}/{NUM_RUNS}] ---")
        t0 = time.time()
        p = subprocess.run(
            [sys.executable, str(TEST_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True
        )
        elapsed = time.time() - t0

        output = p.stdout
        if p.returncode == 77:
            print(f"  Result:        SKIPPED (exit code 77)\n{output}")
            print("[SKIP] test_bot_gameplay.py skipped (missing retail data or binary); skipping repeatability (exit 77).")
            return 77
        passed = (p.returncode == 0)

        # Parse key metrics from test output
        gdf_disp = "N/A"
        strogg_disp = "N/A"
        combat_found = "COMBAT DETECTED" in output

        for line in output.splitlines():
            if "GDF Bot AAS navigation displacement:" in line:
                gdf_disp = line.split(":")[1].strip()
            elif "Strogg Bot AAS navigation displacement:" in line:
                strogg_disp = line.split(":")[1].strip()

        print(f"  Result:        {'PASSED' if passed else 'FAILED'} (exit code {p.returncode})")
        print(f"  Duration:      {elapsed:.2f}s")
        print(f"  GDF Disp:      {gdf_disp}")
        print(f"  Strogg Disp:   {strogg_disp}")
        print(f"  Combat Active: {combat_found}")

        results.append({
            "run": run_idx,
            "passed": passed,
            "returncode": p.returncode,
            "duration": elapsed,
            "gdf_disp": gdf_disp,
            "strogg_disp": strogg_disp,
            "combat": combat_found
        })

        if not passed:
            print(f"\nERROR: Run {run_idx} failed! Output:\n{output}", file=sys.stderr)
            return 1

        # Brief delay between runs to ensure OS UDP port release
        time.sleep(1.0)

    total_elapsed = time.time() - total_start
    print("\n===============================================================")
    print(" REPEATABILITY SUMMARY")
    print("===============================================================")
    print(f" Total Executions: {NUM_RUNS}")
    print(f" Success Rate:     {sum(1 for r in results if r['passed'])}/{NUM_RUNS} (100%)")
    print(f" Average Duration: {sum(r['duration'] for r in results)/NUM_RUNS:.2f}s")
    print(f" Total Duration:   {total_elapsed:.2f}s")

    for r in results:
        print(f"  Run {r['run']}: code={r['returncode']} time={r['duration']:.2f}s "
              f"GDF={r['gdf_disp']} Strogg={r['strogg_disp']} combat={r['combat']}")

    print("===============================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
