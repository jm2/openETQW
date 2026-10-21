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
              (S1 and S2 over the ticks in which the hull moved at least 0.5 u: at a
              leg's turnaround the badger stops for a moment)
  S3 path     the drawn hull lies on one interpolation path (frame-poses A7b's knot fit)
  S4 steps    the hull's step across a tick is at most 2.5 x the median step within one
              (the presentation draws a fixed age; a stepped hull would cross a tick in
              one step of a whole tick's motion: 4x at cap 120)
  S5 cockpit  every draw carries the badger's cockpit model (ckp=), and its drawn pose
              changes between consecutive draws (>= 99%, over S2's moving ticks): in a seat
              it stands where the viewmodel does on foot, and no draw of this phase (the badger's
              driver seat) has a viewmodel (wpn=-)

Late mouse look (stage 5), first of all, at cap 120 from the parked badger, in the driver's
standard view, the gunner's chase view (smooth_free, which orbits its focus) and the
driver's chase view (smooth_locked, in tophat mode through ui_drivingCameraFreelook 1); the
in_yawSpeed key turn stands in for the mouse:

  L0 seat     every draw of the phase is from that seat and view
  L1 tap      90% of LOOK_TAPS +left/+right taps show the turn on the next draw
  L2 held     a held turn changes the drawn yaw between consecutive draws (>= 99%)
  L3 smooth   under the held turn the yaw never turns back, and its rate across a tick is
              at most 2.5 x the median rate within one (per microsecond between draws), on
              all but max(1, 10%) of the tick crossings (a frame off the wall clock)
  L4 orbit    (the chase view) the camera origin's speed across a tick is at most 1.5 x
              the median speed within one, on all but max(1, 10%) of the tick crossings (a
              wrong pivot would jump at every one)
  L1c control with g_unlock_vehicleLook 0 the turn shows at least a draw later on average
  L5 locked   (smooth_locked) outside tophat mode a tap turns no draw

A rate-limited pivot view (the trojan's gunner, yaw at most 135 deg/s), parked, at cap
120: L0-L4 as above, with the held turn at 300 deg/s so the rate binds (the eye swings on
its arm, so L4 checks the camera origin too). The same L0-L4 in the badger gunner's freepivot
view (its axis is the view angles in the world with the hull's up axis, the eye swinging
with it), and L0-L3 in the husky driver's standard view, whose final axis is held by the
hull-relative clampDamped* clamp (ClampFinalAxis); its yaw range is +-30 deg, so the held
turn there is 30 deg/s and stays inside it. The limits have their own check:

  L6 edge     a turn held past the view's limit: with the late look the drawn angle relative
              to the hull stops within EDGE_TOL of where it stops without it (the tick's own
              clamps), and never turns back on the way. The freepivot gunner's pitch (its
              owner-space clamp, -50..17), and the husky driver's yaw at r_aspectRatio 0 (4:3,
              where its final clamp, 75 deg less half the field of view, coincides with the 30
              deg angle clamp) and 1 (16:9, where the final clamp binds first, at about 22 deg)

Gunner views (vehicle-responsiveness 3.6), parked, at cap 120: the gunner's freepivot view
with the turret held turning (in_yawSpeed):

  G0 seat     every draw is from the gunner's seat (position 1) in its first view
  G1 joints   listed, the hull's drawn joints (FRAME_POSE jnt=, a checksum) change between
              consecutive draws (>= 99%): the presentation blends them
  G2 view     and the view yaw changes between consecutive draws (>= 99%)
  G3 steps    the checksum's step across a tick is at most 2.5 x its step within one
  G1c control unlisted, the drawn joints change only across ticks (at most one pair within)

Remote entities while seated (stage 4; join and listen, which have a headless peer): at
cap 120, with the driver parked in the listed seat, the peer walks forward, then back:

  R1 peer     the peer's drawn pose changes between consecutive draws (>= 99%)
  R2 path     it lies on one interpolation path (A7b's knot fit)

and with the seat unlisted the peer's pose never changes within a tick while it walks.

Negative control (the lowest cap): with g_unlock_vehicleSeats empty the seat is not
listed, and the view, hull and cockpit never change within a tick (at most one pair)
while they move (a stationary tick would break an exact per-tick count). The chase camera (every seat listed; the driver's second view, smooth_locked, cycled
with cycleVehicleCamera): C1/C2 the view and the hull change on >= 99% of draw pairs,
C3/C4 both lie on one interpolation path (its own tick cameras are blended in the world).
The jetpack (icarus, its own recipe since M5 step 14), every seat listed: I1 its
first-person view changes between consecutive draws (>= 99%) while it flies, every draw
from one seat (I0); I2 the view's
step across a tick is at most 2.5 x its step within one; I1c unlisted, the view changes only
across ticks (at most one pair within). A flight that stalled (#151: the jetpack's view did
not move across some tick, as when it hits a wall) is reported and flown once more from a
fresh entry; a second stall fails the test, as a disturbed drive phase does. Each flight
starts on open ground (JETPACK_SPOT, the game's placeVehicle), forward then back: where the
jetpack is entered, walls can sit closer than a drive leg.

Hull lead (M5 step 14b, docs/hull-lead.md), at caps 60 and 120 in the driver's standard view
with g_unlock_vehicleLead listing the badger at L = 1:

  H0 lead     the lead was on: at least 80% of the moving draws carry lead= (alpha >= 0)
  H1 linear   with g_vehicleLeadFadeMsec 0, within each tick the drawn hull minus alpha x the
              tick's lead is one point (within 0.05 u): the hull is drawn along the lead
  H2 steps    with the fade on (the default), S4's rule: the hull's median step across a tick
              is at most 2.5 x its median step within one, and the drawn view's too (H2v)
  H3 off      the seat-drive phases (the built-in lead list, empty) and a phase listing only
              the titan (moving in the badger's driver seat) draw no lead= at all

Safety (nothing presented reaches tick state) is shown by meson seat-gate: its run B lists
every seat and leads every class, so the presentation runs during the seat tour, and SIM_HASH, VEH_TRACE and
SIM_VIEW must equal the run with nothing listed.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the client binary is missing.
"""

import argparse
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_frame_poses import (DevmapClient, EDGE_S, MIN_PAIRS, POS_EPS, fraction_check,  # noqa: E402
                              moved, onpath_check, parse_pose, rate)
from test_reconnect_restart import Failed, connect, player_pos, spawn  # noqa: E402
from test_remote_client_map_change import READY, RemoteClient  # noqa: E402

LISTED = "badger:*:standard"
# the badger single player enters by entityDef (valley's 57), named so that a dedicated
# server enters the same one: by entityDef it takes the first live badger in its spawn
# order, valley's 37, parked on a slope where backing up rolls it
BADGER = "sdVehicle_RigidBody_vehicle_badger_57"
THROTTLE = 96
DRIVE_LEG_S = 1.2     # each drive leg: short, so the badger stays clear of obstacles
STEP_RATIO = 2.5
SHAPE_TOL = 0.05     # per_tick shape: at most this fraction of pairs may disagree


def seat_fields(seat):
    """FRAME_POSE seat= as (entity, position, view mode), or None on foot."""
    if seat is None:
        return None
    parts = [int(x) for x in seat.split(":")]
    return tuple(parts) if len(parts) == 3 else (parts[0], parts[1], -1)


def seat_phase_poses(proc, begin, end):
    poses = [p for ln in proc.snapshot(begin, end) if (p := parse_pose(ln.text))]
    if len(poses) < 3:
        return poses
    t0, t1 = poses[0]["us"] + EDGE_S * 1e6, poses[-1]["us"] - EDGE_S * 1e6
    return [p for p in poses if t0 <= p["us"] <= t1]


LAUNCH_DZ_U = 30.0    # vertical hull motion in one tick that only a launch or a fall gives (900 u/s)


def launched(poses):
    """The largest vertical hull motion between consecutive ticks of a phase when it exceeds
    LAUNCH_DZ_U, else None: driving on valley's ground moves the hull a few units a tick."""
    first = {}
    for p in poses:
        if p["veh"] is not None:
            first.setdefault(p["gt"], p["veh"][2])
    worst = max((abs(first[gt + 33] - z) for gt, z in first.items() if gt + 33 in first), default=0.0)
    return worst if worst > LAUNCH_DZ_U else None


def step_check(poses, key="veh", what="step", eps=POS_EPS):
    """S4: the hull's (or the view's) median step across a tick against its median step within one."""
    cross, within = [], []
    for a, b in zip(poses, poses[1:]):
        if a[key] is None or b[key] is None:
            continue
        d = math.dist(a[key][:3], b[key][:3])
        if d <= eps:
            continue
        (cross if a["gt"] != b["gt"] else within).append(d)
    if len(cross) < 5 or len(within) < 5:
        return {"ok": False, "detail": f"too few moving pairs ({len(cross)} across a tick, {len(within)} within)"}
    ratio = statistics.median(cross) / statistics.median(within)
    return {"ok": ratio <= STEP_RATIO,
            "detail": f"median {what} across a tick {statistics.median(cross):.3f} u, within {statistics.median(within):.3f} u"
                      f" (ratio {ratio:.2f}, need <= {STEP_RATIO})"}


MOVING_TICK_U = 0.5   # S1/S2: a tick counts when the hull moved at least this far over it


def moving_poses(poses):
    """The draws of the ticks over which the hull moved at least MOVING_TICK_U (to the next
    tick's first draw): at a leg's turnaround the badger stops for a moment, and a draw that
    moves less than POS_EPS there is the vehicle, not the presentation."""
    first = {}
    for p in poses:
        if p["veh"] is not None:
            first.setdefault(p["gt"], p["veh"])
    moving = {gt for gt, v in first.items() if (nxt := first.get(gt + 33)) is not None
              and math.dist(v[:3], nxt[:3]) >= MOVING_TICK_U}
    return [p for p in poses if p["gt"] in moving]


LEAD_SHARE = 0.8      # H0: the share of moving draws that must carry a lead
LEAD_LINEAR_U = 0.05  # H1: the spread of hull - alpha x lead within a tick


def lead_linear(poses):
    """H1: within each tick, hull - alpha x lead is one point."""
    by_tick = {}
    for p in poses:
        if p["veh"] is not None and p["lead"] is not None:
            alpha, lead = p["lead"]
            by_tick.setdefault(p["gt"], []).append([v - alpha * l for v, l in zip(p["veh"][:3], lead)])
    spreads = [max(math.dist(a, b) for a in pts for b in pts) for pts in by_tick.values() if len(pts) >= 2]
    if len(spreads) < 5:
        return {"ok": False, "detail": f"too few ticks with two or more led draws ({len(spreads)})"}
    worst = max(spreads)
    return {"ok": worst <= LEAD_LINEAR_U,
            "detail": f"{len(spreads)} ticks: hull - alpha x lead spreads at most {worst:.3f} u within a tick"
                      f" (median {statistics.median(spreads):.3f} u; need <= {LEAD_LINEAR_U})"}


LOOK_TAPS = 10        # L1: +left taps, each LOOK_TAP_S long
LOOK_TAP_S = 0.25
LOOK_HOLD_S = 0.6     # L2: the held turn (the vehicle's key turn is faster than in_yawSpeed alone)
GUN_HOLD_S = 1.5      # G1-G3: the held turret turn in the gunner's view
EDGE_HOLD_S = 1.2     # L6: the held turn past the husky driver's yaw limit (60 deg/s)
EDGE_TOL = 0.25       # L6: degrees the late look may stop from the tick's own edge


# where each jetpack flight starts (placeVehicle, game): open ground 500 u left of the badger's
# mark, facing 67 deg. Where the jetpack is entered, walls can sit closer than one drive leg
# (#151); from here a forward-then-back flight had no still tick (probe, 2026-10-20)
JETPACK_SPOT = (8505, -10154, 1500, 67)


def jetpack_stalled(poses):
    """Why the jetpack's flight stalled, or None: its view did not move across some tick
    (#151). A stalled tick costs I1 about two draw pairs at cap 60, more than I1 allows, so
    any still pair across a tick counts. Judged only on the pairs that cross a tick, which
    a stepped presentation still moves; a presentation that held the view across a tick
    would look like a stall, and is retried once like one, then fails the test.
    Too few pairs is left to I1's own count."""
    cross = [(a, b) for a, b in zip(poses, poses[1:]) if a["gt"] != b["gt"]]
    still = sum(1 for a, b in cross if not moved(a["view"], b["view"]))
    if len(cross) < MIN_PAIRS or still == 0:
        return None
    return f"the jetpack's flight stalled: its view did not move across {still} of {len(cross)} ticks"


def look_response(lines):
    """L1: for each +left tap (marked LOOKTAP in the log, echoed in the frame that runs the
    +left), how many draws come before the first one whose yaw differs from the draw before
    the tap. A late look shows the turn on the next draw; without it the turn first shows
    after the next tick."""
    counts = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != "LOOKTAP":
            i += 1
            continue
        before = next((p for ln in reversed(lines[:i]) if (p := parse_pose(ln))), None)
        n = 0
        for ln in lines[i + 1:]:
            p = parse_pose(ln)
            if p is None:
                continue
            n += 1
            # 0.2 deg: the parked badger's suspension sways the eye by hundredths of a degree;
            # the key turn at 60 deg/s is 0.5 deg a draw at 120 fps
            if before is not None and abs((p["view"][4] - before["view"][4] + 180.0) % 360.0 - 180.0) > 0.2:
                counts.append(n)
                break
            if n > 40:
                break
        i += 1
    return counts


def look_consistency(held, k=2.5):
    """L3: under a held turn the drawn yaw moves one way on every draw that moves it (no
    snap back at a tick, no sawtooth), and its rate (degrees per microsecond of wall time
    between the draws, so a long frame is not a big step) across a tick is at most k times
    the median rate within one (no double count or lag that a tick catches up)."""
    yaw = lambda p: p["view"][4]
    steps = [((yaw(b) - yaw(a) + 180.0) % 360.0 - 180.0) / max(1, b["us"] - a["us"]) * 1e4
             for a, b in zip(held, held[1:])]
    steps = list(zip(steps, (a["gt"] != b["gt"] for a, b in zip(held, held[1:]))))
    moving = [(d, cross) for (d, cross), (a, b) in zip(steps, zip(held, held[1:]))
              if abs((yaw(b) - yaw(a) + 180.0) % 360.0 - 180.0) > 0.01]
    if len(moving) < MIN_PAIRS // 2:
        return False, f"too few moving draw pairs under the held turn ({len(moving)})"
    sign = 1.0 if sum(d for d, _ in moving) > 0 else -1.0
    back = sum(1 for d, _ in moving if d * sign < 0)
    inside = sorted(abs(d) for d, cross in moving if not cross)
    across = [abs(d) for d, cross in moving if cross]
    median = inside[len(inside) // 2] if inside else 0.0
    worst = max(across) / median if across and median > 0 else float("inf")
    # a frame drawn off the wall clock (a prediction-clock correction or a late frame under
    # load, frame-poses A8) can make one cross-tick pair fast; a snap-back or double count
    # shows on every tick, so at most max(1, 10%) of the tick crossings may exceed k
    over = sum(1 for v in across if median <= 0 or v / median > k)
    allowed = max(1, len(across) // 10)
    ok = back == 0 and bool(across) and over <= allowed
    return ok, (f"held yaw: {back} of {len(moving)} moving pairs turn back; {over} of {len(across)} cross-tick rates"
                f" over {k}x the median in-tick rate (allowed {allowed}; worst {worst:.2f}x)")


def orbit_consistency(held, k=1.5):
    """L4 (a chase view): under a held turn the camera orbits its focus, so its origin moves
    too; its speed across a tick is at most k times the median speed within one. A draw
    turned about the wrong point (or not moved at all) ends each tick off the camera the
    next tick computes, and the draw across the tick jumps by that error: with the parked
    badger's ~2 deg of turn a tick, a pivot about 40 u off shows as 1.5x. It does not check
    pitch, which the tick's camera follows only approximately as an orbit."""
    speeds = [(math.dist(a["view"][:3], b["view"][:3]) / max(1, b["us"] - a["us"]) * 1e6, a["gt"] != b["gt"])
              for a, b in zip(held, held[1:])]
    inside = sorted(v for v, cross in speeds if not cross)
    across = [v for v, cross in speeds if cross]
    if len(inside) < MIN_PAIRS // 2 or not across:
        return False, f"too few draw pairs under the held turn ({len(inside)} within a tick, {len(across)} across)"
    median = inside[len(inside) // 2]
    worst = max(across) / median if median > 0 else float("inf")
    # one off-clock frame allowed, as in L3; a wrong pivot jumps at every tick
    over = sum(1 for v in across if median <= 0 or v / median > k)
    allowed = max(1, len(across) // 10)
    return over <= allowed, (f"camera origin: median speed {median:.0f} u/s within a tick; {over} of {len(across)}"
                             f" tick crossings over {k}x (allowed {allowed}; worst {worst:.2f}x)")


def cockpit_check(poses):
    """S5: every draw carries the cockpit model (FRAME_POSE ckp=) and none a viewmodel, and
    the cockpit's drawn pose changes between consecutive draws over the ticks in which the
    hull moved, as S2's hull does."""
    have = sum(1 for p in poses if p["ckp"] is not None)
    weapon = sum(1 for p in poses if p["wpn"] is not None)
    res = fraction_check(moving_poses(poses), lambda a, b: moved(a["ckp"], b["ckp"]), "cockpit's drawn pose")
    ok = res["ok"] and have == len(poses) and weapon == 0
    return {"ok": ok, "detail": f"cockpit in {have}/{len(poses)} draws, a viewmodel in {weapon}; {res['detail']}"}


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

    def seat_in_mode(mode):
        """The player in the badger's driver seat (position 0) with view mode `mode`: entered
        again when on foot or in another seat (a launch can throw the player out, a respawn
        leaves them on foot), the camera cycled to the mode."""
        for _ in range(5):
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            fields = seat_fields(last["seat"]) if last is not None and last["seat"] is not None else None
            if fields is not None and fields[1] == 0 and fields[2] == mode:
                return True
            if fields is None or fields[1] != 0:
                if fields is not None:
                    srv.send(f"exitVehicle {obs.slot}")
                    time.sleep(1.0)
                seat_up()
                continue
            srv.send(f"cycleVehicleCamera {obs.slot}")
            time.sleep(1.5)
        return False

    def phase(name, cap, drive, check_launch=True, mode=0, stalled=None, reseat=None):
        """One drive phase's draws, from the driver's seat in view mode `mode` (None: whatever
        seat the caller set up, unchecked). A disturbed phase is driven again once the badger
        has settled: the hull launched or fell (issue #84: a collision can throw the badger
        thousands of units up, and then the guards rightly refuse some ticks), or some draws
        were not from that seat and mode (the player thrown out, or respawned on foot), or
        stalled(poses) gives a reason (the jetpack's flight stalled, #151); reseat() then seats
        the player afresh before the second attempt. A second disturbed attempt fails the test
        with that reason."""
        for attempt in range(2):
            if mode is not None:
                # the badger back where the test marked it, at rest: the drive legs leave it further
                # out every phase (it reverses slower than it drives), onto ground that can
                # throw it (#84); resetVehicle is a cheat the test servers allow
                # (a badger that replaced a destroyed one has no mark and stays where it is)
                srv.send(f"resetVehicle {BADGER}")
                time.sleep(1.5)
            if mode is not None and not seat_in_mode(mode):
                raise Failed(f"phase {name} at cap {cap}: could not seat the player in the driver's seat, view mode {mode}")
            begin = obs.mark()
            obs.send(f"echo FP_BEGIN {name} {cap}")
            drive()
            obs.send(f"echo FP_END {name} {cap}")
            hit = obs.wait_for(lambda s: s.strip() == f"FP_END {name} {cap}", 10.0, begin)
            if not hit:
                raise Failed(f"no end of phase {name} at cap {cap}")
            poses = seat_phase_poses(obs, begin, hit[0])
            jump = launched(poses) if check_launch else None
            off = 0 if mode is None else sum(1 for p in poses if p["seat"] is None
                                              or seat_fields(p["seat"])[1:] != (0, mode))
            stall = stalled(poses) if stalled is not None and jump is None and off == 0 else None
            if jump is None and off == 0 and stall is None:
                return poses
            why = (f"the hull moved {jump:.0f} u vertically in one tick (launched or falling)" if jump is not None
                   else f"{off} of {len(poses)} draws were not from the driver's seat in view mode {mode}" if off
                   else stall)
            print(f"[seat] phase {name} at cap {cap}: {why}; attempt {attempt + 1}", flush=True)
            if attempt == 0:
                time.sleep(5.0)
                if reseat is not None:
                    reseat()
        raise Failed(f"phase {name} at cap {cap}: disturbed in both attempts ({why})")

    def peer_walk():
        peer.send("clientMove 127 0 0")
        time.sleep(args.phase_seconds)
        peer.send("clientMove -127 0 0")
        time.sleep(args.phase_seconds)
        peer.send("clientMove 0 0 0")

    legs = {"order": 1}

    def drive():
        # three-quarter throttle: at full throttle the badger can roll or wreck itself. The
        # legs alternate (forward then back, then back then forward): the badger reverses
        # slower than it drives, so the same order every phase walks it into an obstacle
        legs["order"] = -legs["order"]
        for sign in (legs["order"], -legs["order"]):
            obs.send(f"clientMove {sign * THROTTLE} 0 0")
            time.sleep(DRIVE_LEG_S)
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

    def seated_up(tries=3):
        """seat_up, then make sure the player is in the badger's driver seat: after the drive
        phases the player can be dead or respawning, and an entry then fails quietly."""
        for _ in range(tries):
            seat_up()
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            if last is not None and last["seat"] is not None and seat_fields(last["seat"])[1:] == (0, 0):
                return True
            print("[seat] not in the driver's seat after an entry: trying again", flush=True)
            time.sleep(2.0)
        return False

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

        # stage 5, the late look, first of all: +left/+right taps from the parked badger,
        # before any drive can move it: the driver's standard view, then the gunner's chase
        # view (smooth_free, an orbit about its focus)
        cap = 120
        obs.send(f"set com_maxFPS {cap}")

        def late_look(label, want_seat, orbit=False, hold_speed=60):
            results_look = {}
            for setting in ("1", "0"):
                obs.send(f"set g_unlock_vehicleLook {setting}")
                time.sleep(0.5)
                # the driver's yaw is clamped (badger: -65..80 deg): taps alternate left and
                # right, and the held turn (LOOK_HOLD_S) stays inside the clamp from where they
                # leave it
                obs.send("set in_yawSpeed 60")
                begin = obs.mark()
                for k in range(LOOK_TAPS):
                    key = "left" if k % 2 == 0 else "right"
                    # one line: the marker lands in the frame that runs the key
                    obs.send(f"+{key}; echo LOOKTAP")
                    time.sleep(LOOK_TAP_S)
                    obs.send(f"-{key}")
                    time.sleep(0.4)
                # the held turn, at hold_speed: above a rate-limited class's rate, the rate binds
                obs.send(f"set in_yawSpeed {hold_speed}")
                obs.send("+left")
                mark = obs.mark()
                time.sleep(LOOK_HOLD_S)
                end = obs.mark()
                obs.send("-left")
                obs.send("+right")
                time.sleep(LOOK_HOLD_S)
                obs.send("-right")
                obs.send("set in_yawSpeed 60")
                time.sleep(0.5)
                lines = [ln.text for ln in obs.snapshot(begin)]
                held = [p for ln in obs.snapshot(mark, end) if (p := parse_pose(ln.text))]
                seats = {p["seat"] for p in (parse_pose(ln) for ln in lines) if p is not None}
                results_look[setting] = (seats, held, look_response(lines), fraction_check(
                    held[len(held) // 8:], lambda a, b: abs((a["view"][4] - b["view"][4] + 180.0) % 360.0 - 180.0) > 0.01,
                    "view yaw"))
            obs.send("set g_unlock_vehicleLook 1")
            look_seats, on_held, on, on_turn = results_look["1"]
            off_seats, _, off, off_turn = results_look["0"]
            # L0: every draw of the phase is from the expected seat and view, so an on-foot
            # (or other seat's) late look cannot pass L1 and L2
            look_seats |= off_seats
            l0 = len(look_seats) == 1 and None not in look_seats and seat_fields(next(iter(look_seats)))[1:] == want_seat
            l3, l3_detail = look_consistency(on_held[len(on_held) // 8:])
            checks = []
            if orbit:
                l4, l4_detail = orbit_consistency(on_held[len(on_held) // 8:])
                checks.append(("L4", l4, l4_detail))
            mean = lambda xs: sum(xs) / len(xs) if xs else float("inf")
            print(f"[seat] late look, {label}, at cap {cap}: draws to the first changed yaw after +left, on {on}"
                  f" (mean {mean(on):.2f}), off {off} (mean {mean(off):.2f}); held, on: {on_turn['detail']}")
            l1 = len(on) >= LOOK_TAPS - 1 and sorted(on)[int(0.9 * (len(on) - 1))] <= 1
            l1c = len(off) >= LOOK_TAPS - 1 and mean(off) >= mean(on) + 1.0
            for check, ok, detail in (("L0", l0, f"seats {sorted(x or '-' for x in look_seats)}, want position:mode {want_seat}"),
                                      ("L3", l3, l3_detail),
                                      ("L1", l1, f"90% of taps show the turn on the next draw (counts {on})"),
                                      ("L2", on_turn["ok"], on_turn["detail"]),
                                      ("L1c", l1c, f"with g_unlock_vehicleLook 0 the turn shows at least a draw later on average"
                                                   f" (mean {mean(off):.2f} against {mean(on):.2f})")) + tuple(checks):
                print(f"  {cap:3} {label} {check}  {'pass' if ok else 'FAIL'}  {detail}")
                if not ok:
                    failures.append(f"late look, {label}, {check}: {detail}")

        def edge_check(label, want_seat, key, back_key, index, sign, note=""):
            """L6: a turn held past the view's limit (key, at 60 deg/s; back_key undoes it). index
            is the view angle (4 yaw, 3 pitch) and sign the way key turns it. With the late look
            the drawn angle relative to the hull stops within EDGE_TOL of where it stops without
            it (the tick's own clamps), and never turns back on the way."""
            speed = "in_yawSpeed" if index == 4 else "in_pitchSpeed"
            rel = lambda p: (p["view"][index] - p["veh"][index] + 180.0) % 360.0 - 180.0

            def hold(setting):
                obs.send(f"set g_unlock_vehicleLook {setting}")
                obs.send(f"set {speed} 60")
                time.sleep(0.5)
                obs.send(f"+{key}")
                mark = obs.mark()
                time.sleep(EDGE_HOLD_S)
                end = obs.mark()
                obs.send(f"-{key}")
                obs.send(f"+{back_key}")
                time.sleep(EDGE_HOLD_S / 2)
                obs.send(f"-{back_key}")
                time.sleep(0.5)
                return [p for ln in obs.snapshot(mark, end) if (p := parse_pose(ln.text)) and p["veh"] is not None]

            edges = {s: hold(s) for s in ("0", "1")}
            obs.send("set g_unlock_vehicleLook 1")
            obs.send("set in_yawSpeed 60")
            obs.send("set in_pitchSpeed 140")
            on = edges["1"]
            seats = {p["seat"] for s in edges for p in edges[s]}
            moving = [sign * (rel(b) - rel(a)) for a, b in zip(on, on[1:]) if abs(rel(b) - rel(a)) > 0.01]
            back = sum(1 for d in moving if d < 0)
            e0 = max((sign * rel(p) for p in edges["0"]), default=float("nan"))
            e1 = max((sign * rel(p) for p in on), default=float("nan"))
            at_edge = sum(1 for p in on if sign * rel(p) > e0 - EDGE_TOL)
            ok = (len(seats) == 1 and None not in seats and seat_fields(next(iter(seats)))[1:] == want_seat
                  and len(on) >= MIN_PAIRS and abs(e1 - e0) <= EDGE_TOL and back == 0 and at_edge >= 10)
            what = "yaw" if index == 4 else "pitch"
            detail = (f"{note}held past the limit: drawn {what} to the hull stops at {sign * e1:.2f} deg with the late"
                      f" look, {sign * e0:.2f} without (need within {EDGE_TOL}); {back} of {len(moving)} moving pairs"
                      f" turn back; {at_edge} draws at the edge (seats {sorted(x or '-' for x in seats)})")
            print(f"  {cap:3} {label} L6  {'pass' if ok else 'FAIL'}  {detail}")
            if not ok:
                failures.append(f"late look, {label}, L6: {detail}")

        obs.send(f"set g_unlock_vehicleSeats {LISTED}")
        seat_up()
        # where the drive phases start from: the badger parked where it spawned (#84)
        srv.send(f"resetVehicle {BADGER} mark")
        late_look("driver", (0, 0))
        # the gunner's seat (position 1), cycled to its chase view (mode 1, smooth_free)
        obs.send(f"set g_unlock_vehicleSeats {LISTED},badger:*:smooth_free")
        srv.send(f"swapVehicleSeat {obs.slot}")
        time.sleep(1.5)
        for _ in range(2):
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            if last is not None and (seat_fields(last["seat"]) or (0, 0, 0))[1:] == (1, 1):
                break
            srv.send(f"cycleVehicleCamera {obs.slot}")
            time.sleep(1.5)
        late_look("chase gunner", (1, 1), orbit=True)
        # the driver's chase view (mode 1, smooth_locked): its look is free only in tophat
        # mode, which ui_drivingCameraFreelook 1 makes the default (the tophat inverts it).
        # Back to the driver's seat by swapping, not by leaving and entering again: an exit
        # can leave the player against the hull, and the push can throw the badger (#84)
        obs.send("set ui_drivingCameraFreelook 1")
        obs.send(f"set g_unlock_vehicleSeats {LISTED},badger:*:smooth_locked")
        for _ in range(5):
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            if last is not None and (seat_fields(last["seat"]) or (0, -1, 0))[1] == 0:
                break
            srv.send(f"swapVehicleSeat {obs.slot}")
            time.sleep(1.5)
        for _ in range(2):
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            if last is not None and (seat_fields(last["seat"]) or (0, 0, 0))[1:] == (0, 1):
                break
            srv.send(f"cycleVehicleCamera {obs.slot}")
            time.sleep(1.5)
        late_look("chase driver", (0, 1), orbit=True)
        # L5, its negative control: outside tophat mode (freelook off) the camera follows the
        # hull, and a tap turns no draw (the late look must not show a turn the tick ignores)
        obs.send("set ui_drivingCameraFreelook 0")
        time.sleep(1.5)
        begin = obs.mark()
        for k in range(LOOK_TAPS):
            obs.send(f"+{'left' if k % 2 == 0 else 'right'}; echo LOOKTAP")
            time.sleep(LOOK_TAP_S)
            obs.send(f"-{'left' if k % 2 == 0 else 'right'}")
            time.sleep(0.4)
        turned = look_response([ln.text for ln in obs.snapshot(begin)])
        taps = sum(1 for ln in obs.snapshot(begin) if ln.text.strip() == "LOOKTAP")
        # every draw from the driver's chase view: an ejection or a camera change would turn
        # nothing and pass vacuously (CodeRabbit, #83)
        l5_seats = {p["seat"] for ln in obs.snapshot(begin) if (p := parse_pose(ln.text))}
        in_view = len(l5_seats) == 1 and None not in l5_seats and seat_fields(next(iter(l5_seats)))[1:] == (0, 1)
        ok5 = taps >= LOOK_TAPS - 1 and in_view and not turned
        print(f"  {cap:3} chase driver L5  {'pass' if ok5 else 'FAIL'}  outside tophat mode {len(turned)} of {taps} taps"
              f" turned the drawn view (seats {sorted(x or '-' for x in l5_seats)})")
        if not ok5:
            failures.append(f"late look, chase driver, L5: outside tophat mode {len(turned)} of {taps} taps turned the view")
        obs.send(f"set g_unlock_vehicleSeats {LISTED}")

        # gunner views (vehicle-responsiveness 3.6): the gunner's first view (freepivot, mode 0)
        # rides the turret's eye joint; with the seat listed the hull is drawn with blended
        # joints, so a held turret turn changes the drawn joints (jnt=) on every draw
        for _ in range(5):
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            fields = seat_fields(last["seat"]) if last is not None and last["seat"] is not None else None
            if fields is not None and fields[1:] == (1, 0):
                break
            if fields is not None and fields[1] == 1:
                srv.send(f"cycleVehicleCamera {obs.slot}")
            else:
                srv.send(f"swapVehicleSeat {obs.slot}")
            time.sleep(1.5)
        # stage 5, freepivot: the gunner's late look (the turret turns with the taps and the hold)
        obs.send(f"set g_unlock_vehicleSeats {LISTED},badger:*:freepivot")
        late_look("freepivot gunner", (1, 0), orbit=True)
        # its pitch limit (-50..17 in the hull's frame): freepivot's owner-space clamp
        edge_check("freepivot gunner", (1, 0), "lookup", "lookdown", 3, -1.0)
        results_gun = {}
        for listed, key in ((True, "+left"), (False, "+right")):
            obs.send(f"set g_unlock_vehicleSeats {LISTED + ',badger:*:freepivot' if listed else LISTED}")
            time.sleep(1.0)
            obs.send(key)
            time.sleep(0.5)
            mark = obs.mark()
            time.sleep(GUN_HOLD_S)
            end = obs.mark()
            obs.send("-" + key[1:])
            time.sleep(0.5)
            results_gun[listed] = [p for ln in obs.snapshot(mark, end) if (p := parse_pose(ln.text))]
        obs.send(f"set g_unlock_vehicleSeats {LISTED}")
        gun = results_gun[True]
        gun_seats = {p["seat"] for p in gun} | {p["seat"] for p in results_gun[False]}
        g0 = len(gun_seats) == 1 and None not in gun_seats and seat_fields(next(iter(gun_seats)))[1:] == (1, 0)
        jnt_changed = lambda a, b: a["jnt"] is not None and b["jnt"] is not None and abs(a["jnt"][0] - b["jnt"][0]) > 1e-5
        g1 = fraction_check(gun, jnt_changed, "drawn joints (jnt=)")
        g2 = fraction_check(gun, lambda a, b: abs((a["view"][4] - b["view"][4] + 180.0) % 360.0 - 180.0) > 0.01,
                            "view yaw")
        g3 = step_check(gun, "jnt", "joint checksum step", eps=1e-5)   # the threshold G1 uses
        control = results_gun[False]
        within = [(a, b) for a, b in zip(control, control[1:]) if a["gt"] == b["gt"]]
        changed_within = sum(1 for a, b in within if jnt_changed(a, b))
        moved_any = sum(1 for a, b in zip(control, control[1:]) if jnt_changed(a, b))
        g1c = len(within) >= MIN_PAIRS and changed_within <= 1 and moved_any >= 5
        print(f"[seat] gunner (freepivot) at cap {cap}, turret held:")
        for check, ok, detail in (("G0", g0, f"seats {sorted(x or '-' for x in gun_seats)}, want position:mode (1, 0)"),
                                  ("G1", g1["ok"], g1["detail"]), ("G2", g2["ok"], g2["detail"]),
                                  ("G3", g3["ok"], g3["detail"]),
                                  ("G1c", g1c, f"unlisted, the drawn joints changed within a tick on {changed_within} of"
                                               f" {len(within)} pairs ({moved_any} changed in all)")):
            print(f"  {cap:3} gunner {check}  {'pass' if ok else 'FAIL'}  {detail}")
            if not ok:
                failures.append(f"gunner {check}: {detail}")

        # a rate-limited pivot view (stage 5's rate tier): the trojan's gunner (position 1, first
        # view, pivot; yaw at most 135 deg/s), its eye swinging on its arm; the held turn at
        # 300 deg/s, so the rate binds: L3 and L4 show no step back or jump at the ticks where
        # the draws end on what the next tick shows
        srv.send(f"exitVehicle {obs.slot}")
        time.sleep(1.0)
        obs.send(f"set g_unlock_vehicleSeats {LISTED},trojan:*:pivot")
        srv.send(f"enterVehicle {obs.slot} vehicle_trojan")
        time.sleep(3.0)
        for _ in range(6):
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            fields = seat_fields(last["seat"]) if last is not None and last["seat"] is not None else None
            if fields is not None and fields[1:] == (1, 0):
                break
            if fields is not None and fields[1] == 1:
                srv.send(f"cycleVehicleCamera {obs.slot}")
            else:
                srv.send(f"swapVehicleSeat {obs.slot}")
            time.sleep(1.5)
        late_look("pivot gunner", (1, 0), orbit=True, hold_speed=300)
        obs.send(f"set g_unlock_vehicleSeats {LISTED}")

        # stage 5, the clampDamped* tier: the husky driver's standard view (yaw -30..30 and the
        # final axis held to -75..75 less half the field of view about the eye joint, auto-centre)
        srv.send(f"exitVehicle {obs.slot}")
        time.sleep(1.0)
        obs.send(f"set g_unlock_vehicleSeats {LISTED},husky:*:standard")
        srv.send(f"enterVehicle {obs.slot} vehicle_husky")
        time.sleep(3.0)
        for _ in range(6):
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            fields = seat_fields(last["seat"]) if last is not None and last["seat"] is not None else None
            if fields is not None and fields[1:] == (0, 0):
                break
            if fields is not None and fields[1] == 0:
                srv.send(f"cycleVehicleCamera {obs.slot}")
            else:
                srv.send(f"swapVehicleSeat {obs.slot}")
            time.sleep(1.5)
        late_look("husky driver", (0, 0), hold_speed=30)

        # L6 at both aspect ratios: at 16:9 the field of view is wider (CalcFov), so the final
        # axis's clampDamped* clamp binds before the angle clamp and only the draw's
        # ClampFinalAxisPure holds the late turn there
        for ar in ("0", "1"):
            obs.send(f"set r_aspectRatio {ar}")
            time.sleep(0.5)
            edge_check("husky driver", (0, 0), "left", "right", 4, 1.0, f"r_aspectRatio {ar}, ")
        obs.send("set r_aspectRatio 0")
        obs.send(f"set g_unlock_vehicleSeats {LISTED}")

        # back on foot: the next phase seats the player in the driver's seat again
        srv.send(f"exitVehicle {obs.slot}")
        time.sleep(1.0)

        # stage 4 first, while the badger is parked where it spawned (after the drives it
        # can end up across the peer's walk)
        if peer is not None:
            # stage 4: the peer walks while the driver sits parked in the listed seat
            cap = 120
            obs.send(f"set com_maxFPS {cap}")
            seat_up()
            def peer_pose_of(p):
                return (p["clients"].get(peer.slot) or {}).get("pose")
            def peer_phase(name):
                """A peer phase. If the peer barely moved, the server says whether it really
                did not move (playerPos): wedged against the parked badger next to its spawn
                (Windows' GDF spawn spot), it strafes clear, and if that fails it respawns
                elsewhere ('kill'), before one retry. A jump of more than 200 u between draws
                is a respawn mid-phase (the interpolation smears a teleport across a tick, as in
                the SDK), also retried."""
                for attempt in range(3):
                    before = player_pos(srv, peer.slot)["org"]
                    poses = phase(name, cap, peer_walk)
                    after = player_pos(srv, peer.slot)["org"]
                    moving = sum(1 for a, b in zip(poses, poses[1:]) if moved(peer_pose_of(a), peer_pose_of(b)))
                    jumped = any(peer_pose_of(a) and peer_pose_of(b) and
                                 math.dist(peer_pose_of(a)[:3], peer_pose_of(b)[:3]) > 200.0
                                 for a, b in zip(poses, poses[1:]))
                    if moving >= MIN_PAIRS and not jumped:
                        return poses
                    server_moved = math.dist(before[:3], after[:3])
                    print(f"[seat] {name}: the peer's drawn pose moved on {moving} draw pairs"
                          f"{', and jumped (a respawn)' if jumped else ''}; on the server it moved {server_moved:.1f} u"
                          f" ({before[0]:.0f},{before[1]:.0f} to {after[0]:.0f},{after[1]:.0f})")
                    if attempt == 2:
                        break
                    if attempt == 0 and not jumped:
                        peer.send("clientMove 0 127 0")
                        time.sleep(1.5)
                        peer.send("clientMove 0 0 0")
                        time.sleep(0.5)
                    else:
                        # still stuck (or just respawned): respawn somewhere else
                        mark = srv.mark()
                        peer.send("kill")
                        spawn(srv, peer, "gdf", mark=mark)
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
            within = [(a, b) for a, b in zip(poses, poses[1:]) if a["gt"] == b["gt"]]
            changed = sum(1 for a, b in within if moved(peer_pose_of(a), peer_pose_of(b)))
            moving = sum(1 for a, b in zip(poses, poses[1:]) if moved(peer_pose_of(a), peer_pose_of(b)))
            print(f"[seat] negative control (not listed) at cap {cap}: the peer changed within a tick on {changed}"
                  f" of {len(within)} pairs ({moving} pairs moved in all)")
            if len(within) < MIN_PAIRS or changed > 1 or moving < MIN_PAIRS:
                failures.append(f"negative control: unlisted, the peer changed within a tick on {changed} of {len(within)}"
                                f" pairs ({moving} moved in all)")
            obs.send(f"set g_unlock_vehicleSeats {LISTED}")

        for cap in caps:
            obs.send(f"set com_maxFPS {cap}")
            seat_up()
            poses = phase("seatdrive", cap, drive)
            seats = {p["seat"] for p in poses}
            # the driver's seat (position 0) in its first view (mode 0, the standard view)
            ok0 = len(poses) - 1 >= MIN_PAIRS and len(seats) == 1 and None not in seats \
                and seat_fields(next(iter(seats)))[1:] == (0, 0)
            view, hull = shapes(moving_poses(poses))
            path = onpath_check(poses, lambda p: p["veh"], "vehicle's drawn pose")
            steps = step_check(poses)
            led = sum(1 for p in poses if p["lead"] is not None)
            cockpit = cockpit_check(poses)
            results = {"S0": {"ok": ok0, "detail": f"{len(poses) - 1} draw pairs, seats {sorted(s or '-' for s in seats)}"},
                       "S1": view, "S2": hull, "S3": path, "S4": steps, "S5": cockpit,
                       "H3": {"ok": led == 0, "detail": f"{led} draws with a lead under the built-in (empty) lead list"}}
            print(f"[seat] cap {cap}: {rate(poses):.1f} fps")
            for check, res in results.items():
                print(f"  {cap:3} {check}  {'pass' if res['ok'] else 'FAIL'}  {res['detail']}")
                if not res["ok"]:
                    failures.append(f"cap {cap} {check}: {res['detail']}")

        # the hull lead (step 14b): the badger at L = 1; H1 with the fade off, H0/H2 with it on
        for cap in (60, 120):
            obs.send(f"set com_maxFPS {cap}")
            obs.send("set g_unlock_vehicleLead badger:1")
            for fade, name in (("0", "leadlinear"), ("25", "leaddrive")):
                obs.send(f"set g_vehicleLeadFadeMsec {fade}")
                seated_up()
                poses = phase(name, cap, drive)
                moving = moving_poses(poses)
                led = sum(1 for p in moving if p["lead"] is not None)
                if fade == "0":
                    results = {"H1": lead_linear(poses)}
                else:
                    share = led / max(1, len(moving))
                    results = {"H0": {"ok": len(moving) >= MIN_PAIRS and share >= LEAD_SHARE,
                                      "detail": f"{led} of {len(moving)} moving draws led ({100 * share:.0f}%, need"
                                                f" {100 * LEAD_SHARE:.0f}%)"},
                               "H2": step_check(poses),
                               # the camera rides the led hull: its in-hull motion must not step
                               "H2v": step_check(poses, "view", "view step")}
                for check, res in results.items():
                    print(f"  {cap:3} {check}  {'pass' if res['ok'] else 'FAIL'}  {res['detail']}")
                    if not res["ok"]:
                        failures.append(f"cap {cap} {check}: {res['detail']}")
            obs.send("set g_vehicleLeadFadeMsec 25")
        # H3: a lead listed for another class only
        obs.send("set g_unlock_vehicleLead titan:1")
        seated_up()
        poses = phase("leadcontrol", caps[0], drive)
        led = sum(1 for p in poses if p["lead"] is not None)
        seated = [p for p in moving_poses(poses) if p["seat"] is not None and seat_fields(p["seat"])[1:] == (0, 0)]
        ok3 = led == 0 and len(seated) >= MIN_PAIRS
        print(f"  {caps[0]:3} H3  {'pass' if ok3 else 'FAIL'}  {led} draws led with only the titan listed"
              f" ({len(seated)} moving draws in the badger's driver seat, need {MIN_PAIRS})")
        if not ok3:
            failures.append(f"H3: {led} draws led with only the titan listed, {len(seated)} moving seated draws")
        obs.send("set g_unlock_vehicleLead default")

        # negative control: the seat not listed
        cap = caps[0]
        obs.send(f"set com_maxFPS {cap}")
        obs.send("set g_unlock_vehicleSeats \"\"")
        seat_up()
        poses = phase("controldrive", cap, drive)
        for key, what in (("view", "view"), ("veh", "hull"), ("ckp", "cockpit")):
            changed, n = within_tick_changes(poses, key)
            moving = sum(1 for a, b in zip(poses, poses[1:]) if moved(a[key], b[key]))
            print(f"[seat] negative control (not listed) at cap {cap}: the {what} changed within a tick on {changed}"
                  f" of {n} pairs ({moving} pairs moved in all)")
            if n < MIN_PAIRS or changed > 1 or moving < MIN_PAIRS:
                failures.append(f"negative control: unlisted, the {what} changed within a tick on {changed} of {n} pairs"
                                f" ({moving} moved in all)")

        # the chase camera unlisted (only the standard views are): it must not blend
        obs.send(f"set g_unlock_vehicleSeats {LISTED}")
        seat_up()
        for _ in range(2):
            srv.send(f"cycleVehicleCamera {obs.slot}")
            time.sleep(1.5)
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            if last is not None and (seat_fields(last["seat"]) or (0, 0, 0))[2] == 1:
                break
        poses = phase("chasecontrol", cap, drive, mode=1)
        for key, what in (("view", "view"), ("veh", "hull")):
            changed, n = within_tick_changes(poses, key)
            moving = sum(1 for a, b in zip(poses, poses[1:]) if moved(a[key], b[key]))
            print(f"[seat] chase camera unlisted at cap {cap}: the {what} changed within a tick on {changed} of {n} pairs"
                  f" ({moving} pairs moved in all)")
            if n < MIN_PAIRS or changed > 1 or moving < MIN_PAIRS:
                failures.append(f"chase control: unlisted, the {what} changed within a tick on {changed} of {n} pairs")
        srv.send(f"cycleVehicleCamera {obs.slot}")   # back to the first view
        time.sleep(1.5)

        # the chase camera (the driver's second view, smooth_locked), every seat listed: its
        # own tick cameras blended in the world (M5 step 14, chase views)
        obs.send("set g_unlock_vehicleSeats *")
        seat_up()
        for _ in range(2):
            srv.send(f"cycleVehicleCamera {obs.slot}")
            time.sleep(1.5)
            last = next((p for ln in reversed(obs.snapshot()) if (p := parse_pose(ln.text))), None)
            if last is not None and (seat_fields(last["seat"]) or (0, 0, 0))[2] == 1:
                break
        poses = phase("chasedrive", cap, drive, mode=1)
        modes = {(seat_fields(p["seat"]) or (0, 0, -1))[2] for p in poses}
        view, hull = shapes(moving_poses(poses))
        cview = onpath_check(poses, lambda p: p["view"], "chase camera's drawn origin")
        chull = onpath_check(poses, lambda p: p["veh"], "vehicle's drawn pose")
        print(f"[seat] chase camera (listed) at cap {cap}, view modes {sorted(modes)}:")
        for check, res in (("C1", view), ("C2", hull), ("C3", cview), ("C4", chull)):
            print(f"  {cap:3} {check}  {'pass' if res['ok'] else 'FAIL'}  {res['detail']}")
            if not res["ok"]:
                failures.append(f"chase cap {cap} {check}: {res['detail']}")
        if modes != {1}:
            failures.append(f"the chase phase drew view modes {sorted(modes)}, not only the chase view (1)")
        # the jetpack (icarus, its own recipe since M5 step 14): listed, its first-person view is
        # drawn on every frame while it flies; unlisted, only across ticks
        def jetpack_up():
            seat_up("vehicle_icarus")
            srv.send("placeVehicle vehicle_icarus {} {} {} {}".format(*JETPACK_SPOT))
            time.sleep(3.0)

        def fly():
            # forward, then back: the legs the open spot was checked with (drive() alternates)
            for sign in (1, -1):
                obs.send(f"clientMove {sign * THROTTLE} 0 0")
                time.sleep(DRIVE_LEG_S)
            obs.send("clientMove 0 0 0")

        jetpack_up()
        poses = phase("icarus", cap, fly, check_launch=False, mode=None,   # a jetpack flies
                      stalled=jetpack_stalled, reseat=jetpack_up)
        # I0: every draw from a seat (the jetpack's; on foot, I1 would pass on the unlocked view)
        seats_i = {p["seat"] for p in poses}
        i0 = len(seats_i) == 1 and None not in seats_i
        i1 = fraction_check(poses, lambda a, b: moved(a["view"], b["view"]), "jetpack view origin")
        i2 = step_check(poses, "view", "jetpack view step")
        obs.send(f"set g_unlock_vehicleSeats {LISTED}")
        time.sleep(1.0)
        jetpack_up()
        control = phase("icaruscontrol", cap, fly, check_launch=False, mode=None,
                        stalled=jetpack_stalled, reseat=jetpack_up)
        changed, n = within_tick_changes(control, "view")
        moving = sum(1 for a, b in zip(control, control[1:]) if moved(a["view"], b["view"]))
        i1c = n >= MIN_PAIRS and changed <= 1 and moving >= MIN_PAIRS
        print(f"[seat] jetpack (icarus) at cap {cap}:")
        for check, ok, detail in (("I0", i0, f"seats {sorted(x or '-' for x in seats_i)}"),
                                  ("I1", i1["ok"], i1["detail"]), ("I2", i2["ok"], i2["detail"]),
                                  ("I1c", i1c, f"unlisted, the view changed within a tick on {changed} of {n} pairs"
                                               f" ({moving} moved in all)")):
            print(f"  {cap:3} {check}  {'pass' if ok else 'FAIL'}  {detail}")
            if not ok:
                failures.append(f"jetpack {check}: {detail}")

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
                if failures:
                    # keep the client's poses and phase marks for a failure that does not recur
                    keep = Path(tempfile.gettempdir()) / f"seat-poses-{args.mode}-{os.getpid()}.log"
                    try:
                        keep.write_text("\n".join(t for t in lines if any(m in t for m in ("FRAME_POSE", "FP_", "SEAT", "LOOKTAP"))) + "\n")
                        print(f"[seat] the client's FRAME_POSE lines are kept in {keep}")
                    except OSError as e:
                        print(f"[seat] could not keep the client's FRAME_POSE lines in {keep}: {e}")
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
