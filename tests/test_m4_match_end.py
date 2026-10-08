#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M4: an objective match ends both ways, read from the server's MATCH lines.

Two fresh dedicated valley servers (objective rules, g_warmup 0, cheats on for
completeObjective), each with a paused GDF soldier and Strogg aggressor; the
match is started with 'admin startMatch'. With g_logGameEvents the game prints a
MATCH line at each state change and when a game ends, with the reason
(event=timelimit or event=scriptEndGame) and the winning team.
- T (the defenders' win, si_timelimit 0.5): no objective is completed. T1 the
  match goes to gameon; T2 the time limit is hit 30 s (+-3 s) after gameon,
  with Strogg (valley's defenders) winning; T3 then gamereview, winner strogg;
  T4 no scriptEndGame and no objective completed.
- O (the attackers' win, si_timelimit 20): objectives 0-3 are completed in turn.
  O1 each completion advances the GDF objective (objectiveStatus); O2 the last
  ends the game through the script (event=scriptEndGame), winner gdf; O3 then
  gamereview, winner gdf; O4 no time limit hit.
- Both: no failure marker, and each server exits 0 on quit.

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


class Failed(Exception):
    pass


def matches(srv, start=0):
    return [dict(time=int(m.group(1)), rules=m.group(2), event=m.group(3), state=m.group(4), winner=m.group(5))
            for l in srv.snapshot(start) if (m := MATCH_RE.search(l.text))]


def objective_status(srv):
    out = srv.command("objectiveStatus") or []
    m = next((m for l in out if (m := STATUS_RE.search(l.text))), None)
    return None if m is None else {"gdf": int(m.group(1)), "strogg": int(m.group(2)), "state": int(m.group(3)),
                                   "winner": m.group(4)}


def start_match(binary, data, port, timelimit, log_path):
    srv = h.DedicatedServer(binary, data, "valley", port, log_path=log_path,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "g_warmup": "0", "si_timelimit": str(timelimit), "net_allowCheats": "1",
                                   "bot_enable": "1", "bot_sleepWhenServerEmpty": "0", "g_logGameEvents": "1"})
    srv.start()
    if srv.sync(180.0) is None:
        raise Failed("the server did not answer after loading the map")
    start = srv.mark()
    srv.send("addbot gdf soldier")
    srv.send("addbot strogg aggressor")
    if srv.wait_for(lambda s: s.startswith("SPAWN:") and " client=1 " in s, 60.0, start) is None:
        raise Failed("the bots did not spawn")
    srv.command("bot_pause 1")
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        srv.command("admin startMatch")   # the rules drop a start request made at map start: ask until gameon
        if any(e["event"] == "state" and e["state"] == "gameon" for e in matches(srv)):
            return srv
        time.sleep(1.0)
    raise Failed("the match did not reach gameon after 'admin startMatch'")


def wait_event(srv, pred, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [e for e in matches(srv) if pred(e)]
        if found:
            return found[0]
        time.sleep(0.5)
    return None


def run_timelimit(binary, data, port, log_dir):
    failures = []
    srv = start_match(binary, data, port, 0.5, (log_dir / "timelimit.log") if log_dir else None)
    try:
        gameon = next(e for e in matches(srv) if e["event"] == "state" and e["state"] == "gameon")
        print(f"T1 gameon at {gameon['time']} ms")
        hit = wait_event(srv, lambda e: e["event"] == "timelimit", 60.0)
        if hit is None:
            failures.append("T2: no time limit hit within 60 s")
        else:
            after = (hit["time"] - gameon["time"]) / 1000.0
            print(f"T2 time limit hit {after:.1f} s after gameon, winner {hit['winner']}")
            if abs(after - 30.0) > 3.0 or hit["winner"] != "strogg":
                failures.append(f"T2: time limit {after:.1f} s after gameon (want 30 +- 3), winner {hit['winner']}")
        review = wait_event(srv, lambda e: e["event"] == "state" and e["state"] == "gamereview", 10.0)
        print(f"T3 gamereview: {review}")
        if review is None or review["winner"] != "strogg":
            failures.append(f"T3: gamereview {review}, want winner strogg")
        status = objective_status(srv)
        if any(e["event"] == "scriptEndGame" for e in matches(srv)) or status is None or status["gdf"] > 0:
            failures.append(f"T4: a script end or a completed objective in the time-limit match (status {status})")
    finally:
        code = srv.quit()
        bad = h.scan_failures(l.text for l in srv.snapshot())
        srv.cleanup()
    if bad or code != 0:
        failures.append(f"T: failure markers {bad[:3]}, exit code {code}")
    return failures


def run_objectives(binary, data, port, log_dir):
    failures = []
    srv = start_match(binary, data, port, 20, (log_dir / "objectives.log") if log_dir else None)
    try:
        before = objective_status(srv)
        for obj in range(4):
            srv.command(f"completeObjective {obj}")
            time.sleep(2.0)
            status = objective_status(srv)
            print(f"O1 completeObjective {obj}: {status}")
            if obj < 3 and (status is None or before is None or status["gdf"] <= before["gdf"]):
                failures.append(f"O1: objective {obj} did not advance the GDF objective ({before} -> {status})")
            before = status
        end = wait_event(srv, lambda e: e["event"] == "scriptEndGame", 15.0)
        print(f"O2 script end: {end}")
        if end is None or end["winner"] != "gdf":
            failures.append(f"O2: script end {end}, want winner gdf")
        review = wait_event(srv, lambda e: e["event"] == "state" and e["state"] == "gamereview", 10.0)
        print(f"O3 gamereview: {review}")
        if review is None or review["winner"] != "gdf":
            failures.append(f"O3: gamereview {review}, want winner gdf")
        if any(e["event"] == "timelimit" for e in matches(srv)):
            failures.append("O4: the time limit was hit in the objective match")
    finally:
        code = srv.quit()
        bad = h.scan_failures(l.text for l in srv.snapshot())
        srv.cleanup()
    if bad or code != 0:
        failures.append(f"O: failure markers {bad[:3]}, exit code {code}")
    return failures


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--port", type=int, default=29972)
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
    for run in (run_timelimit, run_objectives):
        try:
            failures += run(binary, data, args.port, log_dir)
        except Failed as e:
            failures.append(f"{run.__name__}: {e}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: the defenders win when the time limit runs out with no objective done, the attackers win when "
          "the last objective ends the game through the script, each with its own MATCH reason, clean exits")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
