#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""all-stock-maps' aas_area_validity: an area-0 sample is a miss only when the bot is
stuck off the AAS (owner decision, 2026-10-31, option c; #390). Synthetic samples, no
game data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_all_stock_maps as T  # noqa: E402

failures = 0


def check(name: str, ok: bool, detail: str) -> None:
    global failures
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    if not ok:
        failures += 1


def sample(x: float, y: float, area: int, fwd: int = 127, ground: int = 1) -> dict:
    return {"org": (x, y, 400.0), "vel": (0.0, 0.0, 0.0), "team": "gdf", "dead": 0,
            "spectating": 0, "hp": 100, "atk": 0, "atk_cmd": 0, "vehicle": -1, "tele": 0,
            "ground": ground, "area": area, "fwd": fwd, "side": 0}


def run(path: list[tuple[float, float, int, int]], interval: float = 0.5) -> T.BotTracker:
    """A bot that spawns, then gives one sample per (x, y, area, fwd) every interval."""
    tr = T.BotTracker(0)
    tr.on_spawn({"name": "bot", "spectating": 0, "team": "gdf", "org": (0.0, 0.0, 400.0), "client": 0})
    for i, (x, y, area, fwd) in enumerate(path):
        tr.on_sample(sample(x, y, area, fwd), 100.0 + i * interval)
    tr.finish()
    return tr


def walk(n: int, x0: float = 0.0, area: int = 7) -> list[tuple[float, float, int, int]]:
    # 185 u per 0.5 s: walking speed
    return [(x0 + 185.0 * i, 0.0, area, 127) for i in range(n)]


def passes(tr: T.BotTracker) -> bool:
    frac = tr.area_fraction()
    return frac is not None and frac >= T.AREA_VALID_MIN_FRACTION


# a bot walking through gaps at full speed twice (ark, slipgate): left out, so the check
# passes; counting every area-0 sample, as before, gave 40/46 (87%) and failed
gap = [(185.0 * i, 0.0, 0, 127) for i in range(3)]
path = (walk(20) + [(3700.0 + x, y, a, f) for x, y, a, f in gap] + walk(20, 4255.0)
        + [(7955.0 + x, y, a, f) for x, y, a, f in gap])
tr = run(path)
check("walk_through_gap", passes(tr) and tr.off_left_out == 6 and tr.off_stuck_samples == 0,
      f"{tr.area_valid}/{tr.area_known}, left out {tr.off_left_out}, stuck {tr.off_stuck_samples}")

# airborne samples inside an off-AAS run keep it one run
path = walk(20) + [(3700.0, 0.0, 0, 127)] + [(3885.0, 0.0, 0, 127)] + walk(20, 4255.0)
tr = T.BotTracker(0)
tr.on_spawn({"name": "bot", "spectating": 0, "team": "gdf", "org": (0.0, 0.0, 400.0), "client": 0})
for i, (x, y, area, fwd) in enumerate(path):
    tr.on_sample(sample(x, y, area, fwd, ground=0 if i == 21 else 1), 100.0 + i * 0.5)
tr.finish()
check("airborne_inside_run", tr.off_runs == 1 and tr.off_left_out == 1,
      f"runs {tr.off_runs}, left out {tr.off_left_out}")

# a bot idling on a prop above the AAS (ark, 3 s, no movement commanded), then moving on
path = walk(20) + [(3700.0, 0.0, 0, 0)] * 6 + walk(20, 3885.0)
tr = run(path)
check("idle_on_prop", passes(tr) and tr.off_left_out == 6 and tr.off_stuck_samples == 0,
      f"{tr.area_valid}/{tr.area_known}, left out {tr.off_left_out}")

# a bot wedged off the AAS: commands movement for 4 s but stays within 20 u -> FAIL
path = walk(20) + [(3700.0 + 2.5 * i, 0.0, 0, 127) for i in range(9)] + walk(20, 3885.0)
tr = run(path)
check("wedged_fails", not passes(tr) and tr.off_stuck_samples == 9 and tr.off_stuck_runs == 1,
      f"{tr.area_valid}/{tr.area_known} ({tr.area_fraction():.1%}), stuck {tr.off_stuck_samples}")

# just under the stuck time: 1.0 s pushing in place is not stuck
path = walk(20) + [(3700.0, 0.0, 0, 127)] * 3 + walk(20, 3885.0)
tr = run(path)
check("short_push_not_stuck", tr.off_stuck_samples == 0 and tr.off_left_out == 3,
      f"stuck {tr.off_stuck_samples}, left out {tr.off_left_out}")

# exactly the stuck time: 1.5 s pushing in place is stuck
path = walk(20) + [(3700.0, 0.0, 0, 127)] * 4 + walk(20, 3885.0)
tr = run(path)
check("push_at_threshold_stuck", tr.off_stuck_samples == 4, f"stuck {tr.off_stuck_samples}")

# lost off the AAS: wandering for 6 s without reaching an area -> counted
path = walk(20) + [(3700.0 + 100.0 * i, 0.0, 0, 127) for i in range(13)] + walk(20, 5100.0)
tr = run(path)
check("lost_counted", tr.off_stuck_samples == 13 and not passes(tr),
      f"{tr.area_valid}/{tr.area_known}, stuck {tr.off_stuck_samples}")

# a run still open at the end of the window is classified by finish()
path = walk(20) + [(3700.0, 0.0, 0, 127)] * 8
tr = run(path)
check("open_run_at_end", tr.off_stuck_samples == 8 and not passes(tr), f"stuck {tr.off_stuck_samples}")

# a death ends the run
tr = T.BotTracker(0)
tr.on_spawn({"name": "bot", "spectating": 0, "team": "gdf", "org": (0.0, 0.0, 400.0), "client": 0})
t = 100.0
for x, y, area, fwd in walk(10) + [(1900.0, 0.0, 0, 127)] * 2:
    tr.on_sample(sample(x, y, area, fwd), t); t += 0.5
dead = sample(1900.0, 0.0, 0); dead["dead"] = 1; dead["hp"] = 0
tr.on_sample(dead, t); t += 0.5
for x, y, area, fwd in [(1900.0, 0.0, 0, 127)] * 2:
    tr.on_sample(sample(x, y, area, fwd), t); t += 0.5
tr.finish()
check("death_ends_run", tr.off_runs == 2 and tr.off_stuck_samples == 0,
      f"runs {tr.off_runs}, stuck {tr.off_stuck_samples}")

print(f"{failures} failure(s)")
sys.exit(1 if failures else 0)
