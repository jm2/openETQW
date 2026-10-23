#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M4: a started match on maps beyond the campaign's three.

STATUS.md § Milestones, M4: the campaign tests follow valley, outskirts and
area22 only; a started match on another retail 1.5 map is not shown.  For each
of quarry and island a fresh dedicated server with the objective rules adds two
settled bots, starts the match with 'admin startMatch' and runs it to the time
limit (si_timelimit 0.5, 30 s +-3 s):
  M1 the named map loads (the server's 'Map: maps/<name>.world' line);
  M2 the bots spawn and the match reaches gameon;
  M3 the time limit ends it with the map's defenders winning (from each map
     script's OnTimeLimitHit: GDF on quarry, Strogg on island) and gamereview
     agrees, with no scriptEndGame and no completed objective;
  M4 the server exits 0 on quit with no failure marker.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

MATCH_RE = re.compile(r"MATCH: time=(\d+) rules=(\S+) event=(\S+) state=(\S+) winner=(\S+)")
STATUS_RE = re.compile(r"OBJECTIVE_STATUS:\s*gdf_obj=(-?\d+)\s+strogg_obj=(-?\d+)\s+state=(-?\d+)\s+winner=(\S*)")
SPAWN_RE = re.compile(r"SPAWN: time=\d+ client=(\d+) team=(\S+) class=(\S+)")

MAPS = ("quarry", "island")
DEFENDERS = {"quarry": "gdf", "island": "strogg"}
TIME_LIMIT_S = 30.0
TIME_TOLERANCE_S = 3.0


class Failed(Exception):
    pass


def matches(srv, start=0):
    return [dict(time=int(m.group(1)), rules=m.group(2), event=m.group(3), state=m.group(4), winner=m.group(5))
            for line in srv.snapshot(start) if (m := MATCH_RE.search(line.text))]


def objective_status(srv):
    out = srv.command("objectiveStatus") or []
    m = next((m for line in out if (m := STATUS_RE.search(line.text))), None)
    return None if m is None else {"gdf": int(m.group(1)), "strogg": int(m.group(2)), "state": int(m.group(3)),
                                   "winner": m.group(4)}


def wait_event(srv, pred, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [e for e in matches(srv) if pred(e)]
        if found:
            return found[0]
        time.sleep(0.5)
    return None


def run_map(binary, data, port, map_name, log_dir):
    failures = []
    srv = h.DedicatedServer(binary, data, map_name, port,
                            log_path=(log_dir / f"{map_name}.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "g_warmup": "0", "si_timelimit": "0.5", "net_allowCheats": "1",
                                   "bot_enable": "1", "bot_sleepWhenServerEmpty": "0", "bot_threading": "0",
                                   "g_logGameEvents": "1"})
    srv.start()
    try:
        if srv.sync(180.0) is None:
            raise Failed(f"{map_name}: the server did not answer after loading the map")
        marker = f"Map: maps/{map_name}.world"
        loaded = srv.wait_for(lambda s: marker in s, 120.0)
        print(f"M1 {map_name} loaded: {loaded is not None}")
        if loaded is None:
            raise Failed(f"{map_name}: the server did not load {marker}")

        start = srv.mark()
        srv.send("addbot gdf soldier")
        srv.send("addbot strogg aggressor")
        spawned = {}
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            for line in srv.snapshot(start):
                m = SPAWN_RE.search(line.text)
                if m:
                    spawned[m.group(2).lower()] = int(m.group(1))
            if {"gdf", "strogg"} <= spawned.keys():
                break
            time.sleep(0.5)
        if not {"gdf", "strogg"} <= spawned.keys():
            raise Failed(f"{map_name}: the bots did not spawn, one per team (saw teams {sorted(spawned)})")
        print(f"M2 {map_name} bots: gdf client {spawned['gdf']}, strogg client {spawned['strogg']}")
        srv.command("bot_pause 1")

        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            srv.command("admin startMatch")   # a start request made at map start is dropped: ask until gameon
            if any(e["event"] == "state" and e["state"] == "gameon" for e in matches(srv)):
                break
            time.sleep(1.0)
        gameon = next((e for e in matches(srv) if e["event"] == "state" and e["state"] == "gameon"), None)
        print(f"M2 {map_name} gameon: {gameon}")
        if gameon is None:
            raise Failed(f"{map_name}: the match did not reach gameon after 'admin startMatch'")

        timelimit = wait_event(srv, lambda e: e["event"] == "timelimit", 60.0)
        print(f"M3 {map_name} time limit: {timelimit}")
        if timelimit is None:
            failures.append(f"M3: {map_name}: the time limit was not hit within 60 s")
        else:
            after = (timelimit["time"] - gameon["time"]) / 1000.0
            print(f"M3 {map_name} time limit hit {after:.1f} s after gameon, winner {timelimit['winner']}")
            if abs(after - TIME_LIMIT_S) > TIME_TOLERANCE_S:
                failures.append(f"M3: {map_name}: time limit {after:.1f} s after gameon "
                                f"(want {TIME_LIMIT_S:.0f} +- {TIME_TOLERANCE_S:.0f})")
            if timelimit["winner"] != DEFENDERS[map_name]:
                failures.append(f"M3: {map_name}: time limit winner {timelimit['winner']}, "
                                f"want the defenders, {DEFENDERS[map_name]}")
            review = wait_event(srv, lambda e: e["event"] == "state" and e["state"] == "gamereview", 10.0)
            print(f"M3 {map_name} gamereview: {review}")
            if review is None or review["winner"] != timelimit["winner"]:
                failures.append(f"M3: {map_name}: gamereview {review}, want winner {timelimit['winner']}")

        status = objective_status(srv)
        script = any(e["event"] == "scriptEndGame" for e in matches(srv))
        print(f"M3 {map_name} objective status {status}, script end {script}")
        if status is None or status["gdf"] != 0 or status["strogg"] != 0 or script:
            failures.append(f"M3: {map_name}: an objective was completed or the script ended the match "
                            f"(status {status}, script end {script})")
    finally:
        code = srv.quit()
        bad = h.scan_failures(line.text for line in srv.snapshot())
        srv.cleanup()
    if bad or code != 0:
        failures.append(f"M4: {map_name}: failure markers {bad[:3]}, exit code {code}")
    return failures


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--port", type=int, default=29993)
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
    for map_name in MAPS:
        try:
            failures += run_map(binary, data, args.port, map_name, log_dir)
        except Failed as e:
            failures.append(f"{map_name}: {e}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: an objective match started, ran to its time limit and reviewed on quarry and island")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
