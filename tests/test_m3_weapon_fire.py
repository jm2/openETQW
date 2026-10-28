#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""A weapon's tracer reaches another client, not just the shooter (M3).

A GDF client joins the dedicated server as a covertops with class option 1
(option 0, item 1 of the covertops class data is 'package/weapons/sniperrifle';
`clientClass covertops 1` sends that option, so no game-side diagnostic command
is needed). A second, headless client joins on the Strogg side. The shooter
holds the attack key for a number of cycles.

The game prints 'EVENT_TRACER: server broadcast tracer weapon ...' on the
server when it sends the unreliable event, and 'EVENT_TRACER: client received
tracer weapon ...' on every client that handles it. The test requires:

  1. the shooter's SPAWN line (g_logGameEvents 1) says class=covertops, the
     server's weapon-state diagnostic (g_debugWeaponState) names the sniper
     rifle the class option selects, and both players are on the ground and
     at rest before firing;
  2. at least three broadcasts, at most one per attack cycle, and all from one
     weapon entity (a single-shot weapon refires slower than the 0.35 s
     attack cycle; this is an observable check, not the item name);
  3. the shooter and the observer each receive every broadcast;
  4. the start and end of each received tracer equal the server's copy, in
     order (within 0.01 units);
  5. the server's playerPos is polled every 0.25 s while firing, for the
     shooter's own client number, and the shooter stays within 2 units in
     three dimensions (a falling spawn or a push would fail).

Every process must exit 0 on 'quit' and log no crash or fatal marker.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the dedicated binary is
missing; the binary is both the server and the two headless clients.
"""

import argparse
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_remote_client_map_change import RemoteClient  # noqa: E402

SPAWN_RE = re.compile(r"SPAWN:\s+time=\d+\s+client=(\d+)\s+team=(\S+)\s+class=(\S+)")
TRACER_RE = re.compile(
    r"EVENT_TRACER: (server broadcast|client received) tracer weapon '([^']+)' "
    r"start=\(([-\d.]+), ([-\d.]+), ([-\d.]+)\) "
    r"end=\(([-\d.]+), ([-\d.]+), ([-\d.]+)\)")
EQUIPPED_RE = re.compile(r"Weapon \('([^']+)'\) State for Client (\d+):")
FATAL = ("FATAL ERROR", "FATAL SIGNAL CAUGHT", "Unhandled exception", "Access violation")
SNIPER_ITEM = "inventory/weapons/sniperrifle"
# the weapon-state diagnostic lists every weapon entity the client updates,
# tools included (GDF covertops carries inventory/tools/gdf_parachute), so
# only the items under this prefix are weapons the class option chooses.
WEAPON_PREFIX = "inventory/weapons/"
POLL_INTERVAL = 0.25
EPSILON = 0.011
DRIFT_TOLERANCE = 2.0


def tracers(proc, start, kind):
    """(weapon, start, end) of one tracer kind ('server broadcast' or
    'client received') from lines at index >= start, in order."""
    out = []
    for line in proc.snapshot(start):
        m = TRACER_RE.search(line.text)
        if m and m.group(1) == kind:
            out.append((m.group(2),
                        tuple(float(m.group(i)) for i in (3, 4, 5)),
                        tuple(float(m.group(i)) for i in (6, 7, 8))))
    return out


def near(a, b):
    return all(abs(x - y) <= EPSILON for x, y in zip(a, b))


def fatal_lines(proc, start=0):
    # the shared markers (bind failures, ERROR-banner drops, AAS and wrong-thread
    # markers) as every other retail test checks them, plus this test's own crash strings
    texts = [line.text for line in proc.snapshot(start)]
    bad = [t for t in texts if any(m in t for m in FATAL)]
    for f in h.scan_failures(texts):
        if f["text"] not in bad:
            bad.append(f["text"])
    return bad


def positions(srv, clients):
    seen = {}
    for client in clients:
        for line in srv.command(f"playerPos {client}") or []:
            pos = h.parse_player_pos(line.text)
            if pos and pos["client"] == client:
                seen[client] = pos
    return seen


def wait_at_rest(srv, clients, timeout=60.0):
    """Both players grounded and still (3D)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen = positions(srv, clients)
        if len(seen) == len(clients) and all(
                p.get("ground") == 1
                and h.dist2d(p["vel"][:2], (0.0, 0.0)) < 1.0
                and abs(p["vel"][2]) < 1.0
                for p in seen.values()):
            return seen
        time.sleep(0.5)
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
    h.install_signal_handlers()

    def log(name):
        return (log_dir / name) if log_dir else None

    failures = []
    srv = h.DedicatedServer(
        ded, data, args.map, args.port, log_path=log("server.log"),
        cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0",
               "g_teamSwitchDelay": "0", "si_maxPlayers": "16",
               "bot_enable": "0", "net_allowCheats": "1",
               "g_logGameEvents": "1"})
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

        spawn_mark = srv.mark()
        shooter.send("clientTeam gdf")
        shooter.send("clientClass covertops 1")
        shooter.send("spawn")
        observer.send("clientTeam strogg")
        observer.send("clientClass soldier")
        observer.send("spawn")

        spawn = srv.wait_for(lambda s: (m := SPAWN_RE.search(s)) and m.group(2).lower() == "gdf" or None,
                             60.0, spawn_mark)
        if not spawn:
            print("FAIL: the shooter's SPAWN line never arrived")
            return 1
        m = SPAWN_RE.search(spawn[1].text)
        shooter_client, klass = int(m.group(1)), m.group(3).lower()
        print(f"shooter is client {shooter_client}, class={klass}")
        if klass != "covertops":
            failures.append(f"the shooter spawned as class={klass}, not covertops")

        other_client = 1 if shooter_client == 0 else 0
        if wait_at_rest(srv, [shooter_client, other_client]) is None:
            print("FAIL: both players did not settle on the ground")
            return 1

        # the item the class option selected is observable on the server: the
        # game's weapon-state diagnostic names the item, and a rejected
        # `clientClass covertops 1` still spawns the class with other weapons.
        srv.send(f"set g_debugWeaponState {shooter_client}")
        srv_mark = srv.mark()
        shooter_mark = shooter.mark()
        observer_mark = observer.mark()
        samples = []
        for _ in range(args.shots):
            shooter.send("-attack")
            time.sleep(0.15)
            shooter.send("+attack")
            time.sleep(0.25)
            srv.send(f"playerPos {shooter_client}")
            srv.send(f"playerPos {other_client}")
            time.sleep(POLL_INTERVAL)
        shooter.send("-attack")
        time.sleep(4.0)
        srv.send("set g_debugWeaponState -1")

        for line in srv.snapshot(srv_mark):
            pos = h.parse_player_pos(line.text)
            if pos and pos["client"] == shooter_client:
                samples.append(pos)

        server_tracers = tracers(srv, srv_mark, "server broadcast")
        shooter_tracers = tracers(shooter, shooter_mark, "client received")
        observer_tracers = tracers(observer, observer_mark, "client received")
        print(f"broadcasts={len(server_tracers)} shooter={len(shooter_tracers)} "
              f"observer={len(observer_tracers)} playerPos={len(samples)}")

        if not 3 <= len(server_tracers) <= args.shots:
            failures.append(f"the server broadcast {len(server_tracers)} tracers, "
                            f"not 3..{args.shots}")
        names = {name for name, _, _ in server_tracers}
        equipped = {m.group(1) for line in srv.snapshot(srv_mark)
                    if (m := EQUIPPED_RE.search(line.text)) and int(m.group(2)) == shooter_client}
        print(f"equipped by client {shooter_client}: {sorted(equipped)}")
        weapons = {name for name in equipped if name.startswith(WEAPON_PREFIX)}
        if weapons != {SNIPER_ITEM}:
            failures.append(f"the shooter's weapons were {sorted(weapons)}, not {SNIPER_ITEM} "
                            f"(full item set: {sorted(equipped)})")
        if len(names) > 1:
            failures.append(f"the broadcasts came from {len(names)} weapon entities: {sorted(names)}")
        if len(shooter_tracers) != len(server_tracers):
            failures.append(f"the shooter received {len(shooter_tracers)} of "
                            f"{len(server_tracers)} broadcasts")
        if len(observer_tracers) != len(server_tracers):
            failures.append(f"the observer received {len(observer_tracers)} of "
                            f"{len(server_tracers)} broadcasts")
        for i, (server, got) in enumerate(zip(server_tracers, observer_tracers)):
            if not (near(server[1], got[1]) and near(server[2], got[2])):
                failures.append(f"observer tracer {i} differs from the server: {got} != {server}")
        for i, (server, got) in enumerate(zip(server_tracers, shooter_tracers)):
            if not (near(server[1], got[1]) and near(server[2], got[2])):
                failures.append(f"shooter tracer {i} differs from the server: {got} != {server}")

        if len(samples) < args.shots:
            failures.append(f"only {len(samples)} playerPos samples for the shooter "
                            f"in {args.shots} shots")
        if not samples:
            failures.append("no playerPos samples for the shooter")
        else:
            mx = statistics.median(p["org"][0] for p in samples)
            my = statistics.median(p["org"][1] for p in samples)
            mz = statistics.median(p["org"][2] for p in samples)
            drift = max(((p["org"][0] - mx) ** 2 + (p["org"][1] - my) ** 2
                         + (p["org"][2] - mz) ** 2) ** 0.5 for p in samples)
            print(f"shooter samples={len(samples)} max 3D drift={drift:.2f} u")
            if drift > DRIFT_TOLERANCE:
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
