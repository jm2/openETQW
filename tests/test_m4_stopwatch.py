#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M4: a stopwatch pair on valley, read from the server's STOPWATCH and MATCH lines.

A dedicated valley server (stopwatch rules, g_warmup 0, si_timelimit 0.5, cheats on
for completeObjective) with a paused GDF soldier (bot 0) and Strogg aggressor (bot 1);
each round is started with 'admin startMatch'. With g_logGameEvents the game prints
a STOPWATCH line when a round ends: the round, its time, the time to beat, the
attackers and the winners. Checked:
- W1 round 1, no objective done: the time limit ends it with GDF attacking and Strogg
  winning; the time to beat is the round's time, 30 s (+-3 s);
- W2 the teams swap for round 2 (bot 0 joins Strogg, bot 1 GDF);
- W3 round 2, objectives 0-3 completed: GDF (now bot 1) attacks against round 1's
  time to beat, finishes in less, and wins; round 1's winner is Strogg;
- W4 the teams swap back after the pair (bot 0 joins GDF again);
- W5 no failure marker, and the server exits 0 on quit.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

STOPWATCH_RE = re.compile(r"STOPWATCH: time=(\d+) round=(\d) roundTime=(\d+) timeToBeat=(\d+) attackers=(\S+) "
                          r"winner=(\S+) firstRoundWinner=(\S+)")


class Failed(Exception):
    pass


def count(srv, text, start=0):
    return sum(text in l.text for l in srv.snapshot(start))


def rounds(srv):
    return [dict(round=int(m.group(2)), roundTime=int(m.group(3)), timeToBeat=int(m.group(4)), attackers=m.group(5),
                 winner=m.group(6), firstRoundWinner=m.group(7))
            for l in srv.snapshot() if (m := STOPWATCH_RE.search(l.text))]


def start_round(srv, n, timeout=40.0):
    """Start round n: gameon counted from the line where the start was first asked, not over the whole log."""
    mark = srv.mark()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        srv.command("admin startMatch")   # the rules drop a start request made at map start: ask until gameon
        if count(srv, "event=state state=gameon", mark) >= 1:
            srv.command("bot_pause 1")
            return
        time.sleep(1.0)
    raise Failed(f"round {n} did not reach gameon after 'admin startMatch'")


def wait_round(srv, n, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = [r for r in rounds(srv) if r["round"] == n]
        if found:
            return found[0]
        time.sleep(0.5)
    raise Failed(f"no STOPWATCH line for round {n} within {timeout:.0f} s")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--port", type=int, default=29985)
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

    srv = h.DedicatedServer(binary, data, "valley", args.port, log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesStopWatch", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "g_warmup": "0", "si_timelimit": "0.5", "g_gameReviewPause": "0.1",
                                   "net_allowCheats": "1", "bot_enable": "1", "bot_sleepWhenServerEmpty": "0",
                                   "g_logGameEvents": "1"})
    failures = []
    try:
        srv.start()
        if srv.sync(180.0) is None:
            raise Failed("the server did not answer after loading the map")
        start = srv.mark()
        srv.send("addbot gdf soldier")
        srv.send("addbot strogg aggressor")
        if srv.wait_for(lambda s: s.startswith("SPAWN:") and " client=1 " in s, 60.0, start) is None:
            raise Failed("the bots did not spawn")

        start_round(srv, 1)
        r1 = wait_round(srv, 1, 60.0)
        print(f"W1 round 1: {r1}")
        if r1["attackers"] != "gdf" or r1["winner"] != "strogg" or abs(r1["timeToBeat"] - 30000) > 3000 \
                or r1["timeToBeat"] != r1["roundTime"]:
            failures.append(f"W1: round 1 {r1}, want GDF attacking, Strogg winning, time to beat = round time = 30 s")

        swap = srv.mark()
        if srv.wait_for(lambda s: "Bot 0 joined the Strogg" in s, 90.0, 0) is None \
                or srv.wait_for(lambda s: "Bot 1 joined the GDF" in s, 10.0, 0) is None:
            failures.append("W2: the teams did not swap for round 2")
        else:
            print("W2 teams swapped for round 2")

        start_round(srv, 2)
        for obj in range(4):
            srv.command(f"completeObjective {obj}")
            time.sleep(1.5)
        r2 = wait_round(srv, 2, 30.0)
        print(f"W3 round 2: {r2}")
        if r2["attackers"] != "gdf" or r2["winner"] != "gdf" or r2["timeToBeat"] != r1["timeToBeat"] \
                or r2["roundTime"] >= r2["timeToBeat"] or r2["firstRoundWinner"] != "strogg":
            failures.append(f"W3: round 2 {r2}, want GDF attacking and winning in less than {r1['timeToBeat']} ms, "
                            "round 1 won by Strogg")

        if srv.wait_for(lambda s: "Bot 0 joined the GDF" in s, 90.0, swap) is None:
            failures.append("W4: the teams did not swap back after the pair")
        else:
            print("W4 teams swapped back")
    except Failed as e:
        failures.append(str(e))
    finally:
        code = srv.quit()
        bad = h.scan_failures(l.text for l in srv.snapshot())
        srv.cleanup()
    if bad or code != 0:
        failures.append(f"W5: failure markers {bad[:3]}, exit code {code}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: round 1 timed out and set the time to beat, the teams swapped, round 2's attackers beat it on "
          "objectives, the teams swapped back, clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
