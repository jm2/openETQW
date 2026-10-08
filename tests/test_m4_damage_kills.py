#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M4: damage and kills on a dedicated server, read from the server's own record.

A dedicated valley server (objective rules, cheats on for damageClient) with
three paused bots: GDF soldier 0, Strogg aggressor 1, GDF medic 2. The game's
g_logGameEvents prints a KILL line for each death; damageClient damages a
player as another player (or the world) through the player's own Damage and
Killed path, by a damage decl times a scale. Checked:
- D1 a single hit lowers the Strogg's health and does not kill;
- D2 hits from the GDF soldier kill the Strogg: one KILL with that victim,
  attacker, both teams and the decl, at the hit that took health to <= 0;
- D3 the dead Strogg is spawned again (a spawn line, not spectating);
- D4 a world kill: KILL with attacker -1;
- D5 a team kill with si_teamDamage 1: GDF soldier kills the GDF medic, and
  the KILL's two teams are both gdf;
- D6 no KILL line the test did not cause (the bots are paused), no failure
  marker (etqw_harness.scan_failures), and the server exits 0 on quit.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

KILL_RE = re.compile(r"KILL: time=(\d+) victim=(\d+) team=(\S+) class=(\S+) attacker=(-?\d+) attackerTeam=(\S+) "
                     r"attackerEnt=(\S+) damage=(-?\d+) decl=(\S+) location=(-?\d+)")
HIT_RE = re.compile(r"DAMAGE_CLIENT: victim=(\d+) attacker=(-?\d+) decl=(\S+) health (-?\d+) -> (-?\d+) inv=(\d+)")
DECL = "damage_gpmg_trojan"


class Failed(Exception):
    pass


def parse_kill(text):
    m = KILL_RE.search(text)
    if not m:
        return None
    g = m.groups()
    return {"victim": int(g[1]), "team": g[2], "class": g[3], "attacker": int(g[4]), "attackerTeam": g[5],
            "decl": g[8]}


def hit(srv, victim, attacker, scale):
    """One damageClient; returns (health before, health after, kills printed with it)."""
    out = srv.command(f"damageClient {victim} {attacker} {scale} {DECL}")
    if out is None:
        raise Failed(f"damageClient {victim} {attacker} {scale}: no reply")
    hits = [m for l in out if (m := HIT_RE.search(l.text))]
    if len(hits) != 1:
        raise Failed(f"damageClient {victim} {attacker} {scale}: expected one DAMAGE_CLIENT line, got "
                     + repr([l.text for l in out]))
    kills = [k for l in out if (k := parse_kill(l.text))]
    return int(hits[0].group(4)), int(hits[0].group(5)), kills


def wait_vulnerable(srv, client, timeout=30.0):
    """Wait out spawn protection: a zero-scale damageClient deals nothing and reports inv=<ms left>."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = srv.command(f"damageClient {client} -1 0 {DECL}") or []
        m = next((m for l in out if (m := HIT_RE.search(l.text))), None)
        if m is not None and int(m.group(6)) == 0:
            return
        time.sleep(min(2.0, int(m.group(6)) / 1000 + 0.2) if m is not None else 1.0)
    raise Failed(f"client {client} stayed invulnerable for {timeout:.0f} s")


def respawn(srv, client, start, timeout=90.0):
    """A dead bot waits for a medic until it taps out, which a paused bot never does: let the bots
    think (ignoring enemies and goals) until this one is spawned again, then pause them again."""
    srv.command("bot_pause 0")
    try:
        return wait_alive(srv, client, start, timeout)
    finally:
        srv.command("bot_pause 1")


def wait_alive(srv, client, start, timeout=60.0):
    """Wait for a spawn line of this client, not spectating, at or after start."""
    def spawned(s):
        p = h.parse_spawn(s)
        return p is not None and p["client"] == client and p["spectating"] == 0
    r = srv.wait_for(spawned, timeout, start)
    if r is None:
        raise Failed(f"client {client} was not spawned within {timeout:.0f} s")
    return r[1]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29958)
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

    srv = h.DedicatedServer(binary, data, args.map, args.port, log_path=(log_dir / "server.log") if log_dir else None,
                            cvars={"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0",
                                   "net_allowCheats": "1", "bot_enable": "1", "bot_sleepWhenServerEmpty": "0",
                                   "si_teamDamage": "1", "g_logGameEvents": "1",
                                   "bot_ignoreEnemies": "1", "bot_ignoreGoals": "1"})
    failures = []
    exit_code = None
    try:
        srv.start()
        if srv.sync(180.0) is None:
            raise Failed("the server did not answer after loading the map")
        start = srv.mark()
        for cmd, client in (("addbot gdf soldier", 0), ("addbot strogg aggressor", 1), ("addbot gdf medic", 2)):
            srv.send(cmd)
            wait_alive(srv, client, start)
        srv.command("bot_pause 1")
        for client in (0, 1, 2):
            wait_vulnerable(srv, client)
        expected_kills = 0

        # D1: one hit
        before, after, kills = hit(srv, 1, 0, 1)
        print(f"D1 one hit: Strogg health {before} -> {after}")
        if not (0 < after < before) or kills:
            failures.append(f"D1: one hit took health {before} -> {after} with kills {kills}")

        # D2: hits until the Strogg dies
        hits, kill = 1, None
        while kill is None and hits < 40:
            before, after, kills = hit(srv, 1, 0, 1)
            hits += 1
            if kills:
                kill = kills[0]
                expected_kills += len(kills)
                if after > 0 or len(kills) != 1:
                    failures.append(f"D2: kill printed at health {after}, kills {kills}")
            elif after <= 0:
                failures.append(f"D2: health {after} with no KILL line")
                break
        want = {"victim": 1, "team": "strogg", "attacker": 0, "attackerTeam": "gdf", "decl": DECL}
        if kill is None:
            failures.append(f"D2: no KILL after {hits} hits")
        elif any(kill[k] != v for k, v in want.items()):
            failures.append(f"D2: KILL {kill}, want {want}")
        print(f"D2 kill after {hits} hits: {kill}")

        # D3: the Strogg comes back
        line = respawn(srv, 1, srv.mark())
        print(f"D3 respawned: {line.text.strip()[:120]}")

        # D4: a world kill (the GDF soldier, attacker -1)
        wait_vulnerable(srv, 0)
        mark4 = srv.mark()
        _, after, kills = hit(srv, 0, -1, 20)
        expected_kills += len(kills)
        if len(kills) != 1 or kills[0]["victim"] != 0 or kills[0]["attacker"] != -1 or kills[0]["decl"] != DECL \
                or after > 0:
            failures.append(f"D4: world kill gave health {after}, kills {kills}")
        print(f"D4 world kill: {kills}")

        # D5: a team kill (GDF soldier 0 kills GDF medic 2); the soldier is respawned first
        respawn(srv, 0, mark4)
        wait_vulnerable(srv, 0)
        wait_vulnerable(srv, 2)
        _, after, kills = hit(srv, 2, 0, 20)
        expected_kills += len(kills)
        if len(kills) != 1 or kills[0]["victim"] != 2 or kills[0]["attacker"] != 0 \
                or kills[0]["team"] != "gdf" or kills[0]["attackerTeam"] != "gdf" or kills[0]["class"] != "medic":
            failures.append(f"D5: team kill gave health {after}, kills {kills}")
        print(f"D5 team kill: {kills}")

        # D6: nothing else died
        all_kills = [k for l in srv.snapshot() if (k := parse_kill(l.text))]
        if len(all_kills) != expected_kills:
            failures.append(f"D6: {len(all_kills)} KILL lines, the test caused {expected_kills}: {all_kills}")
    except Failed as e:
        failures.append(str(e))
    finally:
        exit_code = srv.quit()
        bad = h.scan_failures(l.text for l in srv.snapshot())
        srv.cleanup()
    if bad:
        failures.append(f"D6: failure markers: {bad[:3]}")
    if exit_code != 0:
        failures.append(f"D6: server exit code {exit_code}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: damage lowers health, a GDF soldier kills a Strogg (KILL with both teams and the decl), the Strogg "
          "respawns, a world kill and a team kill are recorded, nothing else died, clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
