#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Automated Client Lifecycle and Graphics Verification Test for OpenETQW.

Validates:
1. Quick startup, swapchain initialization, SDL3 audio initialization (on a host with an audio output; see audio_check), and clean quit (+quit) with exit code 0.
2. Full Valley map loading, MegaTexture terrain streaming, world geometry rendering, continuous Vulkan frame presentation, and clean shutdown (SIGTERM on POSIX, stdin 'quit' on Windows) with exit code 0.
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


NO_AUDIO_DEVICE = "SDL3: failed to open audio stream: No default audio device available"


def audio_check(output: str) -> str | None:
    """The phase 1 audio check: None when it passes, else the failure.

    A host with no audio output (no speakers or headset, or a sleeping display whose
    HDMI audio has gone) has no default playback device. The engine warns and plays
    on, and that is the machine, not the engine, so it is reported rather than failed.
    This covers SDL's no-default-device error, as seen on Windows. A host whose audio
    subsystem fails earlier (e.g. "failed to initialize audio subsystem" on Linux with
    no output) and any other audio failure still fail, with SDL's message quoted."""
    if "SDL3 Audio initialized" in output:
        return None
    if NO_AUDIO_DEVICE in output:
        print("[NOTE] this host has no default audio output device: SDL3 audio was not opened "
              "(reported, not failed)")
        return None
    sdl_errors = [line.strip() for line in output.splitlines() if "SDL3: failed" in line]
    return "Missing SDL3 audio initialization message" + (
        f" ({'; '.join(sdl_errors)})" if sdl_errors else "")


CLIENT_BIN = h.build_binary("client")
# archived cvars this test sets must not reach other runs (etqw_harness.private_savepath)
SAVEPATH = h.private_savepath("etqw-client-test-")


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


def test_quick_quit() -> None:
    print("=================================================================")
    print(" Phase 1: Client Quick-Quit Lifecycle Test")
    print(f" Command: {CLIENT_BIN.name} +quit")
    print("=================================================================")

    args = [
        str(CLIENT_BIN),
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_savepath", str(SAVEPATH),
        "+set", "fs_game", "base",
        "+quit",
    ]

    start_time = time.time()
    proc = h.spawn_engine(
        args,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output_lines: list[str] = []
    out_q: queue.Queue[str] = queue.Queue()
    t = threading.Thread(target=stream_reader, args=(proc.stdout, out_q), daemon=True)
    t.start()

    while time.time() - start_time < 30.0:
        if proc.poll() is not None:
            break
        try:
            line = out_q.get(timeout=0.2)
            output_lines.append(line)
        except queue.Empty:
            continue

    # Drain remaining lines
    while True:
        try:
            line = out_q.get_nowait()
            output_lines.append(line)
        except queue.Empty:
            break

    ret = proc.poll()
    if ret is None:
        proc.kill()
        raise TimeoutError("Client quick-quit timed out after 30 seconds")

    full_output = "\n".join(output_lines)

    if ret != 0:
        print("\n--- Output on failure ---")
        print(full_output[-3000:])
        raise RuntimeError(f"Client quick-quit exited with non-zero exit code: {ret}")

    # Assertions on output
    audio_failure = audio_check(full_output)
    assert audio_failure is None, audio_failure
    assert "renderSystem initialized" in full_output, "Missing renderSystem initialization message"
    assert "game initialized" in full_output, "Missing game initialized message"
    assert "Vulkan frames presented:" in full_output, "Missing Vulkan presentation summary"

    audio_note = " (audio: no device on this host)" if "SDL3 Audio initialized" not in full_output else ""
    print(f"[PASSED] Phase 1: Client initialized and quit cleanly in {time.time() - start_time:.2f}s "
          f"(exit code 0){audio_note}.\n")


def test_valley_render_lifecycle() -> None:
    print("=================================================================")
    print(" Phase 2: Valley Map Rendering & MegaTexture Streaming Lifecycle")
    print(" Map: maps/valley.world, MegaTexture: megatextures/valley_lit.mega")
    print("=================================================================")

    args = [
        str(CLIENT_BIN),
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_savepath", str(SAVEPATH),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+devmap", "valley",
        "+clientTeam", "gdf",
        "+clientClass", "soldier",
        "+closeLimbo",
    ]

    start_time = time.time()
    proc = h.spawn_engine(
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
            raise RuntimeError(f"Client exited unexpectedly before completion with code {ret}")

        try:
            line = out_q.get(timeout=0.2)
            output_lines.append(line)
            # Log significant milestones
            if "Loading map: maps/valley.world" in line or line.startswith("Map: maps/valley.world"):
                print(f"  [CLIENT] {line}")
                map_loaded = True
                map_load_time = time.time()
            elif "InitFromMegaFile:" in line:
                print(f"  [CLIENT] {line}")
            elif is_active_marker(line):
                if game_running_time is None:
                    print(f"  [CLIENT] Player spawned and game active in Valley ({line})")
                    game_running_time = time.time()
            elif "Game initialized" in line:
                print(f"  [CLIENT] {line}")
        except queue.Empty:
            pass

        # the player must actually spawn (is_active_marker): a loaded map alone is not enough

        # Once player is spawned and game is running, let it render for 6 seconds
        if game_running_time is not None and not sigterm_sent:
            elapsed_in_game = time.time() - game_running_time
            if elapsed_in_game >= 6.0:
                request = h.graceful_terminate(proc)
                print(f"  [TEST] Rendered in Valley for {elapsed_in_game:.1f}s. Sent {request} for graceful shutdown...")
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

    # Extract Vulkan metrics summary
    # Format: Vulkan frames presented: %llu, world draws: %llu, GUI draws: %llu
    # and: special draws: ... MegaTexture, %llu
    frames_match = re.search(r"Vulkan frames presented:\s*(\d+),\s*world draws:\s*(\d+)", full_output)
    mega_match = re.search(r"(\d+)\s+MegaTexture", full_output)

    assert frames_match is not None, "Failed to locate Vulkan frames presented in output metrics"
    frames = int(frames_match.group(1))
    world_draws = int(frames_match.group(2))
    mega_draws = int(mega_match.group(1)) if mega_match else 0

    print(f"\n[METRICS SUMMARY]")
    print(f"  Total Vulkan frames presented: {frames}")
    print(f"  Total 3D world draws:          {world_draws}")
    print(f"  Total MegaTexture draws:       {mega_draws}")
    print(f"  Client exit code:              {exit_code}")

    assert frames >= 50, f"Expected at least 50 Vulkan frames presented, got {frames}"
    assert world_draws >= 100, f"Expected at least 100 world draws, got {world_draws}"
    assert mega_draws >= 100, f"Expected at least 100 MegaTexture draws, got {mega_draws}"

    print("[PASSED] Phase 2: Valley rendering, MegaTexture streaming, and clean SIGTERM shutdown verified.\n")


def main() -> int:
    print("=================================================================")
    print(" OpenETQW Client Lifecycle Test")
    print(f" Binary: {CLIENT_BIN}")
    print(f" Retail Data: {DATA_PATH}")
    print("=================================================================")

    if not CLIENT_BIN.is_file():
        print(f"[SKIP] Client binary not found at {CLIENT_BIN}. Skipping lifecycle test (exit code 77).")
        return 77

    # the engine refuses to start without pak000-pak008 (the retail data patched to 1.5)
    if not DATA_PATH.is_dir() or h.missing_required_paks(DATA_PATH):
        print(f"[SKIP] ETQW retail data not found or incomplete at {DATA_PATH} (missing: {', '.join(h.missing_required_paks(DATA_PATH)) or 'the directory'}). Skipping lifecycle test (exit code 77).")
        return 77

    try:
        test_quick_quit()
        test_valley_render_lifecycle()
    except Exception as exc:
        print(f"\n[FAILED] Lifecycle test error: {exc}", file=sys.stderr)
        return 1

    print("=================================================================")
    print(" ALL CLIENT LIFECYCLE TESTS COMPLETED SUCCESSFULLY (EXIT 0)")
    print("=================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
