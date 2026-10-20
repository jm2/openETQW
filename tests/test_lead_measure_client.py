#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Hull-lead measurement driven by the player's own commands: each class's one-tick
predictor errors as the player's game draws them, on a network client and a listen host
(M5 step 14b).

docs/hull-lead.md step 2, "repeat on a join client ... and on a listen host" (§6).
lead-measure drives each class from the server's schedule (g_determinismTest 4), which a
network client would mispredict: the server replaces the client's commands. Here the server
runs the same schedule with g_leadMeasureDrive 0. It spawns each rigid-body class in turn on
the same open spot, puts the player in it, and takes them out and removes it, while the
player's own commands drive:
  ground    clientMove: throttle, a right and a left turn at speed, brake and reverse,
            forward again (the schedule's legs);
  aircraft  clientMove for the collective and the pedals, and +lookdown/+left/+lookup at
            the schedule's rates (in_pitchSpeed 12, in_yawSpeed 24 degrees a second) for
            the nose and the roll: lift-off, nose down, a yaw and roll ramp, nose up, descent.
The player's game logs VEH_PRED (g_logVehiclePred) on each new tick of a presented seat
(g_unlock_vehicleSeats *): how far each predictor's guess for this tick (S the samples, P
the physics step, H the hybrid) landed from the pose the player then draws from, which on a
network client is after the prediction-error decay.

  --mode join     a dedicated server and the Vulkan client: the network client
  --mode listen   the Vulkan client as a listen host
  --mode devmap   single player with the same drive, for comparison (not a meson test)

It prints, per class, the median, p95 and max error of each predictor over the ticks that
moved at least MIN_MOVE_U. Those are a measurement for the owner's per-class decision
(docs/hull-lead.md §4), not a check. It fails only when the run did not measure:
  C1  a class the schedule spawned was not entered, or (the titan aside) logged fewer than
      MIN_TICKS moving VEH_PRED ticks (not presented, or the drive did not reach it);
  C2  the schedule did not finish (no LEAD_DONE).
The titan is not presented (its driver's first view is a third-person pivot), so it logs no
VEH_PRED; it is entered but not measured.

Exit 77 when ETQW_DATA_PATH is unset/invalid or a binary is missing.
"""

from __future__ import annotations

import argparse
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_frame_poses import DevmapClient  # noqa: E402
from test_reconnect_restart import Failed, connect, spawn  # noqa: E402
from test_remote_client_map_change import READY  # noqa: E402

CLASSES = ("titan", "hog", "desecrator", "husky", "badger", "trojan", "anansi", "bumblebee", "hornet")
AIRCRAFT = {"anansi", "bumblebee", "hornet"}
UNPRESENTED = {"titan"}
TICK_S = 1.0 / 30.0
SEGMENT_TICKS = 200          # the schedule's LEAD_SEGMENT
MIN_TICKS = 60
MIN_MOVE_U = 0.5
CAP = 120

ENTER_RE = re.compile(r"ENTER_VEHICLE: client (\d+) entered sdVehicle_RigidBody_vehicle_(\S+?)_\d+ ")
SEGMENT_RE = re.compile(r"LEAD_SEGMENT: tick=(\d+) class=vehicle_(\S+) ent=(-?\d+)")
DONE_RE = re.compile(r"LEAD_DONE: tick=(\d+)")
PRED_RE = re.compile(r"VEH_PRED: tick=(\d+) class=(\S+) L=\S+ pred=\S k=(\d) ground=(\d) errS=(\S+) errP=(\S+) "
                     r"errH=(\S+) rotS=\S+ rotP=\S+ move=(\S+) coll=(-?\d+)")

# (ticks, clientMove forward side up, keys held): the schedule's legs, from its entry tick
GROUND_LEGS = [
    (42, (127, 0, 0), ()),          # throttle
    (30, (127, 127, 0), ()),        # a right turn at speed
    (20, (127, -127, 0), ()),       # a left turn
    (35, (-127, 0, 0), ()),         # brake, then reverse
    (50, (127, 0, 0), ()),          # forward again
]
AIR_LEGS = [
    (37, (127, 0, 0), ()),                  # collective: lift off
    (35, (40, 0, 0), ("lookdown",)),        # nose down, a ramp
    (35, (40, 127, 0), ("left",)),          # the pedals (yaw) and the mouse's yaw axis (roll), a ramp
    (35, (40, 0, 0), ("lookup",)),          # nose up again
    (30, (-127, 0, 0), ()),                 # collective down
]


def drive(cl, legs) -> None:
    for ticks, (fwd, side, up), keys in legs:
        cl.send(f"clientMove {fwd} {side} {up}")
        for k in keys:
            cl.send(f"+{k}")
        time.sleep(ticks * TICK_S)
        for k in keys:
            cl.send(f"-{k}")
    cl.send("clientMove 0 0 0")


def stats(values: list[float]) -> str:
    if not values:
        return "-"
    values = sorted(values)
    p95 = values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]
    return f"{statistics.median(values):.3f} / {p95:.2f} / {values[-1]:.2f}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("join", "listen", "devmap"), default="join")
    ap.add_argument("--ded-binary")
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29840)
    ap.add_argument("--cap", type=int, default=CAP, help="the player's com_maxFPS")
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)
    try:
        vk = find_client(args.client_binary)
        ded = h.find_ded_binary(args.ded_binary) if args.mode == "join" else None
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    def log(name):
        return (log_dir / f"{args.mode}-{name}.log") if log_dir else None

    schedule = {"g_determinismTest": "4", "g_leadMeasureDrive": "0"}
    game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
            "si_teamForceBalance": "0", "net_allowCheats": "1"}
    view = {"com_unlockFPS": "1", "r_swapInterval": "0", "r_vkValidation": "0"}
    # set from the console once the game runs: on the command line they would take the
    # listen host past MAX_COMMAND_ARGS. Every seat presented, and the player's turn rates
    # for the aircraft's ramps, without the run key's multiplier
    later = {"com_maxFPS": str(args.cap), "g_unlock_vehicleSeats": "*", "in_pitchSpeed": "12",
             "in_yawSpeed": "24", "in_angleSpeedKey": "1"}
    if args.mode == "join":
        srv = h.DedicatedServer(ded, data, "valley", args.port, log_path=log("server"), cvars={**game, **schedule})
        srv.name = "SERVER"
        cl = VulkanClient(vk, data, args.port + 1, connect_to=f"127.0.0.1:{args.port}", log_path=log("client"),
                          cvars={"si_pure": "0", "ui_name": "Driver", "net_clientShowSnapshots": "1", **view})
        procs = [cl, srv]
    elif args.mode == "listen":
        cl = VulkanClient(vk, data, args.port, map_name="valley", log_path=log("host"),
                          cvars={**game, **schedule, "ui_name": "Driver", **view})
        srv = cl
        procs = [cl]
    else:
        cl = DevmapClient(vk, data, args.port, map_name="valley", log_path=log("devmap"),
                          cvars={"si_rules": "sdGameRulesObjective", **schedule, "ui_name": "Driver", **view})
        srv = cl
        procs = [cl]
    cl.name = "DRIVER"
    failures = []
    try:
        if args.mode == "join":
            srv.start()
            if not srv.wait_for_text(READY, 180.0):
                raise Failed("the server never became ready")
            srv.sync()
            connect(srv, cl, "join")
        else:
            cl.start()
            if not cl.wait_for_text(READY, 180.0) or not cl.wait_for_text("Map: maps/valley", 180.0):
                raise Failed("the map never loaded")
            cl.sync(60.0)
            cl.slot = 0
        for k, v in later.items():
            cl.send(f"set {k} {v}")
        cl.send("set g_logVehiclePred 1")   # a cheat: once in the game
        spawn(srv, cl, "gdf", timeout=60.0)
        start = srv.wait_for(lambda s: re.search(r"LEAD_START: tick=(\d+) client=(\d+)", s), 30.0)
        if not start:
            raise Failed("the schedule never started (LEAD_START)")
        if int(start[2].group(2)) != cl.slot:
            raise Failed(f"the schedule took client {start[2].group(2)}, not the driver ({cl.slot})")
        print(f"[{args.mode}] the schedule starts at tick {start[2].group(1)}; driver is client {cl.slot},"
              f" com_maxFPS {args.cap}", flush=True)
        mark = start[0]
        entered = []
        deadline = time.monotonic() + len(CLASSES) * SEGMENT_TICKS * TICK_S + 60.0
        while time.monotonic() < deadline:
            hit = srv.wait_for(lambda s: ENTER_RE.search(s) or DONE_RE.search(s), deadline - time.monotonic(), mark)
            if not hit:
                break
            mark = hit[0] + 1
            if hit[2].re is DONE_RE:
                break
            if int(hit[2].group(1)) != cl.slot:
                continue
            cls = hit[2].group(2)
            entered.append(cls)
            print(f"  {cls}: entered, driving", flush=True)
            drive(cl, AIR_LEGS if cls in AIRCRAFT else GROUND_LEGS)
        lines = [ln.text for ln in srv.snapshot()]
        if not any(DONE_RE.search(t) for t in lines):
            failures.append("C2 the schedule did not finish (no LEAD_DONE)")
        spawned = {m[2] for t in lines if (m := SEGMENT_RE.search(t)) and int(m[3]) >= 0}
        for cls in CLASSES:
            if cls in spawned and cls not in entered:
                failures.append(f"C1 {cls}: spawned, never entered")
            elif cls not in spawned:
                failures.append(f"C1 {cls}: never spawned")

        preds: dict[str, list[tuple]] = {}
        for ln in cl.snapshot():
            if (m := PRED_RE.search(ln.text)) and float(m[8]) >= MIN_MOVE_U:
                preds.setdefault(m[2], []).append((float(m[5]), float(m[6]), float(m[7]), int(m[4])))
        print(f"--- one-tick predictor errors, {args.mode}, com_maxFPS {args.cap}: median / p95 / max (u),"
              f" ticks that moved at least {MIN_MOVE_U} u ---")
        print("| class | ticks | grounded | S (samples) | P (physics step) | H (hybrid) |")
        print("|---|---|---|---|---|---|")
        for cls in CLASSES:
            rows = preds.get(cls, [])
            if cls in UNPRESENTED:
                print(f"| {cls} | not presented | | | | |")
                continue
            if len(rows) < MIN_TICKS:
                failures.append(f"C1 {cls}: {len(rows)} moving VEH_PRED ticks, expected at least {MIN_TICKS}")
            print(f"| {cls} | {len(rows)} | {sum(r[3] for r in rows)} | {stats([r[0] for r in rows])} |"
                  f" {stats([r[1] for r in rows if r[1] >= 0])} | {stats([r[2] for r in rows if r[2] >= 0])} |")
    except Failed as e:
        failures.append(str(e))
    finally:
        for p in procs:
            p.quit()
            p.cleanup()

    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return h.EXIT_FAIL
    print(f"PASS: every class entered and driven by the player's own commands ({args.mode});"
          " the presented ones measured")
    return h.EXIT_PASS


if __name__ == "__main__":
    h.install_signal_handlers()
    sys.exit(main(sys.argv[1:]))
