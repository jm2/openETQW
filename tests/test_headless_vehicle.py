#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A headless remote client survives being put in a vehicle, and its seat replicates.

One dedicated server (valley, cheats on) and one headless remote client (the
dedicated binary started with +connect, as test_remote_client_map_change.py
does). The client joins GDF as a soldier and spawns; then, for each vehicle:
1. The server puts it in the vehicle (enterVehicle <client> <entityDef>).
2. Both sides print the client's seat each tick with g_logVehicleViews
   (VIEW_STATE: ... client=<n> ent=<vehicle> pos=<seat> ...): the client must
   report the same vehicle entity and position as the server, its first-person
   view origin at the last tick both printed must be within 2 units of the
   server's, and it must still be running.
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
from test_remote_client_map_change import GAME_INIT_RE, RemoteClient  # noqa: E402

VIEW_RE = re.compile(r"VIEW_STATE: side=(cli|srv) at=\w+ tick=(\d+) client=(\d+) ent=(\d+) pos=(\d+) .*?"
                     r"fpo\(([-\d.e+]+),([-\d.e+]+),([-\d.e+]+)\)")
VEHICLES = ("vehicle_badger", "vehicle_titan")


def views(proc, start, side, client):
    """{tick: (ent, pos, fpo)} of the client's VIEW_STATE lines on one side."""
    out = {}
    for ln in proc.snapshot(start):
        m = VIEW_RE.search(ln.text)
        if m and m.group(1) == side and int(m.group(3)) == client:
            out[int(m.group(2))] = (int(m.group(4)), int(m.group(5)), tuple(float(v) for v in m.group(6, 7, 8)))
    return out


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
        time.sleep(8)
        for command in ("g_logVehicleViews 1", "clientTeam gdf", "clientClass soldier", "spawn"):
            cl.send(command)
        time.sleep(6)

        for vehicle in VEHICLES:
            srv_mark, cl_mark = srv.mark(), cl.mark()
            srv.send(f"enterVehicle {client_num} {vehicle}")
            time.sleep(8)
            if not cl.alive():
                failures.append(f"the client died after enterVehicle {client_num} {vehicle}")
                break
            srv_views = views(srv, srv_mark, "srv", client_num)
            cl_views = views(cl, cl_mark, "cli", client_num)
            print(f"[{vehicle}] VIEW_STATE ticks: server {len(srv_views)}, client {len(cl_views)}")
            if not srv_views:
                failures.append(f"[{vehicle}] the server never put client {client_num} in a vehicle")
                continue
            if not cl_views:
                failures.append(f"[{vehicle}] the client never saw itself in a vehicle")
                continue
            srv_seat = {v[:2] for v in srv_views.values()}
            cl_seat = {v[:2] for v in cl_views.values()}
            print(f"[{vehicle}] seat (vehicle entity, position): server {sorted(srv_seat)}, client {sorted(cl_seat)}")
            if len(srv_seat) != 1 or srv_seat != cl_seat:
                failures.append(f"[{vehicle}] seats differ: server {sorted(srv_seat)}, client {sorted(cl_seat)}")
            common = sorted(set(srv_views) & set(cl_views))
            if not common:
                failures.append(f"[{vehicle}] no tick printed by both sides")
            else:
                tick = common[-1]
                a, b = srv_views[tick][2], cl_views[tick][2]
                error = sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5
                print(f"[{vehicle}] tick {tick}: first-person view origin server {a}, client {b}, distance {error:.2f}")
                if error > 2.0:
                    failures.append(f"[{vehicle}] client view origin {error:.2f} units from the server's at tick {tick}")

            srv.send(f"exitVehicle {client_num}")
            time.sleep(3)
            settle = cl.mark()
            time.sleep(3)
            still = views(cl, settle, "cli", client_num)
            print(f"[{vehicle}] after exitVehicle: client VIEW_STATE ticks {len(still)} (from the 3 s after a 3 s settle)")
            if still:
                failures.append(f"[{vehicle}] the client still reports a seat after exitVehicle")
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
