#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Two remote clients survive a reconnect, a client restart and a map restart.

One dedicated server and two headless remote clients (the dedicated binary
started with +connect, as test_remote_client_map_change.py does) on one map:
1. Both clients connect, spawn on opposite teams (GDF, Strogg) and land.
   Each client's copy of the other player is within 2 units of the server's.
2. Reconnect: client A runs 'reconnect'. The server gives it a game init
   again, A receives snapshots again, spawns again, and both clients again
   see each other where the server has them.
3. Client restart: client A's process is killed without a disconnect and a
   new process connects. A client socket takes a random port, so the server
   cannot tell it is the same player: the new process gets a new slot and
   plays as above, and the dead one's slot is freed by the client timeout
   ('client N dropped: timed out'; the server runs with
   net_serverClientTimeout --client-timeout), leaving 2 slots in use.
4. Map restart: 'serverMapRestart' on the server with both clients connected.
   Both clients keep receiving snapshots, the game respawns both players on
   their teams, and they see each other.
After each step one client walks forward and the other must see it move
(>= 50 units, ending within 2 units of the server's position), alternating
which client walks. All three processes then exit 0 on 'quit', and no log
has a failure marker (etqw_harness.scan_failures).

--latency/--jitter/--drop apply net_forceLatency/net_forceJitter/net_forceDrop
to all three processes. Exit 77 when ETQW_DATA_PATH is unset/invalid or the
binary is missing.
"""

import argparse
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_remote_client_map_change import GAME_INIT_RE, READY, RemoteClient, snapshots  # noqa: E402

SLOT_LINE_RE = re.compile(r"^slot\s+(\d+):\s+(\w+)\s")  # 'status': slot N: <state> human|bot '<name>'
TOLERANCE = 2.0          # units between a client's copy of a player and the server's
MIN_DISPLACEMENT = 50.0  # units a walking player must be seen to move
MIN_SNAPSHOTS = 5


class Failed(Exception):
    pass


def player_pos(proc, client_num):
    lines = proc.command(f"playerPos {client_num}") or []
    for ln in lines:
        p = h.parse_player_pos(ln.text)
        if p and p["client"] == client_num:
            return p
    raise Failed(f"no PLAYER_POS for client {client_num} from {proc.name}")


def wait_snapshots(cl, start, need=MIN_SNAPSHOTS, timeout=120.0):
    cl.wait_for(lambda s: len(snapshots(cl, start)) >= need or None, timeout, start)
    got = snapshots(cl, start)
    if len(got) < need:
        raise Failed(f"{cl.name} received {len(got)} snapshots (expected >= {need})")
    return got


def spawn(srv, cl, team, timeout=30.0, mark=None):
    """clientTeam/clientClass on the client (a player spawns with the next
    respawn wave); wait for the server's spawn line for that client on that
    team from line mark on (default: now), then for the player to land."""
    if mark is None:
        mark = srv.mark()
        cl.send(f"clientTeam {team}")
        cl.send("clientClass soldier")
        for extra in getattr(cl, "spawn_commands", ()):  # the Vulkan client also closes its limbo menu
            cl.send(extra)
    hit = srv.wait_for(lambda s: (p := h.parse_spawn(s)) and p["client"] == cl.slot
                       and p["team"].lower() == team and not p["spectating"] and p or None, timeout, mark)
    if not hit:
        raise Failed(f"client {cl.slot} ({cl.name}) never spawned on {team}")
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        p = player_pos(srv, cl.slot)
        if p["ground"] == 1 and not p["spectating"]:
            return p
        time.sleep(0.5)
    raise Failed(f"client {cl.slot} ({cl.name}) did not land within 30 s")


def check_mutual(srv, a, b, step):
    for observer, target in ((a, b), (b, a)):
        if observer is srv:
            continue  # a listen host's view of a player is the server's own state
        settle(srv, target.slot)
        want = player_pos(srv, target.slot)["org"]
        seen = player_pos(observer, target.slot)["org"]
        err = math.dist(want, seen)
        print(f"[{step}] {observer.name} sees client {target.slot} at {seen}; server: {want} (error {err:.2f})")
        if err > TOLERANCE:
            raise Failed(f"[{step}] {observer.name}'s copy of client {target.slot} is {err:.2f} units off")


def settle(srv, client_num):
    """Wait until the server has the player at rest (it slides for about half a
    second after a stop), then for a few more snapshots to reach the clients."""
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and math.hypot(*player_pos(srv, client_num)["vel"]) >= 1.0:
        time.sleep(0.25)
    time.sleep(0.5)


def check_walk(srv, mover, observer, step, fwd=127, seconds=1.5):
    """The mover walks (clientMove fwd) for a while; the observer's copy of it must
    have moved >= MIN_DISPLACEMENT and, once it is at rest, be within TOLERANCE of
    the server's. When the observer is the server itself (a listen host), only the
    displacement is checked: it shows the server applied the mover's usercmds."""
    before = player_pos(observer, mover.slot)["org"]
    mover.send(f"clientMove {fwd} 0 0")
    time.sleep(seconds)
    mover.send("clientMove 0 0 0")
    settle(srv, mover.slot)
    seen = player_pos(observer, mover.slot)["org"]
    moved = h.dist2d(before, seen)
    if observer is srv:
        print(f"[{step}] the server moved client {mover.slot} {moved:.2f} units from its usercmds")
        if moved < MIN_DISPLACEMENT:
            raise Failed(f"[{step}] the server moved client {mover.slot} only {moved:.2f} < {MIN_DISPLACEMENT} units")
        return
    want = player_pos(srv, mover.slot)["org"]
    lag = h.dist2d(seen, want)
    print(f"[{step}] {observer.name} saw client {mover.slot} walk {moved:.2f} units; {lag:.2f} from the server's position")
    if moved < MIN_DISPLACEMENT:
        raise Failed(f"[{step}] {observer.name} saw client {mover.slot} move {moved:.2f} < {MIN_DISPLACEMENT} units")
    if lag > TOLERANCE:
        raise Failed(f"[{step}] {observer.name}'s copy of client {mover.slot} is {lag:.2f} units from the server's")


def slots_in_use(srv, expect=None, timeout=15.0):
    """[number of client slots in 'status' that are not zombies]; None when status
    printed no slot table. A dropped slot stays listed as a zombie for
    net_serverZombieTimeout (5 s) and is not counted. With expect, poll until the
    count is [expect]."""
    deadline = time.monotonic() + timeout
    while True:
        lines = srv.command("status")
        states = [m.group(2) for ln in (lines or []) if (m := SLOT_LINE_RE.match(ln.text))]
        used = [sum(1 for s in states if s != "zombie")] if lines else None
        if expect is None or used == [expect] or time.monotonic() >= deadline:
            return used
        time.sleep(0.5)


def connect(srv, cl, what):
    """Start cl (a new process); wait for its game init, its slot and snapshots."""
    mark = srv.mark()
    cl.start()
    hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0, mark)
    if not hit:
        raise Failed(f"{what}: the server never sent {cl.name} a game init")
    cl.slot = int(hit[2].group(1))
    got = wait_snapshots(cl, 0)
    print(f"[{what}] {cl.name} is client {cl.slot}; snapshots from {got[0]}")
    return hit[0]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29950)
    ap.add_argument("--log-dir")
    ap.add_argument("--drop", type=int, default=0, help="percentage of outgoing packets each process drops")
    ap.add_argument("--latency", type=int, default=0, help="ms added to every received packet")
    ap.add_argument("--jitter", type=int, default=0, help="up to this many more ms per packet (order kept)")
    ap.add_argument("--client-timeout", type=int, default=20,
                    help="the server's net_serverClientTimeout (s); the restarted client must connect within it")
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

    def log(name):
        return (log_dir / f"{name}.log") if log_dir else None

    net = {"net_forceDrop": str(args.drop), "net_forceLatency": str(args.latency), "net_forceJitter": str(args.jitter)}
    srv = h.DedicatedServer(binary, data, args.map, args.port, log_path=log("server"),
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "net_serverClientTimeout": str(args.client_timeout), **net})
    srv.name = "SERVER"

    def client(name, port, logname):
        cl = RemoteClient(binary, data, args.port, port, log_path=log(logname), net=net)
        cl.cvars["ui_name"] = name
        cl.name, cl.slot = name, None
        return cl

    a = client("ClientA", args.port + 1, "client_a")
    b = client("ClientB", args.port + 2, "client_b")
    procs = [("server", srv), ("client_a", a), ("client_b", b)]
    retired = []  # killed client processes, whose logs are still scanned
    failures = []
    try:
        srv.start()
        if not srv.wait_for_text(READY, 180.0):
            raise Failed("the server never became ready")
        srv.sync()

        # 1. both clients join and play
        connect(srv, a, "join")
        connect(srv, b, "join")
        spawn(srv, a, "gdf")
        spawn(srv, b, "strogg")
        check_mutual(srv, a, b, "join")
        check_walk(srv, a, b, "join")

        # 2. 'reconnect' on a connected client
        mark = srv.mark()
        a_mark = a.mark()
        a.send("reconnect")
        hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0, mark)
        if not hit:
            raise Failed("reconnect: the server never sent client A a new game init")
        a.slot = int(hit[2].group(1))
        wait_snapshots(a, a_mark)
        used = slots_in_use(srv, 2)
        print(f"[reconnect] client A is client {a.slot}; server slots in use: {used}")
        if used != [2]:
            raise Failed(f"reconnect: server status says {used} client slots in use (expected [2])")
        spawn(srv, a, "gdf")
        check_mutual(srv, a, b, "reconnect")
        check_walk(srv, b, a, "reconnect")

        # 3. the client process dies and a new one connects
        dead_slot = a.slot
        a.kill()
        mark = srv.mark()
        retired.append(("client_a (killed)", a))
        a = client("ClientA", args.port + 1, "client_a2")
        procs[1] = ("client_a2", a)
        joined = connect(srv, a, "restart")
        timed_out = srv.wait_for(lambda s: s.strip() == f"client {dead_slot} dropped: timed out",
                                 args.client_timeout + 15.0, mark)
        used = slots_in_use(srv, 2)
        print(f"[restart] new slot {a.slot}; dead slot {dead_slot} timed out: {bool(timed_out)}; server slots in use: {used}")
        if not timed_out:
            raise Failed(f"restart: the dead client's slot {dead_slot} never timed out")
        if a.slot == dead_slot:
            # the dead slot is free for this address once it has timed out
            if joined < timed_out[0]:
                raise Failed(f"restart: the new client got slot {dead_slot} while the dead client still held it")
            raise Failed(f"restart: the new client connected only after the dead slot timed out"
                         f" ({args.client_timeout} s); raise --client-timeout")
        if used != [2]:
            raise Failed(f"restart: server status says {used} client slots in use (expected [2])")
        spawn(srv, a, "gdf")
        check_mutual(srv, a, b, "restart")
        check_walk(srv, a, b, "restart")

        # 4. map restart with both clients connected: the game respawns both
        # players on their teams
        a_mark, b_mark, srv_mark = a.mark(), b.mark(), srv.mark()
        srv.send("serverMapRestart")
        srv.sync()
        for cl, m in ((a, a_mark), (b, b_mark)):
            got = wait_snapshots(cl, m)
            print(f"[map restart] {cl.name} snapshots after the restart: from {got[0]}")
        used = slots_in_use(srv, 2)
        print(f"[map restart] server slots in use: {used}")
        if used != [2]:
            raise Failed(f"map restart: server status says {used} client slots in use (expected [2])")
        spawn(srv, a, "gdf", mark=srv_mark)
        spawn(srv, b, "strogg", mark=srv_mark)
        check_mutual(srv, a, b, "map restart")
        check_walk(srv, b, a, "map restart")
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs[1:] + procs[:1]:  # clients first, the server last
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs + retired:
            for f in h.scan_failures(ln.text for ln in proc.snapshot()):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            proc.cleanup()
    print(f"quit exit codes: {codes}")
    if not failures and any(c != 0 for c in codes.values()):
        failures.append(f"quit exit codes: {codes}")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: both clients played through a reconnect, a client restart and a map restart")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
