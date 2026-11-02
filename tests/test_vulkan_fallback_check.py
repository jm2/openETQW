#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Vulkan client tests fail when the client ran on OpenGL instead (#414).

A test that asked for Vulkan used to pass when the client fell back to OpenGL: on a
macOS host without MoltenVK the HUD minimap test passed on OpenGL's numbers.
VulkanClient (test_client_network.py) now returns a failing exit status from quit()
when its log has the fallback line and it was asked for Vulkan.

These are synthetic logs: no engine, no retail data, no ports. Pinned here:
  * a client asked for Vulkan (the default) whose log has either fallback line fails,
    and its status names the line;
  * the same client with no fallback line keeps its own exit status;
  * a client asked for OpenGL is never flagged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402

failures: list[str] = []


def expect(ok: bool, what: str) -> None:
    if not ok:
        print("FAIL: " + what)
        failures.append(what)


class _ExitedProc:
    """A process that has already exited with the given code."""

    def __init__(self, code: int) -> None:
        self.returncode = code

    def poll(self) -> int:
        return self.returncode


# the two fallback lines the client prints (src/sys/sdl/sdl_glimp.cpp,
# src/renderer/RenderSystem_init.cpp)
WINDOW_FALLBACK = "SDL3: failed to create a Vulkan window (no driver); falling back to OpenGL."
INIT_FALLBACK = "Vulkan initialization failed; falling back to OpenGL for this run."


CLIENTS: list[VulkanClient] = []


def client(lines: list[str], cvars: dict | None = None, code: int = 0) -> VulkanClient:
    c = VulkanClient(Path("openETQW-client"), Path("data"), 29999, map_name="valley", cvars=cvars)
    c.lines = [h.LogLine(0.0, s) for s in lines]
    c.proc = _ExitedProc(code)
    c._reader = None
    CLIENTS.append(c)  # cleanup() removes the private savepath each one made
    return c


def main() -> int:
    for line in (WINDOW_FALLBACK, INIT_FALLBACK):
        c = client(["SDL3 OpenGL window created", line, "Spawning entities"])
        expect(c.opengl_fallback() == line, f"the fallback line is found: {line}")
        code = c.quit()
        expect(code is not None and int(code) != 0, f"a Vulkan client that fell back fails ({code!r})")
        expect(line in str(code), "the failing status names the fallback line")

    c = client(["Spawning entities"], code=0)
    code = c.quit()
    expect(c.opengl_fallback() is None and int(code) == 0, "no fallback: exit 0 stays 0")
    c = client(["Spawning entities"], code=3)
    expect(int(c.quit()) == 3, "no fallback: a client's own exit status is kept")

    c = client(["SDL3 OpenGL window created", WINDOW_FALLBACK], cvars={"r_renderAPI": "opengl"})
    expect(c.opengl_fallback() is None and int(c.quit()) == 0, "a client asked for OpenGL is not flagged")

    c = client(["SDL3 OpenGL window created", WINDOW_FALLBACK], code=-9)
    code = c.quit()
    expect(int(code) == -9, "a killed client that fell back keeps its own code")
    c.proc.returncode = h.ExitStatus(-9, "-9; killed after the 'quit' timeout of 45 s")
    expect("timeout" in str(c.quit()), "the failing status keeps the kill reason")

    for c in CLIENTS:
        c.cleanup()

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: a Vulkan client test fails when the client ran on OpenGL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
