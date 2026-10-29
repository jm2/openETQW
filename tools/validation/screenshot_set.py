#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The standard screenshot set for graphics changes (CONTRIBUTING: "Graphics changes").

Every PR that changes what is drawn (src/renderer, shaders, materials) carries this set from
every platform (Windows x64 with ray tracing on and off, Windows arm64, Linux, macOS), taken
on the PR and on main on the same machine, compared and posted on the PR. The author and a
reviewing session look at every sheet and are the gate; the owner is asked only when
something is ambiguous (for example, what retail looks like). Numbers-only tests miss a dark
viewmodel or a sky drawn as a box; looking at the frames does not.

  capture   start the Vulkan client on each map (devmap, cheats on) and write each view of
            the standard list with r_vkScreenshot (one TGA per view and ray tracing mode),
            plus a contact sheet per mode when Pillow is installed. Capture main first, then
            the PR with main's positions:
              screenshot_set.py capture --out ../shots/main --label <main commit>
              screenshot_set.py capture --out ../shots/pr --label <pr commit> \\
                  --anchors ../shots/main/screenshot_set.json
  compare   every (map, ray tracing, view) of either capture: the difference (mean, max and
            the share of pixels off by more than 16 of 255), shots missing on either side, a
            side-by-side sheet, and the warning heuristics on the PR's frames:
              screenshot_set.py compare ../shots/main ../shots/pr --out ../shots/compare

The client is the build under test: it runs from the nearest folder above the binary that
holds base/vkprogs (an installed tree, or the checkout the binary was built in), so the
shaders are that build's. It gets a private fs_savepath (no saved config is read or written)
and a 1280x720 window (r_mode 12) without multisampling, so captures are comparable. The
engine shrinks a window that does not fit the primary display; capture then warns, because
such frames do not compare with frames at the full size. On a high-density display the frames
are larger than the window (a 1280x720 window gives 2560x1440 frames on a Retina Mac), which
is expected; compare the PR with main on the same machine.

The views (VIEWS) are offsets from a per-map eye position: fixed for valley and canyon
(ANCHORS), else where the player spawned in the first capture, reused with --anchors. They
cover the weapon, terrain near and from the air in two directions, the horizon and the sky in
two directions. A map can add views at fixed positions (EXTRA_VIEWS: canyon's hangar), which
--anchors does not move. A capture with --anchors takes the other capture's positions, the
old canyon anchor included when that capture predates the outdoor one. compare lists an extra
view only one side took as "missing in base" or "not taken by head", neither a problem; a
standard view, map or mode the head lacks is "missing in head", a problem. The heuristics only
warn (a near-black viewmodel corner, a sky band of one flat colour, an all-black or one-colour
frame); the decision is made by looking at the sheets.

capture needs only the standard library; the sheets, compare and the heuristics need Pillow
(pip install pillow). The data root comes from --data or $ETQW_DATA_PATH; tests/ must be
importable for the defaults (etqw_harness), which the script arranges itself.
"""

from __future__ import annotations

import argparse
import json
import math
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
sys.path.insert(0, str(REPO_ROOT / "tests"))

# map, team, class: outdoor maps with terrain and sky in view
MAPS = [
    ("valley", "gdf", "soldier"),
    ("canyon", "strogg", "aggressor"),
    ("outskirts", "gdf", "soldier"),
]

# Fixed eye positions (x, y, z, yaw) the views are taken from, so two captures (main and the
# PR, or ray tracing on and off) show the same frames: spawns are random. Valley's is the GDF
# spawn area the sky reports used (#121), canyon's the GDF spawn area (the MCP, a shelter, the
# canyon walls and open sky). A map without one uses where the player spawned and records it
# (screenshot_set.json); pass that file to the other capture with --anchors.
# Positions are eye positions as getviewpos prints them: a spot even slightly below the floor
# drops the player out of the world, and the "view" is then the map from underneath. Aerial
# views use noclip; ground views do not (in noclip the player's own model shows overhead).
ANCHORS = {
    "valley": (10600.0, -10824.0, 1480.5, 90.0),
    "canyon": (-9710.0, -9379.0, 1057.0, 315.0),
}

# Views at fixed eye positions (x, y, z, yaw), not offsets from the anchor, so --anchors does
# not move them: name, position, pitch, noclip. Taken after the standard views. Canyon's anchor
# was the Strogg spawn, in a hangar, until 2026-10-20; its forward view stays as "hangar", so
# indoor lighting and shadows are still in the set.
EXTRA_VIEWS = {
    "canyon": [("hangar", (-1271.86, 4348.12, 472.0, 225.0), 0, False)],
}

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

WIDTH, HEIGHT = 1280, 720   # r_mode 12, which capture sets
ACTIVE_RE = re.compile(r"FRAME_TIMING: window_ms=[\d.]+ frames=\d+ ticks=(\d+)")
VIEWPOS_RE = re.compile(r"\(\s*(-?[\d.]+)\s+(-?[\d.]+)\s+(-?[\d.]+)\s*\)\s+(-?[\d.]+)\s+(-?[\d.]+)")
SPAWNED_RE = re.compile(r"PLAYER_POS:.*spectating 0")
SHOT_RE = re.compile(r"r_vkScreenshot: (wrote|could not|the swapchain|swapchain format)")
SIZE_RE = re.compile(r"r_vkScreenshot: wrote (\d+)x(\d+)")
SHRUNK_RE = re.compile(r"window: \d+x\d+ does not fit the primary display; using \d+x\d+")
# FRAME_POSE is the game's per-draw line: view=x,y,z,pitch,yaw,roll of the view that frame was
# drawn with (openETQW-game src/game/PlayerView.cpp). It is what proves a capture is the view
# that was asked for rather than the limbo/guided camera or an older view.
FRAME_POSE_RE = re.compile(
    r"FRAME_POSE:.*? view=(-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+),(-?[\d.]+)")
VIEW_MATCH_XY = 2.0        # units; setviewpos puts the eye exactly on the commanded x/y
VIEW_MATCH_ANGLE = 0.5     # degrees; the drawn angles are the commanded ones
VIEW_MATCH_TIMEOUT = 5.0   # seconds to wait for a matching draw after the settle


def find_client(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("OPENETQW_CLIENT_BINARY")
    if env:
        return Path(env).resolve()
    import etqw_harness as h
    return h.build_binary("client").resolve()


def find_data(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    if os.environ.get("ETQW_DATA_PATH"):
        return Path(os.environ["ETQW_DATA_PATH"])
    raise SystemExit("no retail data: pass --data <ET:QW install root> or set ETQW_DATA_PATH")


def client_workdir(binary: Path) -> Path:
    """The folder the client finds vkprogs/ from: the nearest one above the binary that holds
    base/vkprogs (an installed tree, or the checkout it was built in)."""
    for parent in binary.parents:
        if (parent / "base" / "vkprogs").is_dir():
            return parent
    raise SystemExit(f"no base/vkprogs above {binary}: cannot tell which shaders the client would use")


class Client:
    """The Vulkan client with its console on stdin and stdout collected."""

    def __init__(self, args: list[str], log: Path, cwd: Path):
        self.log = log.open("w", encoding="utf-8", errors="replace")
        try:
            self.proc = subprocess.Popen(args, cwd=str(cwd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")
        except OSError as e:
            self.log.close()
            raise RuntimeError(f"cannot start the client: {e}") from e
        self.lines: "queue.Queue[str | None]" = queue.Queue()
        self.last_pose: "tuple[float, ...] | None" = None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        for line in iter(self.proc.stdout.readline, ""):
            self.log.write(line)
            self.lines.put(line.rstrip("\n"))
        self.lines.put(None)

    def send(self, command: str):
        try:
            self.proc.stdin.write(command + "\n")
            self.proc.stdin.flush()
        except OSError:
            pass  # the client has gone; wait_for notices

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
            pose = FRAME_POSE_RE.search(line)
            if pose:
                self.last_pose = tuple(float(group) for group in pose.groups())
            result = predicate(line)
            if result:
                return result
        return None

    def wait_view(self, x: float, y: float, yaw: float, pitch: float, timeout: float) -> bool:
        """True once a FRAME_POSE shows the client drawing the commanded view.

        z is deliberately not compared: setviewpos puts the eye at the anchor's z and the
        player then settles onto the ground, so the drawn z is legitimately lower. A frame
        from any other view (the limbo/guided camera, or a view the console never reached)
        does not match, and the caller must not capture it.
        """
        def drawn(line):
            # only a FRAME_POSE says what was drawn: without this, a later console echo
            # would re-test the previous pose and could match before the client redraws
            if not FRAME_POSE_RE.search(line) or self.last_pose is None:
                return False
            pose = self.last_pose
            dyaw = (pose[4] - yaw + 180.0) % 360.0 - 180.0
            return (abs(pose[0] - x) <= VIEW_MATCH_XY and abs(pose[1] - y) <= VIEW_MATCH_XY
                    and abs(pose[3] - pitch) <= VIEW_MATCH_ANGLE and abs(dyaw) <= VIEW_MATCH_ANGLE)
        return self.wait_for(drawn, timeout) is not None

    def close(self):
        self.send("quit")
        try:
            self.proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=30)
        self.reader.join(timeout=10)
        self.log.close()


def view_target(spawn, offset, yaw_delta, pitch):
    x, y, z, yaw, _ = spawn
    rad = math.radians(yaw)
    fwd, right, up = offset
    tx = x + fwd * math.cos(rad) + right * math.sin(rad)
    ty = y + fwd * math.sin(rad) - right * math.cos(rad)
    return tx, ty, z + up, (yaw + yaw_delta) % 360.0, pitch


def capture_map(client_bin: Path, workdir: Path, data: Path, out: Path, map_name: str, team: str,
                cls: str, rt: str, extra: list[str], settle: float, anchors: dict) -> list[dict]:
    tag = f"{map_name}_rt{rt}"
    import etqw_harness as h
    savepath = h.make_private_dir("etqw-shots-")  # not mkdtemp: see h.PRIVATE_DIR_MODE
    args = [str(client_bin), "+set", "fs_basepath", str(data), "+set", "fs_savepath", str(savepath),
            "+set", "fs_game", "base", "+set", "si_rules", "sdGameRulesObjective",
            # mode 12 is WIDTH x HEIGHT (Common.cpp's mode table) and r_mode's default; set here so a
            # changed default cannot change the captures
            "+set", "r_renderAPI", "vulkan", "+set", "r_fullscreen", "0", "+set", "r_mode", "12",
            "+set", "r_multiSamples", "0", "+set", "r_vkValidation", "0", "+set", "r_rayTracing", rt,
            # the per-draw view: capture_map refuses to shoot a frame that is not the one it
            # asked for, and this line is the only place that says which view was drawn
            "+set", "com_logFrameTiming", "1", "+set", "g_logFramePoses", "1"]
    for i in range(0, len(extra), 2):
        args += ["+set", extra[i], extra[i + 1]]
    args += ["+devmap", map_name, "+clientTeam", team, "+clientClass", cls, "+closeLimbo"]
    results = []
    client = Client(args, out / f"{tag}.log", workdir)
    try:
        if not client.wait_for(lambda s: (m := ACTIVE_RE.search(s)) and int(m.group(1)) >= 25, 240.0):
            raise RuntimeError(f"{tag}: the client never reached play")
        # spawned, not in limbo: the position recorded for --anchors must be the player's
        for _ in range(30):
            client.send("playerPos")
            if client.wait_for(lambda s: SPAWNED_RE.search(s), 1.0):
                break
        else:
            raise RuntimeError(f"{tag}: the player never spawned")
        # god before the settle: the spawn can still be falling, and a death in that window
        # leaves the client in limbo, where setviewpos moves a player nobody is looking through
        client.send("god")
        time.sleep(settle)
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
        targets = [(name, *view_target(spawn, offset, yaw_delta, pitch), needs_noclip)
                   for name, offset, yaw_delta, pitch, needs_noclip in VIEWS]
        targets += [(name, *at, pitch, needs_noclip)
                    for name, at, pitch, needs_noclip in EXTRA_VIEWS.get(map_name, [])]
        noclip = False
        for name, x, y, z, yaw, pitch, needs_noclip in targets:
            if needs_noclip != noclip:
                client.send("noclip")
                noclip = needs_noclip
            # setviewpos takes the eye position; it subtracts the view height itself
            client.send(f"setviewpos {x:.2f} {y:.2f} {z:.2f} {yaw:.2f} {pitch:.2f} 0")
            time.sleep(settle)
            if not client.wait_view(x, y, yaw, pitch, VIEW_MATCH_TIMEOUT):
                raise RuntimeError(
                    f"{tag}_{name}: the client is not drawing the commanded view "
                    f"({x:.2f},{y:.2f} yaw {yaw:.2f} pitch {pitch:.2f}); last FRAME_POSE view "
                    f"{client.last_pose}. Shooting it would capture some other view (a limbo "
                    f"camera, or a command the console never reached)")
            path = out / f"{tag}_{name}.tga"
            if path.exists():
                path.unlink()
            client.send(f'set r_vkScreenshot "{path.as_posix()}"')
            # the engine prints "wrote" after it closed the file, or why it could not
            answer = client.wait_for(lambda s: SHOT_RE.search(s), 20.0)
            ok = bool(answer) and answer.group(1) == "wrote" and path.exists()
            size = SIZE_RE.search(answer.string) if ok else None
            results.append({"map": map_name, "rt": rt, "view": name, "file": path.name if ok else None,
                            "size": [int(size.group(1)), int(size.group(2))] if size else None,
                            "at": [round(x, 1), round(y, 1), round(z, 1), round(yaw, 1), pitch]})
            print(f"  {tag}_{name}: {'ok' if ok else 'NOT WRITTEN'}", flush=True)
            if client.proc.poll() is not None:
                raise RuntimeError(f"{tag}: the client exited (code {client.proc.returncode})")
    finally:
        client.close()
        h.remove_private_dir(savepath)
    return results


# --- Pillow parts: sheets, comparison and the warning heuristics ---

def _pil():
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageStat  # noqa: F401
        return Image, ImageChops, ImageDraw, ImageStat
    except ImportError:
        return None


def _open(path: Path):
    """The image as RGB, or None when it cannot be read (a truncated or broken TGA)."""
    pil = _pil()
    if pil is None:
        return None
    try:
        return pil[0].open(path).convert("RGB")
    except (OSError, ValueError):
        return None


def heuristics(path: Path, view: str) -> list[str] | None:
    """Warnings for one frame; None without Pillow (not checked, as opposed to clean)."""
    pil = _pil()
    if pil is None:
        return None
    _, _, _, ImageStat = pil
    img = _open(path)
    if img is None:
        return ["unreadable image"]
    w, h = img.size
    warnings = []
    whole = ImageStat.Stat(img.convert("L"))
    if whole.mean[0] < 8:
        warnings.append("frame almost entirely black")
    if whole.stddev[0] < 2:
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
    Image, _, ImageDraw, _ = pil
    thumb_w, label_h = 480, 18
    thumb_h = thumb_w * HEIGHT // WIDTH
    rows = max(1, (len(entries) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * thumb_w, rows * (thumb_h + label_h) + label_h), (32, 32, 32))
    draw = ImageDraw.Draw(sheet)
    draw.text((4, 2), title, fill=(255, 255, 255))
    for i, (label, path) in enumerate(entries):
        if label is None:   # padding to the end of a row
            continue
        x, y = (i % cols) * thumb_w, label_h + (i // cols) * (thumb_h + label_h)
        draw.text((x + 4, y + 2), label, fill=(255, 220, 120))
        img = _open(path) if path is not None and path.exists() else None
        if img is not None:
            img.thumbnail((thumb_w, thumb_h))
            sheet.paste(img, (x, y + label_h))
        else:
            draw.text((x + 8, y + label_h + 8), "missing" if path is None or not path.exists() else "unreadable",
                      fill=(255, 80, 80))
    sheet.save(out)
    print(f"  contact sheet: {out}")


def cmd_capture(a) -> int:
    known = {m[0] for m in MAPS}
    wanted = a.maps.split(",") if a.maps else [m[0] for m in MAPS]
    unknown = [m for m in wanted if m not in known]
    if unknown:
        raise SystemExit(f"unknown map(s) {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    maps = [m for m in MAPS if m[0] in wanted]
    client_bin, data = find_client(a.client), find_data(a.data)
    workdir = client_workdir(client_bin)
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    modes = ["1", "0"] if a.rt == "both" else [a.rt]
    anchors = {k: tuple(v) for k, v in ANCHORS.items()}
    if a.anchors:
        anchors.update({k: tuple(v) for k, v in
                        json.loads(Path(a.anchors).read_text(encoding="utf-8")).get("anchors", {}).items()})
    meta = {"platform": f"{platform.system()} {platform.machine()}", "client": str(client_bin),
            "workdir": str(workdir), "label": a.label, "requested_size": [WIDTH, HEIGHT],
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "shots": [], "failed": [], "anchors": anchors}
    print(f"client {client_bin}\nshaders from {workdir / 'base' / 'vkprogs'}", flush=True)
    for rt in modes:
        for map_name, team, cls in maps:
            print(f"{map_name}, r_rayTracing {rt}", flush=True)
            try:
                meta["shots"] += capture_map(client_bin, workdir, data, out, map_name, team, cls, rt,
                                             a.set or [], a.settle, anchors)
            except RuntimeError as e:
                print(f"  FAILED: {e}", flush=True)
                meta["failed"].append({"map": map_name, "rt": rt, "reason": str(e)})
            log = out / f"{map_name}_rt{rt}.log"
            shrunk = SHRUNK_RE.search(log.read_text(encoding="utf-8", errors="replace")) if log.exists() else None
            if shrunk:
                print(f"  WARNING: {shrunk.group(0)}; these frames do not compare with full-size ones", flush=True)
    if _pil() is None:
        print("Pillow not installed: no heuristics or contact sheets (pip install pillow)")
    # a --set of the window size makes WIDTH x HEIGHT the wrong expectation
    sized = set((a.set or [])[0::2]) & {"r_mode", "r_customWidth", "r_customHeight"}
    odd = sorted({tuple(s["size"]) for s in meta["shots"] if s.get("size")
                  if s["size"][0] < WIDTH or s["size"][1] < HEIGHT or s["size"][0] * HEIGHT != s["size"][1] * WIDTH})
    if odd and not sized:
        print(f"WARNING: frames of {', '.join(f'{w}x{h}' for w, h in odd)}: smaller than {WIDTH}x{HEIGHT} or"
              " another shape, so they do not compare with frames from a full-size window")
    for shot in meta["shots"]:
        shot["warnings"] = heuristics(out / shot["file"], shot["view"]) if shot["file"] else None
        for w in shot["warnings"] or []:
            print(f"  WARNING {shot['map']} rt{shot['rt']} {shot['view']}: {w}")
    (out / "screenshot_set.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # one row per map, so each view sits under the same view of the map above it
    cols = len(VIEWS) + max((len(EXTRA_VIEWS.get(m[0], [])) for m in maps), default=0)
    for rt in modes:
        entries = []
        for map_name, _, _ in maps:
            row = [(f"{s['map']} {s['view']}", out / s["file"] if s["file"] else None)
                   for s in meta["shots"] if s["rt"] == rt and s["map"] == map_name]
            if row:
                entries += row + [(None, None)] * (cols - len(row))
        contact_sheet(entries, out / f"sheet_rt{rt}.png", cols,
                      f"{a.label or 'screenshot set'}  {meta['platform']}  r_rayTracing {rt}")
    missing = sum(1 for s in meta["shots"] if not s["file"])
    written = len(meta["shots"]) - missing
    print(f"{written} shots written, {missing} missing, {len(meta['failed'])} map run(s) failed")
    return 1 if meta["failed"] or missing else 0


def cmd_compare(a) -> int:
    pil = _pil()
    if pil is None:
        print("compare needs Pillow (pip install pillow)")
        return 2
    _, ImageChops, _, ImageStat = pil
    base, head, out = Path(a.base), Path(a.head), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    def shots(d: Path) -> dict:
        meta = json.loads((d / "screenshot_set.json").read_text(encoding="utf-8"))
        table = {(s["map"], s["rt"], s["view"]): s for s in meta["shots"]}
        for f in meta.get("failed", []):   # a failed map run has no shots: list each view as missing
            for v in VIEWS + EXTRA_VIEWS.get(f["map"], []):
                table.setdefault((f["map"], f["rt"], v[0]), {"file": None})
        return table

    b_shots, h_shots = shots(base), shots(head)
    order = {m[0]: i for i, m in enumerate(MAPS)}
    vorder = {v[0]: i for i, v in enumerate(VIEWS)}
    keys = sorted(set(b_shots) | set(h_shots),
                  key=lambda k: (k[1] != "1", order.get(k[0], 99), vorder.get(k[2], 99), k[2]))
    head_runs = {k[:2] for k in h_shots}
    report = ["| map | rt | view | mean diff | max | pixels > 16 | warnings on head |", "|---|---|---|---|---|---|---|"]
    entries, problems = [], 0
    for key in keys:
        bs, hs = b_shots.get(key, {}), h_shots.get(key, {})
        bp = base / bs["file"] if bs.get("file") else None
        hp = head / hs["file"] if hs.get("file") else None
        warns = heuristics(hp, key[2]) if hp else None
        ib, ih = (_open(bp) if bp else None), (_open(hp) if hp else None)
        if key not in h_shots and key[2] not in vorder and key[:2] in head_runs:
            # an extra view the head's capture does not take, in a map and mode it did capture
            # (its script has an older view list, e.g. without canyon's hangar, added
            # 2026-10-20): listed, not a problem. A standard view, a map or a mode the head
            # lacks is missing
            cells = ["not taken by head", "", ""]
        elif ih is None:
            cells = ["**missing in head**" if hp is None else "**unreadable in head**", "", ""]
            problems += 1
        elif ib is None:
            cells = ["missing in base" if bp is None else "unreadable in base", "", ""]
        else:
            note = ""
            if ib.size != ih.size:
                note = f" (sizes differ: {ib.size} vs {ih.size})"
                ih = ih.resize(ib.size)
            diff = ImageChops.difference(ib, ih).convert("L")
            hist = diff.histogram()
            over = sum(hist[17:]) / max(1, sum(hist))
            cells = [f"{ImageStat.Stat(diff).mean[0]:.2f}{note}", str(diff.getextrema()[1]), f"{over * 100:.1f}%"]
        report.append(f"| {key[0]} | {key[1]} | {key[2]} | " + " | ".join(cells) +
                      f" | {'; '.join(warns) if warns else ('-' if warns is not None else 'n/a')} |")
        entries += [(f"base {key[0]} rt{key[1]} {key[2]}", bp), (f"head {key[0]} rt{key[1]} {key[2]}", hp)]
    (out / "compare.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    print("\n".join(report))
    contact_sheet(entries, out / "compare_sheet.png", 2, f"left: {base}   right: {head}")
    return 1 if problems else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("capture", help="capture the standard set")
    c.add_argument("--out", required=True, help="output folder (outside the checkout, e.g. ../shots/pr)")
    c.add_argument("--client", help="client binary (default: $OPENETQW_CLIENT_BINARY or the build dir)")
    c.add_argument("--data", help="retail data root (default: $ETQW_DATA_PATH)")
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
