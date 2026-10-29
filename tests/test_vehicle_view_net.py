#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vehicle views on the network: do area-of-relevance calls see the tick's view?

M5 step 6, owner decision 4 (docs/m5-plan.md; docs/vehicle-responsiveness.md stage 1).
Between ticks the server's antilag (AntiLag.cpp) and each snapshot it writes
(AORManager.cpp), and a client reading a snapshot, call idPlayer::GetAORView, which
recalculates a seated player's view with CalculateFirstPersonView( false ); a client
moveable's Think calls it inside a tick, in single player too. No replay asserts that
path. Until step 6, OpenETQW's smooth vehicle view interpolated there from the previous
tick's camera, with a fraction a server never set (a listen host has set its own since M5
step 7), and wrote the result back into the tick's view, and the base draw after an exit between ticks rewound to the vehicle
camera. This test came first (decision 4), with that behaviour as known gaps; step 6
restored the SDK views and reset the origin log at the pose log, and the test now
asserts that every AoR call returns the tick's view origin, and in the smooth views its
angles too and that the call changes nothing (the standard view is compared by origin
only: the SDK's non-full damped path recalculates the axis and rewrites
firstPersonViewAxis and lastReturnedAxis, owner decision 7). With
g_logVehicleViews (a cheat) the game prints, for players in a vehicle:

  VIEW_STATE: side at=pre|post tick client ent pos mode class ud new pose= rvo= phys= vpush=
      at=pre before the tick's entity Think, at=post at its end (a server: every client
      in a vehicle, where the tick logs its pose; a network client: its local player,
      after each predicted tick, new=0 for a tick predicted again)
  AOR_VIEW: side tick client ent pos mode class ud new f before= result= after= rent= phys=
      one GetAORView: the view before it, the origin and angles it returned, the view
      after it, the unlock fraction f; also the vehicle's rendered and physics origins

where a view (pose=, before=, after=) is fpo/fpa, the player's first-person view origin
and angles, and tpo/tpa, the seat view's third-person view, all in full precision.

The Vulkan client hosts valley (+spawnServer) and a second Vulkan client joins it as the
remote client (+connect); both join GDF, and the host allows cheats. The remote client
cannot be the headless dedicated binary, as in frame-poses: put in a vehicle, that
client failed to load the vehicle's HUD GUI ("Invalid Term 'player'" in
guis/vehicles/edf_badger) and crashed (SIGSEGV in
sdPlayerProperties::OnActiveViewProxyChanged, from the snapshot's proxy event). The
schedule:
  enter       the host puts the remote client in the badger's driver seat (enterVehicle:
              the 'standard' view class)
  phase A     the remote client drives: forward 1 s, forward and right 0.5 s, reverse
              0.8 s, stop (clientMove); the host turns meanwhile
  cycle       the host cycles the remote client's camera (cycleVehicleCamera: the
              badger driver's 'smooth_locked' view; no valley seat starts in a smooth one),
              after checking that malformed client numbers ('x', '1x') are refused
  phase B     the same drive, steering left
  phase D     the host itself, with g_logFramePoses 1: enterVehicle into the titan (its
              driver's first view is third person), 1 s, then exitVehicle from the host
              console (between ticks), 1 s
The host stays alive and on foot through A and B, and turns (+left) while the remote
client drives: its antilag then calls the remote client's GetAORView every tick. (A host
that stands still sends that client nothing new, so its antilag has no recent branch to
estimate from and makes no call; the snapshots the host writes to the remote client call
it about two ticks in three either way.)

Checks (server lines are the host's, client lines the remote client's; ticks within 2
of a change of seat or view mode are left out):
  N0  server, standard view: the AoR result origin equals before.fpo exactly (a
      control: it shows the comparison can hold)
  N1  server, smooth view: the result equals before.tpo/tpa and after equals before
  N4  server, smooth view: VIEW_STATE pre(n+1) equals post(n) (fpo, fpa, tpo, tpa)
  N2  client, smooth view: as N1
  N3  client, standard view: as N0
  N5  the host, after the console exit: no two consecutive on-foot draws (seat=-) of the
      ticks after the exit differ by more than 64 u in view origin
  Sanity: both view classes seen, with >= 20 server and >= 10 client AoR lines each; the
  vehicle moved >= 100 u in each phase; the server's smooth camera moved on >= 20 judged
  ticks and N4 has >= 20 tick pairs; ud=1 on the client, and on the server when the host
  ran at least 3 frames a tick in phase B (FRAME_TIMING): since M5 step 7 the host's
  render-only frames set unlockedDraw, and such a call shows the checks cover that window;
  a frame writes its snapshots before it presents, so a server call sees ud=1 only on the
  second of two frames in a row that ran no tick, and a slower host reports it instead; the
  enter, cycle and exit messages all printed. For N5, the first on-foot game time drawn is one tick after the
  last seated draw's (else a slow frame ran two ticks undrawn and the rewind, or its
  absence, was never drawn), and the vehicle camera is more than 64 u from the on-foot
  view (the first draw of the next game time), so that a rewind would fail N5. A sanity
  failure fails the test, whatever the checks say.

KNOWN_GAPS is empty since step 6. As in frame-poses, a check the code fails is listed
there with the shape its failure must have; a gap outside its shape fails as "GAP
CHANGED SHAPE", and a known gap that passes fails as "UNEXPECTED PASS". Before step 6
(engine #43) the table held these shapes, and the failing checks' details still count
violations of that shape, so a return of the old code is recognisable:
  prev_tick (N1, N4)  the first AoR call of each tick in which the camera moved returns
      the previous tick's camera: its result origin equals post(n-1).tpo exactly and its
      angles are within 0.001 degrees of post(n-1).tpa (the slerp at fraction 0: f was 0
      on the server then; the shape no longer tests f, since a listen host sets its own
      fraction from M5 step 7); the tick's later calls pass. For N4, pre(n+1) holds that camera in fpo and
      tpo. Every violation has this shape, and for N1 every such tick has one.
  rewind (N5)  every draw of exactly one game time after the exit is at the vehicle
      camera (the last seated draw's view origin, within 0.01 u) and the next draw is
      more than 100 u away: the base draw's rewind to the previous tick's logged origin
      (originlog), which the exit between ticks left at the vehicle camera.
N2 and N3 give the ud and f of each violation, split by where the call ran (from the
line order): between ticks, a snapshot read, or inside a predicted tick, a client
moveable's Think (rvClientMoveable::Think calls SetupClientAoR). Before step 6 the
snapshot reads already passed, but a client moveable's Think ran the smooth
interpolation with a stale fraction in some runs (2 of 11 on the Linux x64 host, and on macOS).

Also reported, not judged: the server's smooth camera from the cycle on (post tpo/tpa),
as each tick's offset from the midpoint of its two neighbours, sampled at a few ticks
after the cycle (about zero along a smooth path; when odd and even ticks follow two
interleaved chains, the gap between them); and,
on each side and by ud, how often an AoR call found the vehicle's rendered origin away
from its physics origin (rent= against phys=). On a client the rendered origin also
carries the prediction-error decay, so this is context for step 6's C4 (the
UpdateModelTransform skip under unlockedDraw), not a measure of it.

--replay <log dir> evaluates the host.log and peer.log a run saved there.

Both Vulkan clients open a window (it must stay visible; on Linux SDL_VIDEO_DRIVER=offscreen
works) and run from the repository root. Exit 77 when ETQW_DATA_PATH is unset or invalid
or the client binary is missing.
"""

import argparse
import math
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
from test_campaign_client_maps import BAD_MARKERS, find_client  # noqa: E402
from test_client_network import VulkanClient  # noqa: E402
from test_frame_poses import FRAME_POSE_RE  # noqa: E402
from test_reconnect_restart import Failed, connect, spawn  # noqa: E402
from test_remote_client_map_change import READY  # noqa: E402

VIEW_RE = re.compile(r"(AOR_VIEW|VIEW_STATE): (.*)$")
FIELD_RE = re.compile(r"(\w+)=(\S+)")
VECTOR_RE = re.compile(r"(\w*)\(([^)]*)\)")
POSE_KEYS = ("fpo", "fpa", "tpo", "tpa")

EXCLUDE_TICKS = 2       # ticks left out on each side of a change of seat or view mode
FRAME_TIMING_RE = re.compile(r"FRAME_TIMING: window_ms=([\d.]+) frames=(\d+) ticks=(\d+)")
MIN_FRAMES_PER_TICK = 3.0   # the server's ud=1 check needs two frames in a row without a tick
ANG_TOL = 1e-3          # degrees: prev_tick's slerp at fraction 0 against the logged axis
MIN_SERVER_LINES = 20   # AoR lines per view class
MIN_CLIENT_LINES = 10
MIN_TRAVEL = 100.0      # units the vehicle must move in each phase
JUMP = 64.0             # N5: the most two consecutive on-foot draws may differ by
REWIND = 100.0          # N5's rewind: the jump off the vehicle camera is larger than this
DRAW_EPS = 0.01         # units: FRAME_POSE prints %.3f
TICK_MS = 33            # game time per tick
MIN_PAIRS = 20          # N4 tick pairs, and ticks on which the server's smooth camera moved

# (forward, right) for each leg of a phase and how long it lasts; B steers the other way
DRIVES = {
    "A": [((127, 0), 1.0), ((127, 127), 0.5), ((-127, 0), 0.8)],
    "B": [((127, 0), 1.0), ((127, -127), 0.5), ((-127, 0), 0.8)],
}

# Checks the current code fails, with the shape the failure must have. Empty since M5
# step 6; until then {"N1": ("prev_tick",), "N4": ("prev_tick",), "N5": ("rewind",)}.
KNOWN_GAPS: dict[str, tuple] = {}
REPORT_ONLY = ()


def parse_view(text):
    """An AOR_VIEW or VIEW_STATE line as a dict: ints, f as a float, each view field
    (pose, before, after, result) as {name: tuple}, each single vector as a tuple."""
    m = VIEW_RE.search(text)
    if not m:
        return None
    rec = {"kind": m.group(1)}
    for key, value in FIELD_RE.findall(m.group(2)):
        vectors = VECTOR_RE.findall(value)
        if not vectors:
            rec[key] = value
        elif len(vectors) == 1 and vectors[0][0] == "":
            rec[key] = tuple(float(x) for x in vectors[0][1].split(","))
        else:
            rec[key] = {name: tuple(float(x) for x in body.split(",")) for name, body in vectors}
    try:
        for key in ("tick", "client", "ent", "pos", "mode", "ud", "new"):
            rec[key] = int(rec[key])
        if "f" in rec:
            rec["f"] = float(rec["f"])
    except (KeyError, ValueError):
        return None
    return rec


def parse_draw(text):
    m = FRAME_POSE_RE.search(text)
    if not m:
        return None
    return {"gt": int(m.group(4)), "ud": int(m.group(6)), "seat": m.group(12),
            "view": [float(v) for v in m.group(10).split(",")]}


def angle_diff(a, b):
    return max(abs((x - y + 180.0) % 360.0 - 180.0) for x, y in zip(a, b))


def midpoint_offsets(poses):
    """For each inner pose of a run of consecutive ticks, how far its origin and angles lie
    from the midpoint of its two neighbours: about zero along a smooth path, and the gap
    between the two chains when odd and even ticks follow different ones."""
    out = []
    for a, b, c in zip(poses, poses[1:], poses[2:]):
        mid = tuple((x + z) / 2.0 for x, z in zip(a["tpo"], c["tpo"]))
        mid_ang = tuple(x + ((z - x + 180.0) % 360.0 - 180.0) / 2.0 for x, z in zip(a["tpa"], c["tpa"]))
        out.append((math.dist(b["tpo"], mid), angle_diff(b["tpa"], mid_ang)))
    return out


def chain_report(post, first, last):
    """Reported: the server's smooth camera (post tpo/tpa) on the ticks from first (the
    cycle into the smooth view) to last, as midpoint offsets at a few ticks after the
    cycle and the last tick it was over 1 u."""
    poses = []
    for t in range(first, last + 1):
        if t not in post or not is_smooth(post[t]):
            break
        poses.append(post[t]["pose"])
    offsets = midpoint_offsets(poses)
    if not offsets:
        return "server smooth camera after the cycle: too few ticks"
    samples = ", ".join(f"+{k + 1} {offsets[k][0]:.1f} u/{offsets[k][1]:.1f} deg" for k in (0, 9, 19, 29, 39, 59, 79)
                        if k < len(offsets))
    over = [k + 1 for k, (d, _) in enumerate(offsets) if d > 1.0]
    tail = (f"; over 1 u until tick +{over[-1]} ({over[-1] * TICK_MS / 1000.0:.1f} s)" if over else "; never over 1 u")
    return (f"server smooth camera after the cycle (tick {first}), each tick's offset from the midpoint of its"
            f" neighbours (two interleaved chains when large): {samples}{tail}; {len(offsets)} ticks")


def render_report(recs, side):
    """Reported: AoR calls at which the vehicle's rendered origin was not its physics origin."""
    parts = []
    for ud in sorted({r["ud"] for r in recs}):
        sub = [r for r in recs if r["ud"] == ud]
        off = [math.dist(r["rent"], r["phys"]) for r in sub if r["rent"] != r["phys"]]
        parts.append(f"ud={ud}: {len(off)} of {len(sub)}" + (f" (up to {max(off):.3f} u)" if off else ""))
    return f"{side} AoR calls with the vehicle rendered away from its physics origin: {'; '.join(parts) or 'no calls'}"


def is_smooth(rec):
    return rec["class"].startswith("smooth")


def windows(lines, names):
    """{name: (begin, end)} line indices between 'VV_BEGIN <name>' and 'VV_END <name>'."""
    out = {}
    for name in names:
        b = [i for i, s in enumerate(lines) if s.strip() == f"VV_BEGIN {name}"]
        e = [i for i, s in enumerate(lines) if s.strip() == f"VV_END {name}"]
        if b and e and b[0] < e[-1]:
            out[name] = (b[0], e[-1])
    return out


def excluded_ticks(recs):
    """Ticks within EXCLUDE_TICKS of a change of (vehicle, seat, view mode), in log order."""
    out = set()
    last = None
    for r in recs:
        ident = (r["ent"], r["pos"], r["mode"])
        if last is not None and ident != last:
            out.update(range(r["tick"] - EXCLUDE_TICKS, r["tick"] + EXCLUDE_TICKS + 1))
        last = ident
    return out


def view_lines(lines, slot, side):
    """(log index, record) for each AOR_VIEW/VIEW_STATE line of client slot on side."""
    out = []
    for i, s in enumerate(lines):
        if "AOR_VIEW:" not in s and "VIEW_STATE:" not in s:
            continue
        r = parse_view(s)
        if r and r["client"] == slot and r["side"] == side:
            out.append((i, r))
    return out


def in_phases(index, spans):
    return any(b < index < e for b, e in spans.values())


def aor_differences(r):
    """The fields in which an AoR call did not return and keep the tick's third-person
    view (N1, N2): result against before.tpo/tpa, after against before."""
    diff = []
    if r["result"]["org"] != r["before"]["tpo"]:
        diff.append("result.org")
    if r["result"]["ang"] != r["before"]["tpa"]:
        diff.append("result.ang")
    diff += [f"after.{k}" for k in POSE_KEYS if r["after"][k] != r["before"][k]]
    return diff


def mark_client_calls(cli):
    """Tag each client AoR record with where it ran, from the line order. Between the pre
    and post lines of one predicted tick (same tick number) it ran inside that tick:
    'tick' for a new tick (new=1: a client moveable's Think, rvClientMoveable::Think) and
    'again' for a tick predicted again (new=0; none is expected, since client moveables
    think only in new ticks). Otherwise 'between': a snapshot read, which sets the tick
    number back to the snapshot's. The post line of every predicted tick, new=0 ones
    included, closes it."""
    state = None
    for _, r in cli:
        if r["kind"] == "VIEW_STATE":
            state = r
        elif state is not None and state["at"] == "pre" and state["tick"] == r["tick"]:
            r["where"] = "tick" if state["new"] == 1 else "again"
        else:
            r["where"] = "between"


def summarize_client(recs, bad, what):
    """The detail line for a reported client check: by where the calls ran, how many
    differ, with the violations' ud and f."""
    parts = []
    places = [("between", "between ticks (snapshot reads)"), ("tick", "inside ticks (client moveables)")]
    if any(r["where"] == "again" for r in recs):
        places.append(("again", "inside ticks predicted again"))
    for where, label in places:
        n = sum(1 for r in recs if r["where"] == where)
        b = [r for r, _ in bad if r["where"] == where]
        detail = f"{label}: {len(b)} of {n} differ"
        by_ud = {}
        for r in b:
            by_ud.setdefault(r["ud"], []).append(r["f"])
        if by_ud:
            detail += " (" + "; ".join(f"ud={ud}: {len(fs)}, f {min(fs):.4g}..{max(fs):.4g}" for ud, fs in sorted(by_ud.items())) + ")"
        parts.append(detail)
    fields = {}
    for _, diff in bad:
        for d in diff:
            fields[d] = fields.get(d, 0) + 1
    ud1 = sum(1 for r in recs if r["ud"] == 1)
    return (f"{what}, {len(recs)} client AoR calls ({ud1} with ud=1): {'; '.join(parts)}"
            + (f"; fields: {', '.join(f'{k} {v}' for k, v in sorted(fields.items()))}" if fields else ""))


def evaluate(host_lines, peer_lines, slot):
    """(results {check: dict}, sanity failures, report lines)."""
    results, sanity, report = {}, [], []
    host_spans = windows(host_lines, ("A", "B"))
    peer_spans = windows(peer_lines, ("A", "B"))
    for name in ("A", "B"):
        if name not in host_spans:
            sanity.append(f"the host log has no phase {name} markers")
        if name not in peer_spans:
            sanity.append(f"the remote client's log has no phase {name} markers")

    srv = view_lines(host_lines, slot, "srv")
    srv_skip = excluded_ticks(r for _, r in srv)
    srv_aor = [(i, r) for i, r in srv if r["kind"] == "AOR_VIEW" and in_phases(i, host_spans) and r["tick"] not in srv_skip]
    post = {r["tick"]: r for _, r in srv if r["kind"] == "VIEW_STATE" and r["at"] == "post"}
    pre = {r["tick"]: r for _, r in srv if r["kind"] == "VIEW_STATE" and r["at"] == "pre"}
    first_call = {}
    for i, r in srv:
        if r["kind"] == "AOR_VIEW":
            first_call.setdefault(r["tick"], i)

    # N0: the standard view's AoR origin is the tick's
    std = [r for _, r in srv_aor if r["class"] == "standard"]
    bad0 = [r for r in std if r["result"]["org"] != r["before"]["fpo"]]
    results["N0"] = {"ok": not bad0 and bool(std),
                     "detail": f"{len(bad0)} of {len(std)} server AoR calls in the standard view return an origin other than before.fpo"}

    # N1: the smooth view's AoR result is the tick's third-person view, and the call changes nothing
    smooth = [(i, r) for i, r in srv_aor if is_smooth(r)]
    bad1 = [(i, r, d) for i, r in smooth if (d := aor_differences(r))]
    shaped1, first1 = 0, 0
    for i, r, _ in bad1:
        prev = post.get(r["tick"] - 1)
        first = first_call.get(r["tick"]) == i
        first1 += first
        if (prev is not None and first and r["result"]["org"] == prev["pose"]["tpo"]
                and angle_diff(r["result"]["ang"], prev["pose"]["tpa"]) <= ANG_TOL):
            shaped1 += 1
    ticks1 = {r["tick"] for _, r in smooth}
    # the ticks whose first AoR call is judged and in which the camera moved
    judged_first = {r["tick"] for i, r in smooth if first_call.get(r["tick"]) == i}
    moving = {t for t in judged_first if t in post and t - 1 in post and post[t]["pose"]["tpo"] != post[t - 1]["pose"]["tpo"]}
    moving_bad = {r["tick"] for _, r, _ in bad1} & moving
    fields1 = {}
    for _, _, d in bad1:
        for k in d:
            fields1[k] = fields1.get(k, 0) + 1
    results["N1"] = {"ok": not bad1 and bool(smooth),
                     "shaped": bool(bad1) and shaped1 == len(bad1) and moving_bad == moving,
                     "detail": f"{len(bad1)} of {len(smooth)} server AoR calls in the smooth view in {len(ticks1)} ticks differ"
                               f" ({', '.join(f'{k} {v}' for k, v in sorted(fields1.items())) or 'none'});"
                               f" prev_tick shape {shaped1}, first call of their tick {first1}"
                               f" (later calls of a tick: {len(smooth) - len(judged_first)}, differing {len(bad1) - first1});"
                               f" ticks the camera moved in {len(moving)}, with a violation {len(moving_bad)}"}

    # N4: nothing between ticks changes the smooth view
    pairs = []
    phase_ticks = {r["tick"] for i, r in srv if in_phases(i, host_spans)}
    for t in sorted(post):
        a, b = post[t], pre.get(t + 1)
        if b is None or t not in phase_ticks or t + 1 not in phase_ticks or t in srv_skip or t + 1 in srv_skip:
            continue
        if is_smooth(a) and is_smooth(b):
            pairs.append((a, b))
    bad4 = [(a, b) for a, b in pairs if any(b["pose"][k] != a["pose"][k] for k in POSE_KEYS)]
    shaped4 = 0
    for a, b in bad4:
        prev = post.get(a["tick"] - 1)
        if (prev is not None and b["pose"]["tpo"] == prev["pose"]["tpo"] and b["pose"]["fpo"] == prev["pose"]["tpo"]
                and angle_diff(b["pose"]["tpa"], prev["pose"]["tpa"]) <= ANG_TOL
                and angle_diff(b["pose"]["fpa"], prev["pose"]["tpa"]) <= ANG_TOL):
            shaped4 += 1
    results["N4"] = {"ok": not bad4 and bool(pairs), "shaped": bool(bad4) and shaped4 == len(bad4),
                     "detail": f"{len(bad4)} of {len(pairs)} smooth-view ticks start with a view other than the one the"
                               f" last tick logged; prev_tick shape {shaped4}"}
    if len(pairs) < MIN_PAIRS:
        sanity.append(f"N4 has {len(pairs)} smooth-view tick pairs (need {MIN_PAIRS})")
    # proof that the smooth camera moved, whatever N1 and N4 find
    cam_moved = [t for t in sorted(post) if t in phase_ticks and t not in srv_skip and t - 1 in post
                 and is_smooth(post[t]) and is_smooth(post[t - 1]) and post[t]["pose"]["tpo"] != post[t - 1]["pose"]["tpo"]]
    report.append(f"the server's smooth camera moved on {len(cam_moved)} judged ticks")
    if len(cam_moved) < MIN_PAIRS:
        sanity.append(f"the server's smooth camera moved on {len(cam_moved)} judged ticks (need {MIN_PAIRS})")
    smooth_posts = sorted(t for t in post if is_smooth(post[t]))
    if smooth_posts and "B" in host_spans:
        last_b = max((r["tick"] for i, r in srv if i < host_spans["B"][1]), default=smooth_posts[0])
        report.append(chain_report(post, smooth_posts[0], last_b))

    # N2, N3: the client (reported)
    cli = view_lines(peer_lines, slot, "cli")
    mark_client_calls(cli)
    cli_skip = excluded_ticks(r for _, r in cli)
    cli_aor = [r for i, r in cli if r["kind"] == "AOR_VIEW" and in_phases(i, peer_spans) and r["tick"] not in cli_skip]
    cli_smooth = [r for r in cli_aor if is_smooth(r)]
    cli_std = [r for r in cli_aor if r["class"] == "standard"]
    bad2 = [(r, d) for r in cli_smooth if (d := aor_differences(r))]
    bad3 = [(r, ["result.org"]) for r in cli_std if r["result"]["org"] != r["before"]["fpo"]]
    results["N2"] = {"ok": not bad2 and bool(cli_smooth), "detail": summarize_client(cli_smooth, bad2, "smooth view")}
    results["N3"] = {"ok": not bad3 and bool(cli_std), "detail": summarize_client(cli_std, bad3, "standard view")}
    for what, bad in (("N2", bad2), ("N3", bad3)):
        for r, d in bad[:5]:
            report.append(f"{what} violation: tick {r['tick']} ({r['where']}) ud={r['ud']} f={r['f']:.9g}: {', '.join(d)}")
        if len(bad) > 5:
            report.append(f"{what}: {len(bad) - 5} more violations")

    # N5: the host's first on-foot draws after the console exit, which can run in the same
    # frame as the echo before it: the draws after the last seated one, in later game times.
    # A frame that runs no tick after the exit draws in the exit tick's game time, with
    # either view depending on what the exit recalculated; neither is the rewind, which
    # comes with the first on-foot tick's base draws.
    d_span = windows(host_lines, ("D",)).get("D")
    draws = [p for s in host_lines[d_span[0]:d_span[1]] if (p := parse_draw(s))] if d_span else []
    seated = [k for k, p in enumerate(draws) if p["seat"] != "-"]
    if not draws or not seated:
        sanity.append(f"phase D: {len(draws)} host draws, {len(seated)} of them seated")
        results["N5"] = {"ok": False, "shaped": False, "detail": "no draws to judge"}
    else:
        last = draws[seated[-1]]
        foot = [p for p in draws[seated[-1] + 1:] if p["seat"] == "-" and p["gt"] > last["gt"]]
        jumps = [(k, math.dist(a["view"][:3], b["view"][:3])) for k, (a, b) in enumerate(zip(foot, foot[1:]))
                 if math.dist(a["view"][:3], b["view"][:3]) > JUMP]
        shaped5 = False
        if len(jumps) == 1:
            k, size = jumps[0]
            held = foot[:k + 1]
            shaped5 = (size > REWIND and len({p["gt"] for p in held}) == 1
                       and all(math.dist(p["view"][:3], last["view"][:3]) <= DRAW_EPS for p in held))
        gts = sorted({p["gt"] for p in foot})
        results["N5"] = {"ok": not jumps and len(gts) >= 2, "shaped": shaped5,
                         "detail": f"{len(foot)} on-foot draws in {len(gts)} game times after the exit;"
                                   f" jumps over {JUMP:.0f} u: {', '.join(f'{s:.1f} u after draw {k}' for k, s in jumps) or 'none'}"
                                   + (f"; the draws before it: game time {foot[0]['gt']}, at the vehicle camera"
                                      f" (last seated draw, game time {last['gt']})" if shaped5 else "")}
        if len(gts) < 2:
            sanity.append(f"phase D: on-foot draws in {len(gts)} game times after the exit (need 2)")
        else:
            # the rewind shows only in the first on-foot tick's draws, and only if that tick
            # was drawn; and it fails N5 only if the vehicle camera is far from the on-foot
            # view, which the next game time's draws show (they are not rewound to it)
            later = next(p for p in foot if p["gt"] == gts[1])
            gap = math.dist(last["view"][:3], later["view"][:3])
            report.append(f"phase D: the last seated draw in game time {last['gt']}, the first on-foot draws in"
                          f" {gts[0]} (+{gts[0] - last['gt']} ms); the vehicle camera is {gap:.1f} u from the on-foot"
                          f" view of game time {gts[1]}")
            if gts[0] != last["gt"] + TICK_MS:
                sanity.append(f"phase D: the first on-foot game time drawn is {gts[0] - last['gt']} ms after the last"
                              f" seated draw's (need one tick, {TICK_MS} ms): the first on-foot tick was never drawn")
            if gap <= JUMP:
                sanity.append(f"phase D: the vehicle camera is {gap:.1f} u from the on-foot view (need over {JUMP:.0f} u,"
                              f" or N5 cannot see a rewind)")

    # sanity
    counts = {"server standard": len(std), "server smooth": len(smooth),
              "client standard": len(cli_std), "client smooth": len(cli_smooth)}
    for what, n in counts.items():
        need = MIN_SERVER_LINES if what.startswith("server") else MIN_CLIENT_LINES
        if n < need:
            sanity.append(f"{what} AoR lines: {n} (need {need})")
    report.append("AoR lines judged: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    for name, (b, e) in host_spans.items():
        pts = [r["phys"] for i, r in srv if b < i < e and r["kind"] == "VIEW_STATE" and r["at"] == "post"]
        travel = max((math.dist(p, pts[0]) for p in pts), default=0.0)
        classes = sorted({r["class"] for i, r in srv if b < i < e})
        report.append(f"phase {name}: the vehicle moved {travel:.1f} u from where the phase began; server classes {classes}")
        if travel < MIN_TRAVEL:
            sanity.append(f"phase {name}: the vehicle moved {travel:.1f} u (need {MIN_TRAVEL:.0f})")
    if not any(r["ud"] == 1 for r in cli_aor):
        sanity.append("no client AoR call with ud=1 (unlockedDraw set by a render-only frame)")
    # M5 step 7: the host's render-only frames set unlockedDraw too, and an AoR call between
    # one and the next tick (a snapshot write) runs inside that window. The snapshots of a
    # frame are written before it presents, so a call sees ud=1 only on the second of two
    # frames in a row that ran no tick: required only when the host ran >= MIN_FRAMES_PER_TICK
    # frames a tick in phase B, the smooth view's (its FRAME_TIMING), reported otherwise
    srv_ud1 = sum(1 for _, r in srv_aor if r["ud"] == 1)
    b, e = host_spans.get("B", (0, 0))
    timing = [m for ln in host_lines[b:e] if (m := FRAME_TIMING_RE.search(ln))]
    ticks = sum(int(m.group(3)) for m in timing)
    per_tick = sum(int(m.group(2)) for m in timing) / ticks if ticks else None
    report.append(f"server AoR calls with ud=1 (inside the host's unlockedDraw window): {srv_ud1} of {len(srv_aor)};"
                  f" the host ran " + (f"{per_tick:.2f} frames a tick" if per_tick else "no timed frame") + " in phase B")
    if not srv_ud1:
        if per_tick is not None and per_tick >= MIN_FRAMES_PER_TICK:
            sanity.append(f"no server AoR call with ud=1, though the host ran {per_tick:.2f} frames a tick in phase B"
                          " (the host's render-only frames set unlockedDraw since M5 step 7)")
        else:
            report.append(f"no server AoR call with ud=1: not required, the host ran fewer than {MIN_FRAMES_PER_TICK:.0f}"
                          " frames a tick in phase B")
    report.append(render_report([r for _, r in srv_aor], "server"))
    report.append(render_report(cli_aor, "client"))
    return results, sanity, report


def messages(host_lines, slot):
    """Sanity: the enter, cycle and exit messages."""
    missing = []
    wanted = {
        "the remote client's entry into the badger's driver seat": rf"ENTER_VEHICLE: client {slot} entered \S+ \(seat 0\)",
        "the camera cycle into a smooth view": rf"CYCLE_CAMERA: client {slot} in \S+ seat 0 camera mode 0 -> 1 \(smooth",
        "the host's entry into the titan": r"ENTER_VEHICLE: client 0 entered \S+",
        "the host's exit": r"EXIT_VEHICLE: client 0 exited",
    }
    for what, pattern in wanted.items():
        if not any(re.search(pattern, s) for s in host_lines):
            missing.append(f"no message for {what} ({pattern})")
    return missing


def judge(results):
    """Print the table; return the failures."""
    failures = []
    print(f"\n{'check':<5} {'result':<18} detail")
    for check in ("N0", "N1", "N4", "N2", "N3", "N5"):
        r = results.get(check)
        if r is None:
            continue
        gap = KNOWN_GAPS.get(check)
        if check in REPORT_ONLY:
            verdict = "report"
        elif gap is None:
            verdict = "pass" if r["ok"] else "FAIL"
            if not r["ok"]:
                failures.append(f"{check}: {r['detail']}")
        elif r["ok"]:
            verdict = "UNEXPECTED PASS"
            failures.append(f"{check} passes but is a known gap: update KNOWN_GAPS ({r['detail']})")
        elif r["shaped"]:
            verdict = "known gap"
        else:
            verdict = "GAP CHANGED SHAPE"
            failures.append(f"{check}: the known gap is no longer {gap}: {r['detail']}")
        print(f"{check:<5} {verdict:<18} {r['detail']}")
    return failures


def slot_from_log(host_lines):
    for s in host_lines:
        m = re.search(r"ENTER_VEHICLE: client (\d+) entered \S+ \(seat 0\)", s)
        if m and m.group(1) != "0":
            return int(m.group(1))
    return 1


def finish(host_lines, peer_lines, slot, failures):
    results, sanity, report = evaluate(host_lines, peer_lines, slot)
    failures = failures + messages(host_lines, slot) + sanity
    for line in report:
        print(f"  {line}")
    failures += judge(results)
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"\nPASS: every check matched the expected state (known gaps: {', '.join(sorted(KNOWN_GAPS)) or 'none'};"
          f" reported only: {', '.join(REPORT_ONLY) or 'none'})")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--client-binary")
    ap.add_argument("--map", default="valley")
    ap.add_argument("--port", type=int, default=29910)
    ap.add_argument("--log-dir")
    ap.add_argument("--replay", help="evaluate the host.log and peer.log saved in this directory instead of running")
    args = ap.parse_args(argv)
    if args.replay:
        d = Path(args.replay)
        missing = [str(d / name) for name in ("host.log", "peer.log") if not (d / name).is_file()]
        if missing:
            print(f"[replay] FAIL: missing {', '.join(missing)} (--replay takes the --log-dir of a run)")
            return 1
        host_lines = (d / "host.log").read_text(errors="replace").splitlines()
        peer_lines = (d / "peer.log").read_text(errors="replace").splitlines()
        return finish(host_lines, peer_lines, slot_from_log(host_lines), [])

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

    # both players on GDF (team balancing would refuse the second one); g_logVehicleViews
    # and g_logFramePoses are cheats, and the remote client sets its own once connected,
    # when it has the server's net_allowCheats. The remote client draws at 60 fps, so half
    # its frames are render-only (ud=1 at its snapshot reads); the host draws at 120, and
    # since M5 step 7 its render-only frames set unlockedDraw (ud=1 at some of its AoR calls).
    game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0", "si_teamForceBalance": "0",
            "net_allowCheats": "1"}
    view = {"com_unlockFPS": "1", "r_swapInterval": "0", "r_vkValidation": "0"}
    host = VulkanClient(vk, data, args.port, map_name=args.map, log_path=log("host"),
                        cvars={**game, "ui_name": "Host", "com_maxFPS": "120", "in_yawSpeed": "90", "in_angleSpeedKey": "1",
                               "com_logFrameTiming": "1", **view})
    host.name, host.slot = "Host", None
    peer = VulkanClient(vk, data, args.port + 2, connect_to=f"127.0.0.1:{args.port}", log_path=log("peer"),
                        cvars={"si_pure": "0", "ui_name": "Peer", "net_clientShowSnapshots": "1", "com_maxFPS": "60", **view})
    peer.name, peer.slot = "Peer", None
    procs = [("peer", peer), ("host", host)]
    failures = []

    def expect(proc, pattern, what, mark, timeout=10.0):
        hit = proc.wait_for(lambda s: re.search(pattern, s), timeout, mark)
        if not hit:
            raise Failed(f"{what}: no '{pattern}' from the {proc.name.lower()}")
        return hit

    def both(text):
        host.send(f"echo {text}")
        peer.send(f"echo {text}")

    def drive(name):
        mark = host.mark()
        both(f"VV_BEGIN {name}")
        host.send("+left")
        for (forward, right), seconds in DRIVES[name]:
            peer.send(f"clientMove {forward} {right} 0")
            time.sleep(seconds)
        peer.send("clientMove 0 0 0")
        time.sleep(0.5)
        both(f"VV_END {name}")
        host.send("-left")
        expect(host, rf"^VV_END {name}\s*$", f"phase {name}", mark)

    try:
        host.start()
        if not host.wait_for_text(READY, 180.0) or not host.wait_for_text(f"Map: maps/{args.map}", 180.0):
            raise Failed("the listen server never loaded the map")
        host.sync(60.0)
        host.slot = 0
        spawn(host, host, "gdf", timeout=60.0)
        connect(host, peer, "listen")
        spawn(host, peer, "gdf")
        slot = peer.slot

        for proc in (host, peer):
            mark = proc.mark()
            proc.send("set g_logVehicleViews 1")
            proc.sync()
            refused = [ln.text for ln in proc.snapshot(mark) if "cannot be changed" in ln.text]
            if refused:
                raise Failed(f"the {proc.name.lower()} refused g_logVehicleViews: {refused[0]} (are cheats allowed?)")

        mark = host.mark()
        host.send(f"enterVehicle {slot} vehicle_badger")
        expect(host, rf"ENTER_VEHICLE: client {slot} entered \S+ \(seat 0\)", "the remote client's entry", mark)
        if not peer.wait_for(lambda s: "AOR_VIEW: side=cli" in s, 10.0, 0):
            raise Failed("no client AOR_VIEW line after the entry (g_logVehicleViews on the remote client?)")
        time.sleep(1.0)
        drive("A")

        for bad in ("x", f"{slot}x"):  # refused, not read as 0 or as the slot (the lexer splits "1x")
            mark = host.mark()
            host.send(f"cycleVehicleCamera {bad}")
            expect(host, r"usage: cycleVehicleCamera", f"the refusal of the client number '{bad}'", mark)
        mark = host.mark()
        host.send(f"cycleVehicleCamera {slot}")
        expect(host, rf"CYCLE_CAMERA: client {slot} ", "the camera cycle", mark)
        time.sleep(1.0)
        drive("B")

        # phase D: the host's own exit between ticks (N5)
        mark = host.mark()
        host.send("echo VV_BEGIN D")
        host.send("set g_logFramePoses 1")
        host.send("enterVehicle 0 vehicle_titan")
        expect(host, r"ENTER_VEHICLE: client 0 entered", "the host's entry", mark)
        time.sleep(1.0)
        host.send("echo VV_EXIT")
        host.send("exitVehicle 0")
        expect(host, r"EXIT_VEHICLE: client 0 ", "the host's exit", mark)
        time.sleep(1.0)
        host.send("echo VV_END D")
        expect(host, r"^VV_END D\s*$", "phase D", mark)
        host.send("set g_logFramePoses 0")
        host.send("set g_logVehicleViews 0")
        peer.send("set g_logVehicleViews 0")
        host.sync()
    except Failed as e:
        failures.append(str(e))
    finally:
        codes = {}
        for name, proc in procs:
            codes[name] = proc.quit() if proc.alive() else None
        for name, proc in procs:
            # same exit-code predicate as this file's own quit check below: a
            # process that never ran (None) is already a failure there
            run_failed = bool(failures) or any(c != 0 for c in codes.values())
            for f in h.scan_failures((ln.text for ln in proc.snapshot()), run_failed=run_failed):
                failures.append(f"{name} log line {f['line']}: {f['text']}")
            for i, ln in enumerate(proc.snapshot()):
                if any(m in ln.text for m in BAD_MARKERS):
                    failures.append(f"{name} log line {i + 1}: {ln.text}")
        host_lines = [ln.text for ln in host.snapshot()]
        peer_lines = [ln.text for ln in peer.snapshot()]
        for _, proc in procs:
            proc.cleanup()
    print(f"quit exit codes: {codes}")
    if not failures and any(c != 0 for c in codes.values()):
        failures.append(f"quit exit codes: {codes}")
    if failures:
        # a run that stopped early has nothing to judge
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    return finish(host_lines, peer_lines, peer.slot, failures)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
