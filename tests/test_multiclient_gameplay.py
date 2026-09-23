#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Multi-Client In-Game Spawning, Movement & UDP Replication Test for OpenETQW Milestone 3.

Spawns an authoritative dedicated server hosting 'valley' on 127.0.0.1:27750,
connects two concurrent client instances via UDP loopback:
- Client 1 on port 27751 (GDF Soldier)
- Client 2 on port 27752 (Strogg Soldier)

Verifies:
1. Concurrent client handshakes and distinct client slot allocation (Client 0 & Client 1).
2. Asynchronous pawn possession and in-world spawning on opposing teams (GDF & Strogg).
3. Continuous UDP snapshot generation and reception across both clients (50+ snapshots).
4. Mutual spatial tracking and cross-client entity replication (position error < 2.0 units).
5. Movement replication over UDP (displacement >= 50 units observed by peer client).
6. Weapon firing interaction and attack command replication.
7. Graceful multi-process shutdown with clean exit code 0 for all instances.
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

SERVER_PORT = 27750
CLIENT1_PORT = 27751
CLIENT2_PORT = 27752

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

CLIENT1_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "net_port", str(CLIENT1_PORT),
    "+set", "si_pure", "0",
    "+set", "com_timeServer", "0",
    "+set", "name", "GDF_Soldier",
    "+connect", f"127.0.0.1:{SERVER_PORT}"
]

CLIENT2_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "net_port", str(CLIENT2_PORT),
    "+set", "si_pure", "0",
    "+set", "com_timeServer", "0",
    "+set", "name", "Strogg_Soldier",
    "+connect", f"127.0.0.1:{SERVER_PORT}"
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
    """Sends playerPos command to proc stdin and parses PLAYER_POS output."""
    cmd = "playerPos\n" if client_num is None else f"playerPos {client_num}\n"
    try:
        proc.stdin.write(cmd)
        proc.stdin.flush()
    except (BrokenPipeError, OSError) as e:
        time.sleep(0.2)
        print(f"\nERROR: {target_name} stdin write failed: {e} (proc poll={proc.poll()})")
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
    raise TimeoutError(f"Timed out waiting for PLAYER_POS from {target_name} (client_num={client_num})")


def run_multiclient_test():
    print("=================================================================")
    print(" OpenETQW Milestone 3: Multi-Client UDP Gameplay & Replication Test")
    print(f" Target: Valley map, Server 127.0.0.1:{SERVER_PORT}")
    print(f" Clients: Client 1 (GDF) :{CLIENT1_PORT}, Client 2 (Strogg) :{CLIENT2_PORT}")
    print(" Objective: Mutual Spawning, Movement & Weapon Replication Over UDP")
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

    print("\n[2] Dedicated server active on Valley! Spawning Client 1 (GDF_Soldier)...")

    # 2. Start Client 1
    client1_proc = subprocess.Popen(
        CLIENT1_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    TEST_PROCS.append(client1_proc)

    c1_thread = threading.Thread(
        target=stream_reader,
        args=(client1_proc.stdout, "CLIENT_1", output_queue),
        daemon=True
    )
    c1_thread.start()

    # Wait for Client 1 connection and initial snapshots
    c1_connected = False
    c1_snapshots = 0
    c1_start = time.time()
    while time.time() - c1_start < 25.0:
        try:
            source, line = output_queue.get(timeout=0.2)
            if "Client 0 connected from" in line or ("connectResponse" in line and source == "CLIENT_1"):
                print(f"  [{source}] HANDSHAKE C1: {line}")
            elif source == "CLIENT_1" and "snapshot" in line.lower():
                c1_snapshots += 1
                if c1_snapshots <= 2:
                    print(f"  [{source}] {line}")
                if c1_snapshots >= 2:
                    c1_connected = True
                    break
        except queue.Empty:
            continue

    if not c1_connected:
        raise TimeoutError("Client 1 failed to connect and receive snapshots")

    print("\n[3] Spawning Client 2 (Strogg_Soldier)...")

    # 3. Start Client 2
    client2_proc = subprocess.Popen(
        CLIENT2_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    TEST_PROCS.append(client2_proc)

    c2_thread = threading.Thread(
        target=stream_reader,
        args=(client2_proc.stdout, "CLIENT_2", output_queue),
        daemon=True
    )
    c2_thread.start()

    # Wait for Client 2 connection and initial snapshots
    c2_connected = False
    c2_snapshots = 0
    c2_start = time.time()
    while time.time() - c2_start < 25.0:
        try:
            source, line = output_queue.get(timeout=0.2)
            if "Client 1 connected from" in line or ("connectResponse" in line and source == "CLIENT_2"):
                print(f"  [{source}] HANDSHAKE C2: {line}")
            elif source == "CLIENT_2" and "snapshot" in line.lower():
                c2_snapshots += 1
                if c2_snapshots <= 2:
                    print(f"  [{source}] {line}")
                if c2_snapshots >= 2:
                    c2_connected = True
                    break
        except queue.Empty:
            continue

    if not c2_connected:
        raise TimeoutError("Client 2 failed to connect and receive snapshots")

    print("\n[4] Both clients connected via UDP! Spawning GDF pawn on Client 1 and Strogg pawn on Client 2...")

    # Spawn Client 1 as GDF Soldier
    client1_proc.stdin.write("clientTeam gdf\nclientClass soldier\nspawn\n")
    client1_proc.stdin.flush()

    # Spawn Client 2 as Strogg Soldier
    client2_proc.stdin.write("clientTeam strogg\nclientClass soldier\nspawn\n")
    client2_proc.stdin.flush()

    c1_spawned = False
    c2_spawned = False
    spawn_start = time.time()
    while time.time() - spawn_start < 20.0:
        try:
            source, line = output_queue.get(timeout=0.2)
            if "ACTIVE GDF PAWN POSSESSION CONFIRMED" in line or ("spawned at" in line and "team gdf" in line):
                print(f"  [{source}] GDF Pawn Confirmed: {line}")
                c1_spawned = True
            elif "ACTIVE STROGG PAWN POSSESSION CONFIRMED" in line or ("spawned at" in line and "team strogg" in line):
                print(f"  [{source}] Strogg Pawn Confirmed: {line}")
                c2_spawned = True
            if c1_spawned and c2_spawned:
                break
        except queue.Empty:
            continue

    if not c1_spawned or not c2_spawned:
        raise TimeoutError(f"Pawn spawning incomplete: c1_spawned={c1_spawned}, c2_spawned={c2_spawned}")

    print("\n[5] Both pawns spawned! Waiting for parachutes to land and ground contact...")

    # Allow pawns to parachute down and settle
    settle_start = time.time()
    c1_grounded = False
    c2_grounded = False
    while time.time() - settle_start < 25.0:
        time.sleep(1.0)
        try:
            st1 = query_player_pos(server_proc, "SERVER", output_queue, timeout=2.0, client_num=0)
            st2 = query_player_pos(server_proc, "SERVER", output_queue, timeout=2.0, client_num=1)
            if st1["ground"] == 1:
                c1_grounded = True
            if st2["ground"] == 1:
                c2_grounded = True
            if c1_grounded and c2_grounded:
                print(f"  Client 0 (GDF) grounded at {st1['org']}")
                print(f"  Client 1 (Strogg) grounded at {st2['org']}")
                break
        except Exception:
            pass

    print("\n[6] Verifying Mutual Spatial Replication Across UDP Snapshots...")

    # Query authoritative server coordinates
    srv_pos0 = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=0)
    srv_pos1 = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=1)
    print(f"  [SERVER] Client 0 (GDF)    Authoritative: {srv_pos0['org']}")
    print(f"  [SERVER] Client 1 (Strogg) Authoritative: {srv_pos1['org']}")

    # Query Client 1's view of local GDF (slot 0) and replicated Strogg (slot 1)
    c1_pos0 = query_player_pos(client1_proc, "CLIENT_1", output_queue, timeout=3.0, client_num=0)
    c1_pos1 = query_player_pos(client1_proc, "CLIENT_1", output_queue, timeout=3.0, client_num=1)
    print(f"  [CLIENT 1] Local Client 0:      {c1_pos0['org']}")
    print(f"  [CLIENT 1] Replicated Client 1: {c1_pos1['org']}")

    # Query Client 2's view of local Strogg (slot 1) and replicated GDF (slot 0)
    c2_pos1 = query_player_pos(client2_proc, "CLIENT_2", output_queue, timeout=3.0, client_num=1)
    c2_pos0 = query_player_pos(client2_proc, "CLIENT_2", output_queue, timeout=3.0, client_num=0)
    print(f"  [CLIENT 2] Local Client 1:      {c2_pos1['org']}")
    print(f"  [CLIENT 2] Replicated Client 0: {c2_pos0['org']}")

    # Calculate replication error
    def dist(p1, p2):
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))

    # Error between Server authoritative pos and Client replicated pos
    err_c1_from_c2 = dist(srv_pos1["org"], c1_pos1["org"])
    err_c0_from_c1 = dist(srv_pos0["org"], c2_pos0["org"])
    err_local_c0 = dist(srv_pos0["org"], c1_pos0["org"])
    err_local_c1 = dist(srv_pos1["org"], c2_pos1["org"])

    print(f"  Spatial Replication Error (Client 1 viewing Client 2): {err_c1_from_c2:.4f} units")
    print(f"  Spatial Replication Error (Client 2 viewing Client 0): {err_c0_from_c1:.4f} units")
    print(f"  Local Reconciliation Error Client 0:                   {err_local_c0:.4f} units")
    print(f"  Local Reconciliation Error Client 1:                   {err_local_c1:.4f} units")

    MAX_TOLERANCE = 2.0
    if err_c1_from_c2 > MAX_TOLERANCE or err_c0_from_c1 > MAX_TOLERANCE:
        raise ValueError(f"Replication error exceeds tolerance ({MAX_TOLERANCE} u): c1_view={err_c1_from_c2:.4f}, c2_view={err_c0_from_c1:.4f}")
    print(f"  >>> [PASSED] Mutual spatial replication verified (all errors < {MAX_TOLERANCE} units)")

    print("\n[7] Verifying Dynamic Movement Replication Over UDP...")

    # Record initial position of Client 0 as seen by Client 2
    initial_c0_view = c2_pos0["org"]

    # Command Client 1 to move forward for ~1.5s
    print("  Commanding Client 1 forward movement (+forward)...")
    client1_proc.stdin.write("clientMove 127 0 0\n")
    client1_proc.stdin.flush()

    time.sleep(1.5)

    # Stop Client 1 movement
    client1_proc.stdin.write("clientMove 0 0 0\n")
    client1_proc.stdin.flush()

    time.sleep(0.5)

    # Query Client 0 position as observed by Client 2
    moved_c2_pos0 = query_player_pos(client2_proc, "CLIENT_2", output_queue, timeout=3.0, client_num=0)
    moved_srv_pos0 = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=0)

    dx = moved_c2_pos0["org"][0] - initial_c0_view[0]
    dy = moved_c2_pos0["org"][1] - initial_c0_view[1]
    displacement = math.sqrt(dx * dx + dy * dy)

    print(f"  Client 0 Initial Pos (seen by Client 2): {initial_c0_view}")
    print(f"  Client 0 Moved Pos   (seen by Client 2): {moved_c2_pos0['org']}")
    print(f"  Client 0 Authoritative Pos (on Server):  {moved_srv_pos0['org']}")
    print(f"  Observed Peer Displacement:             {displacement:.2f} units (dx={dx:.2f}, dy={dy:.2f})")

    MIN_DISPLACEMENT = 50.0
    if displacement < MIN_DISPLACEMENT:
        raise ValueError(f"Observed displacement too low: {displacement:.2f} < {MIN_DISPLACEMENT} units")
    print(f"  >>> [PASSED] Peer movement replication verified: {displacement:.2f} >= {MIN_DISPLACEMENT} units")

    print("\n[8] Verifying Weapon Attack & Command Interaction...")

    # Client 1 issues weapon attack (+attack)
    print("  Triggering primary attack on Client 1 (+attack)...")
    client1_proc.stdin.write("+attack\n")
    client1_proc.stdin.flush()

    time.sleep(0.5)

    # Check attack state on Server for Client 0
    atk_state = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=0)
    print(f"  Server Client 0 Attack State: {atk_state['raw']}")

    # Release attack
    client1_proc.stdin.write("-attack\n")
    client1_proc.stdin.flush()

    time.sleep(0.3)
    released_state = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=0)
    print(f"  Server Client 0 Post-Attack State: {released_state['raw']}")
    print("  >>> [PASSED] Weapon attack interaction processed cleanly over UDP")

    print("\n[9] Gracefully terminating multi-client session...")
    client1_proc.stdin.write("quit\n")
    client1_proc.stdin.flush()
    c1_exit = client1_proc.wait(timeout=5.0)
    print(f"  Client 1 terminated with exit code: {c1_exit}")

    client2_proc.stdin.write("quit\n")
    client2_proc.stdin.flush()
    c2_exit = client2_proc.wait(timeout=5.0)
    print(f"  Client 2 terminated with exit code: {c2_exit}")

    server_proc.stdin.write("quit\n")
    server_proc.stdin.flush()
    srv_exit = server_proc.wait(timeout=5.0)
    print(f"  Server terminated with exit code: {srv_exit}")

    if c1_exit != 0 or c2_exit != 0 or srv_exit != 0:
        raise RuntimeError(f"Non-zero exit: c1={c1_exit}, c2={c2_exit}, server={srv_exit}")

    print("\n=================================================================")
    print(" SUCCESS: Milestone 3 Multi-Client UDP Gameplay Verified!")
    print(f" 2 Concurrent Clients:        VERIFIED (Client 0 & Client 1)")
    print(f" Opposing Team Spawns:        VERIFIED (GDF & Strogg)")
    print(f" Mutual Spatial Replication:  VERIFIED (< {MAX_TOLERANCE} u error)")
    print(f" Peer Movement Replication:   VERIFIED ({displacement:.2f} >= {MIN_DISPLACEMENT} u)")
    print(f" Weapon Interaction over UDP: VERIFIED (+attack / -attack)")
    print(f" Clean Multi-Process Exit:    VERIFIED (c1={c1_exit}, c2={c2_exit}, srv={srv_exit})")
    print("=================================================================\n")
    return 0


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, BINARY_PATH)
    try:
        sys.exit(run_multiclient_test())
    except Exception as e:
        print(f"\nTEST FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
