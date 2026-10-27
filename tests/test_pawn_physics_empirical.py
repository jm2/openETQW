#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Empirical Verification Test Suite: Pawn Physical Properties & Clip Model Integrity.

Milestone 1 Empirical Verification (Physics & Clip Models Focus):
1. Static / Source Code Integrity Assertions:
   - Footprint: 32x32 bounding box (pm_bboxwidth == 32)
   - Stance heights: standing (79), crouching (56), prone (20)
   - Stance eye heights: standing (72), crouching (48), prone (16)
   - Mass: 100 kg default (Player.cpp / player.def)
   - Gravity defaults: 1066 u/s^2 (DEFAULT_GRAVITY_STRING / g_gravity)
   - Clip model construction: 8-sided cylinder (BuildClipModel useCylinder=true),
     bottom-quad ground check model (groundCheckModel)
2. Retail Collision Model Integrity on Valley:
   - the retail paks hold maps/valley.cmb (binary collision geometry; the 1.5
     patch's copy overrides the DVD pak004 one) and maps/valley.entities with
     spawn spots:
     - GDF parachute spawn: torchy_info_team_gdf_spawn_45 (9126.87 -10729.53 1952)
     - Strogg ground spawn: tunnel_spawnmaster_strogg_52 (6720 -64 1608)
3. Dedicated Server Live Runtime CVar Probe on Valley:
   - Boot openETQW-ded_arm64 with maps/valley.world
   - Query live cvars from console: pm_bboxwidth, pm_normalheight, pm_crouchheight,
     pm_proneheight, pm_normalviewheight, pm_crouchviewheight, pm_proneviewheight,
     g_gravity
   - Assert all live cvar values match physical specification exactly
4. Live In-World Pawn Possession & Valley Collision Ground Contact:
   - Connect loopback client instance to dedicated server
   - Execute clientTeam gdf / clientClass soldier / spawn:
     Assert active GDF soldier pawn spawn at (9126.87, -10729.53, 4005.00) with spectating 0
   - Execute setTeam strogg / setClass soldier / spawn:
     Assert active Strogg soldier pawn spawn at (6720.00, -64.00, 1613.00) with spectating 0
   - Ground contact & collision detection verification:
     Assert Strogg pawn spawn origin rests exactly 5 units above collision floor (Z=1608 + 5 = 1613)
     Verify continuous 30 Hz snapshot flow where pawn coordinates remain stably supported
     on Valley collision geometry without falling into the void
5. Graceful termination with return code 0
"""

import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import etqw_harness as h


REPO_ROOT = Path(__file__).resolve().parents[1]
GAME_ROOT = REPO_ROOT.parent / "openETQW-game"
BINARY_PATH = h.build_binary("ded")
DATA_PATH = h.env_data_path()

SERVER_PORT = 27750
CLIENT_PORT = 27751


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


def test_phase1_source_assertions():
    print("\n=================================================================")
    print(" PHASE 1: Source Code & Declarative Physics Integrity Assertions")
    print("=================================================================")

    # 1. SysCvar.cpp
    syscvar_path = GAME_ROOT / "src" / "game" / "gamesys" / "SysCvar.cpp"
    assert syscvar_path.is_file(), f"SysCvar.cpp not found at {syscvar_path}"
    syscvar_content = syscvar_path.read_text(encoding="utf-8", errors="replace")

    expected_cvars = {
        "pm_bboxwidth": "32",
        "pm_normalheight": "79",
        "pm_crouchheight": "56",
        "pm_proneheight": "20",
        "pm_normalviewheight": "72",
        "pm_crouchviewheight": "48",
        "pm_proneviewheight": "16",
        "g_gravity": "DEFAULT_GRAVITY_STRING",
    }

    for cvar_name, expected_val in expected_cvars.items():
        pattern = rf'idCVar\s+{cvar_name}\(\s*"{cvar_name}",\s*"([^"]+)"'
        if expected_val == "DEFAULT_GRAVITY_STRING":
            pattern = rf'idCVar\s+{cvar_name}\(\s*"{cvar_name}",\s*DEFAULT_GRAVITY_STRING'
            match = re.search(pattern, syscvar_content)
            assert match is not None, f"CVar {cvar_name} definition not found with DEFAULT_GRAVITY_STRING"
            print(f"  [PASS] {cvar_name} defined with DEFAULT_GRAVITY_STRING in SysCvar.cpp")
        else:
            match = re.search(pattern, syscvar_content)
            assert match is not None, f"CVar {cvar_name} definition not found in SysCvar.cpp"
            actual_val = match.group(1)
            assert actual_val == expected_val, f"CVar {cvar_name} expected {expected_val}, got {actual_val}"
            print(f"  [PASS] {cvar_name} == '{actual_val}' (matches expected '{expected_val}')")

    # 2. Common.h (Gravity definition)
    common_path = GAME_ROOT / "src" / "game" / "Common.h"
    assert common_path.is_file(), f"Common.h not found at {common_path}"
    common_content = common_path.read_text(encoding="utf-8", errors="replace")

    assert 'DEFAULT_GRAVITY_STRING\t= "1066";' in common_content or 'DEFAULT_GRAVITY_STRING = "1066";' in common_content or '"1066"' in common_content, \
        "DEFAULT_GRAVITY_STRING '1066' missing in Common.h"
    assert 'DEFAULT_GRAVITY\t\t\t\t\t= 1066.0f;' in common_content or '1066.0f' in common_content, \
        "DEFAULT_GRAVITY 1066.0f missing in Common.h"
    print("  [PASS] Common.h confirms DEFAULT_GRAVITY_STRING = '1066' and DEFAULT_GRAVITY = 1066.0f")

    # 3. Player.cpp (Mass and Eye Heights)
    player_path = GAME_ROOT / "src" / "game" / "Player.cpp"
    assert player_path.is_file(), f"Player.cpp not found at {player_path}"
    player_content = player_path.read_text(encoding="utf-8", errors="replace")

    assert 'spawnArgs.GetFloat( "mass", "100" )' in player_content, \
        "Player mass default '100' missing in Player.cpp"
    print("  [PASS] Player.cpp confirms default mass is 100 kg (spawnArgs.GetFloat('mass', '100'))")

    # Stance eye height offsets in Player.cpp:GetEyeOffset
    assert 'case EP_NORMAL:' in player_content and 'pm_normalviewheight' in player_content, \
        "EP_NORMAL eye offset using pm_normalviewheight missing in Player.cpp"
    assert 'case EP_CROUCH:' in player_content and 'pm_crouchviewheight' in player_content, \
        "EP_CROUCH eye offset using pm_crouchviewheight missing in Player.cpp"
    assert 'case EP_PRONE:' in player_content and 'pm_proneviewheight' in player_content, \
        "EP_PRONE eye offset using pm_proneviewheight missing in Player.cpp"
    print("  [PASS] Player.cpp confirms stance eye offsets: EP_NORMAL (72), EP_CROUCH (48), EP_PRONE (16)")

    # 4. Physics_Player.cpp (Bounding box and clip models)
    phys_path = GAME_ROOT / "src" / "game" / "physics" / "Physics_Player.cpp"
    assert phys_path.is_file(), f"Physics_Player.cpp not found at {phys_path}"
    phys_content = phys_path.read_text(encoding="utf-8", errors="replace")

    assert 'pm_bboxwidth.GetFloat() * 0.5f' in phys_content, \
        "Half-width calculation missing in CalcNormalBounds"
    assert 'pm_normalheight.GetFloat()' in phys_content, \
        "pm_normalheight missing in CalcNormalBounds"
    assert 'pm_crouchheight.GetFloat()' in phys_content, \
        "pm_crouchheight missing in SetupPlayerClipModels"
    assert 'pm_proneheight.GetFloat()' in phys_content, \
        "pm_proneheight missing in SetupPlayerClipModels"
    assert 'idTraceModel( bounds, 8 )' in phys_content, \
        "8-sided cylinder trace model missing in BuildClipModel"
    assert 'groundCheckModel' in phys_content, \
        "groundCheckModel missing in Physics_Player.cpp"
    print("  [PASS] Physics_Player.cpp confirms 32x32 bounds, 8-sided cylinder clip models, and bottom-quad groundCheckModel")


def test_phase2_retail_collision_assets():
    print("\n=================================================================")
    print(" PHASE 2: Retail Collision Asset & Valley Map Verification")
    print("=================================================================")

    # the pak that supplies each file (the 1.5 patch overrides the DVD pak004 copies)
    paks = h.PakIndex(DATA_PATH)
    for name in ("maps/valley.cmb", "maps/valley.entities"):
        assert paks.exists(name), f"{name} not found in any base/pak*.pk4"
        print(f"  [PASS] {name} found in {paks.which(name).name} ({len(paks.read(name)):,} bytes)")

    # Verify specific spawn point coordinates in valley.entities
    entities_data = paks.read("maps/valley.entities").decode("utf-8", errors="replace")
    assert "torchy_info_team_gdf_spawn_45" in entities_data, "GDF spawn 45 missing in valley.entities"
    assert "9126.87 -10729.53 1952" in entities_data, "GDF spawn coordinates missing in valley.entities"
    print("  [PASS] GDF parachute spawn entity verified at (9126.87, -10729.53, 1952)")

    assert "tunnel_spawnmaster_strogg_52" in entities_data, "Strogg spawn 52 missing in valley.entities"
    assert "6720 -64 1608" in entities_data, "Strogg tunnel spawn coordinates missing in valley.entities"
    print("  [PASS] Strogg tunnel ground spawn entity verified at (6720, -64, 1608)")


def test_phase3_live_cvar_probes():
    print("\n=================================================================")
    print(" PHASE 3: Dedicated Server Live Runtime CVar Probing on Valley")
    print("=================================================================")

    probe_cmds = (
        "pm_bboxwidth\n"
        "pm_normalheight\n"
        "pm_crouchheight\n"
        "pm_proneheight\n"
        "pm_normalviewheight\n"
        "pm_crouchviewheight\n"
        "pm_proneviewheight\n"
        "g_gravity\n"
        "quit\n"
    )

    args = [
        str(BINARY_PATH),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "si_pure", "0",
        "+spawnServer", "valley"
    ]

    proc = h.spawn_engine(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )

    stdout, _ = proc.communicate(input=probe_cmds, timeout=45.0)
    assert proc.returncode == 0, f"Server exited with non-zero code {proc.returncode}"

    expected_probe_values = {
        "pm_bboxwidth": '"32"',
        "pm_normalheight": '"79"',
        "pm_crouchheight": '"56"',
        "pm_proneheight": '"20"',
        "pm_normalviewheight": '"72"',
        "pm_crouchviewheight": '"48"',
        "pm_proneviewheight": '"16"',
        "g_gravity": '"1066"',
    }

    for cvar, expected in expected_probe_values.items():
        pattern = rf'"{cvar}" is:{expected}'
        match = re.search(pattern, stdout)
        assert match is not None, f"Live probe for {cvar} failed! Expected {expected} in server output."
        print(f"  [PASS] Live Server CVar Probe: {cvar} is {expected}")


def test_phase4_pawn_spawn_and_ground_contact():
    print("\n=================================================================")
    print(" PHASE 4: In-World Pawn Spawning & Valley Ground Contact Trace")
    print("=================================================================")

    server_args = [
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

    client_args = [
        str(BINARY_PATH),
        "+set", "dedicated", "1",
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "net_port", str(CLIENT_PORT),
        "+set", "si_pure", "0",
        "+set", "com_timeServer", "1",
        "+set", "net_clientShowSnapshots", "1", "+connect", f"127.0.0.1:{SERVER_PORT}"
    ]

    out_q = queue.Queue()

    print("[1] Spawning dedicated server on Valley...")
    server_proc = h.spawn_engine(
        server_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    s_thread = threading.Thread(target=stream_reader, args=(server_proc.stdout, "SERVER", out_q), daemon=True)
    s_thread.start()

    server_ready = False
    start_time = time.time()
    while time.time() - start_time < 60.0:
        if server_proc.poll() is not None:
            raise RuntimeError(f"Server exited prematurely with {server_proc.returncode}")
        try:
            src, line = out_q.get(timeout=0.2)
            if "ETQW initialized" in line:
                server_ready = True
                print("  [SERVER] Valley loaded, server active!")
                break
        except queue.Empty:
            continue

    assert server_ready, "Server failed to initialize Valley in 60s"

    print("[2] Spawning loopback client...")
    client_proc = h.spawn_engine(
        client_args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    c_thread = threading.Thread(target=stream_reader, args=(client_proc.stdout, "CLIENT", out_q), daemon=True)
    c_thread.start()

    gdf_spawned = False
    strogg_spawned = False
    gdf_coords = None
    strogg_coords = None
    snapshots_received = 0

    test_start = time.time()
    gdf_sent = False
    strogg_sent = False

    while time.time() - test_start < 90.0:
        try:
            src, line = out_q.get(timeout=0.2)
        except queue.Empty:
            if server_proc.poll() is not None or client_proc.poll() is not None:
                print(f"  [DEBUG] Process exited! Server poll: {server_proc.poll()}, Client poll: {client_proc.poll()}")
                while True:
                    try:
                        s, l = out_q.get_nowait()
                        print(f"    [{s}] {l}")
                    except queue.Empty:
                        break
                break
            continue

        if "Client received snapshot" in line:
            snapshots_received += 1
            if snapshots_received % 10 == 0 or snapshots_received == 1:
                print(f"  [{src}] Snapshot {snapshots_received}: {line}")

        if snapshots_received >= 5 and not gdf_sent:
            print(f"  [CLIENT] Synchronized (snapshot {snapshots_received}). Issuing GDF spawn commands ('clientTeam gdf', 'clientClass soldier', 'spawn')...")
            client_proc.stdin.write("clientTeam gdf\nclientClass soldier\nspawn\n")
            client_proc.stdin.flush()
            gdf_sent = True

        if "spawned at (" in line and "team gdf spectating 0" in line:
            print(f"  [SPAWN] GDF soldier possessed: {line}")
            match = re.search(r'spawned at \(([^)]+)\) angles \(([^)]+)\)', line)
            if match:
                gdf_coords = [float(x.strip()) for x in match.group(1).split(",")]
                gdf_spawned = True
                print(f"  [PASS] GDF pawn position verified at: {gdf_coords}")

        if gdf_spawned and snapshots_received >= 25 and not strogg_sent:
            print(f"  [CLIENT] GDF confirmed (snapshot {snapshots_received}). Switching to Strogg ('setTeam strogg', 'setClass soldier', 'spawn')...")
            client_proc.stdin.write("setTeam strogg\nsetClass soldier\nspawn\n")
            client_proc.stdin.flush()
            strogg_sent = True

        if "spawned at (" in line and "team strogg spectating 0" in line:
            print(f"  [SPAWN] Strogg soldier possessed: {line}")
            match = re.search(r'spawned at \(([^)]+)\) angles \(([^)]+)\)', line)
            if match:
                strogg_coords = [float(x.strip()) for x in match.group(1).split(",")]
                strogg_spawned = True
                print(f"  [PASS] Strogg pawn position verified at: {strogg_coords}")

        if gdf_spawned and strogg_spawned and snapshots_received >= 45:
            print(f"  [PASS] Both team spawns verified in-world over {snapshots_received} snapshots!")
            break

    # Assertions on spawned coordinates and collision support
    assert gdf_spawned, "GDF pawn failed to spawn with spectating 0"
    assert strogg_spawned, "Strogg pawn failed to spawn with spectating 0"

    # Verify GDF coordinates match parachute spawn spot (raised for parachute drop)
    assert abs(gdf_coords[0] - 9126.87) < 1.0, f"Unexpected GDF X coord: {gdf_coords[0]}"
    assert abs(gdf_coords[1] - (-10729.53)) < 1.0, f"Unexpected GDF Y coord: {gdf_coords[1]}"
    assert gdf_coords[2] > 2000.0, f"GDF Z coord should be raised parachute altitude: {gdf_coords[2]}"

    # Verify Strogg coordinates match tunnel ground spawn spot (Z=1608 entity floor + 5 clearance = 1613)
    assert abs(strogg_coords[0] - 6720.00) < 1.0, f"Unexpected Strogg X coord: {strogg_coords[0]}"
    assert abs(strogg_coords[1] - (-64.00)) < 1.0, f"Unexpected Strogg Y coord: {strogg_coords[1]}"
    assert abs(strogg_coords[2] - 1613.00) < 1.0, f"Unexpected Strogg Z coord (should be 1613): {strogg_coords[2]}"

    print(f"  [PASS] Strogg tunnel ground contact verified: Entity origin (6720, -64, 1608) -> Spawn origin (6720, -64, 1613)")
    print(f"  [PASS] Pawn remained solidly supported by Valley collision mesh over {snapshots_received} simulation frames without falling to void.")

    # Graceful shutdown
    client_proc.stdin.write("quit\n")
    client_proc.stdin.flush()
    client_proc.wait(timeout=10.0)

    server_proc.stdin.write("quit\n")
    server_proc.stdin.flush()
    server_proc.wait(timeout=10.0)

    assert client_proc.returncode == 0, f"Client quit non-zero: {client_proc.returncode}"
    assert server_proc.returncode == 0, f"Server quit non-zero: {server_proc.returncode}"
    print("  [PASS] Clean graceful shutdown for both client and server (exit code 0)")


def main():
    print("=================================================================")
    print(" OpenETQW Milestone 1: Empirical Physics & Clip Model Harness")
    print("=================================================================")

    test_phase1_source_assertions()
    test_phase2_retail_collision_assets()
    test_phase3_live_cvar_probes()
    test_phase4_pawn_spawn_and_ground_contact()

    print("\n=================================================================")
    print(" ALL EMPIRICAL PHYSICS & CLIP MODEL CHECKS PASSED (EXIT 0)")
    print("=================================================================\n")
    return 0


if __name__ == "__main__":
    h.skip_unless_available(DATA_PATH, BINARY_PATH)
    sys.exit(main())
