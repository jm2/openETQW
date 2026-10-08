#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""M4: every class of both teams spawns with its own kit, read from the server's SPAWN lines.

A dedicated valley server (objective rules) adds one bot of each of the ten
classes; with g_logGameEvents the game prints a SPAWN line per spawn with the
team, class, health and every inventory item. Checked:
- C1 ten spawns, one per class, each on the team asked for and alive (health > 0);
- C2 each class carries its role items (the retail 1.5 kits: the soldier's HE
  charge and LAW, the medic's defibrillator, the engineer's pliers and mines,
  the field ops' ammo pack and artillery deploy tool, the covert ops' hacking
  pliers and sniper rifle; the Strogg equivalents);
- C3 no other class carries those role items;
- C4 the team items: every GDF class has the GDF parachute and no Strogg class
  does; every Strogg class has the spikes and no GDF class does;
- C5 no failure marker, and the server exits 0 on quit.

Exit 77 when ETQW_DATA_PATH is unset/invalid or the binary is missing.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

SPAWN_RE = re.compile(r"SPAWN: time=(\d+) client=(\d+) team=(\S+) class=(\S+) revive=(\d) health=(-?\d+) weapon=(\S+) items=(\S*)")

ROLE_ITEMS = {
    ("gdf", "soldier"): ["items/he_charge", "weapons/law", "weapons/gpmg"],
    ("gdf", "medic"): ["tools/defibrillator", "items/healthpack", "weapons/grenades/supply_marker_medic"],
    ("gdf", "engineer"): ["tools/pliers", "tools/deploytool/engineer", "items/landmine"],
    ("gdf", "fieldops"): ["items/ammopack", "tools/deploytool/fieldops", "weapons/grenades/airstrike_marker_gdf"],
    ("gdf", "covertops"): ["tools/pliers_hack", "tools/deploytool/ranger", "items/third_eye", "weapons/sniperrifle"],
    ("strogg", "aggressor"): ["items/plasma_charge", "weapons/obliterator", "weapons/hyperblaster"],
    ("strogg", "technician"): ["tools/spike_meditech", "items/stroyentcells"],
    ("strogg", "constructor"): ["tools/repair_drone", "tools/deploytool/constructor", "items/landmine_strogg"],
    ("strogg", "oppressor"): ["tools/deploytool/oppressor", "tools/force_shield", "weapons/grenades/airstrike_marker_strogg"],
    ("strogg", "infiltrator"): ["tools/spike_infiltrator", "tools/deploytool/infiltrator", "items/teleporter", "weapons/railgun"],
}
TEAM_ITEMS = {"gdf": "tools/gdf_parachute", "strogg": "weapons/spikes"}


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ded-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29962)
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
                                   "si_maxPlayers": "16", "bot_enable": "1", "bot_sleepWhenServerEmpty": "0",
                                   "g_logGameEvents": "1"})
    failures, spawns = [], {}
    try:
        srv.start()
        if srv.sync(180.0) is None:
            raise RuntimeError("the server did not answer after loading the map")
        start = srv.mark()
        for team, cls in ROLE_ITEMS:
            srv.send(f"addbot {team} {cls}")
        r = srv.wait_for(lambda s: s.startswith("SPAWN:") and len([1 for l in srv.snapshot(start)
                                                                  if l.text.startswith("SPAWN:")]) >= len(ROLE_ITEMS),
                         60.0, start)
        if r is None:
            failures.append("C1: fewer than ten SPAWN lines within 60 s")
        for l in srv.snapshot(start):
            m = SPAWN_RE.search(l.text)
            if m and m.group(5) == "0":
                key = (m.group(3), m.group(4))
                spawns.setdefault(key, {"health": int(m.group(6)),
                                        "items": {i.removeprefix("inventory/") for i in m.group(8).split(",") if i}})
        print("spawns:")
        for key in sorted(spawns):
            print(f"  {key[0]:6} {key[1]:11} health {spawns[key]['health']:3}  {len(spawns[key]['items'])} items")
        # C1
        for key in ROLE_ITEMS:
            if key not in spawns:
                failures.append(f"C1: no spawn of {key[0]} {key[1]}")
            elif spawns[key]["health"] <= 0:
                failures.append(f"C1: {key} spawned with health {spawns[key]['health']}")
        extra = set(spawns) - set(ROLE_ITEMS)
        if extra:
            failures.append(f"C1: spawns of classes not asked for: {sorted(extra)}")
        # C2, C3
        for key, role in ROLE_ITEMS.items():
            if key not in spawns:
                continue
            missing = [i for i in role if i not in spawns[key]["items"]]
            if missing:
                failures.append(f"C2: {key} lacks {missing}")
            for other, o in spawns.items():
                shared = [i for i in role if other != key and i in o["items"]]
                if shared:
                    failures.append(f"C3: {other} carries {key}'s role items {shared}")
        # C4
        for key, s in spawns.items():
            for team, item in TEAM_ITEMS.items():
                if (key[0] == team) != (item in s["items"]):
                    failures.append(f"C4: {key} {'lacks' if key[0] == team else 'carries'} {item}")
    except RuntimeError as e:
        failures.append(str(e))
    finally:
        exit_code = srv.quit()
        bad = h.scan_failures(l.text for l in srv.snapshot())
        srv.cleanup()
    if bad:
        failures.append(f"C5: failure markers: {bad[:3]}")
    if exit_code != 0:
        failures.append(f"C5: server exit code {exit_code}")
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: all ten classes spawn on their teams with their role items, no class carries another's, "
          "team items on the right team, clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
