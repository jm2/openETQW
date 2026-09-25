#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A long two-client session over a lossy, jittery link completes without a disconnect.

The charter's M3 soak: a dedicated server and two headless remote clients
(the dedicated binary started with +connect) play for --minutes (default 10)
with every process dropping --drop % (default 2) of its outgoing packets and
delaying each received packet by --latency plus 0..--jitter ms (defaults 20
and 40: 40 ms +/- 20 ms; order is kept, so packets are never reordered).

Both clients spawn on opposite teams. Every --interval seconds one of them
(alternately) walks forward or back for a second, and when both players are
at rest and alive each client's copy of the other must be within 2 units of
the server's. A player who is dead or back in limbo (the match moving from
warmup to game on respawns everyone) is spawned again. Throughout, and at
the end:
- the server drops no client and both slots stay in use;
- each client keeps receiving snapshots: no gap longer than --max-gap seconds;
- no process logs a failure marker (etqw_harness.scan_failures);
- all three processes exit 0 on 'quit'.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_reconnect_restart import (  # noqa: E402
    TOLERANCE, Failed, check_mutual, connect, player_pos, slots_in_use, spawn)
from test_remote_client_map_change import READY, SNAPSHOT_RE, RemoteClient  # noqa: E402


def snapshot_gaps(cl, start):
    """Seconds between consecutive received snapshots from line index start."""
    times = [ln.t for ln in cl.snapshot(start) if SNAPSHOT_RE.search(ln.text)]
    return max((b - a for a, b in zip(times, times[1:])), default=0.0), len(times)


def alive(p):
    return not p["spectating"] and (p["hp"] is None or p["hp"] > 0) and not p.get("dead")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29940)
    ap.add_argument("--log-dir")
    ap.add_argument("--minutes", type=float, default=10.0)
    ap.add_argument("--drop", type=int, default=2, help="percentage of outgoing packets each process drops")
    ap.add_argument("--latency", type=int, default=20, help="ms added to every received packet")
    ap.add_argument("--jitter", type=int, default=40, help="up to this many more ms per packet (order kept)")
    ap.add_argument("--interval", type=float, default=15.0, help="seconds between walk and replication checks")
    ap.add_argument("--max-gap", type=float, default=3.0, help="longest allowed pause between snapshots (s)")
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
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0", **net})
    srv.name = "SERVER"
    clients = []
    for name, port, team in (("ClientA", args.port + 1, "gdf"), ("ClientB", args.port + 2, "strogg")):
        cl = RemoteClient(binary, data, args.port, port, log_path=log(name.lower()), net=net)
        cl.cvars["ui_name"] = name
        cl.name, cl.slot, cl.team = name, None, team
        clients.append(cl)
    a, b = clients
    procs = [("server", srv), ("client_a", a), ("client_b", b)]
    failures = []
    stats = {"checks": 0, "walks": 0, "respawns": 0}
    try:
        srv.start()
        if not srv.wait_for_text(READY, 180.0):
            raise Failed("the server never became ready")
        srv.sync()
        for cl in clients:
            connect(srv, cl, "join")
        for cl in clients:
            spawn(srv, cl, cl.team)
        session_start = srv.mark()
        client_start = {cl.name: cl.mark() for cl in clients}
        print(f"session: {args.minutes} min, drop {args.drop}%, delay {args.latency}..{args.latency + args.jitter} ms")

        end = time.monotonic() + args.minutes * 60.0
        n = 0
        while time.monotonic() < end:
            time.sleep(args.interval)
            n += 1
            dropped = [ln.text for ln in srv.snapshot(session_start) if " dropped: " in ln.text]
            if dropped:
                raise Failed(f"the server dropped a client: {dropped[0]}")
            for cl in clients:
                if not cl.alive():
                    raise Failed(f"{cl.name} exited (code {cl.proc.returncode})")
                gap, count = snapshot_gaps(cl, client_start[cl.name])
                if gap > args.max_gap:
                    raise Failed(f"{cl.name} went {gap:.1f} s without a snapshot")
            for cl in clients:
                if not alive(player_pos(srv, cl.slot)):
                    print(f"[{n}] client {cl.slot} is dead or in limbo; spawning again")
                    spawn(srv, cl, cl.team)
                    stats["respawns"] += 1
            check_mutual(srv, a, b, f"check {n}")
            stats["checks"] += 1
            mover = clients[n % 2]
            mover.send(f"clientMove {127 if (n // 2) % 2 == 0 else -127} 0 0")
            time.sleep(1.0)
            mover.send("clientMove 0 0 0")
            stats["walks"] += 1
            elapsed = args.minutes * 60.0 - (end - time.monotonic())
            gaps = ", ".join(f"{cl.name} {snapshot_gaps(cl, client_start[cl.name])[1]} snapshots"
                             f" (longest gap {snapshot_gaps(cl, client_start[cl.name])[0]:.2f} s)" for cl in clients)
            print(f"[{n}] {elapsed / 60.0:.1f} min: {gaps}")

        used = slots_in_use(srv, 2)
        print(f"end: server slots in use {used}; {stats}")
        if used != [2]:
            raise Failed(f"server status says {used} client slots in use at the end (expected [2])")
        for cl in clients:
            p = player_pos(cl, cl.slot)
            if p is None:
                raise Failed(f"{cl.name} cannot report its own player")
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs[1:] + procs[:1]:
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs:
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
    print(f"\nPASS: {args.minutes} min at {args.drop}% loss and {args.latency}..{args.latency + args.jitter} ms delay"
          f" without a disconnect ({stats['checks']} replication checks, {stats['walks']} walks, {stats['respawns']} respawns)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
