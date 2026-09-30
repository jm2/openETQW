#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vehicle views on the network: do area-of-relevance calls see the tick's view?

M5 step 6, owner decision 4 (docs/m5-plan.md; docs/vehicle-responsiveness.md stage 1):
the network test comes before the vehicle views go back to SDK code. Between ticks the
server's antilag (AntiLag.cpp) and each snapshot it writes (AORManager.cpp), and a
client reading a snapshot, call idPlayer::GetAORView, which recalculates a seated
player's view with CalculateFirstPersonView( false ). OpenETQW's smooth vehicle view
interpolates there from the previous tick's camera, with a fraction servers never set,
and writes the result back into the tick's view; the single-player replay never reaches
that path. With g_logVehicleViews (a cheat) the game prints, for players in a vehicle:

  VIEW_STATE: side at=pre|post tick client ent pos mode class ud new pose= rvo= phys= vpush=
      at=pre before the tick's entity Think, at=post where the tick logs its pose (a
      server: every client in a vehicle; a network client: its local player)
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
              badger driver's 'smooth_locked' view; no valley seat starts in a smooth one)
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
  N2  client, smooth view: as N1                          (reported, not judged)
  N3  client, standard view: as N0                        (reported, not judged)
  N5  the host, after the console exit: no two consecutive on-foot draws (seat=-) of the
      ticks after the exit differ by more than 64 u in view origin
  Sanity: both view classes seen, with >= 20 server and >= 10 client AoR lines each; the
  vehicle moved >= 100 u in each phase; ud=1 on the client; the enter, cycle and exit
  messages all printed. A sanity failure fails the test.

A check the code fails today is listed in KNOWN_GAPS with the shape its failure must
have, as in frame-poses; a gap outside its shape fails as "GAP CHANGED SHAPE", and a
known gap that passes fails as "UNEXPECTED PASS", so the table is updated when the code
changes:
  prev_tick (N1, N4)  the first AoR call of each tick in which the camera moved returns
      the previous tick's camera: its result origin equals post(n-1).tpo exactly, its
      angles are within 0.001 degrees of post(n-1).tpa (the slerp at fraction 0), and f
      is 0; the tick's later calls pass. For N4, pre(n+1) holds that camera in fpo and
      tpo. Every violation has this shape, and for N1 every such tick has one.
  rewind (N5)  every draw of exactly one game time after the exit is at the vehicle
      camera (the last seated draw's view origin, within 0.01 u) and the next draw is
      more than 100 u away: the base draw's rewind to the previous tick's logged origin
      (originlog), which the exit between ticks leaves at the vehicle camera.
N2 and N3 are reported with the ud and f of each violation, split by where the call
ran (from the line order): between ticks, a snapshot read, or inside a predicted tick, a
client moveable's Think (rvClientMoveable::Think calls SetupClientAoR). The client's
behaviour was never measured before this test (docs/frame-path.md section 6.9).

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
ANG_TOL = 1e-3          # degrees: prev_tick's slerp at fraction 0 against the logged axis
MIN_SERVER_LINES = 20   # AoR lines per view class
MIN_CLIENT_LINES = 10
MIN_TRAVEL = 100.0      # units the vehicle must move in each phase
JUMP = 64.0             # N5: the most two consecutive on-foot draws may differ by
REWIND = 100.0          # N5's rewind: the jump off the vehicle camera is larger than this
DRAW_EPS = 0.01         # units: FRAME_POSE prints %.3f

# (forward, right) for each leg of a phase and how long it lasts; B steers the other way
DRIVES = {
    "A": [((127, 0), 1.0), ((127, 127), 0.5), ((-127, 0), 0.8)],
    "B": [((127, 0), 1.0), ((127, -127), 0.5), ((-127, 0), 0.8)],
}

# Checks the current code fails, with the shape the failure must have (M5 step 6 plan,
# section 4, written from the code before this test first ran). Step 6's revert empties it.
KNOWN_GAPS = {
    "N1": ("prev_tick",),
    "N4": ("prev_tick",),
    "N5": ("rewind",),
}
REPORT_ONLY = ("N2", "N3")


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
    """Tag each client AoR record with where it ran, from the line order: 'tick' when it
    comes after the pre line of a new predicted tick with the same tick number and before
    that tick's post line (a client moveable's Think, rvClientMoveable::Think), else
    'between' (a snapshot read, which sets the tick number back to the snapshot's)."""
    state = None
    for _, r in cli:
        if r["kind"] == "VIEW_STATE":
            state = r
        else:
            r["where"] = ("tick" if state is not None and state["at"] == "pre" and state["new"] == 1
                          and state["tick"] == r["tick"] else "between")


def summarize_client(recs, bad, what):
    """The detail line for a reported client check: by where the calls ran, how many
    differ, with the violations' ud and f."""
    parts = []
    for where, label in (("between", "between ticks (snapshot reads)"), ("tick", "inside ticks (client moveables)")):
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
        if (prev is not None and first and r["f"] == 0.0 and r["result"]["org"] == prev["pose"]["tpo"]
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
    # frame as the echo before it: the draws after the last seated one, in later game times
    # (a frame that runs no tick after the exit still draws the exit tick's view)
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
          f" reported: {', '.join(REPORT_ONLY)})")
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
    # its frames are render-only (ud=1 at its snapshot reads).
    game = {"si_rules": "sdGameRulesObjective", "si_pure": "0", "g_teamSwitchDelay": "0", "si_teamForceBalance": "0",
            "net_allowCheats": "1"}
    view = {"com_unlockFPS": "1", "r_swapInterval": "0", "r_vkValidation": "0"}
    host = VulkanClient(vk, data, args.port, map_name=args.map, log_path=log("host"),
                        cvars={**game, "ui_name": "Host", "com_maxFPS": "120", "in_yawSpeed": "90", "in_angleSpeedKey": "1", **view})
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
            for f in h.scan_failures(ln.text for ln in proc.snapshot()):
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
