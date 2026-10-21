#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A weapon's tracer reaches another client, not just the shooter (M3).

The shooter joins the dedicated server as a GDF covertops with class option 1
(option 0, item 1 of the covertops class data is
'package/weapons/sniperrifle'; 'clientClass covertops 1' sends that option, so
no game-side diagnostic command is needed). A second, headless client joins on
the Strogg side. The shooter holds the attack key for a number of cycles.

The game prints 'EVENT_TRACER: server broadcast tracer weapon ...' on the
server when it sends the unreliable event, and 'EVENT_TRACER: client received
tracer weapon ...' on every client that handles it. The test requires:

  1. at least three broadcasts (the sniper refires slower than the attack
     cycles, so the count is below the number of cycles);
  2. the shooter and the observer each receive every broadcast;
  3. the start and end of each received tracer equal the server's copy, in
     order (within 0.01 units);
  4. the server's playerPos is polled every 0.25 s while firing, with the
     shooter at rest (its sampled position stays within 32 units).

Every process must exit 0 on 'quit' and log no crash or fatal marker.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the dedicated binary is
missing; the binary is both the server and the two headless clients.
"""

import argparse
import os
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_remote_client_map_change import RemoteClient  # noqa: E402

TRACER_RE = re.compile(
    r"EVENT_TRACER: (server broadcast|client received) tracer weapon '([^']+)' "
    r"start=\(([-\d.]+), ([-\d.]+), ([-\d.]+)\) "
    r"end=\(([-\d.]+), ([-\d.]+), ([-\d.]+)\)")
FATAL = ("FATAL ERROR", "Fatal signal caught", "Unhandled exception", "Access violation")
POLL_INTERVAL = 0.25
EPSILON = 0.011


def tracers(proc, start, kind):
    """(start, end) float pairs of one tracer kind ('server broadcast' or
    'client received') from lines at index >= start, in order."""
    out = []
    for line in proc.snapshot(start):
        m = TRACER_RE.search(line.text)
        if m and m.group(1) == kind:
            out.append((tuple(float(m.group(i)) for i in (3, 4, 5)),
                        tuple(float(m.group(i)) for i in (6, 7, 8))))
    return out


def near(a, b):
    return all(abs(x - y) <= EPSILON for x, y in zip(a, b))


def fatal_lines(proc, start=0):
    return [line.text for line in proc.snapshot(start) if any(m in line.text for m in FATAL)]


def wait_for_both_spawned(srv, timeout=90.0):
    """Wait until playerPos parses for clients 0 and 1 and both are alive."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        lines = srv.command("playerPos 0") or []
        lines += srv.command("playerPos 1") or []
        seen = {}
        for line in lines:
            pos = h.parse_player_pos(line.text)
            if pos:
                seen[pos["client"]] = pos
        if len(seen) == 2 and all(p.get("dead") in (0, None) for p in seen.values()):
            return seen
        time.sleep(1.0)
    return None


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29974, help="server port; clients use +1 and +2")
    ap.add_argument("--log-dir")
    ap.add_argument("--shots", type=int, default=24)
    args = ap.parse_args(argv)

    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    data = h.env_data_path()
    try:
        ded = h.find_ded_binary(args.ded_binary)
        data = h.resolve_data_path()
    except h.SkipTest as exc:
        print(f"[SKIP] {exc}")
        return 77
    h.skip_unless_available(data, ded)

    def log(name):
        return (log_dir / name) if log_dir else None

    failures = []
    srv = h.DedicatedServer(
        ded, data, args.map, args.port, log_path=log("server.log"),
        cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0",
               "g_teamSwitchDelay": "0", "si_maxPlayers": "16",
               "bot_enable": "0",
               "net_allowCheats": "1"})
    shooter = RemoteClient(ded, data, args.port, args.port + 1,
                           log_path=log("shooter.log"), net={"ui_name": "GDF_Sniper"})
    observer = RemoteClient(ded, data, args.port, args.port + 2,
                            log_path=log("observer.log"), net={"ui_name": "Strogg_Observer"})
    try:
        srv.start()
        if srv.sync(180.0) is None:
            print("FAIL: the dedicated server did not sync")
            return 1
        shooter.start()
        observer.start()
        if shooter.wait_for_text("Client received snapshot", 180.0) is None:
            print("FAIL: the shooter never received a snapshot")
            return 1
        if observer.wait_for_text("Client received snapshot", 180.0) is None:
            print("FAIL: the observer never received a snapshot")
            return 1
        time.sleep(10.0)

        shooter.send("clientTeam gdf")
        shooter.send("clientClass covertops 1")
        shooter.send("spawn")
        observer.send("clientTeam strogg")
        observer.send("clientClass soldier")
        observer.send("spawn")
        if wait_for_both_spawned(srv) is None:
            print("FAIL: both clients did not spawn")
            return 1

        positions = []

        def poll():
            srv.send("playerPos 0")
            srv.send("playerPos 1")

        for _ in range(4):
            poll()
            time.sleep(POLL_INTERVAL)

        srv_mark = srv.mark()
        shooter_mark = shooter.mark()
        observer_mark = observer.mark()
        for _ in range(args.shots):
            shooter.send("-attack")
            time.sleep(0.15)
            shooter.send("+attack")
            time.sleep(0.25)
            poll()
            time.sleep(POLL_INTERVAL)
        shooter.send("-attack")
        time.sleep(4.0)

        for line in srv.snapshot(srv_mark):
            pos = h.parse_player_pos(line.text)
            if pos:
                positions.append(pos)

        server_tracers = tracers(srv, srv_mark, "server broadcast")
        shooter_tracers = tracers(shooter, shooter_mark, "client received")
        observer_tracers = tracers(observer, observer_mark, "client received")
        print(f"broadcasts={len(server_tracers)} shooter={len(shooter_tracers)} "
              f"observer={len(observer_tracers)} playerPos={len(positions)}")

        if len(server_tracers) < 3:
            failures.append(f"the server broadcast {len(server_tracers)} tracers, fewer than 3")
        if len(shooter_tracers) != len(server_tracers):
            failures.append(f"the shooter received {len(shooter_tracers)} of "
                            f"{len(server_tracers)} broadcasts")
        if len(observer_tracers) != len(server_tracers):
            failures.append(f"the observer received {len(observer_tracers)} of "
                            f"{len(server_tracers)} broadcasts")
        for i, (server, got) in enumerate(zip(server_tracers, observer_tracers)):
            if not (near(server[0], got[0]) and near(server[1], got[1])):
                failures.append(f"observer tracer {i} differs from the server: {got} != {server}")
        for i, (server, got) in enumerate(zip(server_tracers, shooter_tracers)):
            if not (near(server[0], got[0]) and near(server[1], got[1])):
                failures.append(f"shooter tracer {i} differs from the server: {got} != {server}")

        if len(positions) < args.shots:
            failures.append(f"only {len(positions)} playerPos samples for {args.shots} shots")
        shooter_positions = [p["org"] for p in positions if p["client"] == 0]
        if not shooter_positions:
            failures.append("no playerPos samples for the shooter")
        else:
            mx = statistics.median(p[0] for p in shooter_positions)
            my = statistics.median(p[1] for p in shooter_positions)
            drift = max(((p[0] - mx) ** 2 + (p[1] - my) ** 2) ** 0.5 for p in shooter_positions)
            print(f"shooter samples={len(shooter_positions)} max drift={drift:.2f} u")
            if drift > 32.0:
                failures.append(f"the shooter moved {drift:.2f} u while firing")

        for name, proc in (("server", srv), ("shooter", shooter), ("observer", observer)):
            bad = fatal_lines(proc)
            if bad:
                failures.append(f"{name} logged a fatal marker: {bad[0]}")

        codes = {}
        for name, proc in (("shooter", shooter), ("observer", observer), ("server", srv)):
            codes[name] = proc.quit(timeout=45.0)
            if codes[name] != 0:
                failures.append(f"{name} exited {codes[name]} on quit (expected 0)")
        print("exit codes:", codes)
    finally:
        for proc in (shooter, observer, srv):
            proc.cleanup()

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: the server broadcast the tracers and both clients received identical start and end points")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
