#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Client Movement Physics, Input & Prediction Replay Integration Test for OpenETQW Milestone 2.

Spawns an authoritative dedicated server hosting 'valley' on 127.0.0.1:27746,
spawns a client instance connecting via loopback on 127.0.0.1:27747 with com_timeServer 0,
selects GDF team and class, spawns into an active physical soldier pawn,
executes and verifies:
1. Forward movement (delta >= 100 units)
2. Strafe movement (delta >= 50 units)
3. Jump impulse (apex / upward velocity and landing)
4. Crouch stance (ducked status and uncrouch)
5. Continuous snapshot reconciliation (50+ snapshots with low prediction error)
and cleanly terminates server and client with exit code 0.
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

import etqw_harness as h

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
BINARY_PATH = h.build_binary("ded")
DATA_PATH = h.env_data_path()

SERVER_PORT = 27746
CLIENT_PORT = 27747

SERVER_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "net_port", str(SERVER_PORT),
    "+set", "si_rules", "sdGameRulesObjective",
    "+set", "si_pure", "0",
    "+set", "g_teamSwitchDelay", "0",
    "+spawnServer", "valley"
]

CLIENT_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "net_port", str(CLIENT_PORT),
    "+set", "si_pure", "0",
    "+set", "com_timeServer", "0",
    "+set", "net_clientShowSnapshots", "1", "+connect", f"127.0.0.1:{SERVER_PORT}"
]

PLAYER_POS_PATTERN = re.compile(
    r"PLAYER_POS:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"vel:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"ground:\s*(\d+)\s*ducked:\s*(\d+)\s*prone:\s*(\d+)\s*jumped:\s*(\d+)"
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


def query_player_pos(proc, target_name, output_queue, timeout=5.0, client_num=None):
    """Sends playerPos command to proc stdin and waits for PLAYER_POS output."""
    cmd = "playerPos\n" if client_num is None else f"playerPos {client_num}\n"
    try:
        proc.stdin.write(cmd)
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        time.sleep(0.2)
        print(f"\nERROR: {target_name} stdin write failed: {e} (proc poll={proc.poll()})")
        while not output_queue.empty():
            try:
                src, l = output_queue.get_nowait()
                print(f"  [{src}] {l}")
            except queue.Empty:
                break
        raise

    start = time.time()
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            if source == target_name and "PLAYER_POS:" in line:
                match = PLAYER_POS_PATTERN.search(line)
                if match:
                    x = float(match.group(1))
                    y = float(match.group(2))
                    z = float(match.group(3))
                    vx = float(match.group(4))
                    vy = float(match.group(5))
                    vz = float(match.group(6))
                    ground = int(match.group(7))
                    ducked = int(match.group(8))
                    prone = int(match.group(9))
                    jumped = int(match.group(10))
                    return {
                        "org": (x, y, z),
                        "vel": (vx, vy, vz),
                        "ground": ground,
                        "ducked": ducked,
                        "prone": prone,
                        "jumped": jumped,
                        "raw": line
                    }
        except queue.Empty:
            continue
    raise TimeoutError(f"Timed out waiting for PLAYER_POS from {target_name}")


def run_movement_test():
    print("=================================================================")
    print(" OpenETQW Milestone 2: Client Movement & Prediction Replay Test")
    print(f" Target: Valley map, Server 127.0.0.1:{SERVER_PORT}, Client :{CLIENT_PORT}")
    print(" Objective: Forward, Strafe, Jump, Crouch, and Snapshot Reconciliation")
    print("=================================================================\n")

    if not BINARY_PATH.is_file():
        raise FileNotFoundError(f"Engine binary not found: {BINARY_PATH}")
    if not DATA_PATH.is_dir():
        raise FileNotFoundError(f"ETQW base data path not found: {DATA_PATH}")

    output_queue = queue.Queue()

    # 1. Start Server
    print("[1] Spawning authoritative dedicated server on Valley...")
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
    while time.time() - server_start_time < 60.0:
        if server_proc.poll() is not None:
            raise RuntimeError(f"Server exited prematurely with code {server_proc.returncode}")
        try:
            source, line = output_queue.get(timeout=0.2)
            if "StartFirstObjective" in line or "Server spawned on" in line:
                print(f"  [{source}] {line}")
                if "StartFirstObjective" in line:
                    server_ready = True
                    break
            elif "Map:" in line or "Game initialized" in line:
                print(f"  [{source}] {line}")
        except queue.Empty:
            continue

    if not server_ready:
        server_proc.kill()
        raise TimeoutError("Server failed to load Valley within 60s")

    print("\n[2] Dedicated server active on Valley! Spawning loopback client (com_timeServer 0)...")

    # 2. Start Client
    client_proc = subprocess.Popen(
        CLIENT_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    TEST_PROCS.append(client_proc)

    c_thread = threading.Thread(
        target=stream_reader,
        args=(client_proc.stdout, "CLIENT", output_queue),
        daemon=True
    )
    c_thread.start()

    client_connected = False
    team_command_sent = False
    spawn_gdf_verified = False
    snapshots_received = 0
    test_start_time = time.time()

    print("[3] Monitoring connection and initiating GDF pawn spawn...")
    while time.time() - test_start_time < 90.0:
        try:
            source, line = output_queue.get(timeout=0.2)
        except queue.Empty:
            if server_proc.poll() is not None or client_proc.poll() is not None:
                while True:
                    try:
                        src, l = output_queue.get_nowait()
                        print(f"  [{src}] {l}")
                    except queue.Empty:
                        break
                if server_proc.poll() is not None:
                    raise RuntimeError(f"Server exited unexpectedly with code {server_proc.returncode}")
                if client_proc.poll() is not None:
                    raise RuntimeError(f"Client exited unexpectedly with code {client_proc.returncode}")
            continue

        if "Client 0 connected from" in line or "connectResponse" in line:
            print(f"  [{source}] HANDSHAKE: {line}")
            client_connected = True

        if "Client received snapshot" in line:
            snapshots_received += 1
            if snapshots_received % 10 == 0 or snapshots_received == 1:
                print(f"  [{source}] {line} (count: {snapshots_received})")

        if client_connected and not team_command_sent and snapshots_received >= 5:
            print(f"\n[4] Client in Valley (snapshot {snapshots_received})! Sending spawn commands ('clientTeam gdf', 'clientClass soldier', 'spawn')...")
            client_proc.stdin.write("clientTeam gdf\nclientClass soldier\nspawn\n")
            client_proc.stdin.flush()
            team_command_sent = True

        if "spawned at (" in line and "team gdf spectating 0" in line:
            print(f"  [{source}] *** ACTIVE GDF PAWN POSSESSION CONFIRMED! ***: {line}")
            spawn_gdf_verified = True
            break

    if not spawn_gdf_verified:
        server_proc.kill()
        client_proc.kill()
        raise RuntimeError("Failed to verify GDF pawn spawn")

    # Helper to drain output queue and count snapshots
    def drain_queue(duration_sec=0.1):
        nonlocal snapshots_received
        end = time.time() + duration_sec
        while time.time() < end:
            try:
                src, l = output_queue.get(timeout=0.05)
                if "Client received snapshot" in l:
                    snapshots_received += 1
                if "DBG_" in l:
                    print(f"  [{src}] {l}")
            except queue.Empty:
                pass

    # Let player settle on ground (wait until ground == 1)
    print("\n[5] Pawn spawned! Settling on ground before movement tests...")
    pos0 = None
    ground_settled = False
    for _ in range(30):
        drain_queue(0.3)
        pos0 = query_player_pos(client_proc, "CLIENT", output_queue)
        print(f"  Raw: {pos0['raw']}")
        if pos0['ground'] == 1:
            ground_settled = True
            break

    if not ground_settled:
        raise RuntimeError(f"Player failed to touch ground: final pos={pos0}")
    print(f"  Ground contact confirmed at {pos0['org']}!")

    # =========================================================================
    # Test 1: Forward Movement
    # =========================================================================
    print("\n[6] Testing Forward Movement (+forward)...")
    client_proc.stdin.write("+forward\n")
    client_proc.stdin.flush()

    # Run forward for 1.8 seconds (approx 54 frames)
    drain_queue(1.8)

    client_proc.stdin.write("-forward\n")
    client_proc.stdin.flush()
    drain_queue(0.5)

    pos1 = query_player_pos(client_proc, "CLIENT", output_queue)
    dx_fwd = pos1['org'][0] - pos0['org'][0]
    dy_fwd = pos1['org'][1] - pos0['org'][1]
    dist_fwd = math.sqrt(dx_fwd * dx_fwd + dy_fwd * dy_fwd)
    print(f"  Raw after fwd: {pos1['raw']}")
    print(f"  Position after forward move: {pos1['org']}, Vel: {pos1['vel']}")
    print(f"  Forward displacement: {dist_fwd:.2f} units (dx={dx_fwd:.2f}, dy={dy_fwd:.2f})")

    if dist_fwd < 100.0:
        raise AssertionError(f"Forward movement failed: displacement {dist_fwd:.2f} < 100.0 units")
    print(f"  >>> [PASSED] Forward movement displacement: {dist_fwd:.2f} >= 100 units")

    # =========================================================================
    # Test 2: Strafe Movement
    # =========================================================================
    print("\n[7] Testing Strafe Movement (+moveright)...")
    client_proc.stdin.write("+moveright\n")
    client_proc.stdin.flush()

    # Strafe for 1.2 seconds (approx 36 frames)
    drain_queue(1.2)

    client_proc.stdin.write("-moveright\n")
    client_proc.stdin.flush()
    drain_queue(0.5)

    pos2 = query_player_pos(client_proc, "CLIENT", output_queue)
    dx_strafe = pos2['org'][0] - pos1['org'][0]
    dy_strafe = pos2['org'][1] - pos1['org'][1]
    dist_strafe = math.sqrt(dx_strafe * dx_strafe + dy_strafe * dy_strafe)
    print(f"  Position after strafe move: {pos2['org']}, Vel: {pos2['vel']}")
    print(f"  Strafe displacement: {dist_strafe:.2f} units (dx={dx_strafe:.2f}, dy={dy_strafe:.2f})")

    if dist_strafe < 50.0:
        raise AssertionError(f"Strafe movement failed: displacement {dist_strafe:.2f} < 50.0 units")
    print(f"  >>> [PASSED] Strafe movement displacement: {dist_strafe:.2f} >= 50 units")

    # =========================================================================
    # Test 3: Jump Movement
    # =========================================================================
    print("\n[8] Testing Jump (+jump)...")
    z_before_jump = pos2['org'][2]

    client_proc.stdin.write("+jump\n")
    client_proc.stdin.flush()

    # Query mid-jump (around 150-250ms in, velocity should be positive or z elevated)
    drain_queue(0.18)
    pos_jump_air = query_player_pos(client_proc, "CLIENT", output_queue)
    print(f"  Mid-jump state: {pos_jump_air['org']}, Vel: {pos_jump_air['vel']}, Ground: {pos_jump_air['ground']}, Jumped: {pos_jump_air['jumped']}")

    client_proc.stdin.write("-jump\n")
    client_proc.stdin.flush()

    # Wait for player to reach apex and land (0.8s)
    drain_queue(0.8)
    pos_landed = query_player_pos(client_proc, "CLIENT", output_queue)
    print(f"  Landed state: {pos_landed['org']}, Vel: {pos_landed['vel']}, Ground: {pos_landed['ground']}")

    z_elevated = (pos_jump_air['org'][2] > z_before_jump + 8.0) or (pos_jump_air['vel'][2] > 50.0) or (pos_jump_air['jumped'] == 1)
    if not z_elevated:
        raise AssertionError(f"Jump impulse failed: z did not elevate (z0={z_before_jump:.2f}, z_air={pos_jump_air['org'][2]:.2f}, vz={pos_jump_air['vel'][2]:.2f})")
    if pos_landed['ground'] != 1:
        # Give a small grace period for landing
        drain_queue(0.5)
        pos_landed = query_player_pos(client_proc, "CLIENT", output_queue)
        if pos_landed['ground'] != 1:
            raise AssertionError(f"Jump landing failed: ground={pos_landed['ground']} (expected 1)")

    print(f"  >>> [PASSED] Jump impulse and landing verified (z_apex delta = {pos_jump_air['org'][2] - z_before_jump:.2f} u, landed ground=1)")

    # =========================================================================
    # Test 4: Crouch Movement
    # =========================================================================
    print("\n[9] Testing Crouch (+crouch / -crouch)...")
    client_proc.stdin.write("+crouch\n")
    client_proc.stdin.flush()
    drain_queue(0.3)

    pos_crouched = query_player_pos(client_proc, "CLIENT", output_queue)
    print(f"  Crouched state: {pos_crouched['org']}, Ducked: {pos_crouched['ducked']}")
    if pos_crouched['ducked'] != 1:
        raise AssertionError(f"Crouch stance failed: ducked={pos_crouched['ducked']} (expected 1)")

    client_proc.stdin.write("-crouch\n")
    client_proc.stdin.flush()
    drain_queue(0.3)

    pos_uncrouched = query_player_pos(client_proc, "CLIENT", output_queue)
    print(f"  Uncrouched state: {pos_uncrouched['org']}, Ducked: {pos_uncrouched['ducked']}")
    if pos_uncrouched['ducked'] != 0:
        raise AssertionError(f"Uncrouch stance failed: ducked={pos_uncrouched['ducked']} (expected 0)")

    print(f"  >>> [PASSED] Crouch and uncrouch verified (ducked transitions: 1 -> 0)")

    # =========================================================================
    # Test 5: Snapshot Reconciliation & Prediction Accuracy
    # =========================================================================
    print("\n[10] Verifying Snapshot Reconciliation across 50+ frames...")
    # Settle player completely
    drain_queue(0.5)

    pos_client = query_player_pos(client_proc, "CLIENT", output_queue)
    pos_server = query_player_pos(server_proc, "SERVER", output_queue, client_num=0)

    print(f"  Client Position: {pos_client['org']}")
    print(f"  Server Position: {pos_server['org']}")

    err_x = pos_client['org'][0] - pos_server['org'][0]
    err_y = pos_client['org'][1] - pos_server['org'][1]
    err_z = pos_client['org'][2] - pos_server['org'][2]
    recon_error = math.sqrt(err_x * err_x + err_y * err_y + err_z * err_z)

    print(f"  Reconciliation prediction error: {recon_error:.4f} units")
    print(f"  Total snapshots received by client: {snapshots_received}")

    if snapshots_received < 50:
        raise AssertionError(f"Snapshot reconciliation test failed: snapshots {snapshots_received} < 50")
    if recon_error >= 2.0:
        raise AssertionError(f"Snapshot reconciliation failed: prediction error {recon_error:.4f} >= 2.0 units")

    print(f"  >>> [PASSED] Snapshot reconciliation: {snapshots_received} snapshots, error = {recon_error:.4f} units (< 2.0 units)")

    # =========================================================================
    # Graceful Shutdown
    # =========================================================================
    print("\n[11] Shutting down client via 'quit'...")
    client_proc.stdin.write("quit\n")
    client_proc.stdin.flush()
    client_exit = client_proc.wait(timeout=10.0)
    print(f"  Client terminated with exit code: {client_exit}")

    print("[12] Shutting down server via 'quit'...")
    server_proc.stdin.write("quit\n")
    server_proc.stdin.flush()
    server_exit = server_proc.wait(timeout=10.0)
    print(f"  Server terminated with exit code: {server_exit}")

    if client_exit != 0:
        raise RuntimeError(f"Client exited with non-zero status: {client_exit}")
    if server_exit != 0:
        raise RuntimeError(f"Server exited with non-zero status: {server_exit}")

    print("\n=================================================================")
    print(" SUCCESS: Milestone 2 Movement Physics & Prediction Replay Verified!")
    print(f" Forward Displacement: {dist_fwd:.2f} u (>= 100 u)")
    print(f" Strafe Displacement:  {dist_strafe:.2f} u (>= 50 u)")
    print(f" Jump Apex / Landing:  VERIFIED")
    print(f" Crouch / Uncrouch:    VERIFIED")
    print(f" Snapshot Count:       {snapshots_received} (>= 50)")
    print(f" Prediction Error:     {recon_error:.4f} u (< 2.0 u)")
    print("=================================================================\n")
    return 0


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, BINARY_PATH)
    sys.exit(run_movement_test())
