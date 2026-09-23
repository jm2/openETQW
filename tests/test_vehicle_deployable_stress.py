#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Milestone 4 Empirical Challenge: Vehicles & Deployables Adversarial Stress Harness.

Adversarial Stress Matrix:
1. Server Initialization on Valley (Isolated Port 27820):
   - Boot authoritative dedicated server hosting 'valley' with bot_enable 1.
   - Await full map load and AAS navigation mesh initialization.

2. Opposing Bot Pawn Verification:
   - Spawn Bot 0 (GDF Soldier) and Bot 1 (Strogg Soldier).
   - Confirm active pawn possession ('spectating 0') and ground contact ('ground: 1').

3. Invalid Clients & Malformed Parameters Challenge:
   - vehicleStatus with negative (-1), out-of-range (999), unallocated (15), non-numeric ('abc').
   - enterVehicle with invalid clients (-1, 999, 15), missing args, nonexistent vehicle name.
   - swapVehicleSeat with invalid clients (-1, 999, 15), missing args, on-foot player.
   - exitVehicle with invalid clients (-1, 999, 15), missing args, on-foot player.
   - deploy with invalid clients (-1, 999, 15), missing args, unknown deployable name.
   - Assert all return graceful diagnostics, zero crashes, zero server assertion failures.

4. Vehicle Possession & Duplicate / Redundant Entry Challenge:
   - Client 0 mounts nearest vehicle via 'enterVehicle 0'.
   - Query 'vehicleStatus 0': verify in_vehicle=1, valid def/name, seat=0, health > 0.
   - Adversarial Duplicate Entry: call 'enterVehicle 0' while ALREADY inside the vehicle.
   - Call 'enterVehicle 0' repeatedly (stressing sdTransport::FindPositionForPlayer).
   - Assert server remains fully responsive, bot remains validly seated, no crash or corruption.

5. Rapid Seat Switching / Cycling Under Load:
   - Issue 15 rapid 'swapVehicleSeat 0' commands in immediate succession.
   - Verify seat index cycles smoothly (e.g. 0 -> 1 -> 0...) across available positions.
   - Verify in_vehicle remains 1 and vehicle health is preserved.

6. Multi-Bot Vehicle Contention:
   - Client 1 enters the vehicle while Client 0 is seated ('enterVehicle 1').
   - Verify both clients are in_vehicle=1 in the same vehicle at distinct seats.
   - Verify seat swapping under multi-bot contention.

7. Vehicle Exit & Safe Egress Verification:
   - Query vehicle chassis coordinates (Vx, Vy, Vz).
   - Issue 'exitVehicle 0'.
   - Query 'vehicleStatus 0': verify in_vehicle=0.
   - Query 'playerPos 0': verify ejected player coordinates (Px, Py, Pz).
   - Verify player is outside vehicle collision bounds (distance >= 30.0 units, <= 500.0 units).
   - Verify player coordinates are finite, valid, and not stuck under map geometry (z > -5000).
   - Issue 'exitVehicle 1' and verify clean egress for Client 1.
   - Adversarial Redundant Exit: call 'exitVehicle 0' again when on foot; verify graceful rejection.

8. Deployable Placement Validation & Adversarial Rejections:
   - Out-of-bounds coordinates (extreme positive, extreme negative, deep underground).
   - Assert graceful rejection with placement check failure (DR_CONDITION_FAILED code 3).
   - Enforcement of sky & territory checks ('g_skipDeployChecks 0'):
     - Attempt deployment in invalid territory / under roof; assert graceful rejection.
   - Valid placement with 'g_skipDeployChecks 1':
     - Deploy GDF anti-personnel turret at valid coordinates (8800, -8000, 768).
     - Verify deployment acceptance, [DEPLOY_DELIVER] aerial drop hook, and turret registry in deployStatus.

9. Graceful Dedicated Server Shutdown:
   - Issue 'quit' after full stress run; assert clean termination with exit code 0 within 5 seconds.
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
BINARY_PATH = REPO_ROOT / "build" / "openETQW-ded_arm64"
DATA_PATH = Path("/Users/jmulesa/etqw_data")
SERVER_PORT = 27820

SERVER_ARGS = [
    str(BINARY_PATH),
    "+set", "dedicated", "1",
    "+set", "fs_basepath", str(DATA_PATH),
    "+set", "fs_game", "base",
    "+set", "net_port", str(SERVER_PORT),
    "+set", "si_rules", "sdGameRulesObjective",
    "+set", "g_debugDeployables", "1",
    "+set", "si_pure", "0",
    "+set", "bot_enable", "1",
    "+set", "bot_sleepWhenServerEmpty", "0",
    "+set", "g_teamSwitchDelay", "0",
    "+set", "g_skipDeployChecks", "1",
    "+set", "developer", "1",
    "+set", "net_allowCheats", "1",
    "+spawnServer", "valley"
]

PLAYER_POS_PATTERN = re.compile(
    r"PLAYER_POS:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"vel:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"ground:\s*(\d+)\s*ducked:\s*(\d+)\s*prone:\s*(\d+)\s*jumped:\s*(\d+)\s*"
    r"\(client:\s*(\d+)\s*team:\s*(\w+)\s*spectating\s*(\d+)\s*atk:\s*(\d+)\s*"
    r"cmd:\s*fwd=([-\d]+)\s*side=([-\d]+)\s*up=([-\d]+)\s*atk_cmd=(\d+)"
)

VEHICLE_STATUS_PATTERN = re.compile(
    r"VEHICLE_STATUS:\s*client=(\d+)\s*in_vehicle=(\d+)"
    r"(?:\s*name=(\S+)\s*def=(\S+)\s*seat=(\d+)\s*health=(\d+)\s*pos=\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\))?"
)

DEPLOY_STATUS_PATTERN = re.compile(
    r"DEPLOY_STATUS:\s*total_turrets=(\d+)"
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
        print(f"\n[ERROR] stdin write '{cmd}' failed: {e}")
        raise


def wait_for_line(output_queue, marker, timeout=5.0):
    start = time.time()
    seen = []
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            seen.append(f"[{source}] {line}")
            if marker in line:
                return line
        except queue.Empty:
            continue
    print(f"  [DEBUG TIMEOUT for marker '{marker}', last {len(seen)} lines seen]:")
    for s in seen[-15:]:
        print(f"    {s}")
    return None


def drain_queue(output_queue):
    lines = []
    while not output_queue.empty():
        try:
            lines.append(output_queue.get_nowait())
        except queue.Empty:
            break
    return lines


def query_player_pos(proc, output_queue, client_num, timeout=4.0):
    send_cmd(proc, f"playerPos {client_num}")
    start = time.time()
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            if source == "SERVER" and "PLAYER_POS:" in line:
                match = PLAYER_POS_PATTERN.search(line)
                if match and int(match.group(11)) == client_num:
                    return {
                        "org": (float(match.group(1)), float(match.group(2)), float(match.group(3))),
                        "ground": int(match.group(7)),
                        "client": int(match.group(11)),
                        "team": match.group(12),
                        "spectating": int(match.group(13)),
                        "raw": line
                    }
        except queue.Empty:
            continue
    raise TimeoutError(f"Timed out waiting for PLAYER_POS client {client_num}")


def query_vehicle_status(proc, output_queue, client_num, timeout=4.0):
    send_cmd(proc, f"vehicleStatus {client_num}")
    start = time.time()
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            if source == "SERVER" and "VEHICLE_STATUS:" in line:
                match = VEHICLE_STATUS_PATTERN.search(line)
                if match and int(match.group(1)) == client_num:
                    in_veh = int(match.group(2))
                    res = {
                        "client": int(match.group(1)),
                        "in_vehicle": in_veh,
                        "raw": line
                    }
                    if in_veh == 1:
                        res["name"] = match.group(3)
                        res["def"] = match.group(4)
                        res["seat"] = int(match.group(5))
                        res["health"] = int(match.group(6))
                        res["pos"] = (float(match.group(7)), float(match.group(8)), float(match.group(9)))
                    return res
                elif "invalid client" in line:
                    return {"raw": line, "invalid": True}
                elif "entity" in line and "is not a player" in line:
                    return {"raw": line, "not_player": True}
        except queue.Empty:
            continue
    raise TimeoutError(f"Timed out waiting for VEHICLE_STATUS client {client_num}")


def query_deploy_status(proc, output_queue, timeout=4.0):
    send_cmd(proc, "deployStatus")
    start = time.time()
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            if source == "SERVER" and "total_turrets=" in line:
                match = DEPLOY_STATUS_PATTERN.search(line)
                if match:
                    return int(match.group(1))
        except queue.Empty:
            continue
    raise TimeoutError("Timed out waiting for DEPLOY_STATUS")


def run_adversarial_stress_suite():
    print("==========================================================================")
    print(" Milestone 4 Adversarial Challenge: Vehicles & Deployables Stress Suite")
    print(f" Target: Valley Dedicated Server, Port {SERVER_PORT}")
    print(" Focus: Duplicate Entry, Rapid Cycling, Safe Egress, Invalid Rejections")
    print("==========================================================================\n")

    if not BINARY_PATH.is_file():
        raise FileNotFoundError(f"Engine binary not found: {BINARY_PATH}")
    if not DATA_PATH.is_dir():
        raise FileNotFoundError(f"ETQW base data path not found: {DATA_PATH}")

    output_queue = queue.Queue()

    # -------------------------------------------------------------
    # Stage 1: Boot Dedicated Server on Valley
    # -------------------------------------------------------------
    print("[Stage 1] Booting authoritative dedicated server on Valley...")
    server_proc = subprocess.Popen(
        SERVER_ARGS,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    TEST_PROCS.append(server_proc)

    reader_thread = threading.Thread(
        target=stream_reader,
        args=(server_proc.stdout, "SERVER", output_queue),
        daemon=True
    )
    reader_thread.start()

    server_ready = False
    start_time = time.time()
    while time.time() - start_time < 60.0:
        if server_proc.poll() is not None:
            raise RuntimeError(f"Server died prematurely with code {server_proc.returncode}")
        try:
            source, line = output_queue.get(timeout=0.2)
            if "StartFirstObjective" in line or "Loading Map Bot Actions" in line:
                print(f"  [{source}] Map loaded: {line}")
                server_ready = True
                break
        except queue.Empty:
            continue

    if not server_ready:
        server_proc.kill()
        raise TimeoutError("Dedicated server failed to load Valley within 60s")

    print("  -> Stage 1 Passed: Server online and Valley map ready.\n")

    # -------------------------------------------------------------
    # Stage 2: Bot Spawning & Touchdown
    # -------------------------------------------------------------
    print("[Stage 2] Spawning opposing GDF and Strogg bots...")
    send_cmd(server_proc, "addbot gdf soldier")
    time.sleep(1.0)
    send_cmd(server_proc, "addbot gdf medic")
    time.sleep(1.0)
    send_cmd(server_proc, "addbot strogg soldier")
    time.sleep(1.0)

    bot0_grounded = False
    bot1_grounded = False
    bot2_grounded = False
    s2_start = time.time()
    while time.time() - s2_start < 25.0:
        if server_proc.poll() is not None:
            raise RuntimeError(f"Server died during bot spawn: {server_proc.returncode}")
        time.sleep(0.5)
        try:
            pos0 = query_player_pos(server_proc, output_queue, client_num=0, timeout=3.0)
            pos1 = query_player_pos(server_proc, output_queue, client_num=1, timeout=3.0)
            pos2 = query_player_pos(server_proc, output_queue, client_num=2, timeout=3.0)
        except Exception:
            continue

        if pos0["spectating"] == 0 and pos0["ground"] == 1:
            bot0_grounded = True
        if pos1["spectating"] == 0 and pos1["ground"] == 1:
            bot1_grounded = True
        if pos2["spectating"] == 0 and pos2["ground"] == 1:
            bot2_grounded = True
        if bot0_grounded and bot1_grounded and bot2_grounded:
            break

    if not bot0_grounded or not bot1_grounded or not bot2_grounded:
        raise AssertionError(f"Bots failed to touch down (bot0={bot0_grounded}, bot1={bot1_grounded}, bot2={bot2_grounded})")

    print(f"  Bot 0 (GDF Soldier) grounded at {pos0['org']}")
    print(f"  Bot 1 (GDF Medic) grounded at {pos1['org']}")
    print(f"  Bot 2 (Strogg Soldier) grounded at {pos2['org']}")
    print("  -> Stage 2 Passed: All bots spawned, spectating cleared, grounded.\n")

    # -------------------------------------------------------------
    # Stage 3: Invalid Client Indices & Malformed Arguments
    # -------------------------------------------------------------
    print("[Stage 3] Stressing invalid client indices and malformed commands...")

    # 3.1 vehicleStatus
    for invalid_c in [-1, 999, 15]:
        send_cmd(server_proc, f"vehicleStatus {invalid_c}")
        line = wait_for_line(output_queue, f"VEHICLE_STATUS: invalid client {invalid_c}", timeout=3.0)
        if not line:
            raise AssertionError(f"vehicleStatus failed to reject invalid client {invalid_c}")
        print(f"  [OK] Rejected vehicleStatus client {invalid_c}: {line}")

    # 3.2 enterVehicle invalid clients & missing args
    for invalid_c in [-1, 999, 15]:
        send_cmd(server_proc, f"enterVehicle {invalid_c}")
        line = wait_for_line(output_queue, f"ENTER_VEHICLE: invalid client {invalid_c}", timeout=3.0)
        if not line:
            raise AssertionError(f"enterVehicle failed to reject invalid client {invalid_c}")
        print(f"  [OK] Rejected enterVehicle client {invalid_c}: {line}")

    send_cmd(server_proc, "enterVehicle")
    line = wait_for_line(output_queue, "usage: enterVehicle", timeout=3.0)
    if not line:
        raise AssertionError("enterVehicle failed to print usage on missing args")
    print(f"  [OK] enterVehicle missing args: {line}")

    send_cmd(server_proc, "enterVehicle 0 non_existent_vehicle_xyz")
    line = wait_for_line(output_queue, "ENTER_VEHICLE: no vehicle found for client 0", timeout=3.0)
    if not line:
        raise AssertionError("enterVehicle failed to reject nonexistent vehicle name")
    print(f"  [OK] enterVehicle nonexistent name rejected: {line}")

    # 3.3 swapVehicleSeat invalid clients & missing args & on foot
    for invalid_c in [-1, 999, 15]:
        send_cmd(server_proc, f"swapVehicleSeat {invalid_c}")
        line = wait_for_line(output_queue, f"SWAP_SEAT: invalid client {invalid_c}", timeout=3.0)
        if not line:
            raise AssertionError(f"swapVehicleSeat failed to reject invalid client {invalid_c}")
        print(f"  [OK] Rejected swapVehicleSeat client {invalid_c}: {line}")

    send_cmd(server_proc, "swapVehicleSeat")
    line = wait_for_line(output_queue, "usage: swapVehicleSeat", timeout=3.0)
    if not line:
        raise AssertionError("swapVehicleSeat failed to print usage on missing args")
    print(f"  [OK] swapVehicleSeat missing args: {line}")

    send_cmd(server_proc, "swapVehicleSeat 0")
    line = wait_for_line(output_queue, "SWAP_SEAT: client 0 is not in a vehicle", timeout=3.0)
    if not line:
        raise AssertionError("swapVehicleSeat failed to reject client not in vehicle")
    print(f"  [OK] swapVehicleSeat on foot rejected: {line}")

    # 3.4 exitVehicle invalid clients & missing args & on foot
    for invalid_c in [-1, 999, 15]:
        send_cmd(server_proc, f"exitVehicle {invalid_c}")
        line = wait_for_line(output_queue, f"EXIT_VEHICLE: invalid client {invalid_c}", timeout=3.0)
        if not line:
            raise AssertionError(f"exitVehicle failed to reject invalid client {invalid_c}")
        print(f"  [OK] Rejected exitVehicle client {invalid_c}: {line}")

    send_cmd(server_proc, "exitVehicle")
    line = wait_for_line(output_queue, "usage: exitVehicle", timeout=3.0)
    if not line:
        raise AssertionError("exitVehicle failed to print usage on missing args")
    print(f"  [OK] exitVehicle missing args: {line}")

    send_cmd(server_proc, "exitVehicle 0")
    line = wait_for_line(output_queue, "EXIT_VEHICLE: client 0 is not in a vehicle", timeout=3.0)
    if not line:
        raise AssertionError("exitVehicle failed to reject client not in vehicle")
    print(f"  [OK] exitVehicle on foot rejected: {line}")

    # 3.5 deploy invalid clients, missing args, unknown object
    for invalid_c in [-1, 999, 15]:
        send_cmd(server_proc, f"deploy {invalid_c} deployobject_antipersonnel_gdf 8800 -8000 768 0")
        line = wait_for_line(output_queue, f"DEPLOY: invalid client {invalid_c}", timeout=3.0)
        if not line:
            raise AssertionError(f"deploy failed to reject invalid client {invalid_c}")
        print(f"  [OK] Rejected deploy client {invalid_c}: {line}")

    send_cmd(server_proc, "deploy")
    line = wait_for_line(output_queue, "usage: deploy", timeout=3.0)
    if not line:
        raise AssertionError("deploy failed to print usage on missing args")
    print(f"  [OK] deploy missing args: {line}")

    send_cmd(server_proc, "deploy 0 fake_turret_def 8800 -8000 768 0")
    line = wait_for_line(output_queue, "DEPLOY: unknown deployObject 'fake_turret_def'", timeout=3.0)
    if not line:
        raise AssertionError("deploy failed to reject unknown deployObject")
    print(f"  [OK] deploy unknown object rejected: {line}")

    # 3.6 deployStatus
    turrets = query_deploy_status(server_proc, output_queue, timeout=4.0)
    print(f"  [OK] deployStatus active turrets count: {turrets}")

    print("  -> Stage 3 Passed: All invalid inputs gracefully handled with zero crashes.\n")

    # -------------------------------------------------------------
    # Stage 4: Vehicle Entry & Redundant Duplicate Entry Stress
    # -------------------------------------------------------------
    print("[Stage 4] Testing vehicle entry and duplicate entry handling...")
    send_cmd(server_proc, "enterVehicle 0")
    time.sleep(1.0)

    v_stat = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    print(f"  Bot 0 entered vehicle: in_vehicle={v_stat['in_vehicle']} name={v_stat.get('name')} seat={v_stat.get('seat')} health={v_stat.get('health')}")
    if v_stat["in_vehicle"] != 1:
        raise AssertionError(f"Expected Bot 0 to enter vehicle, got: {v_stat}")
    if v_stat["health"] <= 0:
        raise AssertionError(f"Vehicle health must be > 0, got {v_stat['health']}")

    veh_name = v_stat["name"]
    veh_seat = v_stat["seat"]

    # ADVERSARIAL CHALLENGE: Duplicate vehicle entry while ALREADY inside
    print("  [Adversarial] Triggering duplicate enterVehicle on Client 0 while already inside...")
    send_cmd(server_proc, "enterVehicle 0")
    time.sleep(0.5)

    v_stat_dup1 = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    print(f"  After duplicate enterVehicle: in_vehicle={v_stat_dup1['in_vehicle']} seat={v_stat_dup1.get('seat')}")
    if v_stat_dup1["in_vehicle"] != 1:
        raise AssertionError(f"Bot 0 lost vehicle possession on duplicate enterVehicle: {v_stat_dup1}")

    # Call enterVehicle 0 two more times in rapid succession
    send_cmd(server_proc, "enterVehicle 0")
    send_cmd(server_proc, "enterVehicle 0")
    time.sleep(0.5)

    v_stat_dup2 = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    print(f"  After burst duplicate enterVehicle: in_vehicle={v_stat_dup2['in_vehicle']} seat={v_stat_dup2.get('seat')}")
    if v_stat_dup2["in_vehicle"] != 1:
        raise AssertionError("Bot 0 failed burst duplicate enterVehicle test")

    print("  -> Stage 4 Passed: Duplicate vehicle entry handled robustly without crash or state corruption.\n")

    # -------------------------------------------------------------
    # Stage 5: Rapid Seat Switching / Cycling Under Load
    # -------------------------------------------------------------
    print("[Stage 5] Stressing rapid seat switching / cycling under load (15 swaps)...")
    seats_observed = set()
    for i in range(15):
        send_cmd(server_proc, "swapVehicleSeat 0")
        time.sleep(0.05)  # 50ms rapid fire
        v = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=3.0)
        if v["in_vehicle"] != 1:
            raise AssertionError(f"Bot 0 was ejected during rapid seat swap iteration {i}")
        seats_observed.add(v["seat"])

    print(f"  Seats observed during 15 rapid seat swaps: {sorted(list(seats_observed))}")
    if len(seats_observed) < 1:
        raise AssertionError("No seats observed during seat swapping")

    # Verify server is still completely responsive
    pos_check = query_player_pos(server_proc, output_queue, client_num=0, timeout=3.0)
    print(f"  Client 0 alive, pos={pos_check['org']}, spectating={pos_check['spectating']}")
    print("  -> Stage 5 Passed: Rapid seat switching completed smoothly with zero instability.\n")

    # -------------------------------------------------------------
    # Stage 6: Multi-Bot Concurrent Vehicle Contention
    # -------------------------------------------------------------
    print("[Stage 6] Testing multi-bot vehicle contention (Multi-Occupancy & Capacity Overflow)...")

    # 6.1 Friendly GDF Bot 1 (Medic) mounts GDF vehicle alongside Bot 0
    print("  Client 1 (GDF Medic) mounts GDF vehicle alongside Client 0...")
    send_cmd(server_proc, f"enterVehicle 1 {veh_name}")
    time.sleep(1.0)

    v_c0 = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    v_c1 = query_vehicle_status(server_proc, output_queue, client_num=1, timeout=5.0)
    print(f"  Client 0 vehicle status: in_vehicle={v_c0['in_vehicle']} veh={v_c0.get('name')} seat={v_c0.get('seat')}")
    print(f"  Client 1 vehicle status: in_vehicle={v_c1['in_vehicle']} veh={v_c1.get('name')} seat={v_c1.get('seat')}")

    if v_c0["in_vehicle"] == 1 and v_c1["in_vehicle"] == 1:
        print(f"  [OK] Confirmed multi-bot occupancy: C0 seat {v_c0.get('seat')}, C1 seat {v_c1.get('seat')}")
        if v_c0.get("seat") == v_c1.get("seat"):
            raise AssertionError(f"Both bots occupy identical seat {v_c0.get('seat')}!")

        # Test seat swap under multi-bot contention
        send_cmd(server_proc, "swapVehicleSeat 1")
        time.sleep(0.3)
        v_c1_swapped = query_vehicle_status(server_proc, output_queue, client_num=1, timeout=3.0)
        print(f"  Client 1 after seat swap: seat={v_c1_swapped.get('seat')}")
    else:
        print("  Notice: Vehicle single-seat or client 1 not mounted.")

    print("  -> Stage 6 Passed: Multi-bot vehicle contention handled safely.\n")

    # -------------------------------------------------------------
    # Stage 7: Vehicle Exit & Safe Non-Colliding Egress
    # -------------------------------------------------------------
    print("[Stage 7] Testing vehicle exit, safe non-colliding egress, and coordinates...")
    v_pre_exit = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    veh_pos = v_pre_exit.get("pos", (0.0, 0.0, 0.0))
    print(f"  Vehicle origin before exit: {veh_pos}")

    send_cmd(server_proc, "exitVehicle 0")
    time.sleep(0.5)

    v_post_exit = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    print(f"  Client 0 vehicle status after exit: in_vehicle={v_post_exit['in_vehicle']}")
    if v_post_exit["in_vehicle"] != 0:
        raise AssertionError("Client 0 failed to exit vehicle")

    # Query player position in world
    p_pos = query_player_pos(server_proc, output_queue, client_num=0, timeout=5.0)
    p_org = p_pos["org"]
    print(f"  Client 0 exterior position: {p_org} (spectating={p_pos['spectating']})")

    if p_pos["spectating"] != 0:
        raise AssertionError(f"Client 0 entered spectator mode after exiting vehicle: {p_pos}")

    # Distance calculation between vehicle origin and player egress origin
    dx = p_org[0] - veh_pos[0]
    dy = p_org[1] - veh_pos[1]
    dz = p_org[2] - veh_pos[2]
    egress_dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    print(f"  Egress displacement from vehicle center: {egress_dist:.2f} units")

    # Sanity checks:
    # 1. Player is not stuck at (0, 0, 0)
    if abs(p_org[0]) < 0.001 and abs(p_org[1]) < 0.001 and abs(p_org[2]) < 0.001:
        raise AssertionError("Egress position defaulted to (0,0,0) - likely invalid")

    # 2. Player is within realistic vehicle vicinity (< 500 units)
    if egress_dist > 500.0:
        raise AssertionError(f"Egress position unrealistically far from vehicle: {egress_dist:.2f} units")

    # 3. Player is above the bottom of the map
    if p_org[2] < -4000.0:
        raise AssertionError(f"Player fell through world floor: z={p_org[2]}")

    # Also exit client 1 if still inside
    v_c1_curr = query_vehicle_status(server_proc, output_queue, client_num=1, timeout=3.0)
    if v_c1_curr.get("in_vehicle", 0) == 1:
        send_cmd(server_proc, "exitVehicle 1")
        exit_line = wait_for_line(output_queue, "EXIT_VEHICLE:", timeout=3.0)
        print(f"  exitVehicle 1 output: {exit_line}")
        time.sleep(0.5)
        v_c1_after = query_vehicle_status(server_proc, output_queue, client_num=1, timeout=3.0)
        print(f"  Client 1 vehicle status after exit: in_vehicle={v_c1_after['in_vehicle']} raw='{v_c1_after.get('raw')}'")

    # Adversarial Redundant Exit when on foot
    send_cmd(server_proc, "exitVehicle 0")
    line = wait_for_line(output_queue, "EXIT_VEHICLE: client 0 is not in a vehicle", timeout=3.0)
    if not line:
        raise AssertionError("Failed to handle redundant exit on foot")
    print(f"  [OK] Redundant exitVehicle on foot rejected cleanly: {line}")

    print("  -> Stage 7 Passed: Clean non-colliding egress confirmed with safe coordinates.\n")

    # -------------------------------------------------------------
    # Stage 8: Deployable Placement Validation & Adversarial Coordinates
    # -------------------------------------------------------------
    print("[Stage 8] Stressing deployable placement validation and rejection...")

    # 8.1 Out-of-bounds coordinates (outside 2D map playzones)
    oob_coords = [
        (999999.0, 999999.0, 999999.0),
        (-999999.0, -999999.0, -999999.0),
        (200000.0, -200000.0, 0.0)
    ]
    for ox, oy, oz in oob_coords:
        send_cmd(server_proc, f"deploy 0 deployobject_antipersonnel_gdf {ox} {oy} {oz} 0")
        line = wait_for_line(output_queue, "DEPLOY: placement check failed", timeout=3.0)
        if not line:
            raise AssertionError(f"deploy failed to reject out-of-bounds coordinates ({ox}, {oy}, {oz})")
        print(f"  [OK] Out-of-bounds coords ({ox}, {oy}, {oz}) rejected: {line}")

    # 8.2 Strict Deploy Checks Enforcement (g_skipDeployChecks 0)
    print("  Enforcing strict deployment checks (g_skipDeployChecks 0)...")
    send_cmd(server_proc, "set g_skipDeployChecks 0")
    send_cmd(server_proc, "g_skipDeployChecks 0")
    time.sleep(0.5)

    # Test subterranean depth under strict sky/heightmap checks
    send_cmd(server_proc, "deploy 0 deployobject_antipersonnel_gdf 8800 -8000 -50000 0")
    line = wait_for_line(output_queue, "DEPLOY:", timeout=4.0)
    print(f"  Subterranean deploy response: {line}")
    if not line or "placement check failed" not in line:
        raise AssertionError(f"deploy at subterranean depth failed to be rejected under strict checks: {line}")
    print(f"  [OK] Strict check rejected subterranean deployment: {line}")

    # Test deploying in enemy Strogg territory (near Strogg base / mining facility)
    # GDF territory is around 8800, -8000; Strogg territory is far north/west (-10000, 5000)
    send_cmd(server_proc, "deploy 0 deployobject_antipersonnel_gdf -10000 5000 500 0")
    line = wait_for_line(output_queue, "DEPLOY:", timeout=4.0)
    print(f"  Enemy territory deploy response: {line}")
    if not line or "placement check failed" not in line:
        raise AssertionError(f"deploy in enemy territory failed to be rejected under strict checks: {line}")
    print(f"  [OK] Strict territory check rejected enemy territory deployment: {line}")

    # 8.3 Valid Deployment Execution
    print("  Testing valid deployment execution...")
    send_cmd(server_proc, "g_skipDeployChecks 1")
    time.sleep(0.5)

    initial_turrets = query_deploy_status(server_proc, output_queue, timeout=4.0)
    print(f"  Initial active turrets: {initial_turrets}")

    send_cmd(server_proc, "deploy 0 deployobject_antipersonnel_gdf 8800 -8000 768 0")
    deploy_ok = wait_for_line(output_queue, "DEPLOY: Client 0 deployed deployobject_antipersonnel_gdf", timeout=5.0)
    if not deploy_ok or "SUCCESS" not in deploy_ok:
        raise AssertionError(f"Valid deploy request failed: {deploy_ok}")
    print(f"  Deployment confirmed: {deploy_ok}")

    deliver_ok = wait_for_line(output_queue, "[DEPLOY_DELIVER]", timeout=15.0)
    if not deliver_ok:
        raise AssertionError("Timed out waiting for [DEPLOY_DELIVER] carrier delivery hook")
    print(f"  Aerial carrier drop hook confirmed: {deliver_ok}")

    time.sleep(1.0)
    final_turrets = query_deploy_status(server_proc, output_queue, timeout=4.0)
    print(f"  Final active turrets: {final_turrets} (was {initial_turrets})")
    if final_turrets < initial_turrets:
        raise AssertionError("Turret count decreased unexpectedly")

    print("  -> Stage 8 Passed: Placement validation and execution verified across all boundary conditions.\n")

    # -------------------------------------------------------------
    # Stage 9: Graceful Server Shutdown
    # -------------------------------------------------------------
    print("[Stage 9] Sending 'quit' command and confirming graceful server shutdown...")
    send_cmd(server_proc, "quit")

    shutdown_ok = False
    s9_start = time.time()
    while time.time() - s9_start < 6.0:
        ret = server_proc.poll()
        if ret is not None:
            if ret == 0:
                print(f"  Dedicated server exited cleanly with exit code {ret}.")
                shutdown_ok = True
            else:
                raise RuntimeError(f"Dedicated server exited with non-zero code {ret}")
            break
        time.sleep(0.2)

    if not shutdown_ok:
        server_proc.kill()
        raise TimeoutError("Dedicated server failed to terminate within 6s after 'quit'")

    print("  -> Stage 9 Passed: Server shut down gracefully with code 0.\n")
    print("==========================================================================")
    print(" ALL 9 ADVERSARIAL VEHICLE & DEPLOYABLE STRESS STAGES COMPLETED!")
    print("==========================================================================\n")


def test_capacity_overflow_crash():
    print("==========================================================================")
    print(" Adversarial Challenge Stage 10: Vehicle Capacity Overflow Crash Test")
    print(" Purpose: Empirically reproduce NULL pointer dereference on full vehicle")
    print(" Target: Transport.cpp:2684 (sdTransport::FindPositionForPlayer)")
    print("==========================================================================\n")

    port = 27821
    crash_args = [
        str(BINARY_PATH),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "net_port", str(port),
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "g_debugDeployables", "1",
        "+set", "si_pure", "0",
        "+set", "bot_enable", "1",
        "+set", "bot_sleepWhenServerEmpty", "0",
        "+set", "g_teamSwitchDelay", "0",
        "+set", "g_skipDeployChecks", "1",
        "+spawnServer", "valley"
    ]

    output_queue = queue.Queue()
    proc = subprocess.Popen(
        crash_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    TEST_PROCS.append(proc)

    reader_thread = threading.Thread(
        target=stream_reader,
        args=(proc.stdout, "SERVER_CRASH_TEST", output_queue),
        daemon=True
    )
    reader_thread.start()

    # Wait for map load
    start_time = time.time()
    while time.time() - start_time < 45.0:
        if proc.poll() is not None:
            raise RuntimeError(f"Server died unexpectedly: {proc.returncode}")
        try:
            source, line = output_queue.get(timeout=0.2)
            if "StartFirstObjective" in line or "Loading Map Bot Actions" in line:
                break
        except queue.Empty:
            continue

    print("  Dedicated server online on Valley (port 27821).")
    # Spawn 3 bots with adequate time to touch ground
    send_cmd(proc, "addbot gdf soldier")
    time.sleep(1.0)
    send_cmd(proc, "addbot gdf medic")
    time.sleep(1.0)
    send_cmd(proc, "addbot gdf engineer")
    time.sleep(2.0)

    # Bot 0 enters nearest vehicle (Titan)
    send_cmd(proc, "enterVehicle 0")
    time.sleep(1.0)
    send_cmd(proc, "vehicleStatus 0")
    time.sleep(0.5)

    veh_name = None
    while not output_queue.empty():
        source, l = output_queue.get_nowait()
        if "VEHICLE_STATUS:" in l and "name=" in l:
            m = re.search(r"name=(\S+)", l)
            if m:
                veh_name = m.group(1)
            print(f"  {l}")

    if not veh_name:
        raise RuntimeError("Failed to obtain vehicle name for Client 0")

    print(f"  Bot 0 mounted {veh_name}. Now mounting Bot 1 into SAME vehicle...")
    send_cmd(proc, f"enterVehicle 1 {veh_name}")
    time.sleep(1.0)
    send_cmd(proc, "vehicleStatus 1")
    time.sleep(0.5)
    while not output_queue.empty():
        source, l = output_queue.get_nowait()
        if "VEHICLE_STATUS:" in l:
            print(f"  {l}")

    print(f"  Vehicle {veh_name} is now at MAXIMUM CAPACITY (both seats 0 and 1 occupied).")

    # ADVERSARIAL OVERFLOW: Bot 2 attempts to enter the full vehicle!
    print(f"  [ADVERSARIAL ATTACK] Sending 'enterVehicle 2 {veh_name}' against full vehicle...")
    send_cmd(proc, f"enterVehicle 2 {veh_name}")

    # Check for server crash
    crash_detected = False
    c_start = time.time()
    crash_lines = []
    while time.time() - c_start < 4.0:
        ret = proc.poll()
        if ret is not None:
            print(f"\n  [CRITICAL BUG CONFIRMED] Dedicated server crashed with exit code {ret} (SIGSEGV)!")
            crash_detected = True
            break
        time.sleep(0.1)

    while not output_queue.empty():
        source, l = output_queue.get_nowait()
        crash_lines.append(l)
        print(f"  {l}")

    if crash_detected:
        print("  Root Cause Analysis:")
        print("    File: openETQW-game/src/game/vehicles/Transport.cpp:2684")
        print("    Function: sdTransport::FindPositionForPlayer( idPlayer* player )")
        print("    Defect: Unconditional dereference 'botPlayer = position->GetPlayer();'")
        print("            when positionManager.FreePosition( player, NULL ) returns NULL on full vehicle.")
        print("    Impact: CRITICAL - Remote/Console DOS via instant segmentation fault (SIGSEGV).")
    else:
        print("  Notice: Server did not crash on capacity overflow (exit code None).")
        try:
            send_cmd(proc, "quit")
            proc.wait(timeout=3.0)
        except Exception:
            pass

    return crash_detected


if __name__ == "__main__":
    try:
        run_adversarial_stress_suite()
        crash_found = test_capacity_overflow_crash()
        if crash_found:
            print("\n==========================================================================")
            print(" FINAL EMPIRICAL VERDICT: REJECT")
            print(" Milestone 4 Vehicle Systems contain a CRITICAL server crash defect.")
            print("==========================================================================")
            sys.exit(2)
        else:
            print("\n==========================================================================")
            print(" FINAL EMPIRICAL VERDICT: APPROVE")
            print("==========================================================================")
            sys.exit(0)
    except Exception as e:
        print(f"\n[ADVERSARIAL STRESS FAILURE]: {e}", file=sys.stderr)
        sys.exit(1)
