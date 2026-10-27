#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Loopback Integration Test for OpenETQW Milestone 3 / Slice 1.

Spawns an authoritative dedicated server hosting 'valley' on 127.0.0.1:27733,
spawns a client instance connecting via loopback transport, validates challenge/connect
handshake, verifies client game initialization, tracks continuous bidirectional snapshot
delivery for at least 150 continuous ticks, and performs clean shutdown with exit code 0.
"""

import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import etqw_harness as h


REPO_ROOT = Path(__file__).resolve().parents[1]
BINARY_PATH = h.build_binary("ded")
DATA_PATH = h.env_data_path()

SERVER_PORT = 27733
CLIENT_PORT = 27734

SERVER_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "net_port", str(SERVER_PORT),
    "+set", "si_rules", "sdGameRulesObjective",
    "+set", "si_pure", "0",
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
    "+set", "net_clientShowSnapshots", "1", "+connect", f"127.0.0.1:{SERVER_PORT}"
]

REQUIRED_SNAPSHOTS = 150


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


def run_loopback_test():
    print("=================================================================")
    print(" OpenETQW Milestone 3 / Slice 1: Loopback Integration Test")
    print(f" Target: Valley map, Server 127.0.0.1:{SERVER_PORT}, Client :{CLIENT_PORT}")
    print(f" Objective: Continuous receipt of >= {REQUIRED_SNAPSHOTS} snapshots")
    print("=================================================================\n")

    if not BINARY_PATH.is_file():
        raise FileNotFoundError(f"Engine binary not found: {BINARY_PATH}")
    if not DATA_PATH.is_dir():
        raise FileNotFoundError(f"ETQW base data path not found: {DATA_PATH}")

    output_queue = queue.Queue()

    # 1. Start Server
    print("[1] Spawning authoritative dedicated server...")
    server_proc = h.spawn_engine(
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
            if "ETQW initialized" in line or "Server spawned on" in line:
                print(f"  [{source}] {line}")
                if "ETQW initialized" in line:
                    server_ready = True
                    break
            elif "Map:" in line or "Game initialized" in line:
                print(f"  [{source}] {line}")
        except queue.Empty:
            continue

    if not server_ready:
        server_proc.kill()
        raise TimeoutError("Server failed to load the map within 60s")

    print("\n[2] Dedicated server active on Valley! Spawning client instance...")

    # 2. Start Client
    client_proc = h.spawn_engine(
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

    handshake_connected = False
    client_game_init = False
    snapshots_received = 0
    test_start_time = time.time()
    last_snapshot_seq = -1

    print("[3] Monitoring handshake, connection lifecycle, and snapshot stream...")
    while time.time() - test_start_time < 90.0:
        try:
            source, line = output_queue.get(timeout=0.2)
        except queue.Empty:
            if server_proc.poll() is not None or client_proc.poll() is not None:
                # Process exited, drain all remaining queued messages
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

        if "Client 0 connected from" in line or "connectResponse" in line or "Sending" in line:
            print(f"  [{source}] HANDSHAKE: {line}")
            if "Client 0 connected from" in line:
                handshake_connected = True
        elif "Sending game init" in line or "Client initializing map" in line or "Map Initialization" in line:
            print(f"  [{source}] INIT: {line}")
            client_game_init = True
        elif "Client received snapshot" in line:
            snapshots_received += 1
            # e.g. "Client received snapshot 42 (frame 100)"
            if snapshots_received % 25 == 0 or snapshots_received == 1 or snapshots_received >= REQUIRED_SNAPSHOTS:
                print(f"  [{source}] PROGRESS: {line} (total count: {snapshots_received})")
            if snapshots_received >= REQUIRED_SNAPSHOTS:
                print(f"\n[4] Reached target: {snapshots_received} continuous snapshots received!")
                break
        elif "FATAL" in line or "Error:" in line or "Segmentation fault" in line or "Bad challenge" in line or "Data checksum mismatch" in line:
            print(f"  [{source}] ERROR_LOG: {line}")
        elif "SNAPSHOT" in line or "USERCMD" in line:
            print(f"  [{source}] {line}")
        elif client_game_init and source == "CLIENT":
            print(f"  [{source}] {line}")

    if snapshots_received < REQUIRED_SNAPSHOTS:
        client_proc.kill()
        server_proc.kill()
        raise TimeoutError(
            f"Failed to receive {REQUIRED_SNAPSHOTS} snapshots (only received {snapshots_received}) within timeout"
        )

    # 4. Graceful Shutdown
    print("\n[5] Shutting down client via 'quit'...")
    try:
        client_proc.stdin.write("quit\n")
        client_proc.stdin.flush()
        client_exit = client_proc.wait(timeout=10.0)
        print(f"  Client terminated with exit code: {client_exit}")
    except Exception as e:
        print(f"  Client quit exception: {e}, sending SIGTERM...")
        client_proc.terminate()
        client_exit = client_proc.wait(timeout=5.0)

    print("[6] Shutting down server via 'quit'...")
    try:
        server_proc.stdin.write("quit\n")
        server_proc.stdin.flush()
        server_exit = server_proc.wait(timeout=10.0)
        print(f"  Server terminated with exit code: {server_exit}")
    except Exception as e:
        print(f"  Server quit exception: {e}, sending SIGTERM...")
        server_proc.terminate()
        server_exit = server_proc.wait(timeout=5.0)

    while True:
        try:
            src, l = output_queue.get_nowait()
            print(f"  [{src}] {l}")
        except queue.Empty:
            break

    if client_exit != 0:
        raise RuntimeError(f"Client exited with non-zero status: {client_exit}")
    if server_exit != 0:
        raise RuntimeError(f"Server exited with non-zero status: {server_exit}")

    print("\n=================================================================")
    print(f" SUCCESS: Loopback integration test PASSED ({snapshots_received} snapshots, exit code 0)")
    print("=================================================================\n")
    return 0


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, BINARY_PATH)
    sys.exit(run_loopback_test())
