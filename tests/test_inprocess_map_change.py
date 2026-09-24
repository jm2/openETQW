#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""In-process map changes on one dedicated server, with bots.

Checks, on a single server process (no restart between maps):
1. 'spawnServer <other map>' on a running server really loads the other map
   ('Map: maps/<m>.world' and that map's retail entity count), instead of
   restarting the current one.
2. A console line written in the same pipe write as the map-changing command
   still runs after the load (it used to be read ahead and discarded).
3. Bot client slots do not leak.  An admin/console map change resets the
   rules (only the rules' own NextMap progression keeps teams), so the engine
   re-begins the bot clients on the new map, they come back without a team
   and the game's spectator timer kicks them, as in retail.  The test checks
   that 'status' never shows more than 2 slots in use, that the old bots are
   released, and that 2 new bots then take those same slots and get a real
   team spawn on every map.
4. 'spawnServer <same map>' restarts it (serverMapRestart path).
5. 'spawnServer <same map>' after an si_rules change is a full map load, so
   the game makes the new rules (the map restart keeps the old rules object).
6. 'quit' exits 0.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

READY = "------------- ETQW initialized -------------"
SLOTS_RE = re.compile(r"^(\d+) client slots? in use")
ENTS_RE = re.compile(r"\.\.\.(\d+) entities spawned, (\d+) inhibited")


def wait_map_loaded(srv, map_name, start, timeout=120.0):
    """Index of the '...N entities spawned' line of map_name after start."""
    hit = srv.wait_for_text(f"Map: maps/{map_name}.world", timeout, start)
    if not hit:
        return None, None
    ents = srv.wait_for(lambda s: ENTS_RE.search(s), timeout, hit[0])
    if not ents:
        return None, None
    m = ents[2]
    return ents[0], int(m.group(1)) + int(m.group(2))


def bot_spawns(srv, start):
    spawns = {}
    for ln in srv.snapshot(start):
        s = h.parse_spawn(ln.text)
        if s and s.get("spectating") == 0 and s.get("team") in ("gdf", "strogg"):
            spawns.setdefault(s["client"], s)
    return spawns


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ded-binary")
    ap.add_argument("--maps", default="quarry,outskirts,ark",
                    help="comma-separated sequence; the first is loaded at startup")
    ap.add_argument("--port", type=int, default=29950)
    ap.add_argument("--log", help="server log path")
    args = ap.parse_args(argv)
    maps = [m for m in args.maps.split(",") if m]

    try:
        binary = h.find_ded_binary(args.ded_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    paks = h.PakIndex(data)
    h.install_signal_handlers()

    failures = []
    srv = h.DedicatedServer(binary, data, maps[0], args.port,
                            log_path=Path(args.log) if args.log else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0",
                                   "bot_enable": "1", "bot_sleepWhenServerEmpty": "0",
                                   "g_teamSwitchDelay": "0"})
    try:
        srv.start()
        if not srv.wait_for_text(READY, 180.0):
            print("FAIL: server never became ready")
            return 1
        srv.sync()
        start = srv.mark()
        srv.send("addbot gdf soldier")
        srv.send("addbot strogg soldier")
        srv.sync()
        srv.wait_for(lambda s: len(bot_spawns(srv, start)) >= 2 or None, 20.0, start)
        first = bot_spawns(srv, start)
        print(f"[{maps[0]}] bot spawns: { {c: s['team'] for c, s in first.items()} }")
        if len(first) != 2:
            failures.append(f"{maps[0]}: expected 2 bot spawns, got {len(first)}")

        sequence = maps[1:] + [maps[-1]]  # last entry repeats -> same-map restart
        for i, m in enumerate(sequence):
            same = i == len(sequence) - 1
            start = srv.mark()
            token = f"AFTER_MAPCHANGE_{i}"
            # one pipe write: the echo must survive the map load
            srv.send(f"spawnServer {m}\necho {token}")
            if same:
                ok = srv.wait_for_text("entities spawned", 120.0, start)
                print(f"[{m}] same-map spawnServer -> restart {'seen' if ok else 'NOT seen'}")
                if not ok:
                    failures.append(f"{m}: same-map spawnServer did not restart the map")
            else:
                idx, total = wait_map_loaded(srv, m, start)
                expected = h.retail_entity_count(paks, m)
                print(f"[{m}] loaded={idx is not None} entities={total} retail={expected}")
                if idx is None:
                    failures.append(f"{m}: 'spawnServer {m}' did not load maps/{m}.world")
                elif expected is not None and total != expected:
                    failures.append(f"{m}: entities {total} != retail {expected}")
            if not srv.wait_for(lambda s: s.strip() == token, 60.0, start):
                failures.append(f"{m}: command written after the map change was lost")
            else:
                print(f"[{m}] echo after map change: OK")
            srv.sync()
            if same:
                # a same-map restart (serverMapRestart) keeps the bots and respawns them
                srv.wait_for(lambda s: len(bot_spawns(srv, start)) >= 2 or None, 20.0, start)
                spawns = bot_spawns(srv, start)
                lines = srv.command("status") or []
                used = [int(SLOTS_RE.match(l.text).group(1)) for l in lines if SLOTS_RE.match(l.text)]
                print(f"[{m}] restart kept bots: {sorted(spawns)}, status slots in use: {used}")
                if sorted(spawns) != sorted(first) or used != [2]:
                    failures.append(f"{m}: after same-map restart bots {sorted(spawns)}, slots {used} (expected {sorted(first)}, [2])")
                continue
            # the carried-over bot clients are released by the spectator timer
            released = srv.wait_for(lambda s: s.startswith("client 1 dropped") or None, 90.0, start)
            lines = srv.command("status") or []
            used = [int(SLOTS_RE.match(l.text).group(1)) for l in lines if SLOTS_RE.match(l.text)]
            print(f"[{m}] carried-over bots released: {bool(released)}, status slots in use: {used}")
            if not used or used[0] > 2:
                failures.append(f"{m}: status says {used} client slots in use (leak)")
            re_start = srv.mark()
            srv.send("addbot gdf soldier")
            srv.send("addbot strogg soldier")
            srv.sync()
            srv.wait_for(lambda s: len(bot_spawns(srv, re_start)) >= 2 or None, 20.0, re_start)
            spawns = bot_spawns(srv, re_start)
            print(f"[{m}] bot spawns: { {c: (s['team'], round(s['org'][0]), round(s['org'][1])) for c, s in spawns.items()} }")
            if sorted(spawns) != sorted(first):
                failures.append(f"{m}: new bots got slots {sorted(spawns)}, expected the freed {sorted(first)}")
            for c, sp in spawns.items():
                if all(abs(v) < 1 for v in sp["org"]):
                    failures.append(f"{m}: bot {c} spawned at the origin")
            lines = srv.command("status") or []
            used = [int(SLOTS_RE.match(l.text).group(1)) for l in lines if SLOTS_RE.match(l.text)]
            print(f"[{m}] status slots in use after re-adding: {used}")
            if used != [2]:
                failures.append(f"{m}: expected 2 client slots in use, status says {used}")

        last = sequence[-1]
        start = srv.mark()
        srv.send("set si_rules sdGameRulesStopWatch")
        srv.send(f"spawnServer {last}")
        loaded = srv.wait_for_text(f"Map: maps/{last}.world", 120.0, start)
        print(f"[{last}] si_rules change + same-map spawnServer -> full load {'seen' if loaded else 'NOT seen'}")
        if not loaded:
            failures.append(f"{last}: spawnServer after an si_rules change restarted the map instead of loading it")
        elif not srv.wait_for_text("entities spawned", 120.0, loaded[0]):
            failures.append(f"{last}: the reload after the si_rules change never finished")
        srv.sync()

        rc = srv.quit()
        print(f"quit exit code: {rc}")
        if rc != 0:
            failures.append(f"quit exit code {rc}")
        bad = h.scan_failures(l.text for l in srv.snapshot())
        for b in bad:
            failures.append(f"log: {b}")
    finally:
        srv.cleanup()

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nPASS: in-process map changes load the requested map, keep queued commands, and never leak bot slots")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
