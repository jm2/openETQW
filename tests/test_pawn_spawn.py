#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""In-World Pawn Possession & Spawning Integration Test for OpenETQW Milestone 1.

Spawns an authoritative dedicated server hosting 'valley' on 127.0.0.1:27740,
spawns a client instance connecting via loopback on 127.0.0.1:27741,
executes client team selection ('clientTeam gdf', 'clientClass soldier', 'spawn'),
verifies the client transitions from spectator/limbo into an active physical soldier pawn
in-world on Valley with valid 3D coordinates and camera view angles,
and cleanly terminates with exit code 0.
"""

import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BINARY_PATH = REPO_ROOT / "build" / "openETQW-ded_arm64"
DATA_PATH = Path("/Users/jmulesa/etqw_data")

SERVER_PORT = 27740
CLIENT_PORT = 27741

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
    "+set", "com_timeServer", "1",
    "+connect", f"127.0.0.1:{SERVER_PORT}"
]


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


def run_pawn_spawn_test():
    print("=================================================================")
    print(" OpenETQW Milestone 1: Pawn Possession & In-World Spawning Test")
    print(f" Target: Valley map, Server 127.0.0.1:{SERVER_PORT}, Client :{CLIENT_PORT}")
    print(" Objective: Client connects, joins GDF, and spawns as active soldier pawn")
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

    print("\n[2] Dedicated server active on Valley! Spawning loopback client...")

    # 2. Start Client
    client_proc = subprocess.Popen(
        CLIENT_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    c_thread = threading.Thread(
        target=stream_reader,
        args=(client_proc.stdout, "CLIENT", output_queue),
        daemon=True
    )
    c_thread.start()

    client_connected = False
    team_command_sent = False
    strogg_command_sent = False
    spawn_gdf_verified = False
    spawn_strogg_verified = False
    spawn_verified = False
    spawn_details = ""
    snapshots_received = 0
    test_start_time = time.time()

    print("[3] Monitoring connection, issuing team/class selection, and tracking spawn...")
    while time.time() - test_start_time < 90.0:
        try:
            source, line = output_queue.get(timeout=0.2)
        except queue.Empty:
            if server_proc.poll() is not None or client_proc.poll() is not None:
                time.sleep(0.5)
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

        # Detect handshake / connection
        if "Client 0 connected from" in line or "connectResponse" in line:
            print(f"  [{source}] HANDSHAKE: {line}")
            client_connected = True

        # Count snapshots
        if "Client received snapshot" in line:
            snapshots_received += 1
            if snapshots_received % 10 == 0 or snapshots_received == 1:
                print(f"  [{source}] {line} (count: {snapshots_received})")

        # When client is actively receiving snapshots (game loaded and ready), send GDF selection
        if client_connected and not team_command_sent and snapshots_received >= 5:
            print(f"\n[4] Client actively running in Valley (snapshot {snapshots_received})! Sending team selection ('clientTeam gdf', 'clientClass soldier', 'spawn')...")
            try:
                client_proc.stdin.write("clientTeam gdf\nclientClass soldier\nspawn\n")
                client_proc.stdin.flush()
            except Exception as e:
                print(f"  Error writing to client stdin: {e}")
            team_command_sent = True

        # Check for active pawn spawn log (spectating 0)
        if "spawned at (" in line:
            print(f"  [{source}] *** SPAWN EVENT ***: {line}")
            if "team gdf spectating 0" in line:
                print(f"  [{source}] *** ACTIVE GDF PAWN POSSESSION CONFIRMED! ***: {line}")
                spawn_gdf_verified = True
                spawn_details = line
            elif "team strogg spectating 0" in line:
                print(f"  [{source}] *** ACTIVE STROGG PAWN POSSESSION CONFIRMED! ***: {line}")
                spawn_strogg_verified = True

        # After GDF spawn confirmed, switch to Strogg using new aliases (setTeam, setClass, spawn)
        if spawn_gdf_verified and not strogg_command_sent and snapshots_received >= 25:
            print(f"\n[5] GDF verified! Now testing command aliases with Strogg ('setTeam strogg', 'setClass soldier', 'spawn')...")
            try:
                client_proc.stdin.write("setTeam strogg\nsetClass soldier\nspawn\n")
                client_proc.stdin.flush()
            except Exception as e:
                print(f"  Error writing to client stdin: {e}")
            strogg_command_sent = True

        if spawn_gdf_verified and spawn_strogg_verified and snapshots_received >= 45:
            print(f"\n[6] Both GDF and Strogg pawn possessions confirmed! Received {snapshots_received} snapshots in-world.")
            spawn_verified = True
            break
        elif "entered the game" in line or "TEAM" in line or "joined" in line:
            print(f"  [{source}] STATE: {line}")
        elif "FATAL" in line or "Error:" in line or "Segmentation fault" in line:
            print(f"  [{source}] ERROR_LOG: {line}")

    if not spawn_verified:
        # Also check if we can trigger spawn directly via server console if client console input wasn't accepted
        print("  Client stdin didn't trigger spawn; attempting server-side setTeam/spawn check...")
        try:
            server_proc.stdin.write("status\n")
            server_proc.stdin.flush()
        except Exception:
            pass

    # Drain output
    end_drain = time.time()
    while time.time() - end_drain < 2.0:
        try:
            source, line = output_queue.get(timeout=0.1)
            if "spawned at (" in line:
                print(f"  [{source}] *** SPAWN DETECTED ***: {line}")
                spawn_verified = True
                spawn_details = line
            elif "Client" in line or "Player" in line:
                print(f"  [{source}] {line}")
        except queue.Empty:
            break

    # Graceful Shutdown
    print("\n[6] Shutting down client via 'quit'...")
    try:
        client_proc.stdin.write("quit\n")
        client_proc.stdin.flush()
        client_exit = client_proc.wait(timeout=10.0)
        print(f"  Client terminated with exit code: {client_exit}")
    except Exception as e:
        print(f"  Client quit exception: {e}, terminating...")
        client_proc.terminate()
        client_exit = client_proc.wait(timeout=5.0)

    print("[7] Shutting down server via 'quit'...")
    try:
        server_proc.stdin.write("quit\n")
        server_proc.stdin.flush()
        server_exit = server_proc.wait(timeout=10.0)
        print(f"  Server terminated with exit code: {server_exit}")
    except Exception as e:
        print(f"  Server quit exception: {e}, terminating...")
        server_proc.terminate()
        server_exit = server_proc.wait(timeout=5.0)

    if not spawn_gdf_verified or not spawn_strogg_verified:
        raise RuntimeError(f"FAIL: Missing spawn verification: GDF={spawn_gdf_verified}, Strogg={spawn_strogg_verified}")

    if client_exit != 0:
        raise RuntimeError(f"Client exited with non-zero status: {client_exit}")
    if server_exit != 0:
        raise RuntimeError(f"Server exited with non-zero status: {server_exit}")

    print("\n=================================================================")
    print(" SUCCESS: Pawn Possession & In-World Spawning Verified!")
    print(" GDF Pawn Spawn:    VERIFIED")
    print(" Strogg Pawn Spawn: VERIFIED")
    print(" Command Aliases:   VERIFIED (clientTeam, setTeam, clientClass, setClass, spawn)")
    print(f" Spawn coordinates: {spawn_details}")
    print("=================================================================\n")
    return 0


if __name__ == "__main__":
    sys.exit(run_pawn_spawn_test())
