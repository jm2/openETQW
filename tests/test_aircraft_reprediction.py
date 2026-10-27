#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Aircraft re-prediction (M5 step 14a; docs/vehicle-responsiveness.md stage 6).

sdAirVehicleControl carries oldCmdAngles and collective between ticks, and neither is
networked, so on the first tick a network client replays after a snapshot of tick S they
hold its newest prediction (tick P) instead of tick S's: the cyclic delta is taken against
cmd(P), not cmd(S), and collective has run P - S ticks ahead. g_airControlHistory (default
1) restores the state the client predicted for tick S. With g_logAirControl the game prints
an AIR_CTL line each tick a piloted aircraft's control runs, on the server and on every
tick the client predicts or replays; a client's replay of tick F and the server's run of
it share framenum F.

A Vulkan listen host on valley spawns an anansi about 280 u above open ground and seats a
Vulkan pilot client in it at once (the headless dedicated binary crashes as a client in a vehicle, and a
dedicated server has no local player to spawn from). The pilot takes off on full
collective, then flies three phases per leg:
  still       full collective (saturated, hovering under the flight ceiling): the floor
  roll        full collective; +left and +right alternating each second (mouse X is roll)
  collective  clientMove -30 for 1 s, then 30 for 1.5 s, repeated: collective moves, then
              saturates at the top for about half a second, which realigns the sides
The legs, loopback latency first so latency only goes up: fix off, fix on; then with
net_forceLatency 50 on both processes (the client runs further ahead, so the cyclic is
reversed rather than lost), fix off, fix on; then simple flight controls, reported only.
After the latency rises the test waits until the server logs no duplicated command for 2 s.

Pairing: each client line with pred=1 since=1 (the first tick replayed after a snapshot)
against the server's line for the same tick and aircraft; their uf (the usercmd's frame)
must match. A pair is dropped when the server duplicated the command of tick S or S+1
(dup>0), or when ahead= is above 30 (a prediction restart); for the collective check also
from a server duplicate until both sides are next at the ceiling. Each judged leg and phase
needs MIN_PAIRS kept pairs with at most a quarter dropped, both sides on the leg's controls,
and in the collective phase MIN_PAIRS / 2 pairs with collective moving; otherwise the run is
inconclusive.

Checks:
  R1 fix on    in every kept pair the client's base (the angles the cyclic delta is taken
               from) equals the server's; with advanced controls on both sides, so does in=
  R2 fix off   the negative control: base differs in >= 80% of the roll phase's kept
               pairs, and coll in >= 80% of the collective phase's kept pairs where the
               server's value is below the ceiling
  R3 coll      fix on: coll equals the server's in every kept pair of the collective phase
  R4 physics   report only: the origin, orientation and angular-velocity error at since=2,
               per leg, in the still phase (the floor) and in the others
A fix-on failure of R1 or R3 is a residual: then the air-control state is networked
(owner, 2026-09-29).

Usage: python tests/test_aircraft_reprediction.py --client-binary build/openETQW-client_arm64
Needs retail data (ETQW_DATA_PATH; exits 77 without it) and a display. --replay <dir>
re-judges the host.log and pilot.log of an earlier --log-dir.
"""

import argparse
import math
import re
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_reconnect_restart import Failed, connect, spawn  # noqa: E402
from test_remote_client_map_change import READY  # noqa: E402

AIR_RE = re.compile(r"AIR_CTL: (.*)$")
FIELD_RE = re.compile(r"(\w+)=(\([^)]*\)|\S+)")
MARK_RE = re.compile(r"^AR_(BEGIN|END) (\S+) (\S+)\s*$")

# about 280 u above open ground beside the badger's start spot. On its gear the anansi
# does not reliably leave the ground on collective alone; in the air, a driver seated at
# once starts the rotor before it falls far (as schedule 2 of g_determinismTest does)
ANANSI_SPAWN = 'spawn vehicle_anansi origin "9100 -10100 1700" angle 67'
CLIMB = 100.0           # units above the lowest point before the legs start
MAX_PREDICTION = 30     # net_clientMaxPrediction (1000 ms) in ticks: beyond it, a restart
MIN_PAIRS = 60          # kept pairs per judged leg and phase
MAX_DROPPED = 0.25
FIX_OFF_MIN = 0.80
EDGE_TICKS = 15         # ticks left out at each end of a phase (0.5 s)
LATENCY = 50

LEGS = [
    # name, latency (ms), g_airControlHistory, advanced controls, judged
    ("loop_off", 0, 0, 1, True),
    ("loop_on", 0, 1, 1, True),
    ("lat_off", LATENCY, 0, 1, True),
    ("lat_on", LATENCY, 1, 1, True),
    ("lat_simple", LATENCY, 1, 0, False),
]
PHASES = ("still", "roll", "collective")


def parse(lines):
    """AIR_CTL lines as dicts, with the index of the line in its log."""
    out = []
    for i, s in enumerate(lines):
        m = AIR_RE.search(s)
        if not m:
            continue
        r = dict(FIELD_RE.findall(m.group(1)))
        for k in ("tick", "ent", "uf", "dup", "adv", "pred", "since", "ahead", "grounded", "gear"):
            r[k] = int(r[k])
        r["i"] = i
        out.append(r)
    return out


def vec(s):
    return [float(x) for x in s.strip("()").split(",")]


def quat_angle(a, b):
    """Degrees between the rotations of unit quaternions a and b (4 atan2(|a-b|, |a+b|) with
    b on a's side: unlike 2 acos(a.b), exact for tiny angles)."""
    if sum(x * y for x, y in zip(a, b)) < 0.0:
        b = [-y for y in b]
    return math.degrees(4.0 * math.atan2(math.dist(a, b), math.hypot(*(x + y for x, y in zip(a, b)))))


def p95(xs):
    return statistics.quantiles(xs, n=20)[-1] if len(xs) >= 20 else max(xs)


def phase_spans(lines):
    """(leg, phase) -> (first, last) line index in the host's log, from its AR_ markers."""
    spans, open_ = {}, {}
    for i, s in enumerate(lines):
        m = MARK_RE.match(s.strip())
        if not m:
            continue
        key = (m.group(2), m.group(3))
        if m.group(1) == "BEGIN":
            open_[key] = i
        elif key in open_:
            spans[key] = (open_.pop(key), i)
    return spans


def evaluate(host_lines, pilot_lines):
    """Returns (results, report): results maps a check to (ok, detail)."""
    srv_rows = [r for r in parse(host_lines) if r["side"] == "srv"]
    cl_rows = [r for r in parse(pilot_lines) if r["side"] == "cl"]
    spans = phase_spans(host_lines)
    results, report = {}, []
    if not srv_rows or not cl_rows:
        results["data"] = (False, f"AIR_CTL lines: server {len(srv_rows)}, client {len(cl_rows)}"
                                  " (does the game build have g_logAirControl?)")
        return results, report
    srv = {(r["tick"], r["ent"]): r for r in srv_rows}
    # the collective's ceiling, where the still and roll phases hold it and each collective
    # cycle ends (the phases never take it down to its floor)
    top = max(float(r["coll"]) for r in srv_rows)

    def at_top(v):
        return float(v) == top

    # the server tick range of each phase, from the server's lines between the host's markers:
    # (first judged tick, last judged tick, the phase's first tick)
    ranges = {}
    for key, (a, b) in spans.items():
        ticks = [r["tick"] for r in srv_rows if a < r["i"] < b]
        if len(ticks) > 2 * EDGE_TICKS:
            ranges[key] = (min(ticks) + EDGE_TICKS, max(ticks) - EDGE_TICKS, min(ticks))
    firsts = sorted((r for r in cl_rows if r["pred"] == 1 and r["since"] == 1), key=lambda r: r["tick"])
    seconds = [r for r in cl_rows if r["pred"] == 1 and r["since"] == 2]

    lockstep_errors = []
    fails = {"R1": [], "R2": [], "R3": []}
    data_problems = []
    for leg, latency, fix, adv, judged in LEGS:
        for phase in PHASES:
            rng = ranges.get((leg, phase))
            if rng is None:
                data_problems.append(f"{leg}/{phase}: no phase span in the host's log")
                continue
            cands = [r for r in firsts if rng[0] <= r["tick"] <= rng[1]]
            kept, dropped, coll_kept = [], {"no server line": 0, "dup": 0, "restart": 0}, []
            tainted = False
            last_tick = rng[2]
            for c in cands:
                t = c["tick"]
                s1, s0 = srv.get((t, c["ent"])), srv.get((t - 1, c["ent"]))
                # a server duplicate anywhere since the phase began or the last pair taints
                # collective until both sides are at the ceiling again
                if any(srv.get((u, c["ent"]), {}).get("dup", 0) > 0 for u in range(last_tick, t + 1)):
                    tainted = True
                last_tick = t
                if s1 is None or s0 is None:
                    dropped["no server line"] += 1
                    continue
                if s1["uf"] != c["uf"]:
                    lockstep_errors.append(f"{leg}/{phase} tick {t}: client uf={c['uf']}, server uf={s1['uf']}")
                    continue
                if s0["dup"] > 0 or s1["dup"] > 0:
                    dropped["dup"] += 1
                    continue
                if c["ahead"] > MAX_PREDICTION:
                    dropped["restart"] += 1
                    continue
                kept.append((c, s1))
                if tainted and at_top(c["coll"]) and at_top(s1["coll"]):
                    tainted = False
                if not tainted:
                    coll_kept.append((c, s1))
            n_drop = sum(dropped.values())
            total = len(kept) + n_drop
            aheads = sorted(c["ahead"] for c, _ in kept)
            ahead_hist = {a: aheads.count(a) for a in sorted(set(aheads))}
            base_diff = sum(1 for c, s in kept if c["base"] != s["base"])
            in_diff = sum(1 for c, s in kept if c["adv"] == 1 and s["adv"] == 1 and c["in"] != s["in"])
            in_diff_all = sum(1 for c, s in kept if c["in"] != s["in"])
            moving = [(c, s) for c, s in coll_kept if not at_top(s["coll"])]
            coll_diff_moving = sum(1 for c, s in moving if c["coll"] != s["coll"])
            coll_diff = sum(1 for c, s in coll_kept if c["coll"] != s["coll"])
            tainted_drops = len(kept) - len(coll_kept)
            wrong_adv = sum(1 for c, s in kept if c["adv"] != adv or s["adv"] != adv)
            report.append(f"{leg:10s} {phase:10s} pairs {len(kept):3d} kept, {n_drop:3d} dropped {dropped};"
                          f" ahead {ahead_hist}; base differs {base_diff}, in differs {in_diff} (any controls {in_diff_all});"
                          f" coll differs {coll_diff} of {len(coll_kept)} ({coll_diff_moving} of {len(moving)} moving;"
                          f" {tainted_drops} after a duplicate)")
            if not judged:
                continue
            if len(kept) < MIN_PAIRS or (total and n_drop > MAX_DROPPED * total):
                data_problems.append(f"{leg}/{phase}: {len(kept)} kept pairs, {n_drop} of {total} dropped"
                                     f" (need >= {MIN_PAIRS} kept and <= {MAX_DROPPED:.0%} dropped)")
                continue
            if wrong_adv:
                # the server learns the controls from userinfo, which can lag the pilot's change
                data_problems.append(f"{leg}/{phase}: {wrong_adv} kept pairs without adv={adv} on both sides")
                continue
            if phase == "collective":
                if tainted_drops > MAX_DROPPED * len(kept):
                    data_problems.append(f"{leg}/collective: {tainted_drops} of {len(kept)} pairs follow a server"
                                         " duplicate before collective realigned")
                    continue
                if len(moving) < MIN_PAIRS // 2:
                    data_problems.append(f"{leg}/collective: only {len(moving)} kept pairs with collective moving")
                    continue
            if fix:
                if base_diff:
                    ex = next((c, s) for c, s in kept if c["base"] != s["base"])
                    fails["R1"].append(f"{leg}/{phase}: base differs in {base_diff} of {len(kept)}"
                                       f" (tick {ex[0]['tick']}: client {ex[0]['base']}, server {ex[1]['base']})")
                if in_diff:
                    fails["R1"].append(f"{leg}/{phase}: in= differs in {in_diff} of {len(kept)}")
                if phase == "collective" and coll_diff:
                    ex = next((c, s) for c, s in coll_kept if c["coll"] != s["coll"])
                    fails["R3"].append(f"{leg}/{phase}: coll differs in {coll_diff} of {len(coll_kept)}"
                                       f" (tick {ex[0]['tick']}: client {ex[0]['coll']}, server {ex[1]['coll']})")
            else:
                if phase == "roll" and base_diff < FIX_OFF_MIN * len(kept):
                    fails["R2"].append(f"{leg}/roll: base differs in only {base_diff} of {len(kept)}")
                if phase == "collective" and coll_diff_moving < FIX_OFF_MIN * len(moving):
                    fails["R2"].append(f"{leg}/collective: coll differs in only {coll_diff_moving} of {len(moving)}"
                                       " moving pairs")

        # R4 (reported): the pose and angular velocity at since=2, the state after the first
        # replayed tick, in the still phase (the floor snapshot quantization sets) and the others
        for label, phases in (("still (floor)", ("still",)), ("roll+collective", ("roll", "collective"))):
            rngs = [ranges[(leg, p)][:2] for p in phases if (leg, p) in ranges]
            errs_o, errs_q, errs_w = [], [], []
            for c in seconds:
                if not any(a <= c["tick"] <= b for a, b in rngs):
                    continue
                s = srv.get((c["tick"], c["ent"]))
                if s is None or c["ahead"] > MAX_PREDICTION:
                    continue
                errs_o.append(math.dist(vec(c["org"]), vec(s["org"])))
                errs_q.append(quat_angle(vec(c["quat"]), vec(s["quat"])))
                errs_w.append(math.dist(vec(c["ang"]), vec(s["ang"])))
            if errs_o:
                report.append(f"{leg:10s} R4 since=2, {label} ({len(errs_o)}): origin median {statistics.median(errs_o):.3g}"
                              f" p95 {p95(errs_o):.3g} u; orientation median {statistics.median(errs_q):.3g}"
                              f" p95 {p95(errs_q):.3g} deg; angular velocity median {statistics.median(errs_w):.3g}"
                              f" p95 {p95(errs_w):.3g} rad/s")

    results["data"] = (not data_problems and not lockstep_errors,
                       "; ".join(data_problems + lockstep_errors[:5]) or "every judged leg and phase has its pairs, uf in lockstep")
    results["R1"] = (not fails["R1"], "; ".join(fails["R1"]) or "fix on: base (and in= with advanced controls) equal in every kept pair")
    results["R2"] = (not fails["R2"], "; ".join(fails["R2"]) or "fix off: the bug shows in the roll and collective phases")
    results["R3"] = (not fails["R3"], "; ".join(fails["R3"]) or "fix on: coll equal in every kept pair of the collective phase")
    return results, report


def finish(host_lines, pilot_lines, failures):
    results, report = evaluate(host_lines, pilot_lines)
    for line in report:
        print(f"  {line}")
    print(f"{'check':6s} {'result':8s} detail")
    for name, (ok, detail) in results.items():
        print(f"{name:6s} {'pass' if ok else 'FAIL':8s} {detail}")
    failures = failures + [f"{name}: {detail}" for name, (ok, detail) in results.items() if not ok]
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("PASS: the fix restores the snapshot tick's air-control state; without it the test sees the bug")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--port", type=int, default=29900)
    ap.add_argument("--phase-seconds", type=float, default=9.0)
    ap.add_argument("--log-dir")
    ap.add_argument("--replay")
    args = ap.parse_args(argv)

    if args.replay:
        d = Path(args.replay)
        missing = [str(d / n) for n in ("host.log", "pilot.log") if not (d / n).is_file()]
        if missing:
            print(f"[replay] FAIL: missing {', '.join(missing)} (--replay takes the --log-dir of a run)")
            return 1
        return finish((d / "host.log").read_text(errors="replace").splitlines(),
                      (d / "pilot.log").read_text(errors="replace").splitlines(), [])

    try:
        vk = find_client(args.client_binary)
        data = h.resolve_data_path()
    except h.SkipTest as e:
        print(f"[SKIP] {e}")
        return 77
    h.install_signal_handlers()
    log_dir = Path(args.log_dir) if args.log_dir else None
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)

    def log(name):
        return (log_dir / f"{name}.log") if log_dir else None

    # both players on GDF (team balancing would refuse the second one); spawn, enterVehicle's
    # target and g_logAirControl need cheats
    game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0", "si_teamForceBalance": "0",
            "net_allowCheats": "1"}
    view = {"r_vkValidation": "0", "com_maxFPS": "60"}
    host = VulkanClient(vk, data, args.port, map_name="valley", log_path=log("host"),
                        cvars={**game, "ui_name": "Host", **view})
    host.name, host.slot = "Host", None
    # in_yawSpeed 30 with in_angleSpeedKey 1: +left turns about 1 degree a tick, under the
    # rotor's 2-degree clamp on the cyclic delta
    pilot = VulkanClient(vk, data, args.port + 2, connect_to=f"127.0.0.1:{args.port}", log_path=log("pilot"),
                         cvars={"si_pure": "0", "ui_name": "Pilot", "net_clientShowSnapshots": "1",
                                "ui_advancedFlightControls": "1", "in_yawSpeed": "30", "in_angleSpeedKey": "1", **view})
    pilot.name, pilot.slot = "Pilot", None
    procs = [("pilot", pilot), ("host", host)]
    failures = []
    secs = args.phase_seconds

    def both(text):
        host.send(f"echo {text}")
        pilot.send(f"echo {text}")

    def server_rows(mark):
        return [r for r in parse([ln.text for ln in host.snapshot(mark)]) if r["side"] == "srv"]

    def fly_still():
        pilot.send("clientMove 127 0 0")
        time.sleep(secs)

    def fly_roll():
        # centred, so the bank averages out: half a second left, then whole seconds
        pilot.send("clientMove 127 0 0")
        steps = [("+left", "-left", 0.5)]
        side = "right"
        left = secs - 1.0
        while left > 0:
            steps.append((f"+{side}", f"-{side}", 1.0))
            side = "left" if side == "right" else "right"
            left -= 1.0
        steps.append((f"+{side}", f"-{side}", 0.5))
        for on, off, seconds in steps:
            pilot.send(on)
            time.sleep(seconds)
            pilot.send(off)

    def fly_collective():
        cycles = max(1, math.ceil(secs / 2.5))
        for _ in range(cycles):
            pilot.send("clientMove -30 0 0")
            time.sleep(1.0)
            pilot.send("clientMove 30 0 0")
            time.sleep(1.5)
        pilot.send("clientMove 127 0 0")

    def settle_duplicates():
        start = time.monotonic()
        while time.monotonic() - start < 15.0:
            mark = host.mark()
            time.sleep(2.0)
            rows = server_rows(mark)
            if rows and not any(r["dup"] for r in rows):
                return
        raise Failed(f"the server still duplicated the pilot's commands 15 s after net_forceLatency {LATENCY}"
                     " (inconclusive)")

    try:
        host.start()
        if not host.wait_for_text(READY, 180.0) or not host.wait_for_text("Map: maps/valley", 180.0):
            raise Failed("the listen server never loaded the map")
        host.sync(60.0)
        host.slot = 0
        spawn(host, host, "gdf", timeout=60.0)
        connect(host, pilot, "listen")
        spawn(host, pilot, "gdf")
        slot = pilot.slot

        for proc in (host, pilot):
            mark = proc.mark()
            proc.send("set g_logAirControl 1")
            proc.sync()
            refused = [ln.text for ln in proc.snapshot(mark) if "cannot be changed" in ln.text]
            if refused:
                raise Failed(f"the {proc.name.lower()} refused g_logAirControl: {refused[0]} (are cheats allowed?)")

        mark = host.mark()
        host.send(ANANSI_SPAWN)
        host.send(f"enterVehicle {slot} vehicle_anansi")
        if not host.wait_for(lambda s: re.search(rf"ENTER_VEHICLE: client {slot} entered \S+ \(seat 0\)", s), 10.0, mark):
            raise Failed("the pilot never entered the anansi")
        pilot.send("clientMove 127 0 0")
        start = time.monotonic()
        while True:
            time.sleep(1.0)
            rows = server_rows(mark)
            if rows:
                zs = [vec(r["org"])[2] for r in rows]
                if zs[-1] > min(zs) + CLIMB:
                    print(f"[takeoff] the anansi fell to z {min(zs):.0f} and climbed {zs[-1] - min(zs):.0f} u"
                          f" in {time.monotonic() - start:.1f} s")
                    break
            if time.monotonic() - start > 30.0:
                raise Failed(f"the anansi did not climb {CLIMB:.0f} u in 30 s"
                             f" (server AIR_CTL lines: {len(rows)})")

        latency = 0
        for leg, leg_latency, fix, adv, _ in LEGS:
            if leg_latency != latency:
                host.send(f"set net_forceLatency {leg_latency}")
                pilot.send(f"set net_forceLatency {leg_latency}")
                latency = leg_latency
                settle_duplicates()
            pilot.send(f"set g_airControlHistory {fix}")
            pilot.send(f"set ui_advancedFlightControls {adv}")
            time.sleep(1.0)
            for phase, fly in zip(PHASES, (fly_still, fly_roll, fly_collective)):
                mark = host.mark()
                both(f"AR_BEGIN {leg} {phase}")
                fly()
                both(f"AR_END {leg} {phase}")
                if not host.wait_for(lambda s: s.strip() == f"AR_END {leg} {phase}", 10.0, mark):
                    raise Failed(f"the host never echoed the end of {leg}/{phase}")
            print(f"[{leg}] flown")
        host.send("set net_forceLatency 0")
        pilot.send("set net_forceLatency 0")
        pilot.send("clientMove 0 0 0")
        for proc in (host, pilot):
            proc.send("set g_logAirControl 0")
        host.sync()
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs:
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs:
            run_failed = (bool(failures)
                          or any(c not in (0, None) for c in codes.values()))
            for f in h.scan_failures((ln.text for ln in proc.snapshot()), run_failed=run_failed):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            for i, ln in enumerate(proc.snapshot()):
                if any(m in ln.text for m in BAD_MARKERS):
                    failures.append(f"{name} log line {i + 1}: {ln.text}")
        host_lines = [ln.text for ln in host.snapshot()]
        pilot_lines = [ln.text for ln in pilot.snapshot()]
        for _, proc in procs:
            proc.cleanup()
    print(f"quit exit codes: {codes}")
    if not failures and any(c != 0 for c in codes.values()):
        failures.append(f"quit exit codes: {codes}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return finish(host_lines, pilot_lines, failures)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
