#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A headless remote client survives being put in a vehicle, and its seat replicates.

One dedicated server (valley, cheats on) and one headless remote client (the
dedicated binary started with +connect, as test_remote_client_map_change.py
does). The client joins GDF as a soldier and spawns; then, for each vehicle:
1. The server puts it in the vehicle (enterVehicle <client> <entityDef>).
2. Both sides print the client's seat each tick with g_logVehicleViews
   (VIEW_STATE: ... client=<n> ent=<vehicle> pos=<seat> ...): the client must
   report the same vehicle entity and position as the server, and at every
   tick both printed after the first five (the placement; the server's at=pre
   line against the client's at=pre line for the snapshot's own tick, new=1)
   its first-person view origin must be within 0.05 units of the server's; it
   must still be running.
3. exitVehicle <client>: the client stops printing VIEW_STATE for itself.
Both processes then exit 0 on 'quit', and neither log has a failure marker.

Until 2026-10-08 the client crashed in step 1: a dedicated binary cannot parse
HUD GUIs, the vehicle overlay's load error shut the map down, and
sdPlayerProperties::OnActiveViewProxyChanged used the overlay that the nested
call had freed (STATUS, Known broken).

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_remote_client_map_change import GAME_INIT_RE, RemoteClient, snapshots  # noqa: E402

VIEW_RE = re.compile(r"VIEW_STATE: side=(cli|srv) at=(\w+) tick=(\d+) client=(\d+) ent=(\d+) pos=(\d+) "
                     r".*? new=(\d) .*?fpo\(([-\d.e+]+),([-\d.e+]+),([-\d.e+]+)\)")
SPAWNED_RE = re.compile(r"Player (\d+) \('[^']*'\) spawned at .* team (\w+) spectating 0")
VEHICLES = ("vehicle_badger", "vehicle_titan")
VIEW_TOLERANCE = 0.05
PAIRED_TICKS = 60
SETTLE_TICKS = 5  # the placement ticks after enterVehicle, not compared


def views(proc, start, side, client):
    """{tick: (ent, pos, fpo)} of the client's at=pre VIEW_STATE lines on one side;
    on the client only the snapshot's own tick (new=1), not a predicted repeat."""
    out = {}
    for ln in proc.snapshot(start):
        m = VIEW_RE.search(ln.text)
        if not m or m.group(1) != side or int(m.group(4)) != client or m.group(2) != "pre":
            continue
        if side == "cli" and m.group(7) != "1":
            continue
        out[int(m.group(3))] = (int(m.group(5)), int(m.group(6)), tuple(float(v) for v in m.group(8, 9, 10)))
    return out


def wait_until(predicate, timeout, step=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return predicate()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29983)
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)

    try:
        binary = h.find_ded_binary(args.ded_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    srv = h.DedicatedServer(binary, data, args.map, args.port,
                            log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "si_teamForceBalance": "0", "net_allowCheats": "1", "g_logVehicleViews": "1"})
    cl = RemoteClient(binary, data, args.port, args.port + 1, log_path=(log_dir / "client.log") if log_dir else None)
    try:
        srv.start()
        if not srv.wait_for_text("Spawning entities", 180.0):
            print("FAIL: server never loaded the map")
            return 1
        srv.sync()
        cl.start()
        hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0)
        if not hit:
            print("FAIL: the client never connected")
            return 1
        client_num = int(hit[2].group(1))
        if not cl.wait_for(lambda s: "Client initializing map" in s or None, 120.0):
            print("FAIL: the client never loaded the map")
            return 1
        # in the game: its first snapshots arrived (commands sent before are ignored)
        if not cl.wait_for(lambda s: len(snapshots(cl, 0)) >= 3 or None, 120.0):
            print("FAIL: the client received no snapshots")
            return 1
        spawn_mark = srv.mark()
        for command in ("g_logVehicleViews 1", "clientTeam gdf", "clientClass soldier", "spawn"):
            cl.send(command)
        spawned = srv.wait_for(lambda s: (m := SPAWNED_RE.search(s)) and int(m.group(1)) == client_num
                               and m.group(2) == "gdf" or None, 60.0, spawn_mark)
        if not spawned:
            print(f"FAIL: client {client_num} never spawned on GDF")
            return 1

        for vehicle in VEHICLES:
            srv_mark, cl_mark = srv.mark(), cl.mark()
            srv.send(f"enterVehicle {client_num} {vehicle}")
            paired = wait_until(lambda: not cl.alive() or len(set(views(srv, srv_mark, "srv", client_num))
                                                              & set(views(cl, cl_mark, "cli", client_num))) >= PAIRED_TICKS,
                                30.0)
            if not cl.alive():
                failures.append(f"the client died after enterVehicle {client_num} {vehicle}")
                break
            srv_views = views(srv, srv_mark, "srv", client_num)
            cl_views = views(cl, cl_mark, "cli", client_num)
            common = sorted(set(srv_views) & set(cl_views))
            print(f"[{vehicle}] VIEW_STATE at=pre ticks: server {len(srv_views)}, client (new=1) {len(cl_views)}, both {len(common)}")
            if not paired:
                failures.append(f"[{vehicle}] fewer than {PAIRED_TICKS} ticks printed by both sides within 30 s "
                                f"(server {len(srv_views)}, client {len(cl_views)}, both {len(common)})")
            if srv_views and cl_views:
                srv_seat = {v[:2] for v in srv_views.values()}
                cl_seat = {v[:2] for v in cl_views.values()}
                print(f"[{vehicle}] seat (vehicle entity, position): server {sorted(srv_seat)}, client {sorted(cl_seat)}")
                if len(srv_seat) != 1 or srv_seat != cl_seat:
                    failures.append(f"[{vehicle}] seats differ: server {sorted(srv_seat)}, client {sorted(cl_seat)}")
                # The first ticks after enterVehicle are the placement: the client
                # predicts them from the pose it had on foot, so they can differ by
                # one tick of movement (0.127 u, 2-3 runs in 10 on macOS). The seat
                # is compared from SETTLE_TICKS after the first one both printed.
                settled = common[SETTLE_TICKS:]
                worst, worst_tick = 0.0, None
                for tick in settled:
                    a, b = srv_views[tick][2], cl_views[tick][2]
                    error = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
                    if error > worst:
                        worst, worst_tick = error, tick
                print(f"[{vehicle}] first-person view origin, client against server over {len(settled)} ticks "
                      f"(paired from tick {common[0] if common else '-'}, the first {SETTLE_TICKS} skipped): "
                      f"worst {worst:.4f} u (tick {worst_tick})")
                if settled and worst > VIEW_TOLERANCE:
                    failures.append(f"[{vehicle}] client view origin {worst:.4f} u from the server's at tick {worst_tick} "
                                    f"(tolerance {VIEW_TOLERANCE})")

            # always leave the seat, so a failure above doesn't spill into the next vehicle
            exit_mark = cl.mark()
            srv.send(f"exitVehicle {client_num}")

            def seat_gone():
                tail = cl.mark()
                time.sleep(1.0)
                return not views(cl, tail, "cli", client_num)

            gone = wait_until(lambda: seat_gone() and cl.mark() > exit_mark, 15.0, step=0.0)
            print(f"[{vehicle}] after exitVehicle: client stopped reporting a seat: {bool(gone)}")
            if not gone:
                failures.append(f"[{vehicle}] the client still reports a seat 15 s after exitVehicle")
            if not cl.alive():
                failures.append(f"the client died after exitVehicle {client_num} ({vehicle})")
                break

        crc = cl.quit()
        src = srv.quit()
        print(f"quit exit codes: server {src}, client {crc}")
        if src != 0 or crc != 0:
            failures.append(f"quit exit codes: server {src}, client {crc}")
        for name, proc in (("server", srv), ("client", cl)):
            for b in h.scan_failures(l.text for l in proc.snapshot()):
                failures.append(f"{name} log: {b}")
    finally:
        cl.cleanup()
        srv.cleanup()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nPASS: the headless client sat in {', '.join(VEHICLES)}, its seat replicated, and it survived")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
