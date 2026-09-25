#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vulkan client spot checks, one stock map per retail campaign (stock-maps M3).

North America: outskirts, Northern Europe: ark, South Pacific: canyon,
Africa: refinery (override with --maps). For each map the Vulkan client loads
the map (+devmap, r_shadows 1), joins GDF as a soldier, renders for a while,
receives SIGTERM, and must exit 0. The metrics the backend prints at shutdown
are asserted:
  - frames presented, world draws, MegaTexture (terrain) draws > 0
  - light interaction draws > 0; stencil shadow draws > 0 whenever a shadow-casting
    light with shadow casters was in view (the "Vulkan shadow lights" diagnostic)
  - no Vulkan validation error / VK_ERROR / device loss, no ERROR banner,
    no fatal signal, no MegaTexture open failure
Vulkan validation messages can only appear when VK_LAYER_KHRONOS_validation is
installed; the test reports whether it was active.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the client binary is missing.
"""

import argparse
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN_MAPS = {"outskirts": "North America", "ark": "Northern Europe", "canyon": "South Pacific", "refinery": "Africa",
                 "valley": "North America", "area22": "North America", "quarry": "Northern Europe", "salvage": "Northern Europe",
                 "sewer": "South Pacific", "volcano": "South Pacific", "slipgate": "Africa", "island": "Africa"}
# Default spot-check views, one per campaign, each known to have shadow-casting lights with
# casters in view, so stencil shadows are asserted strictly there: (team, setviewpos or None).
# From the GDF spawns of canyon and refinery (and slipgate/island) no shadow-casting light is visible.
DEFAULT_VIEWS = {"outskirts": ("gdf", None), "ark": ("gdf", None), "canyon": ("strogg", None),
                 "refinery": ("gdf", "-4072 3100 200 90")}  # refinery loading bay, light_interior_13..16
METRICS_RE = re.compile(r"Vulkan frames presented:\s*(\d+),\s*world draws:\s*(\d+),\s*GUI draws:\s*(\d+),"
                        r"\s*shadow draws:\s*(\d+),\s*light interactions:\s*(\d+)")
SHADOW_LIGHTS_RE = re.compile(r"Vulkan shadow lights:\s*(\d+) shadow-casting light views,\s*(\d+) with shadow casters")
SPECIAL_RE = re.compile(r"special draws:\s*(\d+) depth,\s*(\d+) MegaTexture,\s*(\d+) CPU-skinned,\s*(\d+) GPU-skinned,"
                        r"\s*(\d+) sky,\s*(\d+) extra stages,\s*(\d+) water")
# the game's spawn line for a non-spectator ("Player N (...) spawned at ... spectating 0"),
# or script debug prints that only the unpatched DVD scripts make
ACTIVE_MARKERS = ("spectating 0", "parachute::Idle", "weaponRestored", "RespawnWaves_")
BAD_MARKERS = ("Vulkan validation error", "VK_ERROR_", "DEVICE_LOST", "FATAL ERROR", "FATAL SIGNAL CAUGHT",
               "idMegaTexture::OpenFile: failed", "idMegaTexture::OpenFile: truncated", "idMegaTexture::OpenFile: unsupported")


def find_client(explicit):
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
        raise h.SkipTest(f"client binary not found: {p}")
    for p in h.binary_candidates("client"):
        if p.is_file():
            return p
    raise h.SkipTest("client binary not found under build/")


def run_map(client, data, map_name, render_seconds, log_path, team="gdf", at=None):
    args = [str(client), "+set", "fs_basepath", str(data), "+set", "fs_game", "base",
            "+set", "si_rules", "sdGameRulesObjective", "+set", "r_renderAPI", "vulkan", "+set", "r_shadows", "1",
            "+devmap", map_name, "+clientTeam", team, "+clientClass", "soldier", "+closeLimbo"]
    lines = []
    q = queue.Queue()
    proc = subprocess.Popen(args, cwd=str(REPO_ROOT), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, errors="replace")

    def reader():
        for line in iter(proc.stdout.readline, ""):
            q.put(line.rstrip("\n"))
        q.put(None)
    threading.Thread(target=reader, daemon=True).start()

    start = time.time()
    map_seen = active = None
    sent = False
    moved = False
    result = {"map": map_name, "campaign": CAMPAIGN_MAPS.get(map_name, "?"), "team": team}
    while time.time() - start < 240.0:
        try:
            line = q.get(timeout=0.2)
        except queue.Empty:
            line = ""
        if line is None:
            break
        if line:
            lines.append(line)
            if map_seen is None and f"maps/{map_name}" in line and ("Map:" in line or "Loading map" in line):
                map_seen = time.time()
            if active is None and any(m in line for m in ACTIVE_MARKERS):
                active = time.time()
        if active is None and map_seen is not None and time.time() - map_seen > 25.0:
            active = time.time()  # fallback: map loaded and running for a while
        if at and active is not None and not moved and time.time() - active >= 2.0:
            # devmap enables cheats: move the view next to a shadow-casting light
            try:
                proc.stdin.write(f"setviewpos {at}\n")
                proc.stdin.flush()
            except OSError:
                pass
            moved = True
        if active is not None and not sent and time.time() - active >= render_seconds:
            proc.send_signal(signal.SIGTERM)
            sent = True
        if proc.poll() is not None and (sent or line is None):
            break
    try:
        proc.wait(timeout=30.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        result["error"] = "client did not exit within 30 s of SIGTERM"
    while True:
        try:
            line = q.get(timeout=0.5)
        except queue.Empty:
            break
        if line is None:
            break
        lines.append(line)
    if log_path:
        Path(log_path).write_text("\n".join(lines) + "\n", errors="replace")
    text = "\n".join(lines)
    result["exit_code"] = proc.returncode
    result["sigterm_sent"] = sent
    result["map_loaded"] = map_seen is not None
    result["validation_layer"] = "not installed" if "VK_LAYER_KHRONOS_validation is not installed" in text else "active or not reported"
    m = METRICS_RE.search(text)
    if m:
        result.update(frames=int(m.group(1)), world_draws=int(m.group(2)), gui_draws=int(m.group(3)),
                      shadow_draws=int(m.group(4)), light_interactions=int(m.group(5)))
    sl = SHADOW_LIGHTS_RE.search(text)
    if sl:
        result.update(shadow_light_views=int(sl.group(1)), shadow_light_views_with_casters=int(sl.group(2)))
    s = SPECIAL_RE.search(text)
    if s:
        result.update(megatexture_draws=int(s.group(2)), sky_draws=int(s.group(5)), water_draws=int(s.group(7)))
    bad = sorted({mk for mk in BAD_MARKERS if mk in text})
    errors = [l for i, l in enumerate(lines) if l.startswith("ERROR:") and i > 0 and lines[i - 1].startswith("********")]
    result["bad_markers"] = bad
    result["error_banners"] = errors[:5]
    return result


def check(r):
    fails = []
    if not r.get("map_loaded"):
        fails.append("map never loaded")
    if not r.get("sigterm_sent"):
        fails.append("never reached the rendering window (no SIGTERM sent)")
    if r.get("exit_code") != 0:
        fails.append(f"exit code {r.get('exit_code')}")
    for key in ("frames", "world_draws", "megatexture_draws", "light_interactions"):
        if not r.get(key):
            fails.append(f"{key} = {r.get(key)} (expected > 0)")
    # stencil shadows: whenever a shadow-casting light with shadow casters was in view, shadow
    # volumes must have been drawn; zero shadow-light views means the view had nothing to shadow
    if r.get("shadow_light_views_with_casters") is None:
        fails.append("no 'Vulkan shadow lights' diagnostics line")
    elif r["shadow_light_views_with_casters"] > 0 and not r.get("shadow_draws"):
        fails.append(f"{r['shadow_light_views_with_casters']} shadow-casting light views had casters but shadow_draws = 0")
    if r.get("bad_markers"):
        fails.append(f"bad markers: {r['bad_markers']}")
    if r.get("error_banners"):
        fails.append(f"ERROR banners: {r['error_banners']}")
    if r.get("error"):
        fails.append(r["error"])
    return fails


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--client-binary")
    ap.add_argument("--maps", help="comma-separated maps (default: one per campaign at a view with shadow-casting lights)")
    ap.add_argument("--render-seconds", type=float, default=10.0)
    ap.add_argument("--team", default="gdf", choices=("gdf", "strogg"))
    ap.add_argument("--at", help="'x y z yaw' passed to setviewpos after spawning (cheats are on under devmap)")
    ap.add_argument("--log-dir")
    ap.add_argument("--json")
    args = ap.parse_args(argv)
    try:
        client = find_client(args.client_binary or os.environ.get("OPENETQW_CLIENT_BINARY"))
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
    results, failed = [], False
    maps = [x for x in (args.maps or ",".join(DEFAULT_VIEWS)).split(",") if x]
    for m in maps:
        team, at, strict = args.team, args.at, False
        if not args.maps and m in DEFAULT_VIEWS:
            team, at = DEFAULT_VIEWS[m]
            strict = True
        print(f"[{m}] ({CAMPAIGN_MAPS.get(m, '?')}, {team}{', at ' + at if at else ''}) launching Vulkan client ...", flush=True)
        r = run_map(client, data, m, args.render_seconds, (log_dir / f"{m}-{team}.log") if log_dir else None, team, at)
        f = check(r)
        if strict and not r.get("shadow_draws"):
            f.append("shadow_draws = 0 at a default view that has shadow-casting lights")
        r["failures"] = f
        results.append(r)
        failed |= bool(f)
        print(f"[{m}] {'PASS' if not f else 'FAIL'} frames={r.get('frames')} world={r.get('world_draws')} "
              f"mega={r.get('megatexture_draws')} shadows={r.get('shadow_draws')} interactions={r.get('light_interactions')} "
              f"shadowLightViews={r.get('shadow_light_views')}/{r.get('shadow_light_views_with_casters')} sky={r.get('sky_draws')} water={r.get('water_draws')} exit={r.get('exit_code')} validation={r['validation_layer']}", flush=True)
        for x in f:
            print(f"    - {x}")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=1))
    print(f"\n{sum(1 for r in results if not r['failures'])}/{len(results)} campaign maps passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
