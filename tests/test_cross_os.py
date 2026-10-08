#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cross-OS client/server pair (milestone M3): this host's clients on another host's server.

The server runs on another machine (another OS) and is started there by hand; nothing in
this engine can drive it remotely (rcon is a stub). This test runs on the client host:

    OPENETQW_REMOTE_SERVER=host:port python3 tests/test_cross_os.py [--expect-map MAP]

or meson `cross-os` with that variable set (exit 77, SKIP, without it). The server must
be on the same engine and game commits (the clean-break protocol rejects others), with
si_pure 0, net_allowCheats 1 (playerPos on a client is a cheat-free read, the walk uses
clientMove), si_teamForceBalance 0 and g_teamSwitchDelay 0.

  X1 join       the Vulkan client and a headless peer (the dedicated binary with
                +connect) both join, receive snapshots and spawn on GDF
  X2 walk       each walks (clientMove) in turn; the other's copy of it moves at least
                MIN_DISPLACEMENT units, and once it is at rest the observer's copy is
                within TOLERANCE of the mover's own (both are the server's state)
  X3 reconnect  the Vulkan client quits; a new one joins, spawns, and X2 holds again
  X4 map        with --expect-map, the test prints CROSS_OS_CHANGE_MAP and waits for
                the server's operator to change to that map (spawnServer MAP on the
                server console); both clients load it, respawn, and X2 holds again

The server host's console log (playerPos of both clients) is the other half of the
evidence: the server's own copy of each walk.
"""

from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_remote_client_map_change import RemoteClient, snapshots  # noqa: E402
from test_reconnect_restart import MIN_DISPLACEMENT, TOLERANCE, Failed, player_pos  # noqa: E402

MAP_RE = re.compile(r"^Map: maps/(\S+)\.world")
PEER_CVARS = {"si_pure": "0", "ui_name": "CrossPeer", "net_clientShowSnapshots": "1"}
CLIENT_CVARS = {"si_pure": "0", "ui_name": "CrossClient", "net_clientShowSnapshots": "1", "r_vkValidation": "0"}


class RemotePeer(RemoteClient):
    """The headless peer, joining a server on another host."""

    def __init__(self, binary, data, server, port, log_path=None):
        super().__init__(binary, data, 0, port, log_path=log_path)
        self.cvars = dict(PEER_CVARS)
        self.server = server

    def command_line(self):
        args = super().command_line()
        return args[:-1] + [self.server]


def own(proc):
    """The local player's PLAYER_POS on proc (playerPos without an argument)."""
    for ln in proc.command("playerPos") or []:
        p = h.parse_player_pos(ln.text)
        if p:
            return p
    raise Failed(f"no PLAYER_POS for {proc.name}'s own player")


def join(proc, what, timeout=120.0):
    """X1: start proc, wait for snapshots, put it on GDF and wait until it stands."""
    start = proc.mark()
    if proc.proc is None:
        proc.start()
    proc.wait_for(lambda s: len(snapshots(proc, start)) >= 5 or None, timeout, start)
    if len(snapshots(proc, start)) < 5:
        raise Failed(f"{what}: {proc.name} received no snapshots from the server")
    proc.send("clientTeam gdf")
    proc.send("clientClass soldier")
    for extra in getattr(proc, "spawn_commands", ()):
        proc.send(extra)
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        p = own(proc)
        if not p["spectating"] and p["ground"] == 1 and p["team"].lower() == "gdf":
            proc.slot = p["client"]
            print(f"[{what}] {proc.name} is client {proc.slot} at {p['org']}", flush=True)
            return p
        time.sleep(0.5)
    raise Failed(f"{what}: {proc.name} never spawned on GDF")


def at_rest(proc, slot, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        p = player_pos(proc, slot)
        if max(abs(v) for v in p["vel"][:2]) < 1.0:
            return p
        time.sleep(0.25)
    raise Failed(f"client {slot} is still moving on {proc.name} {timeout:.0f} s after its stop")


def away_move(mover, observer):
    """The clientMove (forward, side) that walks the mover away from the observer's
    player: two players can spawn a body apart in line, and walking into the other
    stops the walk short (the first Windows -> Linux run)."""
    me = own(mover)
    other = own(observer)["org"]
    away = (me["org"][0] - other[0], me["org"][1] - other[1])
    yaw = math.radians(me["yaw"] or 0.0)
    forward = (math.cos(yaw), math.sin(yaw))
    right = (math.sin(yaw), -math.cos(yaw))
    options = [((127, 0), forward), ((-127, 0), (-forward[0], -forward[1])),
               ((0, 127), right), ((0, -127), (-right[0], -right[1]))]
    options.sort(key=lambda o: o[1][0] * away[0] + o[1][1] * away[1], reverse=True)
    return [o[0] for o in options]


def walk(mover, observer, step, seconds=1.5):
    """X2 for one direction: away from the other player; a walk stopped short by a wall
    tries the next direction once. Afterwards the mover walks back the way it came, so a
    later phase does not start against the wall it reached (the second Windows run)."""
    for fwd, side in away_move(mover, observer)[:2]:
        before = player_pos(observer, mover.slot)["org"]
        mover.send(f"clientMove {fwd} {side} 0")
        time.sleep(seconds)
        mover.send("clientMove 0 0 0")
        time.sleep(0.75)
        seen = at_rest(observer, mover.slot)["org"]
        mine = at_rest(mover, mover.slot)["org"]
        moved = h.dist2d(before, seen)
        err = h.dist2d(seen, mine)
        # back the way it came (a backward leg is slower: 1.6 x as long, as mixed-caps found)
        back = seconds * (1.6 if fwd > 0 else 1.0)
        mover.send(f"clientMove {-fwd} {-side} 0")
        time.sleep(back)
        mover.send("clientMove 0 0 0")
        time.sleep(0.75)
        if moved >= MIN_DISPLACEMENT:
            break
        print(f"[{step}] {mover.name} moved only {moved:.1f} u (a wall?): trying another direction", flush=True)
    print(f"[{step}] {observer.name} saw {mover.name} (client {mover.slot}) walk {moved:.1f} u;"
          f" {err:.2f} u from {mover.name}'s own copy", flush=True)
    if moved < MIN_DISPLACEMENT:
        raise Failed(f"[{step}] {observer.name} saw {mover.name} move {moved:.1f} < {MIN_DISPLACEMENT} u")
    if err > TOLERANCE:
        raise Failed(f"[{step}] {observer.name}'s copy of {mover.name} is {err:.2f} u from its own")


def both_ways(a, b, step):
    walk(a, b, f"{step} {a.name}")
    walk(b, a, f"{step} {b.name}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", default=os.environ.get("OPENETQW_REMOTE_SERVER", ""),
                    help="host:port of the remote dedicated server (default: $OPENETQW_REMOTE_SERVER)")
    ap.add_argument("--expect-map", default="", help="X4: wait for the operator to change the server to this map")
    ap.add_argument("--port", type=int, default=29970, help="this host's first local port")
    ap.add_argument("--log-dir")
    args = ap.parse_args(argv)
    if not args.server:
        print("[SKIP] no remote server (set OPENETQW_REMOTE_SERVER=host:port)")
        return h.EXIT_SKIP
    try:
        data = h.resolve_data_path()
        ded = h.find_ded_binary(None)
        vk = h.build_binary("client")
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return h.EXIT_SKIP
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    def log(name):
        return (log_dir / f"cross-os-{name}.log") if log_dir else None

    procs = []
    failures = []
    try:
        peer = RemotePeer(ded, data, args.server, args.port + 1, log_path=log("peer"))
        peer.name = "CrossPeer"
        procs.append(peer)
        gfx = VulkanClient(vk, data, args.port, connect_to=args.server, log_path=log("client"), cvars=dict(CLIENT_CVARS))
        gfx.name = "CrossClient"
        procs.append(gfx)
        peer.start()
        if not peer.wait_for_text("------------- ETQW initialized -------------", 120.0):
            raise Failed("the peer never initialized")
        join(peer, "X1")
        join(gfx, "X1")
        both_ways(gfx, peer, "X2")

        # X3: the Vulkan client leaves and a new one joins
        gfx.quit()
        gfx.cleanup()
        procs.remove(gfx)
        gfx = VulkanClient(vk, data, args.port + 2, connect_to=args.server, log_path=log("client2"),
                           cvars=dict(CLIENT_CVARS))
        gfx.name = "CrossClient"
        procs.append(gfx)
        join(gfx, "X3")
        both_ways(gfx, peer, "X3")

        if args.expect_map:
            print(f"CROSS_OS_CHANGE_MAP {args.expect_map}: run 'spawnServer {args.expect_map}' on the server console",
                  flush=True)
            marks = {p.name: p.mark() for p in (gfx, peer)}
            for p in (gfx, peer):
                hit = p.wait_for(lambda s: (m := MAP_RE.search(s)) and m.group(1) == args.expect_map or None,
                                 600.0, marks[p.name])
                if not hit:
                    raise Failed(f"X4: {p.name} never loaded {args.expect_map}")
            time.sleep(5.0)
            join(peer, "X4")
            join(gfx, "X4")
            both_ways(gfx, peer, "X4")
    except Failed as e:
        failures.append(str(e))
    finally:
        for p in procs:
            if p.alive():
                p.quit()
            p.cleanup()
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return h.EXIT_FAIL
    print(f"PASS: this host's Vulkan client and headless peer joined {args.server}, walked both ways, the client"
          f" reconnected{', and both followed a map change' if args.expect_map else ''}")
    return h.EXIT_PASS


if __name__ == "__main__":
    h.install_signal_handlers()
    sys.exit(main(sys.argv[1:]))
