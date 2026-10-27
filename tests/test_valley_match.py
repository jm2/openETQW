#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Valley Match & Mission Objectives Automated Integration Test for OpenETQW Milestone 4.

Spawns an authoritative dedicated server hosting 'valley' with bot_enable 1,
spawns GDF and Strogg soldier bots, and verifies:
1. Server initialization and Valley map load.
2. Bot spawning, spectating clearance, and ground contact.
3. Vehicle mounting, seat swapping, and egress.
4. Deployable placement validation, aerial carrier delivery ([DEPLOY_DELIVER]), and active turret registry.
5. Objective 0: Construction of the Valley Bridge (gdf_obj >= 1).
6. Objective 1: Capture of the Forward Outpost (gdf_obj >= 2).
7. Objective 2: Neutralization of the Strogg Mining Facility Shield (gdf_obj >= 3).
8. Objective 3: Destruction of the Contaminator core, FinalEffectsThread execution,
   match victory recording (winner=gdf), and transition to GS_GAMEREVIEW (state=4).
9. Graceful dedicated server shutdown via 'quit' with clean exit code 0.
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
DATA_PATH = h.env_data_path()
SERVER_PORT = 27795

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
    "+set", "g_debugDeployables", "1",
    "+set", "g_warmup", "0",
    # the console drives vehicle entry/exit; keep the bot AI from boarding vehicles on its own
    "+set", "bot_useVehicles", "0",
    "+set", "bot_useAirVehicles", "0",
    "+set", "si_pure", "0",
    "+set", "net_allowCheats", "1",  # completeObjective, deploy, enterVehicle, exitVehicle and swapVehicleSeat are cheats
    "+set", "bot_enable", "1",
    "+set", "bot_sleepWhenServerEmpty", "0",
    "+set", "g_teamSwitchDelay", "0",
    "+set", "g_skipDeployChecks", "1",
    "+spawnServer", "valley"
]

PLAYER_POS_PATTERN = re.compile(
    r"PLAYER_POS:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"vel:\s*\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\)\s*"
    r"ground:\s*(\d+)\s*ducked:\s*(\d+)\s*prone:\s*(\d+)\s*jumped:\s*(\d+)\s*"
    r"\(client:\s*(\d+)\s*team:\s*(\w+)\s*spectating\s*(\d+)\s*atk:\s*(\d+)\s*"
    r"cmd:\s*fwd=([-\d]+)\s*side=([-\d]+)\s*up=([-\d]+)\s*atk_cmd=(\d+)"
)

SWAP_SEAT_PATTERN = re.compile(r"SWAP_SEAT: client (\d+) in \S+ now in seat (\d+)")
VEHICLE_STATUS_PATTERN = re.compile(
    r"VEHICLE_STATUS:\s*client=(\d+)\s*in_vehicle=(\d+)"
    r"(?:\s*name=(\S+)\s*def=(\S+)\s*seat=(\d+)\s*health=(\d+)\s*pos=\(([-\d.]+),\s*([-\d.]+),\s*([-\d.]+)\))?"
)

OBJECTIVE_STATUS_PATTERN = re.compile(
    r"OBJECTIVE_STATUS:\s*gdf_obj=(\d+)\s*strogg_obj=(\d+)\s*state=(\d+)\s*winner=(\w+)"
)

DEPLOY_STATUS_PATTERN = re.compile(
    r"DEPLOY_STATUS:\s*total_turrets=(\d+)"
)


LOG_PATH = None  # --log: the server's output is also written there


# every line each process printed, checked for failure markers when the test ends
# (a common->Error drop or a crash banner does not always change the exit code)
PROCESS_LINES = {}
READER_THREADS = []  # joined before the scan, so the last lines a process printed are read


def scan_process_output(run_failed: bool) -> list[str]:
    """Failure markers (etqw_harness.scan_failures) in any process's output.

    run_failed is the run's own outcome, passed from exit_with_scan()'s rc. A failed
    phase raises out of the run function before this scan is reached, so rc is 0 on
    the path that gets here; passing it keeps the pacing rule explicit (#321)."""
    found = []
    for name, lines in PROCESS_LINES.items():
        for b in h.scan_failures(lines, run_failed=run_failed):
            found.append(f"{name} line {b['line']}: {b['text']}")
    return found


def exit_with_scan(rc):
    # the processes have exited (or are being killed); a reader can still be draining
    # the pipe, so let each finish before scanning
    for t in READER_THREADS:
        t.join(timeout=10.0)
    bad = scan_process_output(run_failed=bool(rc))
    if bad:
        print("\nFAILURE MARKERS in process output:")
        for b in bad:
            print(f"  - {b}")
        rc = rc or 1
    sys.exit(rc)


def stream_reader(pipe, name, q):
    READER_THREADS.append(threading.current_thread())
    log = open(LOG_PATH, "w", errors="replace") if LOG_PATH else None
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


def query_player_pos(proc, output_queue, client_num, timeout=5.0):
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


def query_vehicle_status(proc, output_queue, client_num, timeout=5.0):
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
        except queue.Empty:
            continue
    raise TimeoutError(f"Timed out waiting for VEHICLE_STATUS client {client_num}")


def query_objective_status(proc, output_queue, timeout=5.0):
    send_cmd(proc, "objectiveStatus")
    start = time.time()
    while time.time() - start < timeout:
        try:
            source, line = output_queue.get(timeout=0.1)
            if source == "SERVER" and "OBJECTIVE_STATUS:" in line:
                match = OBJECTIVE_STATUS_PATTERN.search(line)
                if match:
                    return {
                        "gdf_obj": int(match.group(1)),
                        "strogg_obj": int(match.group(2)),
                        "state": int(match.group(3)),
                        "winner": match.group(4),
                        "raw": line
                    }
        except queue.Empty:
            continue
    raise TimeoutError("Timed out waiting for OBJECTIVE_STATUS")


def query_deploy_status(proc, output_queue, timeout=5.0):
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


def run_valley_match_test():
    print("=================================================================")
    print(" OpenETQW Milestone 4: Valley Match Integration Test")
    print(f" Target: Valley Map, Server Port {SERVER_PORT}")
    print(" Objectives: Vehicles, Deployables, Mission Objectives (Phases 1-9)")
    print("=================================================================\n")

    if not BINARY_PATH.is_file():
        print(f"[SKIP] Dedicated engine binary not found at {BINARY_PATH}. Skipping test (exit code 77).")
        return 77
    # the engine refuses to start without pak000-pak008 (the retail data patched to 1.5)
    if not DATA_PATH.is_dir() or h.missing_required_paks(DATA_PATH):
        print(f"[SKIP] ETQW retail data not found or incomplete at {DATA_PATH} (missing: {', '.join(h.missing_required_paks(DATA_PATH)) or 'the directory'}). Skipping valley match test (exit code 77).")
        return 77

    output_queue = queue.Queue()

    # -------------------------------------------------------------
    # Phase 1: Boot Authoritative Dedicated Server on Valley
    # -------------------------------------------------------------
    print("[Phase 1] Spawning dedicated server hosting 'valley'...")
    server_proc = h.spawn_engine(
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
            raise RuntimeError(f"Server exited prematurely with code {server_proc.returncode}")
        try:
            source, line = output_queue.get(timeout=0.2)
            if "StartFirstObjective" in line or "Loading Map Bot Actions" in line:
                print(f"  [{source}] Map loaded: {line}")
                server_ready = True
                break
            elif "Map:" in line or "Game initialized" in line or "Loading AAS" in line:
                print(f"  [{source}] {line}")
        except queue.Empty:
            continue

    if not server_ready:
        server_proc.kill()
        raise TimeoutError("Dedicated server failed to load Valley within 60s")

    # A match that is still in warmup cannot end (retail rules), so start it
    # before playing the objectives. g_warmup 0 makes the countdown immediate.
    send_cmd(server_proc, "admin startMatch")
    game_on = False
    gs_start = time.time()
    while time.time() - gs_start < 30.0:
        time.sleep(1.0)
        try:
            status = query_objective_status(server_proc, output_queue, timeout=3.0)
        except TimeoutError:
            continue
        if status["state"] == 3:
            game_on = True
            break
        if status["state"] == 1:
            # 'Loading Map Bot Actions' prints before the map has finished loading, and
            # the rules reset the admin start request at map start: ask again in warmup
            send_cmd(server_proc, "admin startMatch")
    if not game_on:
        raise AssertionError("Match did not reach GS_GAMEON (state 3) after 'admin startMatch'")
    print("  Match started: gameState 3 (GS_GAMEON)")
    print("  -> Phase 1 Passed: Server online and Valley map initialized.\n")

    # -------------------------------------------------------------
    # Phase 2: Bot Spawning & Ground Touchdown
    # -------------------------------------------------------------
    print("[Phase 2] Spawning opposing GDF and Strogg bots and verifying pawns...")
    send_cmd(server_proc, "addbot gdf soldier")
    time.sleep(1.0)
    send_cmd(server_proc, "addbot strogg soldier")
    time.sleep(1.0)

    # Wait until both bots touch ground and clear spectator flags
    bot0_grounded = False
    bot1_grounded = False
    p2_start = time.time()
    while time.time() - p2_start < 50.0:
        if server_proc.poll() is not None:
            raise RuntimeError(f"Server died during bot spawn: {server_proc.returncode}")
        time.sleep(0.5)
        try:
            pos0 = query_player_pos(server_proc, output_queue, client_num=0, timeout=3.0)
            pos1 = query_player_pos(server_proc, output_queue, client_num=1, timeout=3.0)
        except Exception:
            continue

        if pos0["spectating"] == 0 and pos0["ground"] == 1:
            if not bot0_grounded:
                print(f"  GDF Bot 0 touched ground at {pos0['org']} (spectating={pos0['spectating']})")
            bot0_grounded = True

        if pos1["spectating"] == 0 and pos1["ground"] == 1:
            if not bot1_grounded:
                print(f"  Strogg Bot 1 touched ground at {pos1['org']} (spectating={pos1['spectating']})")
            bot1_grounded = True

        if bot0_grounded and bot1_grounded:
            break

    if not bot0_grounded or not bot1_grounded:
        raise AssertionError(f"Bots failed to establish ground contact (bot0={bot0_grounded}, bot1={bot1_grounded})")

    print("  -> Phase 2 Passed: GDF and Strogg bots physically spawned and grounded.\n")

    # -------------------------------------------------------------
    # Phase 3: Vehicle Possession, Seat Swapping & Safe Egress
    # -------------------------------------------------------------
    print("[Phase 3] Testing vehicle possession, seat management, and egress...")
    # Client 0 (the GDF bot) enters the GDF Titan (by entityDef: entity names carry
    # the entity number). The nearest vehicle depended on where the bot spawned and
    # was sometimes the Platypus boat or the Strogg Icarus, which the bot left again
    # at once (1 in 3 runs failed here).
    send_cmd(server_proc, "enterVehicle 0 vehicle_titan")
    time.sleep(1.0)

    v_status1 = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    print(f"  Vehicle Status after enter: in_vehicle={v_status1['in_vehicle']} name={v_status1.get('name')} seat={v_status1.get('seat')} health={v_status1.get('health')}")
    if v_status1["in_vehicle"] != 1:
        raise AssertionError(f"Expected Bot 0 to be in vehicle, got: {v_status1}")
    if v_status1["health"] <= 0:
        raise AssertionError(f"Vehicle health must be > 0, got {v_status1['health']}")

    initial_seat = v_status1["seat"]

    # Swap vehicle seat. SWAP_SEAT reports the seat in the same frame as the swap: a
    # bot alone in a gunner seat takes the driver's seat back a moment later
    # (becomeDriver in its vehicle AI; bot_pause cannot be set in multiplayer).
    send_cmd(server_proc, "swapVehicleSeat 0")
    swapped_seat = None
    start = time.time()
    while time.time() - start < 5.0 and swapped_seat is None:
        try:
            source, line = output_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        m = SWAP_SEAT_PATTERN.search(line) if source == "SERVER" else None
        if m and int(m.group(1)) == 0:
            swapped_seat = int(m.group(2))
    print(f"  Seat after swapVehicleSeat: {swapped_seat} (was {initial_seat})")
    if swapped_seat is None or swapped_seat == initial_seat:
        raise AssertionError(f"Seat swap did not move Bot 0 out of seat {initial_seat} (SWAP_SEAT seat {swapped_seat})")
    time.sleep(0.5)
    v_status2 = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    print(f"  Vehicle Status half a second later: seat={v_status2.get('seat')} (the bot may have taken the driver's seat back)")

    # Exit vehicle
    send_cmd(server_proc, "exitVehicle 0")
    time.sleep(0.5)
    v_status3 = query_vehicle_status(server_proc, output_queue, client_num=0, timeout=5.0)
    print(f"  Vehicle Status after exit: in_vehicle={v_status3['in_vehicle']}")
    if v_status3["in_vehicle"] != 0:
        raise AssertionError(f"Expected Bot 0 to have exited vehicle, got: {v_status3}")

    print("  -> Phase 3 Passed: Vehicle entry, seat swap, and exit verified.\n")

    # -------------------------------------------------------------
    # Phase 4: Deployable Placement, Delivery & Turret Engagement
    # -------------------------------------------------------------
    print("[Phase 4] Testing deployable placement, aerial carrier delivery, and turret system...")
    initial_turrets = query_deploy_status(server_proc, output_queue, timeout=5.0)
    print(f"  Initial active defence turrets on Valley: {initial_turrets}")

    # Request GDF anti-personnel turret deployment near GDF base
    send_cmd(server_proc, "deploy 0 deployobject_antipersonnel_gdf 8800 -8000 768 0")

    # Look for deployment success and carrier delivery hook
    deploy_line = wait_for_line(output_queue, "DEPLOY: Client 0 deployed", timeout=5.0)
    if not deploy_line or "SUCCESS" not in deploy_line:
        raise AssertionError(f"Deployment request failed: {deploy_line}")
    print(f"  Deployment Request Accepted: {deploy_line}")

    deliver_line = wait_for_line(output_queue, "[DEPLOY_DELIVER]", timeout=15.0)
    if not deliver_line:
        raise AssertionError("Timed out waiting for [DEPLOY_DELIVER] carrier hook")
    print(f"  Carrier Delivery Hook Triggered: {deliver_line}")

    # Verify turret registered
    time.sleep(1.0)
    updated_turrets = query_deploy_status(server_proc, output_queue, timeout=5.0)
    print(f"  Updated defence turrets count: {updated_turrets}")
    if updated_turrets < initial_turrets:
        raise AssertionError(f"Turret count decreased: was {initial_turrets}, now {updated_turrets}")

    print("  -> Phase 4 Passed: Deployable validated, carrier delivered, turret active.\n")

    # -------------------------------------------------------------
    # Phase 5: Objective 0 - Bridge Construction
    # -------------------------------------------------------------
    print("[Phase 5] Completing Objective 0: Construct Bridge...")
    send_cmd(server_proc, "completeObjective 0")
    obj0_line = wait_for_line(output_queue, "COMPLETE_OBJECTIVE: 0 triggered", timeout=5.0)
    if not obj0_line:
        raise AssertionError("Failed to trigger objective 0")
    print(f"  {obj0_line}")

    time.sleep(1.0)
    obj_stat0 = query_objective_status(server_proc, output_queue, timeout=5.0)
    print(f"  Status: gdf_obj={obj_stat0['gdf_obj']} strogg_obj={obj_stat0['strogg_obj']} state={obj_stat0['state']} winner={obj_stat0['winner']}")
    if obj_stat0["gdf_obj"] < 1:
        raise AssertionError(f"Expected gdf_obj >= 1, got {obj_stat0['gdf_obj']}")

    print("  -> Phase 5 Passed: Bridge constructed, GDF objective advanced to 1.\n")

    # -------------------------------------------------------------
    # Phase 6: Objective 1 - Forward Outpost Capture
    # -------------------------------------------------------------
    print("[Phase 6] Completing Objective 1: Capture Outpost...")
    send_cmd(server_proc, "completeObjective 1")
    obj1_line = wait_for_line(output_queue, "COMPLETE_OBJECTIVE: 1 triggered", timeout=5.0)
    if not obj1_line:
        raise AssertionError("Failed to trigger objective 1")
    print(f"  {obj1_line}")

    time.sleep(1.0)
    obj_stat1 = query_objective_status(server_proc, output_queue, timeout=5.0)
    print(f"  Status: gdf_obj={obj_stat1['gdf_obj']} strogg_obj={obj_stat1['strogg_obj']} state={obj_stat1['state']} winner={obj_stat1['winner']}")
    if obj_stat1["gdf_obj"] < 2:
        raise AssertionError(f"Expected gdf_obj >= 2, got {obj_stat1['gdf_obj']}")

    print("  -> Phase 6 Passed: Outpost captured, GDF objective advanced to 2.\n")

    # -------------------------------------------------------------
    # Phase 7: Objective 2 - Shield Generator Destruction
    # -------------------------------------------------------------
    print("[Phase 7] Completing Objective 2: Hack Shield Generator & SCUD Strike...")
    send_cmd(server_proc, "completeObjective 2")
    obj2_line = wait_for_line(output_queue, "COMPLETE_OBJECTIVE: 2 triggered", timeout=5.0)
    if not obj2_line:
        raise AssertionError("Failed to trigger objective 2")
    print(f"  {obj2_line}")

    time.sleep(1.0)
    obj_stat2 = query_objective_status(server_proc, output_queue, timeout=5.0)
    print(f"  Status: gdf_obj={obj_stat2['gdf_obj']} strogg_obj={obj_stat2['strogg_obj']} state={obj_stat2['state']} winner={obj_stat2['winner']}")
    if obj_stat2["gdf_obj"] < 3:
        raise AssertionError(f"Expected gdf_obj >= 3, got {obj_stat2['gdf_obj']}")

    print("  -> Phase 7 Passed: Shield hacked, blast doors breached, GDF objective advanced to 3.\n")

    # -------------------------------------------------------------
    # Phase 8: Objective 3 - Contaminator Core & Match Victory
    # -------------------------------------------------------------
    print("[Phase 8] Completing Objective 3: Destroy Contaminator...")
    send_cmd(server_proc, "completeObjective 3")
    obj3_line = wait_for_line(output_queue, "COMPLETE_OBJECTIVE: 3 triggered", timeout=5.0)
    if not obj3_line:
        raise AssertionError("Failed to trigger objective 3")
    print(f"  {obj3_line}")

    # Wait for FinalEffectsThread and 1000ms delay to GS_GAMEREVIEW
    print("  Waiting for match end sequence and state transition to GS_GAMEREVIEW (state 4)...")
    time.sleep(2.5)

    obj_stat3 = query_objective_status(server_proc, output_queue, timeout=5.0)
    print(f"  Final Match Status: gdf_obj={obj_stat3['gdf_obj']} strogg_obj={obj_stat3['strogg_obj']} state={obj_stat3['state']} winner={obj_stat3['winner']}")

    if obj_stat3["winner"] != "gdf":
        raise AssertionError(f"Expected match winner 'gdf', got '{obj_stat3['winner']}'")
    if obj_stat3["state"] != 4:  # GS_GAMEREVIEW
        raise AssertionError(f"Expected gameState 4 (GS_GAMEREVIEW), got {obj_stat3['state']}")

    print("  -> Phase 8 Passed: Contaminator destroyed, GDF declared winner, match transitioned to GS_GAMEREVIEW.\n")

    # -------------------------------------------------------------
    # Phase 9: Graceful Dedicated Server Shutdown
    # -------------------------------------------------------------
    print("[Phase 9] Sending 'quit' command and verifying clean shutdown...")
    send_cmd(server_proc, "quit")

    shutdown_clean = False
    p9_start = time.time()
    while time.time() - p9_start < 5.0:
        ret = server_proc.poll()
        if ret is not None:
            if ret == 0:
                print(f"  Dedicated server exited cleanly with code {ret}.")
                shutdown_clean = True
            else:
                raise RuntimeError(f"Server exited with non-zero code {ret}")
            break
        time.sleep(0.2)

    if not shutdown_clean:
        server_proc.kill()
        raise TimeoutError("Dedicated server failed to exit within 5s after 'quit'")

    print("  -> Phase 9 Passed: Server shut down gracefully with exit code 0.\n")
    print("=================================================================")
    print(" ALL 9 PHASES OF MILESTONE 4 VALLEY MATCH TEST PASSED!")
    print("=================================================================")
    return 0


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", help="write the server's output to this file")
    LOG_PATH = ap.parse_args().log
    try:
        exit_with_scan(run_valley_match_test())
    except Exception as e:
        print(f"\n[FATAL TEST FAILURE]: {e}", file=sys.stderr)
        sys.exit(1)
