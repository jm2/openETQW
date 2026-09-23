#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A connected remote client follows an in-process map change.

One dedicated server and one headless remote client (a second dedicated
binary started with +connect, as test_multiclient_gameplay.py does):
1. The client connects on the first map and receives snapshots.
2. 'spawnServer <other map>' on the server re-sends the game init to that
   client ('Sending game init to client N'); the client loads the new map
   ('Client initializing map ...<other map>') and receives snapshots from the
   new game (the snapshot sequence keeps increasing across the change).
3. The client is still connected afterwards ('status' on the server shows its
   slot) and neither process logs a failure marker.
Both processes then exit 0 on 'quit'.
With --drop N both processes drop N% of their outgoing packets
(net_forceDrop), which exercises the snapshot resend and the reliable game init.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

READY = "------------- ETQW initialized -------------"
SNAPSHOT_RE = re.compile(r"Client received snapshot (\d+) \(frame (\d+)\)")
SLOTS_RE = re.compile(r"^(\d+) client slots? in use")
GAME_INIT_RE = re.compile(r"Sending game init to client (\d+)")


class RemoteClient(h.DedicatedServer):
    """The dedicated binary as a headless client: +connect instead of +spawnServer."""

    def __init__(self, binary, data, server_port, port, log_path=None, drop=0):
        super().__init__(binary, data, "", port, log_path=log_path,
                         cvars={"si_pure": "0", "name": "RemoteClient", "net_forceDrop": str(drop)})
        self.server_port = server_port

    def command_line(self):
        args = super().command_line()
        assert args[-2] == "+spawnServer"
        return args[:-2] + ["+connect", f"127.0.0.1:{self.server_port}"]


def snapshots(proc, start):
    return [int(m.group(1)) for ln in proc.snapshot(start) if (m := SNAPSHOT_RE.search(ln.text))]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--maps", default="quarry,outskirts", help="first map, then the map to change to")
    ap.add_argument("--port", type=int, default=29960)
    ap.add_argument("--log-dir")
    ap.add_argument("--drop", type=int, default=0, help="percentage of outgoing packets each process drops")
    ap.add_argument("--min-snapshots", type=int, default=5, help="snapshots required on each map")
    args = ap.parse_args(argv)
    first, second = [m for m in args.maps.split(",") if m][:2]

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
    srv = h.DedicatedServer(binary, data, first, args.port,
                            log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "net_forceDrop": str(args.drop)})
    cl = RemoteClient(binary, data, args.port, args.port + 1, log_path=(log_dir / "client.log") if log_dir else None,
                      drop=args.drop)
    try:
        srv.start()
        if not srv.wait_for_text(READY, 180.0):
            print("FAIL: server never became ready")
            return 1
        srv.sync()
        cl.start()
        hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0)
        client_num = int(hit[2].group(1)) if hit else None
        need = args.min_snapshots
        cl.wait_for(lambda s: len(snapshots(cl, 0)) >= need or None, 180.0)
        before = snapshots(cl, 0)
        print(f"[{first}] client slot {client_num}, snapshots received: {len(before)}")
        if client_num is None or len(before) < need:
            print(f"FAIL: the client never connected and received {need} snapshots on the first map")
            return 1

        srv_mark, cl_mark = srv.mark(), cl.mark()
        srv.send(f"spawnServer {second}")
        if not srv.wait_for_text(f"Map: maps/{second}.world", 120.0, srv_mark):
            failures.append(f"server did not load maps/{second}.world")
        reinit = srv.wait_for(lambda s: (m := GAME_INIT_RE.search(s)) and int(m.group(1)) == client_num or None, 60.0, srv_mark)
        print(f"[{second}] server re-sent game init to client {client_num}: {bool(reinit)}")
        if not reinit:
            failures.append(f"server never re-sent the game init to client {client_num} after the map change")
        loaded = cl.wait_for(lambda s: "Client initializing map" in s and second in s or None, 60.0, cl_mark)
        print(f"[{second}] client loaded the new map: {bool(loaded)}")
        if not loaded:
            failures.append(f"client never loaded {second}")
        else:
            cl.wait_for(lambda s: len(snapshots(cl, loaded[0])) >= need or None, 180.0, loaded[0])
        after = snapshots(cl, loaded[0]) if loaded else []
        print(f"[{second}] snapshots received after the change: {len(after)}"
              f" (sequence {after[0] if after else '-'}..{after[-1] if after else '-'}, last before: {before[-1]})")
        if len(after) < need:
            failures.append(f"client received {len(after)} snapshots on {second} (expected >= {need})")
        elif after[0] <= before[-1]:
            failures.append(f"snapshot sequence went backwards across the change ({before[-1]} -> {after[0]})")

        lines = srv.command("status") or []
        used = [int(SLOTS_RE.match(l.text).group(1)) for l in lines if SLOTS_RE.match(l.text)]
        print(f"[{second}] server status slots in use: {used}")
        if used != [1]:
            failures.append(f"server status says {used} client slots in use (expected [1])")
        # The client's packets queued during the server's load are read in one burst
        # before the game init goes out (old frame numbers, dropped with a warning,
        # more the longer the load); after the game init the client's frames must
        # line up with the new game, so a steady stream there is the bug.
        dropped = [l.text for l in srv.snapshot(reinit[0] if reinit else srv_mark) if "dropped client usercmds" in l.text]
        print(f"[{second}] 'dropped client usercmds' warnings after the game init: {len(dropped)}")
        if len(dropped) > 10:
            failures.append(f"{len(dropped)} 'Server dropped client usercmds' warnings after the game init")

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
    print(f"\nPASS: the remote client followed the {first} -> {second} map change and kept receiving snapshots")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
