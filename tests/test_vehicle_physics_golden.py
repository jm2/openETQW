#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Vehicle physics guard: VEH_TRACE of the vehicle replay against a recorded golden.

M5 step 5a (docs/m5-plan.md; docs/vehicle-responsiveness.md stage 0). SIM_HASH
hashes only a vehicle's origin and linear velocity, to 0.01, and the other
replays compare one build with itself across render caps, so a change in vehicle
handling would pass them. This test catches it:

1. The game's vehicle replay (g_determinismTest 2 on valley: the badger driven
   with throttle, steering both ways, a wall contact, braking into reverse and
   forward again; then an anansi lifted off and flown with injected pitch and
   yaw/roll) runs at com_maxFPS 60 and 240. Its VEH_TRACE lines (every occupied
   rigid-body transport, every tick, in full precision: origin, quaternion,
   linear and angular velocity, atRest, the last collision time, the control
   state) must be identical at both caps, and must show the schedule doing what
   it is for: both vehicles traced, the badger's wall contact, the anansi's
   collective and injected angles.
2. The trace is compared with tests/golden/vehicle-physics/<toolchain>.txt,
   recorded from main before any vehicle change. <toolchain> names the compiler
   family, its major version and the architecture of the game module (the
   physics runs there), e.g. linux-x64-gcc16: floating-point code generation
   differs between compilers and architectures (fused multiply-adds), so each
   toolchain has its own golden, compared bit-exactly.
3. With no golden for this toolchain, the trace is compared with the reference
   golden (REFERENCE) at fixed precision: the tick, vehicle, class, control kind,
   atRest and collision time exactly, the continuous values within TOLERANCE.
   The tolerances are three to four times the largest difference that a small
   floating-point change alone produced on antec: fused multiply-adds
   (GCC 16, x86-64, -march=x86-64-v3 -ffp-contract=fast against the baseline
   build, 2026-09-30: origin 0.24, linear velocity 3.3, quaternion 0.0017,
   angular velocity 0.073, steering 0.037). The real builds use
   -ffp-contract=off, so other toolchains differ through the maths library and
   code generation instead. A quaternion and its negation are one rotation and
   compare equal. That is a weaker guard, and the test says so; record a golden
   for the toolchain to get the exact one.

What it guards: the wheeled control (the badger) and the hovercopter control
(the anansi), the rigid-body physics under them and the collision model they
drive into. Tracked vehicles (the titan) are compared only across caps, in
high-refresh-determinism; walkers, jetpacks, hover and jet controls and player
physics are not covered.

Every PR that touches src/game/vehicles or src/game/physics in openETQW-game runs
this test in its local merge gate (CONTRIBUTING.md). A golden is regenerated only
on purpose, with a stated reason that goes into its header:

  python3 tests/test_vehicle_physics_golden.py --regenerate --reason "why the handling changed"

Exit codes: 0 pass, 1 fail, 77 skip (no retail data or client binary).
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import platform
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402

REPO_ROOT = h.REPO_ROOT
GOLDEN_DIR = REPO_ROOT / "tests" / "golden" / "vehicle-physics"
REFERENCE = "linux-x64-gcc16"   # the toolchain the first golden was recorded with
CAPS = (60, 240)
RUN_TIMEOUT_S = 150.0
VEH_RE = re.compile(r"^VEH_TRACE: (tick=(\d+) ent=(\d+) class=(\S+) .*)$")
VECTOR_FIELDS = ("org", "quat", "lin", "ang", "cmdang")
SCALAR_FIELDS = ("dir", "collective")
EXACT_FIELDS = ("rest", "coll")
TOLERANCE = {
    "org": 1.0,         # units
    "quat": 0.006,
    "lin": 10.0,        # units per second
    "ang": 0.25,
    "dir": 0.12,        # degrees
    "collective": 0.01,
    "cmdang": 0.001,    # degrees; the injected angles do not depend on the physics
}
# what the schedule must visibly do (g_determinismTest 2 in openETQW-game Game_local.cpp)
GROUND_CLASS = "vehicle_badger"
AIR_CLASS = "vehicle_anansi"


def to_float(text: str) -> float:
    """A printed float; any NaN spelling (glibc "nan", "-nan"; MSVC "-nan(ind)") is NaN."""
    try:
        return float(text)
    except ValueError:
        return float("nan") if "nan" in text.lower() else float("inf") if "inf" in text.lower() else float("nan")


def parse_line(body: str) -> dict:
    rec = {"raw": body}
    m = VEH_RE.match("VEH_TRACE: " + body)
    rec["tick"], rec["ent"], rec["class"] = int(m.group(2)), int(m.group(3)), m.group(4)
    ctl = re.search(r"\bctl=(\S+)", body)
    rec["ctl"] = ctl.group(1) if ctl else "?"
    for field in VECTOR_FIELDS:
        # values up to the closing parenthesis of the group ("-nan(ind)" holds one itself)
        v = re.search(rf"\b{field}=\(((?:[^()]|\([a-z]*\))*)\)", body)
        if v:
            rec[field] = [to_float(x) for x in v.group(1).split(",")]
    for field in SCALAR_FIELDS + EXACT_FIELDS:
        v = re.search(rf"\b{field}=(\S+)", body)
        if v:
            rec[field] = to_float(v.group(1))
    return rec


def trace_of(lines: list[str]) -> list[str]:
    return [m.group(1) for ln in lines if (m := VEH_RE.match(ln.strip()))]


def run_schedule(client: Path, data: Path, savepath: Path, cap: int) -> list[str]:
    args = [
        str(client),
        "+set", "fs_basepath", str(data),
        "+set", "fs_savepath", str(savepath / f"cap{cap}"),
        "+set", "fs_game", "base",
        "+set", "si_rules", "sdGameRulesObjective",
        "+set", "r_swapInterval", "0",
        "+set", "com_maxFPS", str(cap),
        "+set", "g_determinismTest", "2",
        "+set", "g_simHash", "1",
        "+devmap", "valley",
        "+clientTeam", "gdf",
        "+clientClass", "soldier",
        "+closeLimbo",
    ]
    print(f"--- vehicle replay at com_maxFPS {cap} ---", flush=True)
    try:
        done = subprocess.run(args, cwd=str(REPO_ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, errors="replace", timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"cap {cap}: the replay did not quit within {RUN_TIMEOUT_S:.0f} s") from exc
    lines = done.stdout.splitlines()
    failures = h.scan_failures(lines)
    if done.returncode != 0 or failures:
        tail = "\n".join(lines[-15:])
        raise AssertionError(f"cap {cap}: exit {done.returncode}, failure markers {failures[:3]}\n{tail}")
    trace = trace_of(lines)
    print(f"  {len(trace)} VEH_TRACE lines", flush=True)
    return trace


def check_schedule(trace: list[str]) -> list[str]:
    """The replay did what it is for; if the map, the vehicles or the schedule
    changed, say so rather than compare an empty or different drive."""
    recs = [parse_line(b) for b in trace]
    problems = []
    ground = [r for r in recs if r["class"] == GROUND_CLASS]
    air = [r for r in recs if r["class"] == AIR_CLASS]
    if len(ground) < 100:
        problems.append(f"{GROUND_CLASS}: {len(ground)} traced ticks, expected about 155")
    if len(air) < 80:
        problems.append(f"{AIR_CLASS}: {len(air)} traced ticks, expected about 124")
    if ground and not any(r.get("coll", 0) > 0 for r in ground):
        problems.append(f"{GROUND_CLASS}: no collision recorded (the wall contact is missing)")
    if ground and any(r["ctl"] != "wheeled" for r in ground):
        problems.append(f"{GROUND_CLASS}: not traced as a wheeled control")
    if air and any(r["ctl"] != "air" for r in air):
        problems.append(f"{AIR_CLASS}: not traced as an air control")
    if air and not any(abs(x) > 1.0 for r in air for x in r.get("cmdang", [])):
        problems.append(f"{AIR_CLASS}: no injected view angles reached the control")
    if air and len({r.get("collective") for r in air}) < 3:
        problems.append(f"{AIR_CLASS}: the collective never moved")
    ticks = [r["tick"] for r in recs]
    if ticks != sorted(ticks):
        problems.append("VEH_TRACE ticks are out of order")
    return problems


def host_arch() -> str:
    return "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"


def module_path(client: Path) -> Path:
    """The game module the client loads: FindDLL looks beside the executable first
    (a symlink into the game build on Linux and macOS development hosts)."""
    suffix = {"win32": ".dll", "darwin": ".dylib"}.get(sys.platform, ".so")
    return (client.parent / f"game_{host_arch()}{suffix}").resolve()


def game_repo(client: Path | None = None) -> Path:
    """The game checkout the loaded module was built from: the git top level of the
    module's resolved path (a symlink into <game>/build), else $OPENETQW_GAME_REPO,
    else ../openETQW-game."""
    if client is not None:
        top = subprocess.run(["git", "-C", str(module_path(client).parent), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True)
        if top.returncode == 0 and Path(top.stdout.strip()).resolve() != REPO_ROOT.resolve():
            return Path(top.stdout.strip())
    raw = os.environ.get("OPENETQW_GAME_REPO", "").strip()
    return Path(raw).expanduser() if raw else REPO_ROOT.parent / "openETQW-game"


def builds_game_module(build: Path) -> bool:
    targets = build / "meson-info" / "intro-targets.json"
    if not targets.is_file():
        return False
    names = {t.get("name") for t in json.loads(targets.read_text(encoding="utf-8"))}
    return f"game_{host_arch()}" in names


def toolchain_key(client: Path) -> tuple[str, str]:
    """(key, description) of the compiler that built the game module the client loads:
    from the Meson build directory that builds it (the module's own directory when it
    is a symlink into the game build; the game checkout's build/ when the module
    beside the client is a copy, as on Windows), never the engine's."""
    osname = {"win32": "windows", "darwin": "macos"}.get(sys.platform, "linux")
    for build in (module_path(client).parent, game_repo(client) / "build"):
        info = build / "meson-info" / "intro-compilers.json"
        if builds_game_module(build) and info.is_file():
            cpp = json.loads(info.read_text(encoding="utf-8")).get("host", {}).get("cpp")
            if cpp and cpp.get("id") and cpp.get("version"):
                return (f"{osname}-{host_arch()}-{cpp['id']}{cpp['version'].split('.')[0]}",
                        f"{cpp['id']} {cpp['version']} ({build})")
    return f"{osname}-{host_arch()}-unknown", "the game module's compiler could not be determined"


def runtime_description() -> str:
    """The C library and OS: the game calls libm (sinf, atan2, powf, ...), whose
    results can change between releases on the same compiler."""
    libc = " ".join(x for x in platform.libc_ver() if x) or "libc unknown"
    return f"{platform.system()} {platform.release()}; {libc}"


def read_golden(path: Path) -> tuple[list[str], list[str]]:
    header, body = [], []
    for ln in path.read_text(encoding="utf-8").splitlines():
        (header if ln.startswith("#") else body).append(ln)
    return header, [b for b in body if b.strip()]


def first_difference(a: list[str], b: list[str]) -> str:
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return f"line {i + 1}:\n    golden: {x}\n    now:    {y}"
    return f"the traces have {len(a)} and {len(b)} lines"


def compare_fixed(golden: list[str], trace: list[str]) -> list[str]:
    g, t = [parse_line(b) for b in golden], [parse_line(b) for b in trace]
    if [(r["tick"], r["ent"], r["class"], r["ctl"]) for r in g] != [(r["tick"], r["ent"], r["class"], r["ctl"]) for r in t]:
        return ["the traced ticks, vehicles or control kinds differ from the golden"]
    problems = []
    worst: dict[str, tuple[float, int]] = {}
    for a, b in zip(g, t):
        where = f"tick {a['tick']} {a['class']}"
        for field in EXACT_FIELDS:
            if a.get(field) != b.get(field):
                problems.append(f"{where}: {field} {b.get(field)} != golden {a.get(field)}")
        for field in VECTOR_FIELDS + SCALAR_FIELDS:
            if field not in a and field not in b:
                continue
            if (field in a) != (field in b):
                problems.append(f"{where}: {field} is {'missing' if field in a else 'extra'} in this trace")
                continue
            xs = a[field] if isinstance(a[field], list) else [a[field]]
            ys = b[field] if isinstance(b[field], list) else [b[field]]
            if len(xs) != len(ys):
                problems.append(f"{where}: {field} has {len(ys)} values, the golden {len(xs)}")
                continue
            d = max(abs(x - y) for x, y in zip(xs, ys))
            if field == "quat":
                # q and -q are the same rotation, and idMat3::ToQuat's branch (trace > 0)
                # can pick either sign near a half turn (the golden does at tick 116)
                d = min(d, max(abs(x + y) for x, y in zip(xs, ys)))
            if not math.isfinite(d):
                problems.append(f"{where}: {field} is not finite ({ys}; golden {xs})")
                continue
            if d > worst.get(field, (0.0, 0))[0]:
                worst[field] = (d, a["tick"])
    for field, (d, tick) in sorted(worst.items()):
        within = d <= TOLERANCE[field]
        print(f"  {field:10s} max difference {d:.6g} at tick {tick} ({'within' if within else 'over'} {TOLERANCE[field]})")
        if not within:
            problems.append(f"{field}: {d:.6g} at tick {tick} exceeds the cross-toolchain tolerance {TOLERANCE[field]}")
    return problems[:20]


def git_head(path: Path) -> str:
    done = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True)
    return done.stdout.strip() if done.returncode == 0 else "unknown"


def git_dirty(path: Path, subpaths: list[str]) -> str:
    """'' when the tracked files under subpaths are committed, else why not."""
    done = subprocess.run(["git", "-C", str(path), "status", "--porcelain", "--untracked-files=no", "--", *subpaths],
                          capture_output=True, text=True)
    if done.returncode != 0:
        return f"git status failed in {path}: {done.stderr.strip()}"
    return f"uncommitted changes in {path} ({', '.join(subpaths)})" if done.stdout.strip() else ""


def git_commit_time(path: Path) -> float:
    done = subprocess.run(["git", "-C", str(path), "log", "-1", "--format=%ct"], capture_output=True, text=True)
    return float(done.stdout.strip()) if done.returncode == 0 and done.stdout.strip() else 0.0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--regenerate", action="store_true", help="record this toolchain's golden (needs --reason)")
    parser.add_argument("--reason", default="", help="why the golden is recorded again; goes into its header")
    args = parser.parse_args(argv)
    if args.regenerate and not args.reason.strip():
        parser.error("--regenerate needs --reason: a golden changes only on purpose")

    data = h.env_data_path()
    client = h.build_binary("client")
    h.skip_unless_available(data, client)
    savepath = h.private_savepath("etqw-vehicle-golden-")
    key, compiler = toolchain_key(client)
    print(f"toolchain: {key} ({compiler}); {runtime_description()}")

    traces = {cap: run_schedule(client, data, savepath, cap) for cap in CAPS}
    failures = []
    if traces[CAPS[0]] != traces[CAPS[-1]]:
        failures.append(f"VEH_TRACE differs between com_maxFPS {CAPS[0]} and {CAPS[-1]}: "
                        + first_difference(traces[CAPS[0]], traces[CAPS[-1]]))
    trace = traces[CAPS[0]]
    failures += check_schedule(trace)

    game = game_repo(client)
    reference = GOLDEN_DIR / f"{REFERENCE}.txt"
    if args.regenerate:
        refusals = list(failures)
        if key.endswith("-unknown"):
            refusals.append(compiler)
        # the recorded commits must be what the module and the engine were built from
        refusals += [r for r in (git_dirty(game, ["."]), git_dirty(REPO_ROOT, ["src"])) if r]
        if module_path(client).is_file() and module_path(client).stat().st_mtime < git_commit_time(game):
            refusals.append(f"{module_path(client)} is older than {game}'s HEAD: rebuild the game module")
        if refusals:
            print("not recording a golden:")
            for f in refusals:
                print(f"  - {f}")
            return h.EXIT_FAIL
        if key != REFERENCE and reference.is_file():
            print(f"this toolchain against {reference.relative_to(REPO_ROOT)} at fixed precision (for the record):")
            for f in compare_fixed(read_golden(reference)[1], trace):
                print(f"  - {f}")
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        target = GOLDEN_DIR / f"{key}.txt"
        header = [
            "# SPDX-License-Identifier: GPL-3.0-or-later",
            "# openETQW vehicle physics golden: VEH_TRACE of g_determinismTest 2 on valley",
            "# (tests/test_vehicle_physics_golden.py; compared bit-exactly on this toolchain)",
            f"# toolchain: {key}: {compiler.split(' (')[0]}",
            f"# runtime: {runtime_description()}",
            f"# engine: {git_head(REPO_ROOT)}",
            f"# game: {git_head(game)}",
            f"# recorded: {datetime.date.today().isoformat()}",
            f"# reason: {args.reason.strip()}",
        ]
        with open(target, "w", encoding="utf-8", newline="\n") as out:
            out.write("\n".join(header + trace) + "\n")
        print(f"recorded {target} ({len(trace)} lines)")
        return h.EXIT_PASS

    own = GOLDEN_DIR / f"{key}.txt"
    if own.is_file():
        header, golden = read_golden(own)
        print(f"golden: {own.relative_to(REPO_ROOT)} ({len(golden)} lines), exact comparison")
        for ln in header:
            print(f"  {ln}")
        if golden != trace:
            # the size of the difference tells a handling change from floating-point
            # noise (a different libm, say): print it against the fixed-precision bounds
            print("the fixed-precision magnitudes of the difference (diagnostic):")
            noise_only = not compare_fixed(golden, trace)
            failures.append("VEH_TRACE differs from this toolchain's golden: "
                            + ("the differences are within the cross-toolchain tolerances, so a different maths"
                               " library or code generation is as likely as a handling change (compare the"
                               " golden's runtime line). " if noise_only else "vehicle handling changed. ")
                            + first_difference(golden, trace)
                            + "\n  If the change is intended, record a new golden with --regenerate --reason.")
    elif reference.is_file():
        _, golden = read_golden(reference)
        print(f"[NOTE] no golden for {key}: comparing with {reference.relative_to(REPO_ROOT)} at fixed precision"
              " (the weaker cross-toolchain check; record one with --regenerate for the exact guard)")
        failures += compare_fixed(golden, trace)
    else:
        failures.append(f"no golden at all: record {reference.relative_to(REPO_ROOT)} first")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return h.EXIT_FAIL
    print(f"PASS: {len(trace)} VEH_TRACE lines, identical at com_maxFPS {', '.join(map(str, CAPS))}, match the golden")
    return h.EXIT_PASS


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
