#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M5 step 14 (docs/vehicle-responsiveness.md stage 3): what the Vulkan client draws from
a vehicle seat whose presentation the seat gate allows.

--mode devmap: single player on valley (+devmap). --mode join: the Vulkan client joins a
dedicated server, so the network client's tick sample (ClientPrediction, new frames only)
and re-prediction after snapshots are exercised. --mode listen: the Vulkan client hosts
(+spawnServer), the authoritative path with presentation copies. The player spawns, enters a badger's
driver seat (enterVehicle, from the server's console when joined) and drives forward,
then backs up (clientMove). g_logFramePoses prints one
FRAME_POSE line per draw: the drawn view, the seat and the vehicle's render-world pose
(veh=). At each cap, with g_unlock_vehicleSeats listing the badger's standard views:

  S0 seat     every draw of the phase is from the badger's driver seat (pos 0), and each
              phase has >= MIN_PAIRS draw pairs
  S1 view     the drawn view origin changes between consecutive draws (>= 99%)
  S2 hull     the vehicle's drawn pose changes between consecutive draws (>= 99%)
  S3 path     the drawn hull lies on one interpolation path (frame-poses A7b's knot fit)
  S4 steps    the hull's step across a tick is at most 2.5 x the median step within one
              (the presentation draws a fixed age; a stepped hull would cross a tick in
              one step of a whole tick's motion: 4x at cap 120)

Remote entities while seated (stage 4; join and listen, which have a headless peer): at
cap 120, with the driver parked in the listed seat, the peer walks forward, then back:

  R1 peer     the peer's drawn pose changes between consecutive draws (>= 99%)
  R2 path     it lies on one interpolation path (A7b's knot fit)

and with the seat unlisted the peer's pose changes only on the pairs that cross a tick.

Negative control (the lowest cap): with g_unlock_vehicleSeats empty the seat is not
listed, and the view and hull change only on the draw pairs that cross a tick: S1 and S2
fail in the per_tick shape. Guards, with every seat listed ('*'): the badger driver's
chase camera (smooth_locked, cycled with cycleVehicleCamera; no presentable recipe) and
the icarus (a jetpack: no recipe can cover it) draw the tick's view: their view (and the
chase camera's hull) never changes within a tick (at most one pair), while it moves.

Safety (nothing presented reaches tick state) is shown by meson seat-gate: its run B lists
every seat, so the presentation runs during the seat tour, and SIM_HASH, VEH_TRACE and
SIM_VIEW must equal the run with nothing listed.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the client binary is missing.
"""

import argparse
import math
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_frame_poses import (DevmapClient, EDGE_S, MIN_PAIRS, POS_EPS, fraction_check,  # noqa: E402
                              moved, onpath_check, parse_pose, rate)
from test_reconnect_restart import Failed, connect, spawn  # noqa: E402
from test_remote_client_map_change import READY, RemoteClient  # noqa: E402

LISTED = "badger:*:standard"
# the badger single player enters by entityDef (valley's 57), named so that a dedicated
# server enters the same one: by entityDef it takes the first live badger in its spawn
# order, valley's 37, parked on a slope where backing up rolls it
BADGER = "sdVehicle_RigidBody_vehicle_badger_57"
THROTTLE = 96
STEP_RATIO = 2.5
SHAPE_TOL = 0.05     # per_tick shape: at most this fraction of pairs may disagree


def seat_phase_poses(proc, begin, end):
    poses = [p for ln in proc.snapshot(begin, end) if (p := parse_pose(ln.text))]
    if len(poses) < 3:
        return poses
    t0, t1 = poses[0]["us"] + EDGE_S * 1e6, poses[-1]["us"] - EDGE_S * 1e6
    return [p for p in poses if t0 <= p["us"] <= t1]


def step_check(poses):
    """S4: the hull's median step across a tick against its median step within one."""
    cross, within = [], []
    for a, b in zip(poses, poses[1:]):
        if a["veh"] is None or b["veh"] is None:
            continue
        d = math.dist(a["veh"][:3], b["veh"][:3])
        if d <= POS_EPS:
            continue
        (cross if a["gt"] != b["gt"] else within).append(d)
    if len(cross) < 5 or len(within) < 5:
        return {"ok": False, "detail": f"too few moving pairs ({len(cross)} across a tick, {len(within)} within)"}
    ratio = statistics.median(cross) / statistics.median(within)
    return {"ok": ratio <= STEP_RATIO,
            "detail": f"median step across a tick {statistics.median(cross):.3f} u, within {statistics.median(within):.3f} u"
                      f" (ratio {ratio:.2f}, need <= {STEP_RATIO})"}


def per_tick(res):
    """The stepped shape: the pose changes on the pairs that cross a tick and no others."""
    return not res["ok"] and res["n"] >= MIN_PAIRS and res["misaligned"] <= max(1, int(SHAPE_TOL * res["n"]))


def within_tick_changes(poses, key):
    """Guards: how many draw pairs within one tick changed the pose (none may: nothing is
    presented), out of how many such pairs."""
    within = [(a, b) for a, b in zip(poses, poses[1:]) if a["gt"] == b["gt"]]
    return sum(1 for a, b in within if moved(a[key], b[key])), len(within)


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("devmap", "join", "listen"), default="devmap")
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29930)
    ap.add_argument("--caps", default="60,120,240")
    ap.add_argument("--phase-seconds", type=float, default=2.0)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)
    try:
        vk = find_client(args.client_binary)
        ded = h.find_ded_binary(args.ded_binary) if args.mode != "devmap" else None
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    caps = [int(c) for c in args.caps.split(",")]
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
    view_cvars = {"com_unlockFPS": "1", "r_swapInterval": "0", "r_vkValidation": "0"}
    def log(name):
        return (log_dir / f"{args.mode}-{name}.log") if log_dir else None
    if args.mode == "join":
        # g_logFramePoses is a cheat: the server allows cheats, and the client sets it once connected
        game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                "si_teamForceBalance": "0", "net_allowCheats": "1"}
        srv = h.DedicatedServer(ded, data, args.map, args.port, log_path=log("server"), cvars=game)
        srv.name = "SERVER"
        obs = VulkanClient(vk, data, args.port + 1, connect_to=f"127.0.0.1:{args.port}", log_path=log("client"),
                           cvars={"si_pure": "0", "ui_name": "Driver", "net_clientShowSnapshots": "1",
                                  "g_unlock_vehicleSeats": LISTED, **view_cvars})
        obs.name = "DRIVER"
        procs = [("client", obs), ("server", srv)]
    elif args.mode == "listen":
        game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                "si_teamForceBalance": "0", "net_allowCheats": "1"}
        obs = VulkanClient(vk, data, args.port, map_name=args.map, log_path=log("host"),
                           cvars={**game, "ui_name": "Driver", "g_unlock_vehicleSeats": LISTED, **view_cvars})
        obs.name = "DRIVER"
        srv = obs
        procs = [("host", obs)]
    else:
        obs = DevmapClient(vk, data, args.port, map_name=args.map, log_path=log("devmap"),
                           cvars={"si_rules": "sdGameRulesObjective", "ui_name": "Driver", "g_unlock_vehicleSeats": LISTED,
                                  **view_cvars})
        srv = obs
        procs = [("devmap", obs)]
    peer = None
    if args.mode != "devmap":
        peer = RemoteClient(ded, data, args.port, args.port + 2, log_path=log("peer"))
        peer.cvars["ui_name"] = "Peer"
        peer.name, peer.slot = "Peer", None
        procs.insert(0, ("peer", peer))
    failures = []

    def phase(name, cap, drive):
        begin = obs.mark()
        obs.send(f"echo FP_BEGIN {name} {cap}")
        drive()
        obs.send(f"echo FP_END {name} {cap}")
        hit = obs.wait_for(lambda s: s.strip() == f"FP_END {name} {cap}", 10.0, begin)
        if not hit:
            raise Failed(f"no end of phase {name} at cap {cap}")
        return seat_phase_poses(obs, begin, hit[0])

    def peer_walk():
        peer.send("clientMove 127 0 0")
        time.sleep(args.phase_seconds)
        peer.send("clientMove -127 0 0")
        time.sleep(args.phase_seconds)
        peer.send("clientMove 0 0 0")

    def drive():
        # three-quarter throttle: at full throttle the badger can roll or wreck itself
        obs.send(f"clientMove {THROTTLE} 0 0")
        time.sleep(args.phase_seconds)
        obs.send(f"clientMove -{THROTTLE} 0 0")
        time.sleep(args.phase_seconds)
        obs.send("clientMove 0 0 0")

    def seat_up(vehicle=BADGER):
        """(Re)seat the player: each phase starts from a fresh entry, since a drive can roll
        the vehicle on a slope and the player respawns on foot (valley's badger 37 on a
        dedicated server)."""
        last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
        if vehicle != BADGER and last is not None and last["seat"] is not None:
            srv.send(f"exitVehicle {obs.slot}")
            time.sleep(1.0)
            last = None
        if last is None or last["seat"] is None:
            mark = srv.mark()
            srv.send(f"enterVehicle {obs.slot} {vehicle}")
            if srv.wait_for(lambda t: "ENTER_VEHICLE: no vehicle found" in t, 2.0, mark) and vehicle == BADGER:
                # the named badger was destroyed by an earlier phase: the first live one
                srv.send(f"enterVehicle {obs.slot} vehicle_badger")
            time.sleep(3.0)
        else:
            time.sleep(1.5)

    def shapes(poses):
        view = fraction_check(poses, lambda a, b: moved(a["view"], b["view"]), "view origin")
        hull = fraction_check(poses, lambda a, b: moved(a["veh"], b["veh"]), "vehicle's drawn pose")
        return view, hull

    try:
        if args.mode == "join":
            srv.start()
            if not srv.wait_for_text(READY, 180.0):
                raise Failed("the server never became ready")
            srv.sync()
            connect(srv, obs, "join")
        else:
            obs.start()
            if not obs.wait_for_text(READY, 180.0) or not obs.wait_for_text(f"Map: maps/{args.map}", 180.0):
                raise Failed("the map never loaded")
            obs.sync(60.0)
            obs.slot = 0
        spawn(srv, obs, "gdf", timeout=60.0)
        if peer is not None:
            connect(srv, peer, args.mode)
            spawn(srv, peer, "gdf")
        obs.send("set g_logFramePoses 1")

        for cap in caps:
            obs.send(f"set com_maxFPS {cap}")
            seat_up()
            poses = phase("seatdrive", cap, drive)
            seats = {p["seat"] for p in poses}
            ok0 = len(poses) - 1 >= MIN_PAIRS and len(seats) == 1 and None not in seats \
                and next(iter(seats)).endswith(":0")
            view, hull = shapes(poses)
            path = onpath_check(poses, lambda p: p["veh"], "vehicle's drawn pose")
            steps = step_check(poses)
            results = {"S0": {"ok": ok0, "detail": f"{len(poses) - 1} draw pairs, seats {sorted(s or '-' for s in seats)}"},
                       "S1": view, "S2": hull, "S3": path, "S4": steps}
            print(f"[seat] cap {cap}: {rate(poses):.1f} fps")
            for check, res in results.items():
                print(f"  {cap:3} {check}  {'pass' if res['ok'] else 'FAIL'}  {res['detail']}")
                if not res["ok"]:
                    failures.append(f"cap {cap} {check}: {res['detail']}")

        if peer is not None:
            # stage 4: the peer walks while the driver sits parked in the listed seat
            cap = 120
            obs.send(f"set com_maxFPS {cap}")
            seat_up()
            def peer_pose_of(p):
                return (p["clients"].get(peer.slot) or {}).get("pose")
            def peer_phase(name):
                """A peer phase, retried once after the peer strafes clear if it barely moved
                (a spawn behind the parked badger blocks the walk)."""
                poses = phase(name, cap, peer_walk)
                moving = sum(1 for a, b in zip(poses, poses[1:]) if moved(peer_pose_of(a), peer_pose_of(b)))
                if moving < MIN_PAIRS:
                    print(f"[seat] {name}: the peer moved on only {moving} draw pairs; strafing clear and retrying")
                    peer.send("clientMove 0 127 0")
                    time.sleep(1.5)
                    peer.send("clientMove 0 0 0")
                    time.sleep(0.5)
                    poses = phase(name, cap, peer_walk)
                return poses
            poses = peer_phase("seatpeer")
            r1 = fraction_check(poses, lambda a, b: moved(peer_pose_of(a), peer_pose_of(b)), "peer's drawn pose")
            r2 = onpath_check(poses, peer_pose_of, "peer's drawn pose")
            seated = {p["seat"] for p in poses}
            print(f"[seat] remote entities while seated at cap {cap} (seats {sorted(x or '-' for x in seated)}):")
            for check, res in (("R1", r1), ("R2", r2)):
                print(f"  {cap:3} {check}  {'pass' if res['ok'] else 'FAIL'}  {res['detail']}")
                if not res["ok"]:
                    failures.append(f"cap {cap} {check}: {res['detail']}")
            if None in seated:
                failures.append(f"the driver was not seated throughout the seatpeer phase ({sorted(x or '-' for x in seated)})")
            obs.send("set g_unlock_vehicleSeats \"\"")
            time.sleep(1.0)
            poses = peer_phase("controlpeer")
            c = fraction_check(poses, lambda a, b: moved(peer_pose_of(a), peer_pose_of(b)), "peer's drawn pose")
            print(f"[seat] negative control (not listed) at cap {cap}: {c['detail']}")
            if not per_tick(c):
                failures.append(f"negative control: unlisted, the peer was not drawn in the per_tick shape ({c['detail']}, {c['misaligned']} misaligned)")
            obs.send(f"set g_unlock_vehicleSeats {LISTED}")

        # negative control: the seat not listed
        cap = caps[0]
        obs.send(f"set com_maxFPS {cap}")
        obs.send("set g_unlock_vehicleSeats \"\"")
        seat_up()
        view, hull = shapes(phase("controldrive", cap, drive))
        print(f"[seat] negative control (not listed) at cap {cap}: {view['detail']}; {hull['detail']}")
        if not per_tick(view) or not per_tick(hull):
            failures.append(f"negative control: unlisted, the seat did not draw in the per_tick shape"
                            f" ({view['detail']}, {view['misaligned']} misaligned; {hull['detail']}, {hull['misaligned']} misaligned)")

        # guards, every seat listed: the chase camera (no presentable recipe), then the icarus
        obs.send("set g_unlock_vehicleSeats *")
        seat_up()
        srv.send(f"cycleVehicleCamera {obs.slot}")
        time.sleep(1.5)
        poses = phase("guardchase", cap, drive)
        for key, what in (("view", "view"), ("veh", "hull")):
            changed, n = within_tick_changes(poses, key)
            print(f"[seat] guard (chase camera, listed) at cap {cap}: the {what} changed within a tick on {changed} of {n} pairs")
            moving = sum(1 for a, b in zip(poses, poses[1:]) if moved(a[key], b[key]))
            if n < MIN_PAIRS or changed > 1 or moving < MIN_PAIRS:
                failures.append(f"guard: the chase camera's {what} changed within a tick on {changed} of {n} pairs"
                                f" ({moving} pairs moved in all)")
        seat_up("vehicle_icarus")
        poses = phase("guardicarus", cap, drive)
        changed, n = within_tick_changes(poses, "view")
        moving = sum(1 for a, b in zip(poses, poses[1:]) if moved(a["view"], b["view"]))
        print(f"[seat] guard (icarus, listed) at cap {cap}: the view changed within a tick on {changed} of {n} pairs"
              f" ({moving} pairs moved in all)")
        if n < MIN_PAIRS or changed > 1 or moving < MIN_PAIRS:
            failures.append(f"guard: the icarus view changed within a tick on {changed} of {n} pairs ({moving} moved in all)")

    except Failed as e:
        failures.append(str(e))
    finally:
        for name, proc in procs:
            code = proc.quit() if proc.alive() else None
            lines = [ln.text for ln in proc.snapshot()]
            for f in h.scan_failures(lines):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            if proc is obs:
                for i, text in enumerate(lines):
                    if any(m in text for m in BAD_MARKERS):
                        failures.append(f"{name} log line {i + 1}: {text}")
            if code not in (0, None):
                failures.append(f"the {name} exited with {code}")
            proc.cleanup()

    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return h.EXIT_FAIL
    print("PASS: the listed seat's view and hull were drawn on every frame, on one path, without a step at the"
          " ticks; unlisted, and in seats without a presentable recipe, they stepped once per tick")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
