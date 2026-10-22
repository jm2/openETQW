#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M4: a time-limited match played out by bots, which complete no objective.

STATUS.md § Milestones, M4: the transitions are shown by helper commands, but a
match played out by bots is not.  A fresh dedicated server with the objective
rules adds two bots (GDF soldier and Strogg aggressor), leaves them running and
starts the match with 'admin startMatch'.  With si_timelimit 0.5 the defenders
(Strogg on valley) win on the time limit 30 s (+-3 s) after gameon:
  B1 the bots spawn and are seen alive by 'playerPos' during the match;
  B2 at least one bot moves (the bots play, rather than stand at spawn);
  B3 the match reaches gameon and its time limit with winner strogg;
  B4 the game reports no objective completion and no scriptEndGame: the bots
     play no objectives;
  B5 gamereview follows with winner strogg;
  B6 no failure marker and the server exits 0 on quit.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

MATCH_RE = re.compile(r"MATCH: time=(\d+) rules=(\S+) event=(\S+) state=(\S+) winner=(\S+)")
STATUS_RE = re.compile(r"OBJECTIVE_STATUS:\s*gdf_obj=(-?\d+)\s+strogg_obj=(-?\d+)\s+state=(-?\d+)\s+winner=(\S*)")
SPAWN_RE = re.compile(r"SPAWN: time=\d+ client=(\d+) team=(\S+) class=(\S+)")

TIME_LIMIT_S = 30.0
TIME_TOLERANCE_S = 3.0
MOVE_MIN = 100.0


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


def bot_clients(srv, start):
    """client number of the spawn line for each team (the bots are the only players)."""
    found = {}
    for line in srv.snapshot(start):
        m = SPAWN_RE.search(line.text)
        if m:
            found.setdefault(m.group(2).lower(), int(m.group(1)))
    return found


def player_pos(srv, client):
    for line in srv.command(f"playerPos {client}") or []:
        p = h.parse_player_pos(line.text)
        if p and p["client"] == client:
            return p
    return None


def wait_event(srv, pred, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [e for e in matches(srv) if pred(e)]
        if found:
            return found[0]
        time.sleep(0.5)
    return None


def run(binary, data, port, map_name, log_dir):
    failures = []
    srv = h.DedicatedServer(binary, data, map_name, port,
                            log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "g_warmup": "0", "si_timelimit": "0.5", "net_allowCheats": "1",
                                   "bot_enable": "1", "bot_sleepWhenServerEmpty": "0", "bot_threading": "0",
                                   "g_logGameEvents": "1"})
    srv.start()
    try:
        if srv.sync(180.0) is None:
            raise Failed("the server did not answer after loading the map")
        start = srv.mark()
        srv.send("addbot gdf soldier")
        srv.send("addbot strogg aggressor")
        deadline = time.monotonic() + 60.0
        clients = {}
        while time.monotonic() < deadline:
            clients = bot_clients(srv, start)
            if len(clients) >= 2:
                break
            time.sleep(0.5)
        if len(clients) < 2:
            raise Failed(f"the bots did not spawn (saw teams {sorted(clients)})")
        print(f"B1 bots: gdf client {clients['gdf']}, strogg client {clients['strogg']}")

        start_deadline = time.monotonic() + 30.0
        while time.monotonic() < start_deadline:
            srv.command("admin startMatch")
            if any(e["event"] == "state" and e["state"] == "gameon" for e in matches(srv)):
                break
            time.sleep(1.0)
        gameon = next((e for e in matches(srv) if e["event"] == "state" and e["state"] == "gameon"), None)
        if gameon is None:
            raise Failed("the match did not reach gameon after 'admin startMatch'")
        print(f"B3 gameon at {gameon['time']} ms")

        first, moved, alive = {}, {c: 0 for c in clients.values()}, {c: 0 for c in clients.values()}
        end = time.monotonic() + 45.0
        timelimit = None
        while time.monotonic() < end:
            timelimit = next((e for e in matches(srv) if e["event"] == "timelimit"), None)
            if timelimit is not None:
                break
            for team, client in clients.items():
                p = player_pos(srv, client)
                if p is None or p["spectating"]:
                    continue
                alive[client] += 1
                if client not in first:
                    first[client] = p["org"]
                else:
                    moved[client] = max(moved[client], math.dist(first[client], p["org"]))
            time.sleep(0.5)

        for team, client in sorted(clients.items()):
            if alive[client] < 10:
                failures.append(f"B1: {team} bot {client} was seen alive in only {alive[client]} samples")
        best = max(moved.values()) if moved else 0.0
        print(f"B2 bot movement: " + ", ".join(f"client {c} {d:.1f} u" for c, d in sorted(moved.items())))
        if best < MOVE_MIN:
            failures.append(f"B2: no bot moved {MOVE_MIN} u during the match (best {best:.1f} u)")

        print(f"B3 time limit: {timelimit}")
        if timelimit is None:
            failures.append("B3: the time limit was not hit within 45 s")
        else:
            after = (timelimit["time"] - gameon["time"]) / 1000.0
            print(f"B3 time limit hit {after:.1f} s after gameon, winner {timelimit['winner']}")
            if abs(after - TIME_LIMIT_S) > TIME_TOLERANCE_S or timelimit["winner"] != "strogg":
                failures.append(f"B3: time limit {after:.1f} s after gameon (want {TIME_LIMIT_S:.0f} +- "
                                f"{TIME_TOLERANCE_S:.0f}), winner {timelimit['winner']} (want strogg)")

        status = objective_status(srv)
        script = any(e["event"] == "scriptEndGame" for e in matches(srv))
        print(f"B4 objective status {status}, script end {script}")
        if status is None or status["gdf"] != 0 or status["strogg"] != 0 or script:
            failures.append(f"B4: a bot completed an objective or ended the match through the script "
                            f"(status {status}, script end {script})")

        review = wait_event(srv, lambda e: e["event"] == "state" and e["state"] == "gamereview", 10.0)
        print(f"B5 gamereview: {review}")
        if review is None or review["winner"] != "strogg":
            failures.append(f"B5: gamereview {review}, want winner strogg")
    finally:
        code = srv.quit()
        bad = h.scan_failures(line.text for line in srv.snapshot())
        srv.cleanup()
    if bad or code != 0:
        failures.append(f"B6: failure markers {bad[:3]}, exit code {code}")
    return failures


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29992)
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
    try:
        failures += run(binary, data, args.port, args.map, log_dir)
    except Failed as e:
        failures.append(f"run: {e}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: two live bots played a full time-limited match without completing an objective")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
