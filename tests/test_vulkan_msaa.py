#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vulkan MSAA smooths the world's edges (#423).

r_multiSamples did nothing under Vulkan: the main pass drew straight into the
swapchain image at one sample, and the options menu offered only "Off". The main
pass now draws into multisampled colour and depth targets and resolves into the
swapchain image whenever it ends, at r_multiSamples rounded down to a count the
device supports (KosmicKrisp on Apple GPUs stops at 4x).

The Vulkan client hosts valley twice from the same spawn, once with
r_multiSamples 0 and once with 16, and takes a screenshot each time
(r_vkScreenshot). Ray tracing is off (r_rayTracing 0), as it runs single-sampled
and turns MSAA off.
  M1 the 0 run logs "Vulkan MSAA: off"
  M2 the 16 run logs "Vulkan MSAA: Nx" with N the highest count of 16 or less
     that the same line says the device supports (2 or more)
  M3 the two frames show the same view: at least 85% of the pixels are within
     24 of each other in luminance (Apple M1 Max: 99.8%)
  M4 the MSAA frame's hard edges are blended: of the pixels between two
     horizontal neighbours that differ by 48 or more in luminance, the share
     that lies strictly inside their range (by more than 15% of it) rises by
     0.03 or more (Apple M1 Max, valley: 0.674 at 0x, 0.727 at 2x, 0.750 at 4x;
     two runs of each agree to 0.0005)
  M5 neither run logs a "Vulkan validation error:" line, where the
     validation layer is installed (r_vkValidation 1 is VulkanClient's default)
Skips (exit 77) when the device supports no count above 1. The screenshots show
retail content and are deleted with the test's temporary directory.

Usage: test_vulkan_msaa.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (no client binary, no retail data, or no MSAA).
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_limbo_command_map import read_tga  # noqa: E402
from test_reconnect_restart import Failed, spawn  # noqa: E402

# the validation layer's messages (r_vkValidation 1, VulkanClient's default where
# the layer is installed): a pipeline whose sample count differs from the pass's
# attachments is one
VALIDATION_ERROR = "Vulkan validation error:"
MSAA_LINE = re.compile(r"Vulkan MSAA: (off|(\d+)x)\b.*device supports([ 0-9x]*| none)\)")
SAME_VIEW_SHARE = 0.85
SAME_VIEW_LUMINANCE = 24
EDGE_STEP = 48
BLEND_MARGIN = 0.15
BLEND_RISE = 0.03


def luminance_rows(path: Path) -> tuple[int, int, list[list[int]]]:
    width, height, pixel = read_tga(path)
    rows = []
    for y in range(0, height, 2):
        row = []
        for x in range(width):
            r, g, b = pixel(x, y)
            row.append(int(0.299 * r + 0.587 * g + 0.114 * b))
        rows.append(row)
    return width, height, rows


def blended_share(rows: list[list[int]]) -> tuple[float, int]:
    """Of the pixels whose horizontal neighbours differ by EDGE_STEP or more,
    the share strictly inside the neighbours' range (an anti-aliased edge)."""
    steps = blended = 0
    for row in rows:
        for x in range(1, len(row) - 1):
            a, b, c = row[x - 1], row[x], row[x + 1]
            if abs(a - c) < EDGE_STEP:
                continue
            steps += 1
            lo, hi = min(a, c), max(a, c)
            margin = BLEND_MARGIN * (hi - lo)
            if lo + margin < b < hi - margin:
                blended += 1
    return (blended / steps if steps else 0.0), steps


def same_view_share(a: list[list[int]], b: list[list[int]]) -> float:
    close = total = 0
    for row_a, row_b in zip(a, b):
        for va, vb in zip(row_a, row_b):
            total += 1
            close += abs(va - vb) <= SAME_VIEW_LUMINANCE
    return close / total if total else 0.0


def run(client: Path, data: Path, port: int, samples: int, shot: Path, log: Path | None):
    """Host valley at r_multiSamples samples, spawn and screenshot; returns the
    client's "Vulkan MSAA:" match and its exit status."""
    gfx = VulkanClient(client, data, port, map_name="valley", log_path=log,
                       cvars={"si_rules": "sdGameRulesObjective", "r_rayTracing": "0",
                              "r_multiSamples": str(samples)})
    gfx.name = f"VulkanClient (r_multiSamples {samples})"
    gfx.slot = 0
    line = None
    try:
        gfx.start()
        hit = gfx.wait_for(lambda s: MSAA_LINE.search(s), 120.0)
        if not hit:
            raise Failed(f"r_multiSamples {samples}: the client never logged its 'Vulkan MSAA:' line")
        line = hit[2]
        if not gfx.wait_for_text("Spawning entities", 180.0):
            raise Failed(f"r_multiSamples {samples}: the listen server never loaded valley")
        gfx.sync()
        spawn(gfx, gfx, "gdf")
        time.sleep(6.0)
        gfx.send(f'set r_vkScreenshot "{shot}"')
        gfx.sync()
        deadline = time.monotonic() + 20.0
        last_size = -1
        while time.monotonic() < deadline:
            size = shot.stat().st_size if shot.exists() else -1
            if size > 18 and size == last_size:
                break
            last_size = size
            time.sleep(0.5)
        else:
            raise Failed(f"r_multiSamples {samples}: r_vkScreenshot wrote no complete screenshot")
    finally:
        code = gfx.quit()
    errors = [ln.text.strip() for ln in gfx.snapshot() if VALIDATION_ERROR in ln.text]
    return line, code, errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29903)
    ap.add_argument("--log-dir")
    args = ap.parse_args()
    try:
        client = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    h.install_signal_handlers()
    logs = {}
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        logs = {n: Path(args.log_dir) / f"vulkan-msaa-{n}.log" for n in (0, 16)}

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    with h.PrivateTempDir(prefix="openetqw-vulkan-msaa-") as tmp:
        shots = {n: Path(tmp) / f"msaa{n}.tga" for n in (0, 16)}
        try:
            off_line, off_code, off_errors = run(client, data, args.port, 0, shots[0], logs.get(0))
            expect(off_code == 0, f"the r_multiSamples 0 client exits 0 (exit {off_code})")
            expect(off_line.group(1) == "off", f"M1 r_multiSamples 0 logs MSAA off ({off_line.group(0)!r})")
            on_line, on_code, on_errors = run(client, data, args.port, 16, shots[16], logs.get(16))
            expect(on_code == 0, f"the r_multiSamples 16 client exits 0 (exit {on_code})")
            for samples, errors in ((0, off_errors), (16, on_errors)):
                expect(not errors, f"M5 r_multiSamples {samples}: no Vulkan validation errors "
                       f"({len(errors)}{'; first: ' + errors[0] if errors else ''})")
            supported = [int(n) for n in re.findall(r"(\d+)x", on_line.group(3))]
            usable = [n for n in supported if 2 <= n <= 16]
            if not usable:
                print(f"[SKIP] the device supports no MSAA count above 1 ({on_line.group(0)!r})")
                return h.EXIT_SKIP
            want = max(usable)
            got = int(on_line.group(2)) if on_line.group(2) else 1
            expect(got == want, f"M2 r_multiSamples 16 runs at {want}x, the device's highest of 16 or less "
                   f"(got {got}x; {on_line.group(0)!r})")
            width_off, height_off, rows_off = luminance_rows(shots[0])
            width_on, height_on, rows_on = luminance_rows(shots[16])
            # the comparisons below pair pixels up, so a size change (a resized
            # window, a different swapchain) would silently compare part of a frame
            if (width_off, height_off) != (width_on, height_on):
                raise Failed(f"M3 the two screenshots differ in size ({width_off}x{height_off} at 0x, "
                             f"{width_on}x{height_on} at 16); the views cannot be compared")
            share = same_view_share(rows_off, rows_on)
            expect(share >= SAME_VIEW_SHARE, f"M3 both frames show the same view ({100 * share:.1f}% of pixels within "
                   f"{SAME_VIEW_LUMINANCE} in luminance; need >= {100 * SAME_VIEW_SHARE:.0f}%)")
            blend_off, steps_off = blended_share(rows_off)
            blend_on, steps_on = blended_share(rows_on)
            expect(blend_on - blend_off >= BLEND_RISE,
                   f"M4 the {got}x frame's hard edges are blended (blended share {blend_off:.4f} at 0x over "
                   f"{steps_off} edge pixels, {blend_on:.4f} at {got}x over {steps_on}; need a rise >= {BLEND_RISE})")
        except Failed as e:
            failures.append(str(e))
            print(f"FAIL: {e}")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: Vulkan MSAA runs at the device's sample count and smooths the world's edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
