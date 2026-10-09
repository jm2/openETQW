#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The standard screenshot set for graphics changes (CONTRIBUTING: "Graphics changes").

Every PR that changes what is drawn (src/renderer, shaders, materials) carries this set from
every platform (Windows x64 with ray tracing on and off, Windows arm64, Linux, macOS), taken
on the PR and on main on the same machine, compared and posted on the PR. The author and a
reviewing session look at every sheet and are the gate; the owner is asked only when
something is ambiguous (for example, what retail looks like). Numbers-only tests miss a dark
viewmodel or a sky drawn as a box; looking at the frames does not.

  capture   start the Vulkan client on each map (devmap, cheats on), stand at the spawn and
            write each view of the standard list with r_vkScreenshot (one TGA per view and
            ray tracing mode), plus a contact sheet when Pillow is installed:
              screenshot_set.py capture --out shots/pr --rt both
  compare   per-view mean difference between two capture directories (the PR's and main's,
            same machine), a side-by-side sheet, and the warning heuristics on both:
              screenshot_set.py compare shots/main shots/pr --out shots/compare

The views (VIEWS) are relative to where the player spawns, so they need no per-map
coordinates: forward with the weapon, terrain near and from the air in two directions, the
horizon, and the sky in two directions. The heuristics only warn (a near-black viewmodel
corner, a sky band of one flat colour, an all-black or all-one-colour frame); the decision
is made by looking at the sheets.

capture needs nothing beyond the standard library; the sheets, compare and the heuristics
need Pillow (pip install pillow).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# map, team, class: outdoor maps with terrain and sky in view of the spawn
MAPS = [
    ("valley", "gdf", "soldier"),
    ("canyon", "strogg", "aggressor"),
    ("outskirts", "gdf", "soldier"),
]

# Fixed eye positions (x, y, z, yaw) the views are taken from, so two captures (main and the
# PR, or ray tracing on and off) show the same frames: spawns are random. Valley's is the GDF
# spawn area the sky reports used (#121), canyon's the Strogg spawn viewpoint of #108. A map
# without one uses where the player spawned, and records it (screenshot_set.json); pass that
# file to the other capture with --anchors so both use the same place.
ANCHORS = {
    "valley": (10600.0, -10824.0, 1480.5, 90.0),
    "canyon": (-1271.86, 4348.12, 472.0, 225.0),   # the Strogg spawn's eye (getviewpos)
}
# Positions are eye positions as getviewpos prints them: a spot even slightly below the floor
# drops the player out of the world, and the "view" is then the map from underneath. Aerial
# views use noclip; ground views do not (in noclip the player's own model shows overhead).

# name, (forward, right, up) offset in units relative to the anchor, yaw delta, pitch
# (idTech: negative pitch looks up), noclip (needed off the ground)
VIEWS = [
    ("forward", (0, 0, 0), 0, 0, False),             # the weapon viewmodel and the world ahead
    ("terrain_near", (0, 0, 0), 0, 30, False),       # the ground in front, close up
    ("terrain_air", (0, 0, 1200), 0, 35, True),      # terrain from above, ahead
    ("terrain_air_back", (0, 0, 1200), 180, 35, True),
    ("horizon", (0, 0, 0), 90, -5, False),           # where terrain meets the sky, to the side
    ("sky", (0, 0, 0), 0, -60, False),               # the sky ahead
    ("sky_back", (0, 0, 0), 180, -60, False),
]

ACTIVE_RE = re.compile(r"FRAME_TIMING: window_ms=[\d.]+ frames=\d+ ticks=(\d+)")
VIEWPOS_RE = re.compile(r"\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)\s+(-?[\d.]+)\s+(-?[\d.]+)")


def find_client(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("OPENETQW_CLIENT_BINARY")
    if env:
        return Path(env)
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import etqw_harness as h  # noqa: E402
    return h.build_binary("client")


def find_data(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    import etqw_harness as h  # noqa: E402
    return h.resolve_data_path()


class Client:
    """The Vulkan client with its console on stdin and stdout collected."""

    def __init__(self, args: list[str], log: Path):
        self.log = log.open("w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in iter(self.proc.stdout.readline, ""):
            self.log.write(line)
            self.lines.put(line.rstrip("\n"))
        self.lines.put(None)

    def send(self, command: str):
        self.proc.stdin.write(command + "\n")
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                line = self.lines.get(timeout=0.2)
            except queue.Empty:
                if self.proc.poll() is not None:
                    return None
                continue
            if line is None:
                return None
            result = predicate(line)
            if result:
                return result
        return None

    def close(self):
        try:
            self.send("quit")
            self.proc.wait(timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()
        self.log.close()


def view_target(spawn, offset, yaw_delta, pitch):
    import math
    x, y, z, yaw, _ = spawn
    rad = math.radians(yaw)
    fwd, right, up = offset
    tx = x + fwd * math.cos(rad) + right * math.sin(rad)
    ty = y + fwd * math.sin(rad) - right * math.cos(rad)
    return tx, ty, z + up, (yaw + yaw_delta) % 360.0, pitch


def capture_map(client_bin: Path, data: Path, out: Path, map_name: str, team: str, cls: str,
                rt: str, extra: list[str], settle: float, anchors: dict) -> list[dict]:
    tag = f"{map_name}_rt{rt}"
    args = [str(client_bin), "+set", "fs_basepath", str(data), "+set", "fs_game", "base",
            "+set", "si_rules", "sdGameRulesObjective", "+set", "r_renderAPI", "vulkan",
            "+set", "r_fullscreen", "0", "+set", "r_vkValidation", "0", "+set", "r_rayTracing", rt,
            "+set", "com_logFrameTiming", "1", "+set", "g_logFramePoses", "0"]
    for i in range(0, len(extra), 2):
        args += ["+set", extra[i], extra[i + 1]]
    args += ["+devmap", map_name, "+clientTeam", team, "+clientClass", cls, "+closeLimbo"]
    results = []
    client = Client(args, out / f"{tag}.log")
    try:
        if not client.wait_for(lambda s: (m := ACTIVE_RE.search(s)) and int(m.group(1)) >= 25, 240.0):
            raise RuntimeError(f"{tag}: the client never reached play")
        time.sleep(settle)
        client.send("god")
        if map_name in anchors:
            x, y, z, yaw = anchors[map_name]
            spawn = (x, y, z, yaw, 0.0)
        else:
            client.send("getviewpos")
            found = client.wait_for(lambda s: VIEWPOS_RE.search(s), 15.0)
            if not found:
                raise RuntimeError(f"{tag}: no getviewpos answer")
            spawn = tuple(float(found.group(i)) for i in range(1, 6))
            anchors[map_name] = spawn[:4]   # the other ray tracing mode and --anchors reuse it
        noclip = False
        for name, offset, yaw_delta, pitch, needs_noclip in VIEWS:
            # noclip only off the ground: in noclip the GDF player's own model shows overhead
            if needs_noclip != noclip:
                client.send("noclip")
                noclip = needs_noclip
            x, y, z, yaw, pitch = view_target(spawn, offset, yaw_delta, pitch)
            # setviewpos takes the eye position; it subtracts the view height itself
            client.send(f"setviewpos {x:.2f} {y:.2f} {z:.2f} {yaw:.2f} {pitch:.2f} 0")
            time.sleep(settle)
            path = out / f"{tag}_{name}.tga"
            if path.exists():
                path.unlink()
            client.send(f'set r_vkScreenshot "{path.as_posix()}"')
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and not (path.exists() and path.stat().st_size > 0):
                time.sleep(0.2)
            time.sleep(0.3)  # the writer closes the file after it appears
            ok = path.exists() and path.stat().st_size > 0
            results.append({"map": map_name, "rt": rt, "view": name, "file": path.name if ok else None,
                            "at": [round(x, 1), round(y, 1), round(z, 1), round(yaw, 1), pitch]})
            print(f"  {tag}_{name}: {'ok' if ok else 'NOT WRITTEN'}", flush=True)
    finally:
        client.close()
    return results


# --- Pillow parts: sheets, comparison and the warning heuristics ---

def _pil():
    try:
        from PIL import Image, ImageDraw, ImageStat  # noqa: F401
        return Image, ImageDraw, ImageStat
    except ImportError:
        return None


def heuristics(path: Path, view: str) -> list[str]:
    pil = _pil()
    if pil is None:
        return []
    Image, _, ImageStat = pil
    img = Image.open(path).convert("RGB")
    w, h = img.size
    warnings = []
    whole = ImageStat.Stat(img.convert("L"))
    if whole.mean[0] < 8:
        warnings.append("frame almost entirely black")
    if max(whole.stddev[0], 0) < 2:
        warnings.append("frame is one flat colour")
    if view == "forward":
        # the viewmodel sits in the lower right; a lit weapon is not near-black
        corner = img.crop((int(w * 0.55), int(h * 0.55), w, int(h * 0.95))).convert("L")
        if ImageStat.Stat(corner).mean[0] < 20:
            warnings.append("viewmodel corner near-black (weapon unlit?)")
    if view in ("sky", "sky_back"):
        band = img.crop((0, int(h * 0.05), w, int(h * 0.45))).convert("L")
        if ImageStat.Stat(band).stddev[0] < 1.5:
            warnings.append("sky band one flat colour (sky not drawn?)")
    return warnings


def contact_sheet(entries: list[tuple[str, Path | None]], out: Path, cols: int, title: str):
    pil = _pil()
    if pil is None:
        print("  (Pillow not installed: no contact sheet)")
        return
    Image, ImageDraw, _ = pil
    thumb_w, thumb_h, label_h = 480, 270, 18
    rows = (len(entries) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h) + label_h), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    draw.text((4, 2), title, fill=(255, 255, 255))
    for i, (label, path) in enumerate(entries):
        x, y = (i % cols) * thumb_w, label_h + (i // cols) * (thumb_h + label_h)
        draw.text((x + 4, y + 2), label, fill=(255, 220, 120))
        if path and path.exists():
            img = Image.open(path).convert("RGB").resize((thumb_w, thumb_h))
            sheet.paste(img, (x, y + label_h))
        else:
            draw.text((x + 8, y + label_h + 8), "missing", fill=(255, 80, 80))
    sheet.save(out)
    print(f"  contact sheet: {out}")


def cmd_capture(a) -> int:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    client_bin, data = find_client(a.client), find_data(a.data)
    modes = ["1", "0"] if a.rt == "both" else [a.rt]
    maps = [m for m in MAPS if not a.maps or m[0] in a.maps.split(",")]
    anchors = {k: tuple(v) for k, v in ANCHORS.items()}
    if a.anchors:
        anchors.update({k: tuple(v) for k, v in
                        json.loads(Path(a.anchors).read_text(encoding="utf-8")).get("anchors", {}).items()})
    meta = {"platform": f"{platform.system()} {platform.machine()}", "client": str(client_bin),
            "label": a.label, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "shots": [],
            "anchors": anchors}
    failures = 0
    for rt in modes:
        for map_name, team, cls in maps:
            print(f"{map_name}, r_rayTracing {rt}", flush=True)
            try:
                meta["shots"] += capture_map(client_bin, data, out, map_name, team, cls, rt,
                                             a.set or [], a.settle, anchors)
            except RuntimeError as e:
                print(f"  FAILED: {e}", flush=True)
                failures += 1
    for shot in meta["shots"]:
        if shot["file"]:
            shot["warnings"] = heuristics(out / shot["file"], shot["view"])
            for w in shot["warnings"]:
                print(f"  WARNING {shot['map']} rt{shot['rt']} {shot['view']}: {w}")
    (out / "screenshot_set.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    for rt in modes:
        entries = [(f"{s['map']} {s['view']}", out / s["file"] if s["file"] else None)
                   for s in meta["shots"] if s["rt"] == rt]
        contact_sheet(entries, out / f"sheet_rt{rt}.png", len(VIEWS),
                      f"{a.label or 'screenshot set'}  {meta['platform']}  r_rayTracing {rt}")
    missing = sum(1 for s in meta["shots"] if not s["file"])
    print(f"{len(meta['shots']) - missing} shots written, {missing} missing, {failures} map(s) failed")
    return 1 if failures or missing else 0


def cmd_compare(a) -> int:
    pil = _pil()
    if pil is None:
        print("compare needs Pillow (pip install pillow)")
        return 2
    Image, _, ImageStat = pil
    from PIL import ImageChops
    base, head, out = Path(a.base), Path(a.head), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    meta_head = json.loads((head / "screenshot_set.json").read_text(encoding="utf-8"))
    rows, entries = [], []
    for shot in meta_head["shots"]:
        name = shot["file"]
        if not name:
            continue
        b, h = base / name, head / name
        if not b.exists():
            rows.append((name, None, heuristics(h, shot["view"])))
            continue
        ib, ih = Image.open(b).convert("RGB"), Image.open(h).convert("RGB")
        if ib.size != ih.size:
            ih = ih.resize(ib.size)
        diff = ImageStat.Stat(ImageChops.difference(ib, ih).convert("L")).mean[0]
        rows.append((name, diff, heuristics(h, shot["view"])))
        entries += [(f"base {name}", b), (f"head {name}", h)]
    report = ["| shot | mean diff vs base (0-255) | warnings on head |", "|---|---|---|"]
    for name, diff, warns in rows:
        report.append(f"| {name} | {'no base' if diff is None else f'{diff:.2f}'} | {'; '.join(warns) or '-'} |")
    (out / "compare.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    contact_sheet(entries, out / "compare_sheet.png", 2, f"left: {base}   right: {head}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="capture the standard set")
    c.add_argument("--out", required=True)
    c.add_argument("--client", help="client binary (default: $OPENETQW_CLIENT_BINARY or the build dir)")
    c.add_argument("--data", help="retail data root (default: $ETQW_DATA_PATH and the usual search)")
    c.add_argument("--rt", choices=["0", "1", "both"], default="both",
                   help="r_rayTracing modes (both on a GPU with ray tracing; 0 elsewhere)")
    c.add_argument("--maps", help="comma-separated subset of " + ",".join(m[0] for m in MAPS))
    c.add_argument("--label", default="", help="a label for the sheets (for example the commit)")
    c.add_argument("--settle", type=float, default=1.5, help="seconds to wait after each move")
    c.add_argument("--anchors", help="screenshot_set.json of an earlier capture: reuse its per-map positions "
                   "(capture main first, then the PR with --anchors <main>/screenshot_set.json)")
    c.add_argument("--set", nargs=2, action="extend", metavar=("CVAR", "VALUE"),
                   help="extra cvars, repeatable")
    k = sub.add_parser("compare", help="compare two capture directories from the same machine")
    k.add_argument("base")
    k.add_argument("head")
    k.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    return cmd_capture(a) if a.cmd == "capture" else cmd_compare(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
