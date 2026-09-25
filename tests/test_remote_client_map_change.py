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
On each map the client must also get the objective manager's network event
from the server's initial reliable messages ('oe <entity> <index>', sent by the
script through sendNetworkEvent( clientIndex, isRepeater, message ), the retail 1.5
and SDK signature; net_clientShowNetworkEvents)
before its first snapshot of that map: the client handles a packet's reliable
messages before its snapshot, and the map script's own objective broadcast
comes seconds later.
Both processes then exit 0 on 'quit'.
With --drop N both processes drop N% of their outgoing packets
(net_forceDrop), which exercises the snapshot resend and the reliable game init.
--latency/--jitter delay received packets (jitter varies the delay, order kept)
(net_forceLatency/net_forceJitter), and --base-check makes the server send a
checksum of the client's snapshot base with every snapshot
(net_debugSnapshotBase): the client stops with an error if its base differs.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

READY = "------------- ETQW initialized -------------"
SNAPSHOT_RE = re.compile(r"Client received snapshot (\d+) \(frame (\d+)\)")
SLOTS_RE = re.compile(r"^(\d+) client slots? in use")
GAME_INIT_RE = re.compile(r"Sending game init to client (\d+)")
DROPPED_RE = re.compile(r"Server dropped client usercmds: clientGameFrame=(\d+)")
OBJECTIVE_EVENT = "Client network event: objectives 'oe "


class RemoteClient(h.DedicatedServer):
    """The dedicated binary as a headless client: +connect instead of +spawnServer."""

    def __init__(self, binary, data, server_port, port, log_path=None, net=None):
        super().__init__(binary, data, "", port, log_path=log_path,
                         cvars={"si_pure": "0", "ui_name": "RemoteClient", "net_clientShowSnapshots": "1",
                                 "net_clientShowNetworkEvents": "1", **(net or {})})
        self.server_port = server_port

    def command_line(self):
        args = super().command_line()
        assert args[-2] == "+spawnServer"
        return args[:-2] + ["+connect", f"127.0.0.1:{self.server_port}"]


def snapshots(proc, start):
    return [int(m.group(1)) for ln in proc.snapshot(start) if (m := SNAPSHOT_RE.search(ln.text))]


def joined_with_objective(proc, start):
    """The objective event from the join burst arrived before the first snapshot."""
    for ln in proc.snapshot(start):
        if OBJECTIVE_EVENT in ln.text:
            return True
        if SNAPSHOT_RE.search(ln.text):
            return False
    return False


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--maps", default="quarry,outskirts", help="first map, then the map to change to")
    ap.add_argument("--port", type=int, default=29960)
    ap.add_argument("--log-dir")
    ap.add_argument("--drop", type=int, default=0, help="percentage of outgoing packets each process drops")
    ap.add_argument("--min-snapshots", type=int, default=5, help="snapshots required on each map")
    ap.add_argument("--latency", type=int, default=0, help="ms added to every received packet (both processes)")
    ap.add_argument("--jitter", type=int, default=0, help="up to this many more ms per packet (order kept)")
    ap.add_argument("--base-check", action="store_true", help="server sends a snapshot base checksum (net_debugSnapshotBase)")
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
    net = {"net_forceDrop": str(args.drop), "net_forceLatency": str(args.latency), "net_forceJitter": str(args.jitter)}
    srv = h.DedicatedServer(binary, data, first, args.port,
                            log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   **net, "net_debugSnapshotBase": os.environ.get("BASECHECK_LEVEL", "1") if args.base_check else "0"})
    cl = RemoteClient(binary, data, args.port, args.port + 1, log_path=(log_dir / "client.log") if log_dir else None,
                      net=net)
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
        objective = joined_with_objective(cl, 0)
        print(f"[{first}] client got the objective network event on joining: {objective}")
        if not objective:
            failures.append(f"client got no objective network event ('oe ...') before its first snapshot on {first}")

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
        objective = joined_with_objective(cl, loaded[0]) if loaded else False
        print(f"[{second}] client got the objective network event on joining: {objective}")
        if not objective:
            failures.append(f"client got no objective network event ('oe ...') before its first snapshot on {second}")

        lines = srv.command("status") or []
        used = [int(SLOTS_RE.match(l.text).group(1)) for l in lines if SLOTS_RE.match(l.text)]
        print(f"[{second}] server status slots in use: {used}")
        if used != [1]:
            failures.append(f"server status says {used} client slots in use (expected [1])")
        # Until its first snapshot of the new game the client sends usercmds with the
        # game init's frame (it is not in the game yet), which the server may drop
        # as too old; once the client has that snapshot its frames must line up with
        # the server's, so a warning for a frame at or after it is the bug.
        first_frame = None
        if loaded:
            for ln in cl.snapshot(loaded[0]):
                m = SNAPSHOT_RE.search(ln.text)
                if m:
                    first_frame = int(m.group(2))
                    break
        dropped = []
        for ln in srv.snapshot(reinit[0] if reinit else srv_mark):
            m = DROPPED_RE.search(ln.text)
            if m and first_frame is not None and int(m.group(1)) >= first_frame:
                dropped.append(ln.text)
        print(f"[{second}] 'dropped client usercmds' warnings for frames after the first snapshot: {len(dropped)}")
        if dropped:
            failures.append(f"{len(dropped)} 'Server dropped client usercmds' warnings after the client entered the new game")

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
