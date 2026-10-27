#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M4: a campaign plays its three maps, a connected client follows, then an admin map change.

A dedicated server (campaign rules, 'spawnServer campaign_northamerica': valley,
outskirts, area22; g_warmup 0, si_timelimit 0.5, cheats on) with a headless remote
client (the dedicated binary with +connect) on GDF and a paused Strogg bot. Each map
is started with 'admin startMatch' and ends on the time limit, which the map's
defenders win. With g_logGameEvents the game prints a CAMPAIGN line when a map ends.
The campaign moves to its next map with the frame command "map <name>", which the
engine runs as the rules' own change (no OnUserStartMap, so the campaign keeps its
state). Checked:
- C1 each map's CAMPAIGN line names the campaign, the map (n/3) and the map the
  server loaded ("Map: maps/<name>.world"); the defenders win (valley Strogg,
  outskirts Strogg, area22 GDF);
- C2 the wins add up map by map, and si_campaignInfo lists each map's winner
  ("2", "2 2", "2 2 1"), so the game's server info writes reach the server info;
- C3 the remote client loads every map the server loads, and the server re-sends
  it the game init each time; it spawns on GDF on each map (its team carries over);
- C4 after area22 the campaign starts over on valley; an argument-less spawnServer
  then restarts it (the server knows the map it runs, not only the campaign asked for);
- C5 an admin map change ('admin setObjectiveMap outskirts', a user change through
  spawnServer) loads outskirts with the objective rules, and the client follows;
- C6 no failure marker, and both processes exit 0 on quit.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

CAMPAIGN_RE = re.compile(r"CAMPAIGN: time=(\d+) campaign=(\S+) map=(\d+)/(\d+) mapName=(\S+) winner=(\S+)"
                         r"((?: \w+Wins=\d+)+) campaignWinner=(\S+) info=(.*)$")
GAME_INIT_RE = re.compile(r"Sending game init to client (\d+)")
MAPS = [("valley", "strogg"), ("outskirts", "strogg"), ("area22", "gdf")]   # (map, its defenders)
TEAM_INDEX = {"gdf": 1, "strogg": 2}   # si_campaignInfo: winner's team index + 1


class Failed(Exception):
    pass


class RemoteClient(h.DedicatedServer):
    """The dedicated binary as a headless client: +connect instead of +spawnServer."""

    def __init__(self, binary, data, server_port, port, log_path=None):
        super().__init__(binary, data, "", port, log_path=log_path,
                         cvars={"si_pure": "0", "ui_name": "CampaignClient", "net_clientShowSnapshots": "1"})
        self.server_port = server_port

    def command_line(self):
        args = super().command_line()
        assert args[-2] == "+spawnServer"
        return args[:-2] + ["+connect", f"127.0.0.1:{self.server_port}"]


def count(proc, text, start=0):
    return sum(text in l.text for l in proc.snapshot(start))


def start_map(srv, n, mark, timeout=60.0):
    """Start map n: gameon counted from mark (where the map's load began), not over the whole log."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        srv.command("admin startMatch")   # the rules drop a start request made at map start: ask until gameon
        if count(srv, "event=state state=gameon", mark) >= 1:
            srv.command("bot_pause 1")
            return
        time.sleep(1.0)
    raise Failed(f"map {n} did not reach gameon after 'admin startMatch'")


def campaign_lines(srv):
    out = []
    for l in srv.snapshot():
        m = CAMPAIGN_RE.search(l.text)
        if m:
            wins = dict(re.findall(r"(\w+)Wins=(\d+)", m.group(7)))
            out.append(dict(campaign=m.group(2), map=int(m.group(3)), maps=int(m.group(4)), mapName=m.group(5),
                            winner=m.group(6), wins={k: int(v) for k, v in wins.items()},
                            campaignWinner=m.group(8), info=m.group(9).strip()))
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--port", type=int, default=29986)
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

    srv = h.DedicatedServer(binary, data, "campaign_northamerica", args.port,
                            log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesCampaign", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "g_warmup": "0", "si_timelimit": "0.5", "g_gameReviewPause": "0.1",
                                   "net_allowCheats": "1", "bot_enable": "1", "bot_sleepWhenServerEmpty": "0",
                                   "g_logGameEvents": "1"})
    cl = RemoteClient(binary, data, args.port, args.port + 1, log_path=(log_dir / "client.log") if log_dir else None)
    failures = []
    try:
        srv.start()
        if srv.sync(180.0) is None:
            raise Failed("the server did not answer after loading the first map")
        mark = srv.mark()
        cl.start()
        hit = srv.wait_for(lambda s: GAME_INIT_RE.search(s), 120.0, mark)
        if hit is None:
            raise Failed("the server never sent the remote client a game init")
        slot = int(hit[2].group(1))
        # the client knows clientTeam only once its own game runs the map: wait for its snapshots
        if cl.wait_for(lambda s: "Client received snapshot" in s, 120.0, 0) is None:
            raise Failed("the remote client received no snapshot")
        time.sleep(1.0)
        cl.send("clientTeam gdf")
        cl.send("clientClass soldier")
        srv.send("addbot strogg aggressor")
        if srv.wait_for(lambda s: s.startswith("SPAWN:") and f" client={slot} " in s and " team=gdf " in s,
                        60.0, mark) is None:
            raise Failed(f"the remote client (client {slot}) did not spawn on GDF")
        print(f"remote client is client {slot}")

        expected_info, wins = [], {"gdf": 0, "strogg": 0}
        prev_end, prev_cl = srv.mark(), cl.mark()
        for n, (name, defenders) in enumerate(MAPS, 1):
            map_mark = srv.mark()
            if n > 1:
                if srv.wait_for(lambda s, nm=name: f"Map: maps/{nm}.world" in s, 120.0, prev_end) is None:
                    raise Failed(f"C1: the server did not load {name} for map {n}")
                if srv.wait_for(lambda s: GAME_INIT_RE.search(s) and f"client {slot}" in s, 60.0, prev_end) is None:
                    failures.append(f"C3: no game init re-sent to the client for {name}")
                if cl.wait_for(lambda s, nm=name: f"Map: maps/{nm}.world" in s, 120.0, prev_cl) is None:
                    failures.append(f"C3: the remote client did not load {name}")
                if srv.wait_for(lambda s: s.startswith("SPAWN:") and f" client={slot} " in s and " team=gdf " in s,
                                90.0, prev_end) is None:
                    failures.append(f"C3: the remote client did not spawn on GDF on {name}")
            start_map(srv, n, prev_end)
            if srv.wait_for(lambda s, k=n: (m := CAMPAIGN_RE.search(s)) and int(m.group(3)) == k, 90.0, map_mark) is None:
                raise Failed(f"C1: no CAMPAIGN line for map {n} ({name})")
            line = [c for c in campaign_lines(srv) if c["map"] == n][-1]
            wins[defenders] += 1
            expected_info.append(str(TEAM_INDEX[defenders]))
            print(f"C1/C2 map {n}: {line}")
            if line["campaign"] != "campaign_northamerica" or line["maps"] != 3 or line["mapName"] != f"maps/{name}" \
                    or line["winner"] != defenders:
                failures.append(f"C1: map {n} {line}, want maps/{name} won by {defenders}")
            if line["wins"] != wins or line["info"] != " ".join(expected_info):
                failures.append(f"C2: map {n} wins {line['wins']} info '{line['info']}', "
                                f"want {wins} '{' '.join(expected_info)}'")
            prev_end, prev_cl = srv.mark(), cl.mark()

        if srv.wait_for(lambda s: "Map: maps/valley.world" in s, 120.0, prev_end) is None:
            failures.append("C4: the campaign did not start over on valley after area22")
        else:
            print("C4 the campaign starts over on valley")
        srv.sync(120.0)

        # C4b: a spawnServer without a map restarts the running one: si_map names the campaign's current map
        # (valley), which the server recognises as loaded although the campaign was the request
        mark = srv.mark()
        srv.send("spawnServer")
        srv.sync(120.0)
        restart = srv.wait_for(lambda s: "event=state state=warmup" in s, 30.0, mark)
        denied = [l.text for l in srv.snapshot(mark) if "Denied" in l.text or "Unknown Campaign" in l.text]
        other = [l.text for l in srv.snapshot(mark) if l.text.startswith("Map: ") and "valley" not in l.text]
        if restart is None or denied or other:
            failures.append(f"C4b: 'spawnServer' on the campaign's valley: restart {restart is not None}, "
                            f"denied {denied[:1]}, other map {other[:1]}")
        else:
            print("C4b an argument-less spawnServer restarted valley")

        # C5: an admin map change (a user change: setObjectiveMap runs spawnServer with the objective rules)
        mark, cl_mark = srv.mark(), cl.mark()
        srv.send("admin setObjectiveMap outskirts")
        if srv.wait_for(lambda s: "Map: maps/outskirts.world" in s, 120.0, mark) is None:
            failures.append("C5: 'admin setObjectiveMap outskirts' did not load outskirts")
        elif srv.wait_for(lambda s: "rules=sdGameRulesObjective" in s, 60.0, mark) is None:
            failures.append("C5: outskirts did not run the objective rules after the admin change")
        elif cl.wait_for(lambda s: "Map: maps/outskirts.world" in s, 120.0, cl_mark) is None:
            failures.append("C5: the remote client did not follow the admin map change")
        else:
            print("C5 the admin map change loaded outskirts with the objective rules; the client followed")
    except Failed as e:
        failures.append(str(e))
    finally:
        cl_code = cl.quit()
        code = srv.quit()
        bad = h.scan_failures([l.text for l in srv.snapshot()] + [l.text for l in cl.snapshot()],
                              # a bot-thread pacing warning fails only a failed run (#294)
                              run_failed=bool(failures) or code != 0 or cl_code != 0)
        cl.cleanup()
        srv.cleanup()
    if bad or code != 0 or cl_code != 0:
        failures.append(f"C6: failure markers {bad[:3]}, exit codes server {code} client {cl_code}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: the campaign played valley, outskirts and area22 with the wins and si_campaignInfo adding up, the "
          "client followed each map, the campaign started over, an admin map change loaded and was followed, clean exits")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
