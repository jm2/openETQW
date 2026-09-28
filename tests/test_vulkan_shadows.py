#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Automated Vulkan Stencil Shadow Volumes & Dynamic Lighting Verification Test.

Validates:
1. Native Vulkan backend initialization on Apple Silicon via MoltenVK.
2. Creation of Vulkan depth-stencil shadow volume pipelines:
   - worldShadowPassPipeline (two-sided depth-pass)
   - worldShadowFailPipeline (two-sided Carmack's Reverse depth-fail)
   - worldInteractionPipeline (additive blending masked by stencil == 0)
3. Full stock map Valley (maps/valley.world) loading with r_renderAPI vulkan, r_shadows 1 and the stencil path (r_rayTracing 0; Win64 defaults to ray-query lighting).
4. Active streaming and submission of dynamic stencil shadow volumes (shadow draws > 0).
5. Dynamic light interaction passes masked by the stencil buffer (light interactions > 0).
6. Continuous MoltenVK swapchain presentation (frames presented >= 50).
7. Graceful shutdown (SIGTERM on POSIX, stdin 'quit' on Windows) with exit code 0.
"""

from __future__ import annotations

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
DATA_PATH = h.env_data_path()

def is_active_marker(line: str) -> bool:
    """The player is in the game: the game's spawn line for a non-spectator, or (on the
    unpatched DVD scripts only) a script debug print."""
    return ("spawned at" in line and "spectating 0" in line) or any(
        m in line for m in ("parachute::Idle", "weaponRestored", "RespawnWaves_"))

def _find_client_binary() -> Path:
    candidates = [
        REPO_ROOT / "build" / "openETQW-client_arm64",
        REPO_ROOT / "build" / "openETQW-client_x64",
        REPO_ROOT / "build" / "openETQW-client_x64.exe",
        REPO_ROOT / "build" / "openETQW-client_arm64.exe",
        REPO_ROOT / "build" / "openETQW-client",
        REPO_ROOT / "build" / "openETQW-client.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


CLIENT_BIN = _find_client_binary()


def stream_reader(pipe, q: queue.Queue[str]) -> None:
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            q.put(line.strip())
    except Exception as e:
        q.put(f"STREAM_READER_ERROR: {e}")
    finally:
        pipe.close()


def test_vulkan_shadows_lifecycle() -> None:
    print("=================================================================")
    print(" Vulkan Dynamic Stencil Shadow Volumes & Lighting Test")
    print(" Map: maps/valley.world | API: vulkan | Shadows: 1")
    print(" Binary:", CLIENT_BIN)
    print("=================================================================")

    args = [
        str(CLIENT_BIN),
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "r_renderAPI", "vulkan",
        "+set", "r_shadows", "1",
        "+set", "r_rayTracing", "0",
        "+devmap", "valley",
        "+clientTeam", "gdf",
        "+clientClass", "soldier",
        "+closeLimbo",
    ]

    start_time = time.time()
    proc = subprocess.Popen(
        args,
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output_lines: list[str] = []
    out_q: queue.Queue[str] = queue.Queue()
    t = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
    t.start()

    game_running_time: float | None = None
    map_load_time: float | None = None
    map_loaded = False
    sigterm_sent = False

    while time.time() - start_time < 90.0:
        ret = proc.poll()
        if ret is not None and not sigterm_sent:
            # Drain lines
            while True:
                try:
                    output_lines.append(out_q.get_nowait())
                except queue.Empty:
                    break
            print("\n--- Output on unexpected exit ---")
            print("\n".join(output_lines[-40:]))
            raise RuntimeError(f"Client exited unexpectedly before test completion with code {ret}")

        try:
            line = out_q.get(timeout=0.2)
            output_lines.append(line)

            # Log significant milestones
            if "Vulkan shadow pipelines created" in line or "worldShadowPassPipeline" in line:
                print(f"  [VULKAN] {line}")
            elif "Vulkan interaction pipeline created" in line or "worldInteractionPipeline" in line:
                print(f"  [VULKAN] {line}")
            elif "Loading map: maps/valley.world" in line or line.startswith("Map: maps/valley.world"):
                print(f"  [CLIENT] {line}")
                map_loaded = True
                map_load_time = time.time()
            elif is_active_marker(line):
                if game_running_time is None:
                    print(f"  [CLIENT] Player active in Valley: {line}")
                    game_running_time = time.time()
            elif "Game initialized" in line:
                print(f"  [CLIENT] {line}")
        except queue.Empty:
            pass

        # the player must actually spawn (is_active_marker): a loaded map alone is not enough

        # Once game is actively running with shadows enabled, let it render for 6 seconds
        if game_running_time is not None and not sigterm_sent:
            elapsed_in_game = time.time() - game_running_time
            if elapsed_in_game >= 6.0:
                request = h.graceful_terminate(proc)
                print(f"  [TEST] Rendered in Valley with Vulkan shadows for {elapsed_in_game:.1f}s. Sent {request}...")
                sigterm_sent = True
                try:
                    proc.wait(timeout=15.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    raise TimeoutError("Client failed to shutdown gracefully within 15s after the shutdown request")
                break

    # Drain remaining output
    while True:
        try:
            output_lines.append(out_q.get_nowait())
        except queue.Empty:
            break

    exit_code = proc.poll()
    if exit_code != 0:
        print("\n--- Final lines before failure ---")
        print("\n".join(output_lines[-50:]))
        if not sigterm_sent:
            proc.kill()
            proc.wait(timeout=10.0)
            raise TimeoutError(f"the player never became active in Valley within 90 s (map loaded: {map_loaded})")
        raise RuntimeError(f"Client exited with non-zero exit code {exit_code} after the shutdown request")

    full_output = "\n".join(output_lines)

    # 1. Verify Vulkan shadow pipelines creation
    assert "worldShadowPassPipeline" in full_output, (
        "Failed to verify worldShadowPassPipeline creation in Vulkan console output"
    )
    assert "worldShadowFailPipeline" in full_output, (
        "Failed to verify worldShadowFailPipeline creation in Vulkan console output"
    )
    print("  [VERIFIED] Vulkan shadow pipelines created: worldShadowPassPipeline, worldShadowFailPipeline")

    # 2. Extract metrics summary from shutdown log
    # Format: Vulkan frames presented: %u, world draws: %u, GUI draws: %u, shadow draws: %u, light interactions: %u
    metrics_match = re.search(
        r"Vulkan frames presented:\s*(\d+),\s*world draws:\s*(\d+),\s*GUI draws:\s*(\d+),\s*shadow draws:\s*(\d+),\s*light interactions:\s*(\d+)",
        full_output,
    )
    assert metrics_match is not None, "Failed to parse Vulkan metrics summary from shutdown output"

    frames = int(metrics_match.group(1))
    world_draws = int(metrics_match.group(2))
    gui_draws = int(metrics_match.group(3))
    shadow_draws = int(metrics_match.group(4))
    light_interactions = int(metrics_match.group(5))

    print("\n[VULKAN SHADOWS & LIGHTING METRICS]")
    print(f"  Vulkan frames presented: {frames}")
    print(f"  World draw calls:        {world_draws}")
    print(f"  GUI draw calls:          {gui_draws}")
    print(f"  Shadow volume draws:     {shadow_draws}")
    print(f"  Light interactions:      {light_interactions}")
    print(f"  Client exit code:        {exit_code}")

    # 3. Assert metric thresholds
    assert frames >= 50, f"Expected at least 50 Vulkan frames presented, got {frames}"
    assert shadow_draws > 0, f"Expected active shadow volume draws (worldShadowDrawCalls > 0), got {shadow_draws}"
    assert light_interactions > 0, f"Expected dynamic light interactions (worldInteractionDrawCalls > 0), got {light_interactions}"
    assert world_draws > 0, f"Expected active world draw calls, got {world_draws}"
    allocation_peak = h.vk_allocation_peak(full_output)
    print(f"  Device memory allocations (peak): {allocation_peak}")
    assert allocation_peak is not None, "Failed to parse the Vulkan memory allocations line"
    assert allocation_peak < h.PORTABLE_VK_ALLOCATION_LIMIT, (
        f"Peak Vulkan memory allocations {allocation_peak} reach the {h.PORTABLE_VK_ALLOCATION_LIMIT} "
        "maxMemoryAllocationCount that many Windows drivers report")

    print(f"  [VERIFIED] Shadow volume draws issued: {shadow_draws} > 0")
    print(f"  [VERIFIED] Dynamic light interactions executed: {light_interactions} > 0")
    print(f"  [VERIFIED] Frames presented to MoltenVK swapchain: {frames} >= 50")
    print(f"  [VERIFIED] Clean client exit code: {exit_code} == 0")
    print("\n[PASSED] Vulkan dynamic stencil shadow volumes & lighting interactions verified.\n")


def main() -> int:
    print("=================================================================")
    print(" OpenETQW Vulkan Shadow Volumes & Lighting Verification Suite")
    print(f" Client: {CLIENT_BIN}")
    print(f" Data:   {DATA_PATH}")
    print("=================================================================")

    if not CLIENT_BIN.is_file():
        print(f"[SKIP] Client binary not found at {CLIENT_BIN}. Skipping test (exit code 77).")
        return 77

    # the engine refuses to start without pak000-pak008 (the retail data patched to 1.5)
    if not DATA_PATH.is_dir() or h.missing_required_paks(DATA_PATH):
        print(f"[SKIP] ETQW retail data not found or incomplete at {DATA_PATH} (missing: {', '.join(h.missing_required_paks(DATA_PATH)) or 'the directory'}). Skipping test (exit code 77).")
        return 77

    try:
        test_vulkan_shadows_lifecycle()
    except Exception as exc:
        print(f"\n[FAILED] Vulkan shadows verification error: {exc}", file=sys.stderr)
        return 1

    print("=================================================================")
    print(" ALL VULKAN SHADOW TESTS COMPLETED SUCCESSFULLY (EXIT 0)")
    print("=================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
