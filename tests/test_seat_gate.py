#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vehicle seat gate: an excluded seat never unlocks, and the gate changes nothing.

M5 step 13 (docs/m5-plan.md; docs/vehicle-responsiveness.md stage 2;
docs/decisions/0003-vehicle-seat-gate.md). The game's seat predicate
(idPlayer::IsSeatPresentationAllowed) is latched next to canUnlockFrames at each
tick entry and re-checked at draw time; nothing reads it yet (step 14 will). With
g_simHash the game prints a SEAT_GATE line each tick:

  SEAT_GATE: tick cu latch pred draw kind ent pos mode key listed recipe

cu and latch are canUnlockFrames and the seat latch taken at the tick's entry; pred
the predicate at the tick's end; draw the draw-time re-check at the tick's end;
listed and recipe are given for any seat, whatever the other checks say.

The game's seat tour (g_determinismTest 3 on valley; openETQW-game Game_local.cpp,
RunSeatGateSchedule): enter the badger from on foot at tick 21 (the tour's only
entry from on foot), cycle its camera at 31 and 41, swap seat at 51, cycle at 61;
exit and enter the icarus (a jetpack) in the same tick at 71, cycle at 81; exit and
enter the titan at 91, swap seat at 101, cycle at 111; listSeatViews at 121; exit at
125; quit from 130. It runs four times at com_maxFPS 120:

  A  g_unlock_vehicleSeats default                   the baseline; learns the keys
  B  g_unlock_vehicleSeats *                         everything listed, no recipe
  C  items built from A's keys, two of them malformed  key matching
     and three aimed at idStr::Filter's defects
  D  * and g_seatGateTestRecipe "T,U,icarus"          the positive control

T is the view class of the badger's driver seat in its first view, U that of the
seat the tick-51 swap lands in (learned from run A). A test recipe opens the gate
and is never presented; 'icarus' names the jetpack's view class, which no recipe can
cover, so the jetpack must stay shut.

Checks:
1. Format: one SEAT_GATE and one SIM_VIEW per SIM_HASH tick, on the same ticks; exit 0; no failure
   markers; the listSeatViews block present and well-formed.
2. The tour happened: the badger in two seats and in two views of each seat that
   has more than one; the icarus as a jetpack; the titan in two seats; seated on
   every tick from 21 to 124 (the moves between vehicles leave no on-foot tick).
3. The guard: while the game has no built-in recipe (step 13; the listing reports
   recipes=0), latch, pred and draw are 0 on every tick of runs A-C. In every run a
   jetpack or non-transport seat has pred, draw and recipe 0, and no latch is taken
   for one (latch[n] belongs to the seat tick n-1 ended in). canUnlockFrames
   is 0 on every tick after one that ended with a proxy (retail's rule is kept).
4. Key matching: listed agrees on every seated tick and every listed seat view with
   a reference matcher (per ':' field, whole field, ignoring case, * and ?; '*'
   alone lists everything; 'default' is the built-in list, DEFAULT_LIST; whitespace
   removed; malformed items ignored). Run A lists nothing and run B everything; run
   C reports bad=2, each of its well-formed items lists what it should, and nothing
   else is listed: its items include a field that is a strict prefix of a seat's, a
   '?' past the end of a class and a '*' that has to backtrack, the three ways
   idStr::Filter goes wrong.
5. Run D plumbing: recipe exactly where the seat is a transport's in class T or U;
   latch[n] == pred[n-1]; draw[n] == latch[n] and the seat (ent, pos, mode) is
   unchanged since tick n-1 and pred[n]. The run must show the latch set, a camera
   cycle clearing pred under a set latch, and a seat swap into a seat that also
   has a recipe (latch and pred both 1, draw 0: the identity re-check).
6. Nothing changes: SIM_HASH, VEH_TRACE, cu and SIM_VIEW are identical tick by
   tick in all four runs. (Until M5 step 6 the render frames of tick 21, the entry
   from on foot, ran the vehicle proxy branch of ClientUpdateView, whose weapon
   presentation the vehicle ticks logged, and the test allowed that wlog run as
   ENTRY_LEAK; step 6 removed both.) All four runs execute the
   same new code (the latch, SEAT_GATE, listSeatViews at 121) and differ only in
   the two CVars, so this shows that the gate's state changes nothing; that the
   code itself changes nothing against main rests on vehicle-physics-golden (its
   golden predates step 13) and high-refresh-determinism.
7. listSeatViews: the badger, the titan and the icarus (kind=jetpack) are listed;
   recipe is 0 except where run D's test recipe applies; the key of every seat the
   tour sat in equals the listing's key for that vehicle, seat and view.

It also prints each listed seat's view class, flags, eye joint and the IK or weapon
aimer that drives the eye's parents (docs/vehicle-responsiveness.md 5.2).

The local gate for game changes to vehicles/SeatGate.* or the seat predicate
(openETQW-game CONTRIBUTING.md).

Exit codes: 0 pass, 1 fail, 77 skip (no retail data or client binary).
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

REPO_ROOT = h.REPO_ROOT
CAP = 120
RUN_TIMEOUT_S = 150.0
ENTRY_TICK = 21         # the tour's only entry from on foot
SWAP_TICK = 51          # the badger seat swap: U is the class of the seat it lands in
SEATED_TICKS = range(21, 125)
# the game's built-in default list (seatDefaultList in openETQW-game vehicles/SeatGate.cpp):
# empty in step 13; keep it in step as seats join it
DEFAULT_LIST: tuple[str, ...] = ()

SIM_HASH_RE = re.compile(r"^SIM_HASH: tick=(\d+) ")
SIM_VIEW_RE = re.compile(r"^SIM_VIEW: tick=(\d+) (.*)$")
VEH_TRACE_RE = re.compile(r"^VEH_TRACE: (tick=\d+ .*)$")
SEAT_GATE_RE = re.compile(
    r"^SEAT_GATE: tick=(?P<tick>\d+) cu=(?P<cu>[01]) latch=(?P<latch>[01]) pred=(?P<pred>[01]) draw=(?P<draw>[01])"
    r" kind=(?P<kind>none|other|transport|jetpack) ent=(?P<ent>-?\d+) pos=(?P<pos>-?\d+) mode=(?P<mode>-?\d+)"
    r" key=(?P<key>\S+) listed=(?P<listed>[01]) recipe=(?P<recipe>[01])$")
BEGIN_RE = re.compile(r'^SEAT_VIEWS: begin filter=(\S+) list="(.*)" items=(\d+) bad=(\d+) recipes=(\d+)$')
END_RE = re.compile(r"^SEAT_VIEWS: end vehicles=(\d+) seats=(\d+) views=(\d+)$")
VEHICLE_RE = re.compile(r"^SEAT_VEHICLE: ent=(\d+) class=(\S+) script=(\S+) kind=(\S+) positions=(\d+)$")
IK_RE = re.compile(r'^SEAT_IK: ent=(\d+) pos=(-?\d+|-) name="([^"]*)" type=(\S+) joints=(\S+)$')
_CLAMP = r"[01],[01],[^ ,]+,[^ ,]+,[^ ,]+,[^ ,]+,[^ ,]+"
VIEW_RE = re.compile(
    r'^SEAT_VIEW: ent=(?P<ent>\d+) script=(?P<script>\S+) pos=(?P<pos>\d+) name="(?P<name>[^"]*)"'
    r" mode=(?P<mode>\d+)/(?P<modes>\d+) type=(?P<type>\S+) decl=(?P<decl>\S+) key=(?P<key>\S+)"
    r" listed=(?P<listed>[01]) recipe=(?P<recipe>[01]) third=(?P<third>[01]) tophat=(?P<tophat>[01]) center=(?P<center>[01])"
    rf" cy=(?P<cy>{_CLAMP}) cp=(?P<cp>{_CLAMP}) cdy=(?P<cdy>{_CLAMP}) cdp=(?P<cdp>{_CLAMP})"
    r" eye=(?P<eye>\S+) pivot=(?P<pivot>\S+) ik=(?P<ik>\S+)$")
FIELD_RE = re.compile(r"(\w+)=(\([^)]*\)|\S+)")
ITEM_FIELD_RE = re.compile(r"[A-Za-z0-9_*?]+")
FLAGS = ("cu", "latch", "pred", "draw", "listed", "recipe")


class Run:
    """One tour's output, parsed."""

    def __init__(self, name: str, seats: str, recipe: str, lines: list[str]):
        self.name, self.seats, self.recipe = name, seats, recipe
        self.lines = lines
        self.hash_ticks = [int(m.group(1)) for ln in lines if (m := SIM_HASH_RE.match(ln))]
        self.hashes = [ln for ln in lines if SIM_HASH_RE.match(ln)]
        self.veh = [m.group(1) for ln in lines if (m := VEH_TRACE_RE.match(ln))]
        self.views_by_tick = {int(m.group(1)): m.group(2) for ln in lines if (m := SIM_VIEW_RE.match(ln))}
        self.gate_raw = [ln for ln in lines if ln.startswith("SEAT_GATE:")]
        self.gate = []
        self.malformed = []
        for ln in self.gate_raw:
            m = SEAT_GATE_RE.match(ln)
            if not m:
                self.malformed.append(ln)
                continue
            g = m.groupdict()
            for f in ("tick", "ent", "pos", "mode"):
                g[f] = int(g[f])
            for f in FLAGS:
                g[f] = g[f] == "1"
            g["script"] = g["key"].split(":")[0] if g["key"] != "-" else "-"
            g["type"] = g["key"].split(":")[2] if g["key"] != "-" else "-"
            self.gate.append(g)
        self.begin = [m for ln in lines if (m := BEGIN_RE.match(ln))]
        self.end = [m for ln in lines if (m := END_RE.match(ln))]
        self.vehicles = {int(m.group(1)): {"class": m.group(2), "script": m.group(3), "kind": m.group(4),
                                           "positions": int(m.group(5))}
                         for ln in lines if (m := VEHICLE_RE.match(ln))}
        self.ik = [m.groups() for ln in lines if (m := IK_RE.match(ln))]
        self.views = []
        for ln in lines:
            if not ln.startswith("SEAT_VIEW:"):
                continue
            m = VIEW_RE.match(ln)
            if not m or len(ln) >= 1023:
                self.malformed.append(ln)
                continue
            v = m.groupdict()
            for f in ("ent", "pos", "mode", "modes"):
                v[f] = int(v[f])
            for f in ("listed", "recipe", "third", "tophat", "center"):
                v[f] = v[f] == "1"
            self.views.append(v)

    def view_for(self, ent: int, pos: int, mode: int) -> dict | None:
        return next((v for v in self.views if (v["ent"], v["pos"], v["mode"]) == (ent, pos, mode)), None)


def run_tour(client: Path, data: Path, savepath: Path, name: str, seats: str, recipe: str) -> Run:
    args = [
        str(client),
        "+set", "fs_basepath", str(data),
        "+set", "fs_savepath", str(savepath / name),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "r_swapInterval", "0",
        "+set", "com_maxFPS", str(CAP),
        "+set", "g_determinismTest", "3",
        "+set", "g_simHash", "1",
        "+set", "g_unlock_vehicleSeats", seats,
        "+set", "g_seatGateTestRecipe", recipe,
        "+devmap", "valley",
        "+clientTeam", "gdf",
        "+clientClass", "soldier",
        "+closeLimbo",
    ]
    print(f'--- run {name}: g_unlock_vehicleSeats "{seats}" g_seatGateTestRecipe "{recipe}" ---', flush=True)
    try:
        done = subprocess.run(args, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace", timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"run {name}: the tour did not quit within {RUN_TIMEOUT_S:.0f} s") from exc
    lines = [ln.strip() for ln in done.stdout.splitlines()]
    failures = h.scan_failures(lines)
    if done.returncode != 0 or failures:
        tail = "\n".join(lines[-15:])
        raise AssertionError(f"run {name}: exit {done.returncode}, failure markers {failures[:3]}\n{tail}")
    run = Run(name, seats, recipe, lines)
    print(f"  {len(run.gate)} SEAT_GATE, {len(run.hashes)} SIM_HASH, {len(run.veh)} VEH_TRACE,"
          f" {len(run.views)} SEAT_VIEW lines", flush=True)
    return run


def item_fields(item: str) -> list[str] | None:
    """The three globs of a well-formed script:position:viewType item, else None."""
    fields = item.split(":")
    if len(fields) != 3 or not all(ITEM_FIELD_RE.fullmatch(f) for f in fields):
        return None
    return fields


def reference_listed(value: str, key: str) -> bool:
    """g_unlock_vehicleSeats as the game should read it (vehicles/SeatGate.cpp)."""
    items = [i for i in re.sub(r"\s", "", value).split(",") if i]
    expanded = []
    for item in items:
        if item.lower() == "default":
            expanded += DEFAULT_LIST
        else:
            expanded.append(item)
    for item in expanded:
        if item == "*":
            return True
        fields = item_fields(item)
        if fields is None:
            continue                # malformed: ignored
        if all(fnmatch.fnmatchcase(k.lower(), f.lower()) for f, k in zip(fields, key.split(":"))):
            return True
    return False


def reference_counts(value: str) -> tuple[int, int]:
    """(well-formed items, malformed items) of a list value."""
    items = [i for i in re.sub(r"\s", "", value).split(",") if i]
    good = [i for i in items if i.lower() == "default" or i == "*" or item_fields(i) is not None]
    return len(good), len(items) - len(good)


def seated(g: dict) -> bool:
    return g["kind"] in ("transport", "jetpack") and g["key"] != "-"


def check_format(run: Run) -> list[str]:
    problems = [f"run {run.name}: malformed line: {ln[:200]}" for ln in run.malformed[:5]]
    ticks = [g["tick"] for g in run.gate]
    if ticks != run.hash_ticks:
        problems.append(f"run {run.name}: SEAT_GATE ticks differ from SIM_HASH ticks"
                        f" ({len(ticks)} against {len(run.hash_ticks)} lines)")
    if sorted(run.views_by_tick) != run.hash_ticks:
        # check 6 compares SIM_VIEW tick by tick: without the lines it would compare nothing
        problems.append(f"run {run.name}: SIM_VIEW ticks differ from SIM_HASH ticks"
                        f" ({len(run.views_by_tick)} against {len(run.hash_ticks)} lines)")
    if len(run.hash_ticks) < 125:
        problems.append(f"run {run.name}: only {len(run.hash_ticks)} ticks; the tour runs to tick 130")
    if len(run.begin) != 1 or len(run.end) != 1:
        problems.append(f"run {run.name}: {len(run.begin)} listSeatViews begin and {len(run.end)} end markers, expected 1 each")
    elif run.end[0].group(3) != str(len(run.views)) or run.end[0].group(1) != str(len(run.vehicles)):
        problems.append(f"run {run.name}: the listing's end marker counts {run.end[0].group(0)} do not match"
                        f" {len(run.vehicles)} SEAT_VEHICLE and {len(run.views)} SEAT_VIEW lines")
    return problems


def check_tour(run: Run) -> list[str]:
    problems = []
    by_tick = {g["tick"]: g for g in run.gate}
    badger = [g for g in run.gate if g["kind"] == "transport" and g["script"] == "badger"]
    badger_positions = {g["pos"] for g in badger}
    if len(badger_positions) < 2:
        problems.append(f"run {run.name}: the badger in seats {sorted(badger_positions)}, expected two")
    for pos in sorted(badger_positions):
        ent = next(g["ent"] for g in badger if g["pos"] == pos)
        view = run.view_for(ent, pos, 0)
        modes = {g["mode"] for g in badger if g["pos"] == pos}
        if view is not None and view["modes"] > 1 and len(modes) < 2:
            problems.append(f"run {run.name}: badger seat {pos} has {view['modes']} views, the tour saw modes {sorted(modes)}")
    if not any(g["kind"] == "jetpack" and g["script"] == "icarus" for g in run.gate):
        problems.append(f"run {run.name}: no tick in the icarus as a jetpack")
    titan = {g["pos"] for g in run.gate if g["kind"] == "transport" and g["script"] == "titan"}
    if len(titan) < 2:
        problems.append(f"run {run.name}: the titan in seats {sorted(titan)}, expected two")
    on_foot = [t for t in SEATED_TICKS if t in by_tick and not seated(by_tick[t])]
    if on_foot:
        problems.append(f"run {run.name}: not seated on ticks {on_foot[:10]} (an exit or entry failed)")
    return problems


def check_guard(run: Run, gate_closed: bool) -> list[str]:
    problems = []
    for prev, g in zip(run.gate, run.gate[1:]):
        if prev["kind"] != "none" and g["cu"]:
            problems.append(f"run {run.name}: tick {g['tick']}: canUnlockFrames set after a tick that ended with a proxy")
    for i, g in enumerate(run.gate):
        if gate_closed and (g["latch"] or g["pred"] or g["draw"] or g["recipe"]):
            problems.append(f"run {run.name}: tick {g['tick']}: the gate opened with no recipe: {run.gate_raw[i]}")
        # latch is taken at the tick's entry, for the seat the previous tick ended in; pred,
        # draw and recipe are for the seat this tick ends in (the kind printed): a tick that
        # moves from a transport to the jetpack may carry the transport's latch
        latched_kind = run.gate[i - 1]["kind"] if i else "none"
        if latched_kind in ("jetpack", "other") and g["latch"]:
            problems.append(f"run {run.name}: tick {g['tick']}: latched for a {latched_kind} seat: {run.gate_raw[i]}")
        if g["kind"] in ("jetpack", "other") and (g["pred"] or g["draw"] or g["recipe"]):
            problems.append(f"run {run.name}: tick {g['tick']}: a {g['kind']} proxy opened the gate: {run.gate_raw[i]}")
    return problems[:20]


def check_listing(run: Run, recipe_types: set[str]) -> list[str]:
    problems = []
    scripts = {v["script"]: v for v in run.vehicles.values()}
    for script in ("badger", "titan", "icarus"):
        if script not in scripts:
            problems.append(f"run {run.name}: listSeatViews did not list the {script}")
    if "icarus" in scripts and scripts["icarus"]["kind"] != "jetpack":
        problems.append(f"run {run.name}: the icarus is listed as {scripts['icarus']['kind']}, not jetpack")
    if run.begin:
        m = run.begin[0]
        items, bad = reference_counts(run.seats)
        recipes = len([t for t in re.sub(r"\s", "", run.recipe).split(",") if t])
        want = (run.seats, str(items), str(bad), str(recipes))
        if (m.group(2), m.group(3), m.group(4), m.group(5)) != want:
            problems.append(f"run {run.name}: listing header {m.group(0)}; expected list, items, bad, recipes {want}")
            if m.group(5) != str(recipes):
                problems.append(f"run {run.name}: recipes={m.group(5)}: if built-in recipes exist now, this test's"
                                " guard expectations (runs A-C shut) must change with them")
    for v in run.views:
        kind = run.vehicles.get(v["ent"], {}).get("kind")
        want_recipe = kind == "transport" and v["type"].lower() in recipe_types
        if v["recipe"] != want_recipe:
            problems.append(f"run {run.name}: listing {v['key']}: recipe={int(v['recipe'])}, expected {int(want_recipe)}")
        if v["listed"] != reference_listed(run.seats, v["key"]):
            problems.append(f"run {run.name}: listing {v['key']}: listed={int(v['listed'])}, the reference says"
                            f" {int(not v['listed'])}")
    for g in run.gate:
        if not seated(g):
            continue
        v = run.view_for(g["ent"], g["pos"], g["mode"])
        if v is None:
            problems.append(f"run {run.name}: tick {g['tick']}: no listed view for ent {g['ent']} seat {g['pos']} mode {g['mode']}")
        elif v["key"] != g["key"]:
            problems.append(f"run {run.name}: tick {g['tick']}: key {g['key']} is {v['key']} in the listing")
    return problems[:20]


def check_listed(run: Run) -> list[str]:
    problems = []
    for g in run.gate:
        want = seated(g) and reference_listed(run.seats, g["key"])
        if g["listed"] != want:
            problems.append(f"run {run.name}: tick {g['tick']}: {g['key']} listed={int(g['listed'])}, expected {int(want)}")
    return problems[:20]


def check_plumbing(run: Run, recipe_types: set[str]) -> list[str]:
    """Run D. The latch is taken at the start of RunFrame for tick n and pred at the end
    of tick n-1 (EmitSeatGate, after VEH_TRACE); nothing between them changes the seat
    or the predicate's inputs in a single-player replay (render frames only present),
    so latch[n] == pred[n-1] exactly. draw re-checks at the end of tick n against the
    seat latched at its start, which is the seat printed at the end of tick n-1."""
    problems = []
    for g in run.gate:
        want = g["kind"] == "transport" and g["type"].lower() in recipe_types
        if g["recipe"] != want:
            problems.append(f"run {run.name}: tick {g['tick']}: recipe={int(g['recipe'])}, expected {int(want)} ({g['key']})")
    latched = cycled = swapped = 0
    for prev, g in zip(run.gate, run.gate[1:]):
        if g["latch"] != prev["pred"]:
            problems.append(f"run {run.name}: tick {g['tick']}: latch={int(g['latch'])} but pred was {int(prev['pred'])} at tick {prev['tick']}")
        same = (g["ent"], g["pos"], g["mode"]) == (prev["ent"], prev["pos"], prev["mode"])
        want = g["latch"] and same and g["pred"]
        if g["draw"] != want:
            problems.append(f"run {run.name}: tick {g['tick']}: draw={int(g['draw'])}, expected {int(want)}")
        latched += g["latch"]
        cycled += g["latch"] and not g["pred"]
        swapped += g["latch"] and g["pred"] and not same
    print(f"  run {run.name}: latch set on {latched} ticks; pred cleared under the latch on {cycled};"
          f" the seat changed under a latch that still held on {swapped}")
    if latched == 0:
        problems.append(f"run {run.name}: the test recipe never set the latch (the positive control is empty)")
    if cycled == 0:
        problems.append(f"run {run.name}: no tick cleared pred under a set latch (the camera cycle)")
    if swapped == 0:
        problems.append(f"run {run.name}: no seat change under a latch that still held (the identity re-check is untested)")
    return problems[:20]


def view_fields(body: str) -> dict[str, str]:
    return dict(FIELD_RE.findall(body))


def check_unchanged(base: Run, runs: list[Run]) -> list[str]:
    problems = []
    for run in runs:
        pair = f"runs {base.name} and {run.name}"
        if run.hashes != base.hashes:
            problems.append(f"{pair}: SIM_HASH differs: " + first_difference(base.hashes, run.hashes))
        if run.veh != base.veh:
            problems.append(f"{pair}: VEH_TRACE differs: " + first_difference(base.veh, run.veh))
        if [g["cu"] for g in run.gate] != [g["cu"] for g in base.gate]:
            problems.append(f"{pair}: canUnlockFrames differs")
        if sorted(run.views_by_tick) != sorted(base.views_by_tick):
            problems.append(f"{pair}: SIM_VIEW ticks differ")
            continue
        view_diffs = 0
        for tick in sorted(base.views_by_tick):
            a, b = view_fields(base.views_by_tick[tick]), view_fields(run.views_by_tick[tick])
            differ = sorted(f for f in set(a) | set(b) if a.get(f) != b.get(f))
            if not differ:
                continue
            view_diffs += 1
            problems.append(f"{pair}: SIM_VIEW tick {tick}: {', '.join(differ)} differ"
                            f"\n    {base.name}: {base.views_by_tick[tick]}\n    {run.name}: {run.views_by_tick[tick]}")
        print(f"  {pair}: SIM_HASH {'identical' if run.hashes == base.hashes else 'DIFFERENT'},"
              f" VEH_TRACE {'identical' if run.veh == base.veh else 'DIFFERENT'},"
              f" SIM_VIEW {'identical' if not view_diffs else f'DIFFERENT on {view_diffs} ticks'}", flush=True)
    return problems[:20]


def first_difference(a: list[str], b: list[str]) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"line {i + 1}:\n    {x}\n    {y}"
    return f"{len(a)} and {len(b)} lines"


def glob_of(name: str) -> str:
    """A '?' glob of a key field: every '_' becomes '?', or else the first character."""
    return name.replace("_", "?") if "_" in name else "?" + name[1:]


def build_list_c(a: Run) -> tuple[str, dict[str, set[str]]]:
    """Run C's list, from run A's keys, and the keys each well-formed item must list."""
    first = next(g for g in a.gate if g["tick"] == ENTRY_TICK)
    swap = next(g for g in a.gate if g["tick"] == SWAP_TICK)
    if first["script"] != "badger" or swap["script"] != "badger":
        raise AssertionError(f"run A: the tour is not in the badger at ticks {ENTRY_TICK} and {SWAP_TICK}")
    exact = first["key"]
    second = swap["key"].split(":")[1]
    glob = f"BADGER:{glob_of(second)}:*"         # upper case: keys compare ignoring case
    # a badger seat no well-formed item covers: a prefix reading of the malformed
    # 'badger:<seat>' would list it
    other = next((v["key"].split(":")[1] for v in a.views if v["script"] == "badger" and v["pos"] not in (first["pos"], swap["pos"])), None)
    if other is None:
        raise AssertionError("run A: the badger has no third seat to build a malformed item from")
    # three items for the defects idStr::Filter has (decision record 0003), each of which must
    # list nothing that the other items do not: a field that is a strict prefix of the seat's
    # (a prefix matcher lists it), a '?' past the end of the class (a matcher that reads past
    # the terminator can list it), and a '*' whose suffix first occurs before the end of the
    # field (a matcher that does not backtrack misses it; this one must list)
    other_pos = next(v["pos"] for v in a.views if v["script"] == "badger" and v["key"].split(":")[1] == other)
    classes = [v["type"] for v in a.views if v["script"] == "badger" and v["pos"] == other_pos]
    suffix = next((other[-k:] for k in (2, 3, 4) if other.find(other[-k:]) < len(other) - k), None)
    if suffix is None:
        raise AssertionError(f"run A: badger seat {other} has no suffix that also occurs earlier in it")
    prefix_item = f"badger:{other[:-1]}:*"
    past_end_item = f"badger:{other}:{classes[0]}?"
    backtrack_item = f"badger:*{suffix}:{classes[-1]}"
    value = (f"{exact}, titan:*:*,icarus:*:*,{glob},badger,badger:{other},"
             f"{prefix_item},{past_end_item},{backtrack_item}")
    keys = {v["key"] for v in a.views}
    expect = {
        exact: {exact},
        "titan:*:*": {k for k in keys if k.startswith("titan:")},
        "icarus:*:*": {k for k in keys if k.startswith("icarus:")},
        glob: {k for k in keys if k.lower().startswith("badger:" + second.lower() + ":")},
        backtrack_item: {k for k in keys if k.startswith("badger:") and reference_listed(backtrack_item, k)},
    }
    return value, expect


def check_list_c(c: Run, expect: dict[str, set[str]]) -> list[str]:
    problems = []
    listed = {v["key"] for v in c.views if v["listed"]}
    for item, keys in expect.items():
        if not keys:
            problems.append(f"run C: item {item} should list something; run A's listing has no such key")
        missing = keys - listed
        if missing:
            problems.append(f"run C: item {item} did not list {sorted(missing)}")
    extra = listed - set().union(*expect.values())
    if extra:
        problems.append(f"run C: listed beyond its items: {sorted(extra)}")
    return problems


def print_seat_table(run: Run) -> None:
    print("seats (run A's listing; eye: the eye joint the damped views follow, ? if the model lacks it;"
          " ik: the first of the eye's parents that an IK system or weapon aimer named in the decl writes):")
    print(f"  {'script':10s} {'pos':>3s} {'position':34s} {'mode':>4s} {'class':14s} {'third':>5s} {'tophat':>6s}"
          f" {'eye':26s} ik")
    for v in sorted(run.views, key=lambda v: (v["script"], v["ent"], v["pos"], v["mode"])):
        print(f"  {v['script']:10s} {v['pos']:3d} {v['name']:34s} {v['mode']:2d}/{v['modes']:<1d} {v['type']:14s}"
              f" {int(v['third']):5d} {int(v['tophat']):6d} {v['eye']:26s} {v['ik']}")


def main() -> int:
    data = h.env_data_path()
    client = h.build_binary("client")
    h.skip_unless_available(data, client)
    savepath = h.private_savepath("etqw-seat-gate-")

    failures: list[str] = []
    a = run_tour(client, data, savepath, "A", "default", "")
    failures += check_format(a) + check_tour(a)
    if failures:
        # the other runs are built from A's keys
        return report(failures)
    list_c, expect_c = build_list_c(a)
    t = next(g for g in a.gate if g["tick"] == ENTRY_TICK)["type"]
    u = next(g for g in a.gate if g["tick"] == SWAP_TICK)["type"]
    recipe_d = f"{t},{u},icarus"
    b = run_tour(client, data, savepath, "B", "*", "")
    c = run_tour(client, data, savepath, "C", list_c, "")
    d = run_tour(client, data, savepath, "D", "*", recipe_d)
    runs = (a, b, c, d)
    recipe_types = {x.lower() for x in (t, u, "icarus")}

    for run in runs[1:]:
        failures += check_format(run) + check_tour(run)
    for run in runs:
        failures += check_guard(run, gate_closed=run is not d)
        failures += check_listed(run)
        failures += check_listing(run, recipe_types if run is d else set())
    failures += check_list_c(c, expect_c)
    failures += check_plumbing(d, recipe_types)
    failures += check_unchanged(a, [b, c, d])

    print_seat_table(a)
    return report(failures)


def report(failures: list[str]) -> int:
    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return h.EXIT_FAIL
    print("PASS: the seat gate stayed shut without a recipe, never opened for the jetpack, matched keys as the"
          " reference does, latched and re-checked as specified, and changed nothing in SIM_HASH, VEH_TRACE,"
          " canUnlockFrames or SIM_VIEW")
    return h.EXIT_PASS


if __name__ == "__main__":
    sys.exit(main())
