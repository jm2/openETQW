#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M4: a deployable is requested, carried in and landed, for each team, with the deploy checks on.

A dedicated valley server (objective rules, cheats on: 'deploy' is a cheat) with
a paused GDF engineer and a paused Strogg constructor. For each, the test looks for a legal spot near the
team's base: 'deploy' runs the player's own placement check (territory, sky,
deploy mask; g_skipDeployChecks stays 0) at points of a grid until one passes.
With g_debugDeployables the game prints the carrier's spawn, and 'deployStatus'
lists every defence turret with its position. Checked, per team:
- P1 a grid point passes the placement check and the request succeeds;
- P2 the carrier is spawned for that object over that point ([DEPLOY_DELIVER]);
- P3 a new anti-personnel turret of the team appears, and lands within 64 u of
  the point (x, y) and of the ground height the carrier resolved (z), within
  60 s of the request;
- P4 it is still there 5 s later, and the turret count rose by one;
- P5 no failure marker, and the server exits 0 on quit.
Only turrets of the requested type count, so map vehicles respawning on their
timers (valley's construction module) do not matter.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""
import argparse
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

STATUS_RE = re.compile(r"DEPLOY_STATUS: turret=(\S+) type=(\S+) target=\S+ pos=\(([-\d.]+), ([-\d.]+), ([-\d.]+)\)")
TOTAL_RE = re.compile(r"DEPLOY_STATUS: total_turrets=(\d+)")
CARRIER_RE = re.compile(r"\[DEPLOY_DELIVER\] Carrier spawned for (\S+) at \(([-\d.]+), ([-\d.]+), ([-\d.]+)\)")
SUCCESS_RE = re.compile(r"DEPLOY: Client (\d+) deployed (\S+) at \(([-\d.]+), ([-\d.]+), ([-\d.]+)\) -> SUCCESS")

# team, class, deploy object, turret type, search grid around the team's base (x range, y range)
SIDES = [
    ("gdf", "engineer", "deployobject_antipersonnel_gdf", "deployable_antipersonnel_gdf",
     range(8000, 11601, 400), range(-12400, -9199, 400)),
    ("strogg", "constructor", "deployobject_antipersonnel_strogg", "deployable_antipersonnel_strogg",
     range(-12400, -8799, 400), range(9200, 12401, 400)),
]


class Failed(Exception):
    pass


def turrets(srv, kind):
    out = srv.command("deployStatus") or []
    found = [(m.group(1), tuple(float(v) for v in m.groups()[2:])) for l in out
             if (m := STATUS_RE.search(l.text)) and m.group(2) == kind]
    total = next((int(m.group(1)) for l in out if (m := TOTAL_RE.search(l.text))), None)
    return found, total


def deploy_side(srv, client, team, obj, kind, xs, ys):
    before, total_before = turrets(srv, kind)
    point = None
    tries = 0
    for x in xs:
        for y in ys:
            tries += 1
            mark = srv.mark()
            out = srv.command(f"deploy {client} {obj} {x} {y} 2000 0") or []
            m = next((m for l in out if (m := SUCCESS_RE.search(l.text))), None)
            if m:
                point, requested_at = (float(m.group(3)), float(m.group(4))), time.monotonic()
                break
            if any("unknown deployObject" in l.text or "invalid client" in l.text or "not valid in multiplayer" in l.text
                   for l in out):
                raise Failed(f"P1 {team}: " + " | ".join(l.text for l in out))
        if point:
            break
    if point is None:
        raise Failed(f"P1 {team}: no grid point of {tries} passed the placement check")
    print(f"P1 {team}: request accepted at ({point[0]:.0f}, {point[1]:.0f}) after {tries} tries")

    r = srv.wait_for(lambda s: (m := CARRIER_RE.search(s)) and m.group(1) == obj, 30.0, mark)
    if r is None:
        raise Failed(f"P2 {team}: no carrier for {obj} within 30 s")
    m = CARRIER_RE.search(r[1].text)
    ground = float(m.group(4))
    if abs(float(m.group(2)) - point[0]) > 1 or abs(float(m.group(3)) - point[1]) > 1:
        raise Failed(f"P2 {team}: carrier over ({m.group(2)}, {m.group(3)}), not the requested point")
    print(f"P2 {team}: carrier spawned, ground z {ground:.1f}")

    known = {name for name, _ in before}
    landed = None
    while time.monotonic() - requested_at < 60.0:
        now, _ = turrets(srv, kind)
        for name, pos in now:
            if name not in known and abs(pos[0] - point[0]) < 64 and abs(pos[1] - point[1]) < 64 \
                    and abs(pos[2] - ground) < 64:
                landed = (name, pos, time.monotonic() - requested_at)
        if landed:
            break
        time.sleep(1.0)
    if landed is None:
        raise Failed(f"P3 {team}: no new {kind} landed at the point within 60 s")
    print(f"P3 {team}: {landed[0]} landed after {landed[2]:.1f} s at "
          f"({landed[1][0]:.1f}, {landed[1][1]:.1f}, {landed[1][2]:.1f})")

    time.sleep(5.0)
    after, total_after = turrets(srv, kind)
    if landed[0] not in {name for name, _ in after}:
        raise Failed(f"P4 {team}: {landed[0]} was gone 5 s after landing")
    if total_before is None or total_after != total_before + 1:
        raise Failed(f"P4 {team}: turret count {total_before} -> {total_after}, want +1")
    print(f"P4 {team}: still there; turrets {total_before} -> {total_after}")


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29966)
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
                                   "bot_enable": "1", "bot_sleepWhenServerEmpty": "0", "g_logGameEvents": "1",
                                   "g_debugDeployables": "1", "g_skipDeployChecks": "0",
                                   "net_allowCheats": "1"})
    failures = []
    try:
        srv.start()
        if srv.sync(180.0) is None:
            raise Failed("the server did not answer after loading the map")
        start = srv.mark()
        for client, (team, cls, *_rest) in enumerate(SIDES):
            srv.send(f"addbot {team} {cls}")
            if srv.wait_for(lambda s, c=client: s.startswith("SPAWN:") and f" client={c} " in s, 60.0, start) is None:
                raise Failed(f"P1: the {team} {cls} bot did not spawn")
        srv.command("bot_pause 1")
        for client, (team, _cls, obj, kind, xs, ys) in enumerate(SIDES):
            try:
                deploy_side(srv, client, team, obj, kind, xs, ys)
            except Failed as e:
                failures.append(str(e))
    except Failed as e:
        failures.append(str(e))
    finally:
        exit_code = srv.quit()
        run_failed = bool(failures) or exit_code != 0
        bad = h.scan_failures((l.text for l in srv.snapshot()), run_failed=run_failed)
        srv.cleanup()
    if bad:
        failures.append(f"P5: failure markers: {bad[:3]}")
    if exit_code != 0:
        failures.append(f"P5: server exit code {exit_code}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: for both teams a legal spot was found with the checks on, the carrier came, the anti-personnel "
          "turret landed at the spot and stayed, clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
