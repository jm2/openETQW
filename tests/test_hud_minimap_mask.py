#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The HUD minimap keeps its territory outlines inside its circle (#379).

The command map draws territory outlines far larger than the map, masked:
retail's material stage runs trivialMaskImage, which multiplies the outline by
textures/common/cmmask at the draw's mask coordinates. sdGuiModel::ClipToMask
cuts a masked draw only to the square where those coordinates are 0..1, so
without the mask texture the outlines run out of the HUD minimap's circle into
the square's corners (Vulkan). Under OpenGL the stage was not drawn at all: its Cg
vertex program had no ARB port, and the GUI path passed no mask coordinates (#396).

The client hosts valley (objective rules), spawns a GDF soldier and takes a
screenshot (r_vkScreenshot, which OpenGL also writes); --render-api opengl runs it
on the OpenGL renderer. The minimap is a circle of radius about 56 around
(576.75, 63) in the 640x480 GUI space. Territory outlines are the bright green of
friendly territory (green at least 70, and at least 25 above red and 40 above
blue); samples every half unit:
  M1 inside the circle (radius below 0.95 of its own) at least 1,300 samples are
     outline green: the HUD and its masked territory outlines are drawn (Vulkan:
     about 1,660-1,700; OpenGL before #396, without the masked stage: about 920)
  M2 between the circle (radius above 1.08 of its own) and the square around it
     at most 15 samples are outline green (main: about 160)
  M3 there, at most 15 samples are near black (luminance below 12; none on main
     or with the fix): outlines the mask turned black rather than away would
     pass M2
The screenshot shows retail content and is deleted with the test's temporary
directory.

Usage: test_hud_minimap_mask.py [--client-binary PATH] [--port N] [--log-dir DIR] [--vk-validation]
                               [--render-api vulkan|opengl]
Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_limbo_command_map import read_tga  # noqa: E402
from test_reconnect_restart import Failed, spawn  # noqa: E402

CENTRE = (576.75, 63.0)
RADIUS = 56.0
INSIDE = 0.95
OUTSIDE = 1.08
MIN_INSIDE = 1300
MAX_OUTSIDE = 15
DARK = 12.0
MAX_DARK = 15


def outline_green(rgb: tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return g >= 70 and g >= r + 25 and g >= b + 40


def count_outline(path: Path) -> tuple[int, int, int]:
    """Outline-green samples inside the minimap's circle and in the square's
    corners, and near-black samples in the corners."""
    width, height, pixel = read_tga(path)
    cx, cy = CENTRE
    inside = outside = dark = 0
    steps = int(RADIUS * 4)
    for j in range(-steps, steps + 1):
        for i in range(-steps, steps + 1):
            gx = cx + i * 0.5
            gy = cy + j * 0.5
            d = math.hypot(gx - cx, gy - cy) / RADIUS
            if INSIDE <= d <= OUTSIDE:
                continue
            x = min(width - 1, int(gx * width / 640))
            y = min(height - 1, int(gy * height / 480))
            if y < 0:
                continue
            rgb = pixel(x, y)
            if d > OUTSIDE and 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] < DARK:
                dark += 1
            if not outline_green(rgb):
                continue
            if d < INSIDE:
                inside += 1
            else:
                outside += 1
    return inside, outside, dark


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29919)
    ap.add_argument("--log-dir")
    ap.add_argument("--vk-validation", action="store_true",
                    help="run with the Vulkan validation layer (r_vkValidation 1)")
    ap.add_argument("--render-api", choices=("vulkan", "opengl"), default="vulkan")
    args = ap.parse_args()
    try:
        client = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    h.install_signal_handlers()
    log = None
    if args.log_dir:
        Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        log = Path(args.log_dir) / f"hud-minimap-mask-{args.render_api}.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    with h.PrivateTempDir(prefix="openetqw-minimap-mask-") as tmp:
        shot = Path(tmp) / "hud.tga"
        gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                           cvars={"si_rules": "sdGameRulesObjective",
                                  "r_renderAPI": args.render_api,
                                  "r_vkValidation": "1" if args.vk_validation else "0"})
        gfx.name = "VulkanClient"
        gfx.slot = 0
        try:
            gfx.start()
            if not gfx.wait_for_text("Spawning entities", 180.0):
                raise Failed("the listen server never loaded valley")
            gfx.sync()
            spawn(gfx, gfx, "gdf")
            time.sleep(6.0)
            gfx.send(f'set r_vkScreenshot "{shot}"')
            gfx.sync()
            # wait until the file exists and its size has stopped changing
            deadline = time.monotonic() + 20.0
            last_size = -1
            while time.monotonic() < deadline:
                size = shot.stat().st_size if shot.exists() else -1
                if size > 18 and size == last_size:
                    break
                last_size = size
                time.sleep(0.5)
            else:
                raise Failed("r_vkScreenshot wrote no complete screenshot")
            inside, outside, dark = count_outline(shot)
            expect(inside >= MIN_INSIDE,
                   f"M1 the minimap draws its territory outlines ({inside} green samples inside the circle; "
                   f"need >= {MIN_INSIDE})")
            expect(outside <= MAX_OUTSIDE,
                   f"M2 no outline outside the circle ({outside} green samples in the square's corners; "
                   f"need <= {MAX_OUTSIDE})")
            expect(dark <= MAX_DARK,
                   f"M3 nothing drawn black outside the circle ({dark} near-black samples in the square's "
                   f"corners; need <= {MAX_DARK})")
        except Failed as e:
            failures.append(str(e))
            print(f"FAIL: {e}")
        finally:
            code = gfx.quit()
        expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the HUD minimap keeps its territory outlines inside its circle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
