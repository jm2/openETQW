#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Render API selection: Vulkan by default, OpenGL fallback when Vulkan is unavailable.

r_renderAPI defaults to "vulkan" on every platform.  When Vulkan cannot
initialize, the engine falls back to OpenGL for the run instead of failing
(r_renderAPI is not modified; a user who wants OpenGL only sets it).

Phases (client startup + quick-quit; the messages all appear during init). Each
run gets its own empty fs_savepath: r_renderAPI is archived, so a shared
base/etqwconfig.cfg would carry one run's (or a player's) setting into the next.
1. Default run: a Vulkan window is created and no fallback is printed. On a host
   whose Vulkan cannot initialize, the fallback message is accepted instead (it is
   printed only when r_renderAPI is vulkan, so it still shows the default).
2. `+set r_renderAPI opengl`: an OpenGL window is created, the legacy GL
   path runs with a real context (GL_MAX_TEXTURE_SIZE is queried, which is
   where the pre-fallback code died with "Max Texture Size greater than 512"
   because the SDL3 layer never created a GL context), and no fallback is
   printed.
3. Vulkan hidden (the loader is given no ICDs): the engine prints the
   fallback and creates an OpenGL window that initializes like phase 2.

Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import etqw_harness as h


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = h.env_data_path()


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


def run_client(*extra_args: str, env_overrides: dict[str, str] | None = None) -> tuple[int, str]:
    savepath = Path(tempfile.mkdtemp(prefix="etqw-render-api-"))
    try:
        return _run_client(savepath, *extra_args, env_overrides=env_overrides)
    finally:
        shutil.rmtree(savepath, ignore_errors=True)


def _run_client(savepath: Path, *extra_args: str, env_overrides: dict[str, str] | None = None) -> tuple[int, str]:
    args = [
        str(CLIENT_BIN),
        "+set", "fs_basepath", str(DATA_PATH),
        "+set", "fs_game", "base",
        "+set", "fs_savepath", str(savepath),
        *extra_args,
        "+quit",
    ]
    env = dict(os.environ)
    if sys.platform.startswith("linux") and "DISPLAY" not in env and "WAYLAND_DISPLAY" not in env \
            and "SDL_VIDEO_DRIVER" not in env:
        # headless host: the offscreen driver still creates real GL/Vulkan windows
        env["SDL_VIDEO_DRIVER"] = "offscreen"
    if env_overrides:
        env.update(env_overrides)
    proc = subprocess.run(
        args,
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        env=env,
    )
    return proc.returncode, proc.stdout


def max_texture_size(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("GL_MAX_TEXTURE_SIZE:"):
            return int(line.split(":", 1)[1].strip())
    return 0


def check_gl_initialised(output: str, phase: str) -> None:
    """The legacy GL path ran against a real context: no null-context failure."""
    assert "GL_VENDOR:" in output, f"{phase}: missing GL_VENDOR (legacy GL path did not run)"
    assert "Max Texture Size greater than 512" not in output, (
        f"{phase}: GL queries ran without a context (GL_MAX_TEXTURE_SIZE <= 512)"
    )
    size = max_texture_size(output)
    assert size > 512, f"{phase}: GL_MAX_TEXTURE_SIZE is {size}, expected > 512"
    print(f"  GL_VENDOR present, GL_MAX_TEXTURE_SIZE: {size}")


def test_default_is_vulkan() -> None:
    print("=================================================================")
    print(" Phase 1: default r_renderAPI is vulkan")
    print("=================================================================")
    code, out = run_client()
    print(f"  exit code: {code}")
    if "falling back to OpenGL" in out:
        # Vulkan cannot initialize on this host; the fallback is printed only when
        # r_renderAPI is vulkan, so the default is still what this phase checks
        assert "SDL3 OpenGL window created" in out, "Phase 1: fallback without an OpenGL window"
        check_gl_initialised(out, "Phase 1")
        print("  Vulkan unavailable on this host: the default tried Vulkan and fell back to OpenGL")
    else:
        assert "SDL3 Vulkan window created" in out, "Phase 1: expected a Vulkan window by default"
        print("  Vulkan window created, no fallback")
    assert code == 0, f"Phase 1: expected exit 0, got {code}"


def test_explicit_opengl() -> None:
    print("=================================================================")
    print(" Phase 2: +set r_renderAPI opengl")
    print("=================================================================")
    code, out = run_client("+set", "r_renderAPI", "opengl")
    print(f"  exit code: {code}")
    assert "SDL3 OpenGL window created" in out, "Phase 2: expected an OpenGL window"
    assert "SDL3 Vulkan window created" not in out, "Phase 2: Vulkan window despite r_renderAPI opengl"
    assert "falling back to OpenGL" not in out, "Phase 2: explicit opengl is not a fallback"
    check_gl_initialised(out, "Phase 2")
    assert code == 0, f"Phase 2: expected exit 0, got {code}"


def test_fallback_without_vulkan() -> None:
    print("=================================================================")
    print(" Phase 3: Vulkan unavailable -> automatic OpenGL fallback")
    print("=================================================================")
    code, out = run_client(env_overrides={
        # Keep the Vulkan loader from finding any driver: SDL's own library
        # hint first (deterministic on every platform), and no ICDs either.
        "SDL_VULKAN_LIBRARY": os.devnull,
        "VK_ICD_FILENAMES": os.devnull,
        "VK_DRIVER_FILES": os.devnull,
    })
    print(f"  exit code: {code}")
    assert "falling back to OpenGL" in out, (
        "Phase 3: expected the fallback message with Vulkan unavailable"
    )
    assert "SDL3 OpenGL window created" in out, "Phase 3: expected an OpenGL window after the fallback"
    check_gl_initialised(out, "Phase 3")
    assert code == 0, f"Phase 3: expected exit 0, got {code}"


def main() -> int:
    print("=================================================================")
    print(" Render API Selection Test (vulkan default, OpenGL fallback)")
    print(f" Binary: {CLIENT_BIN}")
    print(f" Retail Data: {DATA_PATH}")
    print("=================================================================")

    if not CLIENT_BIN.is_file():
        print(f"[SKIP] Client binary not found at {CLIENT_BIN}. Skipping (exit code 77).")
        return 77

    if not DATA_PATH.is_dir() or h.missing_required_paks(DATA_PATH):
        print("[SKIP] ETQW retail data not found or incomplete at "
              f"{DATA_PATH} (missing: {', '.join(h.missing_required_paks(DATA_PATH)) or 'the directory'}). "
              "Skipping (exit code 77).")
        return 77

    try:
        test_default_is_vulkan()
        test_explicit_opengl()
        test_fallback_without_vulkan()
    except Exception as exc:
        print(f"\n[FAILED] render API test error: {exc}", file=sys.stderr)
        return 1

    print("=================================================================")
    print(" ALL RENDER API TESTS COMPLETED SUCCESSFULLY (EXIT 0)")
    print("=================================================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
