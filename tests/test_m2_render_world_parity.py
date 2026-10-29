#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M2 render-world parity: the same scripted scenes on a listen host and a dedicated server.

The dedicated server's game uses idRenderWorldHeadless; a listen host's game uses the
client's real idRenderWorldLocal. The test runs a render-world probe and three short
gameplay scenes on both:

  probe       NumAreas, PointInArea at the world centre and well outside the world;
  prone       an attempted render-model trace on a prone bot through its combat model
              (ModelTrace, a stub on both worlds today), plus the game collision world's
              hit on that bot for the same segment;
  radiuspush  RadiusPush through a wall the collision world found, whose reach is
              decided by the render world's FastWorldTrace (also a stub on both
              worlds: it answers false, so the wall is ignored);
  exit        a vehicle exit with the vehicle outside the map, decided by
              PointInArea on the exit joints.

The game prints one M2_PARITY line per scene (game command m2Parity; test-only). This
test compares the outcomes and reports the raw numbers. It is the evidence for the
owner's headless-render-world question (open since 2026-10-21), not a decision: the current
relationship is encoded below, so a change - a fix, a regression, or a new stub - fails
and the expectation and ledger 013 are updated with it.

Current outcome (valley, retail 1.5): engine a38e311 is this branch's base and it
changes no engine source; the game is bb8e0c5 plus the test-only m2Parity diagnostic
at 8a13028.
  * the dedicated render world has one area and answers 0 for every point, including
    a point outside the map; the listen host has the map's .procb areas and answers
    -1 outside;
  * both render worlds' ModelTrace answer false (both ModelTrace implementations are
    stubs), while the game collision world hits the prone bot itself with the same
    fraction on both hosts;
  * RadiusPush is identical on both hosts for the same collision-world scene, and that
    identity is stub parity, not collision parity: both worlds' FastWorldTrace answer
    false (idRenderWorldLocal::FastWorldTrace, src/renderer/RenderWorld.cpp:797, and the
    headless stub), so the wall the collision world found is ignored on both and the
    push keeps its full reach. A working render-world trace would shorten it;
  * a vehicle exit outside the map is accepted by the dedicated server (every point
    is area 0) and rejected by the listen host (no exit joint is in an area).

In the radiuspush line, `listed` is how many entities the collision-world radius query
returned and `pushed` how many of those actually move; `wallFound` and `wallDistance`
describe the first wall the collision world found on the way to the entity. `listed` is
asserted equal on both hosts, not against a fixed count: it depends on the scene's
entity set.

Exit 77 when the retail data or either binary is missing. Needs a Vulkan window (on
headless Linux, SDL_VIDEO_DRIVER=offscreen, which this test sets when DISPLAY is unset).
"""

from __future__ import annotations

import argparse
import math
import os
import platform
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import etqw_harness as h  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAP = "valley"
READY_TIMEOUT = 180.0
COMMAND_TIMEOUT = 60.0
PRONE_TICKS_SECONDS = 2.0

# Outside valley's east edge: the exit scene puts the vehicle at (worldMaxX + 4096,
# -10154, 1500) with the player at (8505, -10154, 1500). These are the map's
# coordinates, not a tolerance.
EXIT_REFERENCE = (8505.0, -10154.0, 1500.0)
EXIT_YAW = 0.0

M2_LINE = re.compile(r"M2_PARITY: scene=(\w+)(.*)$")
TOKEN = re.compile(r"\s+(\w+)=((?:\([^()]*\)(?:-\([^()]*\))?)|[^\s]+)")
INT = re.compile(r"^-?\d+$")
FLOAT = re.compile(r"^-?(?:\d+\.\d*|\.\d+)(?:[eE][-+]?\d+)?$")


class Failed(Exception):
    pass


def coerce(value: str):
    if value.startswith("("):
        # worldBounds=(a b c)-(d e f); a plain vector is (x y z), whose components
        # may themselves be negative, so split only on the ") - (" boundary.
        if ")-(" in value:
            first, second = value[1:-1].split(")-(")
            return (tuple(float(x) for x in first.split()),
                    tuple(float(x) for x in second.split()))
        return tuple(float(x) for x in value[1:-1].split())
    if INT.match(value):
        return int(value)
    if FLOAT.match(value):
        return float(value)
    return value


def parse_m2(text: str):
    m = M2_LINE.search(text)
    if not m:
        return None
    scene = m.group(1)
    values = {}
    for t in TOKEN.finditer(m.group(2)):
        values[t.group(1)] = coerce(t.group(2))
    return scene, values


def scene(proc, command: str, wanted: str) -> dict:
    lines = proc.command(command, COMMAND_TIMEOUT)
    if lines is None:
        raise Failed(f"{command}: the console did not answer (echo SYNC timed out)")
    for line in reversed(lines):
        parsed = parse_m2(line.text)
        if parsed and parsed[0] == wanted:
            return parsed[1]
    detail = "; ".join(ln.text for ln in lines if "M2_PARITY" in ln.text) or "no M2_PARITY line"
    raise Failed(f"{command}: no scene={wanted} line ({detail})")


def bot_spawn_predicate(text: str):
    m = h.SPAWN_RE.search(text)
    if (m and m.group(2).startswith("Bot") and m.group(7) == "0"
            and m.group(6).lower() != "none"):
        return m
    return None


def run_scenes(proc, role: str, prone_seconds: float) -> dict:
    """Start the process, run every scene on its bot, and return {scene: fields}."""
    proc.start()
    try:
        if proc.wait_for_text(h.READY_MARKER, READY_TIMEOUT) is None:
            raise Failed(f"{role}: no '{h.READY_MARKER}' within {READY_TIMEOUT:.0f}s")
        if proc.sync(COMMAND_TIMEOUT) is None:
            raise Failed(f"{role}: the console did not answer after load")

        if role == "listen":
            # The local player stays spectating (no team): the listen host's render
            # world exists without a spawned pawn, and spawning one changes the bot's
            # descent and combat state between proneSetup and proneTrace. closeLimbo
            # still runs, because a listen host starts in the limbo menu.
            proc.send("closeLimbo")
            if proc.sync(COMMAND_TIMEOUT) is None:
                raise Failed(f"{role}: closeLimbo did not answer")
        spawn = proc.wait_for(bot_spawn_predicate, 3.0, proc.mark())
        if spawn is None:
            proc.send("addbot gdf soldier")
            spawn = proc.wait_for(bot_spawn_predicate, 60.0)
        if spawn is None:
            raise Failed(f"{role}: no bot joined the GDF and spawned")
        bot = spawn[2].group(1)
        if proc.sync(COMMAND_TIMEOUT) is None:
            raise Failed(f"{role}: the console did not answer after addbot")

        out = {}
        out["probe"] = scene(proc, "m2Parity probe", "probe")
        setup = scene(proc, f"m2Parity prone {bot}", "proneSetup")
        out["proneSetup"] = setup
        time.sleep(prone_seconds)
        out["prone"] = scene(proc, f"m2Parity proneTrace {bot}", "prone")
        out["radiuspush"] = scene(proc, f"m2Parity radiuspush {bot}", "radiuspush")

        vehicle = None
        for line in proc.command("listEntities sdVehicle_RigidBody", COMMAND_TIMEOUT) or []:
            m = re.search(r"sdVehicle_RigidBody_vehicle_(\w+)_(\d+)", line.text)
            if m and m.group(1) == "badger":
                vehicle = m.group(0)
                break
        if vehicle is None:
            raise Failed(f"{role}: no badger in listEntities sdVehicle_RigidBody")
        ref = EXIT_REFERENCE
        out["exit"] = scene(
            proc,
            f"m2Parity exit {bot} {vehicle} {ref[0]:.0f} {ref[1]:.0f} {ref[2]:.0f} {EXIT_YAW:.0f}",
            "exit")
        out["bot"] = bot
        out["vehicle"] = vehicle
        out["lines"] = [ln.text for ln in proc.snapshot() if "M2_PARITY" in ln.text]
        return out
    finally:
        rc = proc.quit(60.0)
        proc.test_exit_code = rc
        print(f"[{role}] exit code {rc}")


def vec_close(a, b, eps=1e-3) -> bool:
    return math.dist(a, b) <= eps


def compare(ded: dict, listen: dict) -> list[str]:
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    def expect(role: str, scene_name: str, key: str, value) -> None:
        rec = (ded if role == "dedicated" else listen)[scene_name]
        if rec.get(key) != value:
            fail(f"{role} {scene_name}: {key}={rec.get(key)!r}, expected {value!r}")

    # probe: the same collision world bounds; the two render worlds answer differently
    if ded["probe"].get("worldBounds") != listen["probe"].get("worldBounds"):
        fail(f"probe: world bounds differ: {ded['probe'].get('worldBounds')} vs {listen['probe'].get('worldBounds')}")
    expect("dedicated", "probe", "numAreas", 1)
    expect("dedicated", "probe", "pointInside", 0)
    expect("dedicated", "probe", "pointOutside", 0)
    expect("listen", "probe", "pointInside", 0)
    expect("listen", "probe", "pointOutside", -1)
    if listen["probe"].get("numAreas", 0) <= 1:
        fail(f"listen probe: numAreas={listen['probe'].get('numAreas')!r}, expected the map's .procb areas (> 1)")

    # prone: both have a combat model with real bounds; neither render world traces it
    for role, rec in (("dedicated", ded["prone"]), ("listen", listen["prone"])):
        for key, value in (("prone", 1), ("frozen", 1), ("combatModel", 1),
                           ("boundsClear", 0), ("modelHit", 0), ("modelFraction", 1.0),
                           ("clipHit", 1)):
            if rec.get(key) != value:
                fail(f"{role} prone: {key}={rec.get(key)!r}, expected {value!r}")
        # clipHit alone could be a wall or another entity: the trace uses
        # MASK_SHOT_RENDERMODEL with no pass entity, so the approach loop's +X fallback
        # can put something else on the line. The hit must be the prone bot itself.
        if rec.get("clipEntity") != rec.get("client"):
            fail(f"{role} prone: clipEntity={rec.get('clipEntity')!r}, expected the prone "
                 f"bot's entity {rec.get('client')!r} (a wall or another entity was hit)")
        size = rec.get("boundsSize")
        if not isinstance(size, tuple) or any(v <= 1.0 for v in size):
            fail(f"{role} prone: combat-model bounds are cleared or degenerate: {size!r}")
    if abs(ded["prone"].get("clipFraction", 0.0) - listen["prone"].get("clipFraction", 0.0)) > 1e-4:
        fail(f"prone: the game collision world's fraction differs: "
             f"{ded['prone'].get('clipFraction')} vs {listen['prone'].get('clipFraction')}")

    # radiuspush: the collision world finds the same wall on both hosts, and both
    # take the same FastWorldTrace decision (a stub that answers false), so the push
    # result is parity of the stub, not of a real collision world
    for key in ("wallFound", "selfListed", "pushed"):
        expect("dedicated", "radiuspush", key, 1)
        expect("listen", "radiuspush", key, 1)
    for key in ("wallDistance", "centerDistance", "speed"):
        a, b = ded["radiuspush"].get(key), listen["radiuspush"].get(key)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or abs(a - b) > 1e-4:
            fail(f"radiuspush: {key} differs: {a!r} vs {b!r}")
    for key in ("reference", "behind", "velocity"):
        a, b = ded["radiuspush"].get(key), listen["radiuspush"].get(key)
        if not isinstance(a, tuple) or not isinstance(b, tuple) or not vec_close(a, b):
            fail(f"radiuspush: {key} differs: {a!r} vs {b!r}")
    if ded["radiuspush"].get("listed") != listen["radiuspush"].get("listed"):
        fail(f"radiuspush: listed entities differ: {ded['radiuspush'].get('listed')} vs {listen['radiuspush'].get('listed')}")

    # exit: the headless world accepts, the real world rejects (ledger 013)
    expect("dedicated", "exit", "entered", 1)
    expect("listen", "exit", "entered", 1)
    expect("dedicated", "exit", "exitAccepted", 1)
    expect("dedicated", "exit", "stillInVehicle", 0)
    expect("dedicated", "exit", "vehicleArea", 0)
    expect("listen", "exit", "exitAccepted", 0)
    expect("listen", "exit", "stillInVehicle", 1)
    expect("listen", "exit", "vehicleArea", -1)
    if ded["exit"].get("numAreas") != ded["probe"].get("numAreas"):
        fail(f"dedicated: probe numAreas={ded['probe'].get('numAreas')} but exit numAreas={ded['exit'].get('numAreas')}")
    if listen["exit"].get("numAreas") != listen["probe"].get("numAreas"):
        fail(f"listen: probe numAreas={listen['probe'].get('numAreas')} but exit numAreas={listen['exit'].get('numAreas')}")

    return failures


def git_commit() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def game_pin() -> str:
    """The game revision the engine branch pins (tools/ci/companion.ref)."""
    try:
        return (REPO_ROOT / "tools" / "ci" / "companion.ref").read_text().strip() or "unknown"
    except OSError:
        return "unknown"


def game_commit() -> str:
    """The game checkout the binaries were built from, when it is a git checkout."""
    repo = Path(os.environ.get("OPENETQW_GAME_REPO") or (REPO_ROOT.parent / "openETQW-game"))
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def main(argv) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default=DEFAULT_MAP, choices=(DEFAULT_MAP,),
                    help="only valley has the recorded exit reference")
    ap.add_argument("--ded-port", type=int, default=29982)
    ap.add_argument("--listen-port", type=int, default=29984)
    ap.add_argument("--log-dir")
    ap.add_argument("--prone-seconds", type=float, default=PRONE_TICKS_SECONDS)
    args = ap.parse_args(argv)

    try:
        ded_bin = h.find_ded_binary(args.ded_binary)
        client_bin = Path(args.client_binary).expanduser() if args.client_binary else None
        if client_bin is not None and not client_bin.is_file():
            raise h.SkipTest(f"client binary not found: {client_bin}")
        if client_bin is None:
            client_bin = next((p for p in h.binary_candidates("client") if p.is_file()), None)
            if client_bin is None:
                raise h.SkipTest("client binary not found under build/")
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP

    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("SDL_VIDEO_DRIVER", "offscreen")

    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else h.make_private_dir("etqw-m2-parity-")
    log_dir.mkdir(parents=True, exist_ok=True)

    print(f"host={platform.node()} os={platform.platform()} arch={platform.machine()}")
    print(f"commit={git_commit()} game={game_commit()} game_pin={game_pin()} data={data} map={args.map}")
    print(f"binaries: dedicated={ded_bin} client={client_bin}")
    print(f"logs: {log_dir}")

    game_cvars = {
        "si_rules": "sdGameRulesObjective", "si_pure": "0", "bot_enable": "1",
        "bot_threading": "0", "net_allowCheats": "1", "si_teamForceBalance": "0",
        "g_warmup": "0",
    }
    ded = h.DedicatedServer(ded_bin, data, args.map, args.ded_port,
                            log_path=log_dir / "dedicated.log", cvars=game_cvars, cwd=REPO_ROOT)
    listen = VulkanClient(client_bin, data, args.listen_port, map_name=args.map,
                          log_path=log_dir / "listen.log",
                          cvars={**game_cvars, "r_vkValidation": "0"})

    ded_scenes = listen_scenes = None
    failures: list[str] = []
    try:
        ded_scenes = run_scenes(ded, "dedicated", args.prone_seconds)
        listen_scenes = run_scenes(listen, "listen", args.prone_seconds)
        for role, rec in (("dedicated", ded_scenes), ("listen", listen_scenes)):
            print(f"[{role}] bot={rec['bot']} vehicle={rec['vehicle']}")
            for line in rec["lines"]:
                print(f"[{role}] {line}")
        failures.extend(compare(ded_scenes, listen_scenes))
        for role, proc in (("dedicated", ded), ("listen", listen)):
            rc = getattr(proc, "test_exit_code", None)
            if rc != 0:
                failures.append(f"{role}: exit code {rc!r} after the scenes (expected 0)")
    except Failed as e:
        failures.append(str(e))
    finally:
        for proc in (ded, listen):
            if proc.alive():
                proc.kill()

    # failures in the engine/game output are failures whatever the scenes say
    for role, proc in (("dedicated", ded), ("listen", listen)):
        found = h.scan_failures((ln.text for ln in proc.snapshot()), run_failed=bool(failures))
        for item in found:
            failures.append(f"{role} log: {item['marker']} at line {item['line']}: {item['text']}")

    if failures:
        print(f"\nFAIL ({len(failures)}):")
        for f in failures:
            print(f"  - {f}")
        return h.EXIT_FAIL
    print("\nPASS: the scenes have the recorded M2 parity relationship")
    return h.EXIT_PASS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
