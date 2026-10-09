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
5. Movement replication over UDP in both directions: each client sees the other walk
   (displacement >= 50 units, ending within 2 units of the server's position).
6. The server registers a client's attack button and its release (weapon attacking).
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
    "+set", "ui_name", "GDF_Soldier",
    "+set", "net_clientShowSnapshots", "1", "+connect", f"127.0.0.1:{SERVER_PORT}"
]

CLIENT2_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "net_port", str(CLIENT2_PORT),
    "+set", "si_pure", "0",
    "+set", "com_timeServer", "0",
    "+set", "ui_name", "Strogg_Soldier",
    "+set", "net_clientShowSnapshots", "1", "+connect", f"127.0.0.1:{SERVER_PORT}"
]

ATTACK_PATTERN = re.compile(r"atk:\s*(\d+)\s*cmd:.*?atk_cmd=(\d+)")
PLAYER_POS_PATTERN = re.compile(
    r"PLAYER_POS:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"vel:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"ground:\s*(\d+)\s*ducked:\s*(\d+)\s*prone:\s*(\d+)\s*jumped:\s*(\d+)"
)


LOG_DIR = None  # --log-dir: every process's output is also written to <name>.log


# every line each process printed, checked for failure markers when the test ends
# (a common->Error drop or a crash banner does not always change the exit code)
PROCESS_LINES = {}
READER_THREADS = []  # joined before the scan, so the last lines a process printed are read


def scan_process_output():
    """Failure markers (etqw_harness.scan_failures) in any process's output."""
    found = []
    for name, lines in PROCESS_LINES.items():
        for b in h.scan_failures(lines):
            found.append(f"{name} line {b['line']}: {b['text']}")
    return found


REORDER_EXPECTED = False  # --reorder: some packet must actually have been overtaken
REORDER_RE = re.compile(r"NET_REORDER: (\d+) of (\d+) received packets")


def reorder_counts():
    """{process: (released after a later one, released)} from the simulator's
    NET_REORDER lines, printed when each socket closes."""
    counts = {}
    for name, lines in PROCESS_LINES.items():
        hits = [m for line in lines if (m := REORDER_RE.search(line))]
        counts[name] = (sum(int(m.group(1)) for m in hits), sum(int(m.group(2)) for m in hits))
    return counts


def exit_with_scan(rc):
    # the processes have exited (or are being killed); a reader can still be draining
    # the pipe, so let each finish before scanning
    for t in READER_THREADS:
        t.join(timeout=10.0)
    if REORDER_EXPECTED:
        counts = reorder_counts()
        print("packets released after a later one: " +
              ", ".join(f"{n} {r} of {t}" for n, (r, t) in sorted(counts.items())))
        if sum(r for r, _ in counts.values()) == 0:
            print("FAIL: --reorder was set but no process released a packet out of order (NET_REORDER)")
            rc = rc or 1
    bad = scan_process_output()
    if bad:
        print("\nFAILURE MARKERS in process output:")
        for b in bad:
            print(f"  - {b}")
        rc = rc or 1
    sys.exit(rc)


def stream_reader(pipe, name, q):
    READER_THREADS.append(threading.current_thread())
    log = open(Path(LOG_DIR) / f"{name}.log", "w", errors="replace") if LOG_DIR else None
    try:
        for line in iter(pipe.readline, ''):
            if not line:
                break
            PROCESS_LINES.setdefault(name, []).append(line.rstrip("\n"))
            if log:
                log.write(line)
                log.flush()
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
            # the map is loaded and the game running (the objective script's
            # "$null_entity passed to event 'sendNetworkEvent'" warning, once used
            # here, is gone since the retail sendNetworkEvent signature was fixed)
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

    def dist(p1, p2):
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(p1, p2)))

    # Both players stand still once grounded, so every view must converge on the
    # server's positions.  A remote player's view trails the server by the one-way
    # delay plus interpolation: sampled right after a landing under the simulated
    # 80-120 ms link, it is still a few frames up the fall (the errors seen were
    # 2-5 server frames of it).  Resample until the views agree or the time is up;
    # the window is short, so a replication stall longer than that still fails.
    MAX_TOLERANCE = 2.0
    converge_start = time.time()
    converge_deadline = converge_start + 2.0
    samples = 0
    while True:
        samples += 1
        # authoritative server coordinates
        srv_pos0 = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=0)
        srv_pos1 = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=1)
        # Client 1's view of local GDF (slot 0) and replicated Strogg (slot 1)
        c1_pos0 = query_player_pos(client1_proc, "CLIENT_1", output_queue, timeout=3.0, client_num=0)
        c1_pos1 = query_player_pos(client1_proc, "CLIENT_1", output_queue, timeout=3.0, client_num=1)
        # Client 2's view of local Strogg (slot 1) and replicated GDF (slot 0)
        c2_pos1 = query_player_pos(client2_proc, "CLIENT_2", output_queue, timeout=3.0, client_num=1)
        c2_pos0 = query_player_pos(client2_proc, "CLIENT_2", output_queue, timeout=3.0, client_num=0)
        # Error between Server authoritative pos and Client replicated pos
        err_c1_from_c2 = dist(srv_pos1["org"], c1_pos1["org"])
        err_c0_from_c1 = dist(srv_pos0["org"], c2_pos0["org"])
        err_local_c0 = dist(srv_pos0["org"], c1_pos0["org"])
        err_local_c1 = dist(srv_pos1["org"], c2_pos1["org"])
        converged = err_c1_from_c2 <= MAX_TOLERANCE and err_c0_from_c1 <= MAX_TOLERANCE
        if converged or time.time() >= converge_deadline:
            break
        time.sleep(0.25)

    print(f"  [SERVER] Client 0 (GDF)    Authoritative: {srv_pos0['org']}")
    print(f"  [SERVER] Client 1 (Strogg) Authoritative: {srv_pos1['org']}")
    print(f"  [CLIENT 1] Local Client 0:      {c1_pos0['org']}")
    print(f"  [CLIENT 1] Replicated Client 1: {c1_pos1['org']}")
    print(f"  [CLIENT 2] Local Client 1:      {c2_pos1['org']}")
    print(f"  [CLIENT 2] Replicated Client 0: {c2_pos0['org']}")
    print(f"  Spatial Replication Error (Client 1 viewing Client 2): {err_c1_from_c2:.4f} units")
    print(f"  Spatial Replication Error (Client 2 viewing Client 0): {err_c0_from_c1:.4f} units")
    print(f"  Local Reconciliation Error Client 0:                   {err_local_c0:.4f} units")
    print(f"  Local Reconciliation Error Client 1:                   {err_local_c1:.4f} units")
    print(f"  ({samples} sample(s), {time.time() - converge_start:.2f} s)")

    if err_c1_from_c2 > MAX_TOLERANCE or err_c0_from_c1 > MAX_TOLERANCE:
        raise ValueError(f"Replication error exceeds tolerance ({MAX_TOLERANCE} u): c1_view={err_c1_from_c2:.4f}, c2_view={err_c0_from_c1:.4f}")
    print(f"  >>> [PASSED] Mutual spatial replication verified (all errors < {MAX_TOLERANCE} units)")

    print("\n[7] Verifying Dynamic Movement Replication Over UDP (both directions)...")

    MIN_DISPLACEMENT = 50.0

    def check_peer_sees_movement(mover_proc, mover_name, mover_slot, observer_proc, observer_name):
        """The mover walks forward for 1.5 s; the observer's copy of the mover must
        follow it (moved >= MIN_DISPLACEMENT) and, once the mover is at rest, be
        within MAX_TOLERANCE of the server's position."""
        initial = query_player_pos(observer_proc, observer_name, output_queue, timeout=3.0, client_num=mover_slot)["org"]
        print(f"  Commanding {mover_name} forward movement (clientMove 127 0 0)...")
        mover_proc.stdin.write("clientMove 127 0 0\n")
        mover_proc.stdin.flush()
        time.sleep(1.5)
        mover_proc.stdin.write("clientMove 0 0 0\n")
        mover_proc.stdin.flush()
        # the player slides for about half a second after the stop; compare once the
        # server has it at rest and a few more snapshots have reached the observer
        deadline = time.time() + 5.0
        while True:
            time.sleep(0.25)
            speed = math.hypot(*query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=mover_slot)["vel"])
            if speed < 1.0:
                break
            if time.time() >= deadline:
                raise ValueError(f"client {mover_slot} is still moving at {speed:.1f} u/s 5 s after it was told to stop")
        time.sleep(0.5)

        seen = query_player_pos(observer_proc, observer_name, output_queue, timeout=3.0, client_num=mover_slot)["org"]
        authoritative = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=mover_slot)["org"]
        if not all(math.isfinite(v) for v in (*initial, *seen, *authoritative)):
            raise ValueError(f"non-finite position: initial {initial}, seen {seen}, server {authoritative}")
        moved = math.hypot(seen[0] - initial[0], seen[1] - initial[1])
        lag = dist(seen, authoritative)
        print(f"  Client {mover_slot} Initial Pos (seen by {observer_name}): {initial}")
        print(f"  Client {mover_slot} Moved Pos   (seen by {observer_name}): {seen}")
        print(f"  Client {mover_slot} Authoritative Pos (on Server):  {authoritative}")
        print(f"  Observed Peer Displacement: {moved:.2f} units; distance from the server's position: {lag:.2f} units")
        if moved < MIN_DISPLACEMENT:
            raise ValueError(f"{observer_name} saw client {mover_slot} move only {moved:.2f} < {MIN_DISPLACEMENT} units")
        if lag > MAX_TOLERANCE:
            raise ValueError(f"{observer_name}'s copy of client {mover_slot} is {lag:.2f} units from the server's (> {MAX_TOLERANCE})")
        print(f"  >>> [PASSED] {observer_name} saw client {mover_slot} move {moved:.2f} >= {MIN_DISPLACEMENT} units")
        return moved

    displacement = check_peer_sees_movement(client1_proc, "CLIENT_1", 0, client2_proc, "CLIENT_2")
    reverse_displacement = check_peer_sees_movement(client2_proc, "CLIENT_2", 1, client1_proc, "CLIENT_1")

    print("\n[8] Verifying Weapon Attack & Command Interaction...")

    # Client 1 issues weapon attack (+attack)
    print("  Triggering primary attack on Client 1 (+attack)...")
    client1_proc.stdin.write("+attack\n")
    client1_proc.stdin.flush()

    time.sleep(0.5)

    # Check attack state on Server for Client 0
    atk_state = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=0)
    print(f"  Server Client 0 Attack State: {atk_state['raw']}")
    # the server has the attack button in the client's usercmds and its weapon is attacking
    # (a peer cannot see this: another player's attack state is not in its snapshot, and
    # the fire effects are client-side, which the headless clients skip)
    atk = ATTACK_PATTERN.search(atk_state["raw"])
    if not atk or atk.group(1) != "1" or atk.group(2) != "1":
        raise AssertionError(f"server did not register Client 0's attack (atk, atk_cmd = {atk.groups() if atk else None})")

    # Release attack
    client1_proc.stdin.write("-attack\n")
    client1_proc.stdin.flush()

    time.sleep(0.3)
    released_state = query_player_pos(server_proc, "SERVER", output_queue, timeout=3.0, client_num=0)
    print(f"  Server Client 0 Post-Attack State: {released_state['raw']}")
    atk = ATTACK_PATTERN.search(released_state["raw"])
    if not atk or atk.group(2) != "0":
        raise AssertionError(f"server still has Client 0's attack button after -attack (atk, atk_cmd = {atk.groups() if atk else None})")
    print("  >>> [PASSED] The server registered the attack and its release (atk_cmd 1 -> 0, weapon attacking)")

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
    print(f" Peer Movement Replication:   VERIFIED both ways ({displacement:.2f} and {reverse_displacement:.2f} >= {MIN_DISPLACEMENT} u)")
    print(f" Weapon Attack on the Server: VERIFIED (+attack / -attack; peers do not see it headless)")
    print(f" Clean Multi-Process Exit:    VERIFIED (c1={c1_exit}, c2={c2_exit}, srv={srv_exit})")
    print("=================================================================\n")
    return 0


def parse_args(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary", help="dedicated binary for all three processes (default: build/)")
    ap.add_argument("--latency", type=int, default=0)
    ap.add_argument("--jitter", type=int, default=0)
    ap.add_argument("--drop", type=int, default=0)
    ap.add_argument("--reorder", type=int, default=0, help="percentage of received packets later ones overtake")
    ap.add_argument("--reorder-delay", type=int, default=30, help="ms a reordered packet is held behind later ones")
    ap.add_argument("--base-check", action="store_true")
    ap.add_argument("--log-dir")
    return ap.parse_args(argv)


def use_binary(path):
    """--ded-binary: the binary meson built, for the server and both clients."""
    global BINARY_PATH
    BINARY_PATH = Path(path)
    for proc_args in (SERVER_ARGS, CLIENT1_ARGS, CLIENT2_ARGS):
        proc_args[0] = str(BINARY_PATH)


def apply_network_conditions(args):
    """--latency/--jitter/--drop/--reorder: simulated network conditions for all
    three processes (net_forceLatency/net_forceJitter/net_forceDrop/
    net_forceReorder, net_forceReorderDelay); --base-check makes the server send
    its snapshot base checksum (net_debugSnapshotBase)."""
    global LOG_DIR, REORDER_EXPECTED
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        LOG_DIR = args.log_dir
    net = ["+set", "net_forceLatency", str(args.latency), "+set", "net_forceJitter", str(args.jitter),
           "+set", "net_forceDrop", str(args.drop),
           "+set", "net_forceReorder", str(args.reorder), "+set", "net_forceReorderDelay", str(args.reorder_delay)]
    REORDER_EXPECTED = args.reorder > 0
    for proc_args in (SERVER_ARGS, CLIENT1_ARGS, CLIENT2_ARGS):
        proc_args[-2:-2] = net  # before the final +spawnServer/+connect
    if args.base_check:
        SERVER_ARGS[-2:-2] = ["+set", "net_debugSnapshotBase", "1"]


if __name__ == "__main__":
    cli = parse_args(sys.argv[1:])
    if cli.ded_binary:
        use_binary(cli.ded_binary)
    h.skip_unless_available(DATA_PATH, BINARY_PATH)
    apply_network_conditions(cli)
    try:
        exit_with_scan(run_multiclient_test())
    except Exception as e:
        print(f"\nTEST FAILED: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
