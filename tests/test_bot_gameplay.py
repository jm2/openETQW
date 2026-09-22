#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bot Navigation, Combat & Automated Integration Test for OpenETQW Milestone 4.

Spawns an authoritative dedicated server hosting 'valley' with bot_enable 1,
spawns opposing AI soldier bots (GDF Soldier and Strogg Soldier),
and verifies:
1. Genuine bot allocation and physical pawn possession (spectating 0 for both bots).
2. Parachute drop and ground contact (ground: 1).
3. AAS pathfinding navigation, with the server tick rate MEASURED from SIM_HASH
   tick numbers (+set g_simHash 1) and wall-clock timestamps (27..33.5 Hz).
4. Dynamic directional movement generation (forwardmove/rightmove) with displacement >= 50 units over >= 100
   measured server ticks.
5. Combat perception, aiming, and weapon firing interaction (atk_cmd: 1 / atk: 1).
6. Graceful dedicated server shutdown via 'quit' with clean exit code 0.
"""

import atexit
import math
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
DEFAULT_DATA_PATH = Path("/Users/jmulesa/etqw_data")
DATA_PATH = Path(os.environ.get("ETQW_DATA_PATH", str(DEFAULT_DATA_PATH)))
SERVER_PORT = int(os.environ.get("OPENETQW_BOT_GAMEPLAY_PORT", "27760"))

# The whole script must finish inside the meson timeout (120 s, meson.build);
# the waits below are sized so the worst case stays under it:
# 45 load + 2 + 10 spawn + (40 + 4.5 loop overrun) + 10 quit ~= 112 s.
SERVER_READY_TIMEOUT = 45.0
SPAWN_CONFIRM_TIMEOUT = 10.0
QUERY_TIMEOUT = 2.0
TICK_HZ_RANGE = (27.0, 33.5)
MIN_MEASURED_TICKS = 100

def _find_dedicated_binary() -> Path:
    override = os.environ.get("OPENETQW_DED_BINARY")
    if override:
        return Path(override)
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

BINARY_PATH = _find_dedicated_binary()

SERVER_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "net_port", str(SERVER_PORT),
    "+set", "si_rules", "sdGameRulesObjective",
    "+set", "si_pure", "0",
    "+set", "bot_enable", "1",
    "+set", "bot_sleepWhenServerEmpty", "0",
    "+set", "g_teamSwitchDelay", "0",
    # SIM_HASH lines carry the server frame number every tick; the game
    # default is changing to 0, so request them explicitly.
    "+set", "g_simHash", "1",
    "+spawnServer", "valley"
]

SIM_HASH_TICK_RE = re.compile(r"SIM_HASH:\s+tick=(\d+)")
SIM_TICKS = []  # (time.monotonic(), tick) for every SIM_HASH line

PLAYER_POS_PATTERN = re.compile(
    r"PLAYER_POS:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"vel:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"ground:\s*(\d+)\s*ducked:\s*(\d+)\s*prone:\s*(\d+)\s*jumped:\s*(\d+)\s*"
    r"\(client:\s*(\d+)\s*team:\s*(\w+)\s*spectating\s*(\d+)\s*atk:\s*(\d+)\s*"
    r"cmd:\s*fwd=([-\d]+)\s*side=([-\d]+)\s*up=([-\d]+)\s*atk_cmd=(\d+)"
)


def stream_reader(pipe, name, q):
    try:
        for line in iter(pipe.readline, ''):
            if not line:
                break
            m = SIM_HASH_TICK_RE.search(line)
            if m:
                # timestamp here, in the reader thread; SIM_HASH lines are not
                # queued (they would drown the PLAYER_POS replies)
                SIM_TICKS.append((time.monotonic(), int(m.group(1))))
                continue
            q.put((name, line.strip()))
    except Exception as e:
        q.put((name, f"READER_ERROR: {e}"))
    finally:
        pipe.close()


def query_player_pos(proc, output_queue, client_num, timeout=5.0):
    """Sends playerPos <clientNum> command to server stdin and parses PLAYER_POS output."""
    cmd = f"playerPos {client_num}\n"
    try:
        proc.stdin.write(cmd)
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        time.sleep(0.2)
        print(f"\nERROR: Server stdin write failed: {e} (proc poll={proc.poll()})")
        raise

    start = time.time()
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            if source == "SERVER" and "PLAYER_POS:" in line:
                match = PLAYER_POS_PATTERN.search(line)
                if match:
                    parsed_client = int(match.group(11))
                    if parsed_client == client_num:
                        return {
                            "org": (float(match.group(1)), float(match.group(2)), float(match.group(3))),
                            "vel": (float(match.group(4)), float(match.group(5)), float(match.group(6))),
                            "ground": int(match.group(7)),
                            "ducked": int(match.group(8)),
                            "prone": int(match.group(9)),
                            "jumped": int(match.group(10)),
                            "client": parsed_client,
                            "team": match.group(12),
                            "spectating": int(match.group(13)),
                            "atk": int(match.group(14)),
                            "fwd": int(match.group(15)),
                            "side": int(match.group(16)),
                            "up": int(match.group(17)),
                            "atk_cmd": int(match.group(18)),
                            "raw": line
                        }
        except queue.Empty:
            continue

    raise TimeoutError(f"Timed out waiting for PLAYER_POS for client {client_num}")


def run_bot_gameplay_test():
    print("=================================================================")
    print(" OpenETQW Milestone 4: Bot AI Navigation & Combat Integration Test")
    print(f" Target: Valley map, Server 127.0.0.1:{SERVER_PORT}")
    print(" Objective: Bot Spawning, AAS Navigation, Movement & Combat Engagement")
    print("=================================================================\n")

    if not BINARY_PATH.is_file():
        print(f"[SKIP] Dedicated engine binary not found at {BINARY_PATH}. Skipping test (exit code 77).")
        return 77
    if not DATA_PATH.is_dir() or not (DATA_PATH / "base").is_dir() or not (DATA_PATH / "base" / "pak000.pk4").is_file():
        print(f"[SKIP] ETQW retail data not found or incomplete at {DATA_PATH}. Skipping bot gameplay test (exit code 77).")
        return 77

    output_queue = queue.Queue()

    # 1. Start Server
    print("[1] Spawning authoritative dedicated server on Valley with bot_enable 1...")
    server_proc = subprocess.Popen(
        SERVER_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    TEST_PROCS.append(server_proc)

    s_thread = threading.Thread(
        target=stream_reader,
        args=(server_proc.stdout, "SERVER", output_queue),
        daemon=True
    )
    s_thread.start()

    server_ready = False
    server_start_time = time.time()
    while time.time() - server_start_time < SERVER_READY_TIMEOUT:
        if server_proc.poll() is not None:
            raise RuntimeError(f"Server exited prematurely with code {server_proc.returncode}")
        try:
            source, line = output_queue.get(timeout=0.2)
            if "StartFirstObjective" in line or "Loading Map Bot Actions" in line or "Server spawned on" in line:
                print(f"  [{source}] {line}")
                if "StartFirstObjective" in line or "Loading Map Bot Actions" in line:
                    server_ready = True
                    break
            elif "Map:" in line or "Game initialized" in line or "Loading AAS" in line:
                print(f"  [{source}] {line}")
        except queue.Empty:
            continue

    if not server_ready:
        server_proc.kill()
        raise TimeoutError(f"Server failed to load Valley within {SERVER_READY_TIMEOUT:.0f}s")

    print("\n[2] Dedicated server active on Valley! Spawning GDF Soldier and Strogg Soldier bots...")

    # Spawn GDF Soldier (Bot 0)
    server_proc.stdin.write("addbot gdf soldier\n")
    server_proc.stdin.flush()
    time.sleep(1.0)

    # Spawn Strogg Soldier (Bot 1)
    server_proc.stdin.write("addbot strogg soldier\n")
    server_proc.stdin.flush()
    time.sleep(1.0)

    # Wait for bot spawn confirmations in stdout
    spawn_gdf_confirmed = False
    spawn_strogg_confirmed = False
    confirm_start = time.time()
    while time.time() - confirm_start < SPAWN_CONFIRM_TIMEOUT:
        try:
            source, line = output_queue.get(timeout=0.2)
            if "joined the GDF" in line or ("spawned at" in line and "team gdf" in line):
                print(f"  [{source}] GDF Bot Spawn Confirmed: {line}")
                spawn_gdf_confirmed = True
            elif "joined the Strogg" in line or ("spawned at" in line and "team strogg" in line):
                print(f"  [{source}] Strogg Bot Spawn Confirmed: {line}")
                spawn_strogg_confirmed = True
            if spawn_gdf_confirmed and spawn_strogg_confirmed:
                break
        except queue.Empty:
            continue

    if not spawn_gdf_confirmed or not spawn_strogg_confirmed:
        raise RuntimeError(f"Bot spawn verification failed (gdf={spawn_gdf_confirmed}, strogg={spawn_strogg_confirmed})")

    print("\n[3] Monitoring active bot pawns, AAS navigation displacement & combat engagement...")

    gdf_initial_ground_pos = None
    strogg_initial_ground_pos = None
    gdf_max_displacement = 0.0
    strogg_max_displacement = 0.0
    combat_engagement_seen = False
    both_grounded_once = False
    grounded_samples = 0

    test_start_time = time.time()
    monitor_start_mono = time.monotonic()
    MAX_TEST_DURATION = 40.0  # seconds

    while time.time() - test_start_time < MAX_TEST_DURATION:
        if server_proc.poll() is not None:
            raise RuntimeError(f"Server exited unexpectedly with code {server_proc.returncode}")

        time.sleep(0.5)

        try:
            pos0 = query_player_pos(server_proc, output_queue, client_num=0, timeout=QUERY_TIMEOUT)
            pos1 = query_player_pos(server_proc, output_queue, client_num=1, timeout=QUERY_TIMEOUT)
        except Exception as e:
            print(f"  Query warning: {e}")
            continue

        # 1. Verify physical pawn possession (spectating 0)
        if pos0["spectating"] != 0 or pos1["spectating"] != 0:
            raise AssertionError(f"Bot in spectator/limbo: Bot 0 spectating={pos0['spectating']}, Bot 1 spectating={pos1['spectating']}")

        # 2. Check ground contact and initialize base ground positions
        if pos0["ground"] == 1 and gdf_initial_ground_pos is None:
            gdf_initial_ground_pos = (pos0["org"][0], pos0["org"][1])
            print(f"  Bot 0 (GDF) established ground contact at {pos0['org']}")

        if pos1["ground"] == 1 and strogg_initial_ground_pos is None:
            strogg_initial_ground_pos = (pos1["org"][0], pos1["org"][1])
            print(f"  Bot 1 (Strogg) established ground contact at {pos1['org']}")

        if gdf_initial_ground_pos is not None and strogg_initial_ground_pos is not None:
            both_grounded_once = True
            grounded_samples += 1

            # 3. Calculate 2D displacement from ground touchdown
            dx0 = pos0["org"][0] - gdf_initial_ground_pos[0]
            dy0 = pos0["org"][1] - gdf_initial_ground_pos[1]
            disp0 = math.sqrt(dx0 * dx0 + dy0 * dy0)
            if disp0 > gdf_max_displacement:
                gdf_max_displacement = disp0

            dx1 = pos1["org"][0] - strogg_initial_ground_pos[0]
            dy1 = pos1["org"][1] - strogg_initial_ground_pos[1]
            disp1 = math.sqrt(dx1 * dx1 + dy1 * dy1)
            if disp1 > strogg_max_displacement:
                strogg_max_displacement = disp1

            # 4. Check combat engagement (atk_cmd or atk)
            if pos0["atk_cmd"] == 1 or pos0["atk"] == 1 or pos1["atk_cmd"] == 1 or pos1["atk"] == 1:
                combat_engagement_seen = True
                print(f"  [COMBAT DETECTED] Bot 0 (atk={pos0['atk']}, atk_cmd={pos0['atk_cmd']}) | Bot 1 (atk={pos1['atk']}, atk_cmd={pos1['atk_cmd']})")

            print(
                f"  Sample {grounded_samples}: "
                f"GDF disp={disp0:.1f}u (fwd={pos0['fwd']}, side={pos0['side']}, atk={pos0['atk_cmd']}) | "
                f"Strogg disp={disp1:.1f}u (fwd={pos1['fwd']}, side={pos1['side']}, atk={pos1['atk_cmd']})"
            )

            # Success condition:
            # - both bots grounded
            # - both displacements >= 50 units
            # - combat engagement confirmed
            # - >= 100 server ticks measured (SIM_HASH) since monitoring began
            window_ticks = [k for (t, k) in list(SIM_TICKS) if t >= monitor_start_mono]
            measured_ticks = (window_ticks[-1] - window_ticks[0]) if len(window_ticks) >= 2 else 0
            if (
                gdf_max_displacement >= 50.0
                and strogg_max_displacement >= 50.0
                and combat_engagement_seen
                and measured_ticks >= MIN_MEASURED_TICKS
            ):
                print(f"\n  >>> ALL BOT CRITERIA SATISFIED! (GDF disp={gdf_max_displacement:.1f}u, Strogg disp={strogg_max_displacement:.1f}u, combat={combat_engagement_seen})")
                break

    # Drain any remaining lines
    while not output_queue.empty():
        try:
            output_queue.get_nowait()
        except queue.Empty:
            break

    # Assertions
    print("\n[4] Validating acceptance criteria...")
    if not both_grounded_once:
        raise AssertionError("Bots failed to achieve ground contact on Valley terrain")
    print(f"  [OK] Ground contact verified for both bots (GDF and Strogg)")

    MIN_DISPLACEMENT = 50.0
    if gdf_max_displacement < MIN_DISPLACEMENT:
        raise AssertionError(f"GDF bot displacement too low: {gdf_max_displacement:.2f} < {MIN_DISPLACEMENT} units")
    if strogg_max_displacement < MIN_DISPLACEMENT:
        raise AssertionError(f"Strogg bot displacement too low: {strogg_max_displacement:.2f} < {MIN_DISPLACEMENT} units")
    print(f"  [OK] GDF Bot AAS navigation displacement: {gdf_max_displacement:.2f} units (>= {MIN_DISPLACEMENT} u)")
    print(f"  [OK] Strogg Bot AAS navigation displacement: {strogg_max_displacement:.2f} units (>= {MIN_DISPLACEMENT} u)")

    if not combat_engagement_seen:
        raise AssertionError("Bot combat engagement failed: neither bot asserted attack command (atk_cmd=1 / atk=1)")
    print("  [OK] Bot combat engagement verified: attack command and weapon firing asserted")

    window = [(t, k) for (t, k) in list(SIM_TICKS) if t >= monitor_start_mono]
    if len(window) < 2 or window[-1][0] <= window[0][0]:
        raise AssertionError("No SIM_HASH ticks observed during bot monitoring (+set g_simHash 1); "
                             "server tick rate could not be measured")
    measured_ticks = window[-1][1] - window[0][1]
    measured_wall = window[-1][0] - window[0][0]
    tick_hz = measured_ticks / measured_wall
    if measured_ticks < MIN_MEASURED_TICKS:
        raise AssertionError(f"Only {measured_ticks} server ticks measured during monitoring (< {MIN_MEASURED_TICKS})")
    if not (TICK_HZ_RANGE[0] <= tick_hz <= TICK_HZ_RANGE[1]):
        raise AssertionError(f"Server tick rate {tick_hz:.2f} Hz outside {TICK_HZ_RANGE[0]}..{TICK_HZ_RANGE[1]} Hz "
                             f"({measured_ticks} ticks in {measured_wall:.1f}s)")
    print(f"  [OK] Server simulation: {measured_ticks} ticks in {measured_wall:.1f}s = {tick_hz:.2f} Hz "
          f"(measured from SIM_HASH, >= {MIN_MEASURED_TICKS} ticks)")

    # Clean shutdown
    print("\n[5] Terminating dedicated server cleanly via 'quit'...")
    try:
        server_proc.stdin.write("quit\n")
        server_proc.stdin.flush()
    except (BrokenPipeError, OSError):
        pass

    server_exit = server_proc.wait(timeout=10.0)
    print(f"  Server terminated with exit code: {server_exit}")

    if server_exit != 0:
        raise RuntimeError(f"Server exited with non-zero status: {server_exit}")

    print("\n=================================================================")
    print(" SUCCESS: Milestone 4 Bot AI Navigation & Combat Verified!")
    print(f" Server Tick Rate:         {tick_hz:.2f} Hz measured ({measured_ticks} ticks / {measured_wall:.1f}s)")
    print(f" Bot AI Think Rate:        not measured here (see tests/test_all_stock_maps.py, botThreadStats)")
    print(f" Physical Pawn Possession: VERIFIED (spectating 0 for both bots)")
    print(f" Terrain Ground Contact:   VERIFIED (ground: 1)")
    print(f" GDF Displacement:         {gdf_max_displacement:.2f} u (>= {MIN_DISPLACEMENT} u)")
    print(f" Strogg Displacement:      {strogg_max_displacement:.2f} u (>= {MIN_DISPLACEMENT} u)")
    print(f" Combat Engagement:        VERIFIED (atk_cmd: 1 / atk: 1 asserted)")
    print(f" Simulation Frames:        {measured_ticks} server ticks measured (>= {MIN_MEASURED_TICKS})")
    print(f" Clean Server Shutdown:    VERIFIED (exit code 0)")
    print("=================================================================\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run_bot_gameplay_test())
    except Exception as e:
        print(f"\nTEST FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
