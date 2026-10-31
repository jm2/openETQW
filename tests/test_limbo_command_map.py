#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The deploy menu draws its campaign map and objective icons.

sdGuiModel clips a GUI draw to the clip rectangles its windows push. It read
idWinding2D::ClipByBounds's result the wrong way round: that returns true when
nothing of the winding is left, so every draw inside a clipping window was dropped.
The deploy (limbo) menu lost its campaign map (the command map, its territories
and objective markers) and the icons beside the objective list, and the masked
territory outlines elsewhere were cut away whole.

The Vulkan client hosts valley (objective rules), joins GDF so the deploy menu
opens, and takes a screenshot (r_vkScreenshot). In the 640x480 GUI space:
  L1 the campaign map's area (inside 283,66 - 573,270) is the bright, varied
     map image: mean luminance >= 50 and standard deviation >= 20 (main: the
     empty panel, about 30 and 7)
  L2 the objective icons' column (232,403 - 250,462) is not blank: standard
     deviation >= 15 (main: 0)
The screenshot shows retail content and is deleted with the test's temporary
directory.

Usage: test_limbo_command_map.py [--client-binary PATH] [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (no client binary or retail data).
"""

from __future__ import annotations

import argparse
import statistics
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed  # noqa: E402

MAP_AREA = (283, 66, 573, 270)
ICON_COLUMN = (232, 403, 250, 462)


def read_tga(path: Path):
    """Width, height and an (x, y) -> (r, g, b) reader for an uncompressed TGA."""
    data = path.read_bytes()
    id_length, colour_map, image_type = data[0], data[1], data[2]
    width, height = struct.unpack_from("<HH", data, 12)
    bpp, descriptor = data[16], data[17]
    if image_type != 2 or colour_map != 0 or bpp not in (24, 32):
        raise Failed(f"{path.name}: not an uncompressed true-colour TGA (type {image_type}, {bpp} bpp)")
    step = bpp // 8
    offset = 18 + id_length
    top_down = bool(descriptor & 0x20)

    def pixel(x: int, y: int) -> tuple[int, int, int]:
        row = y if top_down else height - 1 - y
        i = offset + (row * width + x) * step
        return data[i + 2], data[i + 1], data[i]

    return width, height, pixel


def luminance_stats(path: Path, area: tuple[int, int, int, int]) -> tuple[float, float]:
    """Mean and standard deviation of luminance over a 640x480 GUI-space area."""
    width, height, pixel = read_tga(path)
    x0, y0, x1, y1 = area
    xs = range(int(x0 * width / 640), int(x1 * width / 640), 2)
    ys = range(int(y0 * height / 480), int(y1 * height / 480), 2)
    values = []
    for y in ys:
        for x in xs:
            r, g, b = pixel(x, y)
            values.append(0.299 * r + 0.587 * g + 0.114 * b)
    return statistics.mean(values), statistics.pstdev(values)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29916)
    ap.add_argument("--log-dir")
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
        log = Path(args.log_dir) / "limbo-command-map.log"

    failures: list[str] = []

    def expect(ok: bool, what: str) -> None:
        print(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            failures.append(what)

    with h.PrivateTempDir(prefix="openetqw-limbo-map-") as tmp:
        shot = Path(tmp) / "limbo.tga"
        gfx = VulkanClient(client, data, args.port, map_name="valley", log_path=log,
                           cvars={"si_rules": "sdGameRulesObjective", "r_vkValidation": "0"})
        gfx.name = "VulkanClient"
        gfx.slot = 0
        try:
            gfx.start()
            if not gfx.wait_for_text("Spawning entities", 180.0):
                raise Failed("the listen server never loaded valley")
            gfx.sync()
            time.sleep(5.0)
            gfx.send("clientTeam gdf")
            gfx.sync()
            time.sleep(4.0)
            gfx.send(f'set r_vkScreenshot "{shot}"')
            gfx.sync()
            deadline = time.monotonic() + 20.0
            while not shot.exists() and time.monotonic() < deadline:
                time.sleep(0.5)
            time.sleep(1.0)
            if not shot.exists():
                raise Failed("r_vkScreenshot wrote no screenshot")
            mean, spread = luminance_stats(shot, MAP_AREA)
            expect(mean >= 50.0 and spread >= 20.0,
                   f"L1 the campaign map is drawn (luminance mean {mean:.1f}, deviation {spread:.1f}; "
                   f"need >= 50 and >= 20)")
            _, icons = luminance_stats(shot, ICON_COLUMN)
            expect(icons >= 15.0, f"L2 the objective icons are drawn (luminance deviation {icons:.1f}; need >= 15)")
        except Failed as e:
            failures.append(str(e))
            print(f"FAIL: {e}")
        finally:
            code = gfx.quit()
        expect(code == 0, f"the client exits 0 (exit {code})")

    if failures:
        print(f"FAIL: {len(failures)} check(s) failed")
        return 1
    print("PASS: the deploy menu draws its campaign map and objective icons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
