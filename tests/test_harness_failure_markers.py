#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Failure-marker classification: a bot-thread pacing warning fails only a failed run.

Owner decision, 2026-10-27 (issue #294): `Waiting on BotThread` is the game's own
per-frame pacing warning (game repo src/game/botai/BotThreadData.cpp), so it counts
as a failure only when the same run also fails a gameplay check. The three sightings
behind the decision had 2, 4 and 30 waits and all finished normally, so no wait count
separates a slow host from a wedged thread; the rule keys on the run's outcome.

These are synthetic logs: no engine, no retail data, no ports. Pinned here:
  * an unconditional marker is reported and is not tagged 'pacing';
  * a healthy run with waits returns no failures, but still lets the waits be
    recorded (scan_pacing keeps the line numbers);
  * the negative control: the same log with the run marked failed reports every
    wait, tagged 'pacing', so the marker is not silently lost;
  * the ERROR-banner and script classifications are unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as H  # noqa: E402

failures: list[str] = []


def expect(ok: bool, what: str) -> None:
    if not ok:
        print("FAIL: " + what)
        failures.append(what)


HEALTHY_WAITS = [
    "KILL: time=23661 victim=1 team=strogg class=aggressor damage=220\n",
    "[Respawn] Bot 1\n",
    "WARNING: Waiting on BotThread\n",
    "\n",
    "WARNING: Waiting on BotThread\n",
    "MATCH: time=43593 rules=sdGameRulesCampaign event=timelimit state=gameon winner=strogg\n",
]


def main() -> int:
    # The pacing warning is not an unconditional marker any more; it is a set of its own.
    expect("Waiting on BotThread" not in H.FAILURE_MARKERS,
           "'Waiting on BotThread' is not in FAILURE_MARKERS")
    expect("Waiting on BotThread" in H.PACING_MARKERS,
           "'Waiting on BotThread' is in PACING_MARKERS")
    # Everything the marker scanner failed on before still fails.
    for marker in ("FATAL ERROR", "FATAL SIGNAL CAUGHT", "bind() failed",
                   "Couldn't load AAS file", "does not match its native definition"):
        expect(marker in H.FAILURE_MARKERS, f"{marker!r} stays an unconditional marker")

    # A network field written wider than its bits is a failure (game #67/#68).
    overflow = H.scan_failures(["Spawning entities\n", "WARNING: idBitMsg::WriteBits: value overflow 141 1\n"])
    expect([f["marker"] for f in overflow] == ["idBitMsg::WriteBits: value overflow"],
           f"a WriteBits value overflow is reported (got {[f['marker'] for f in overflow]})")

    # 1. An unconditionally fatal line is reported on a healthy run.
    fatal = H.scan_failures(["SpawnServer: valley\n", "FATAL ERROR: no game dll\n"])
    expect([f["marker"] for f in fatal] == ["FATAL ERROR"],
           f"healthy run still reports FATAL ERROR (got {[f['marker'] for f in fatal]})")
    expect(all(f["kind"] == "marker" for f in fatal),
           "an unconditional marker keeps kind='marker'")

    # 2. Healthy run with waits: the match finished, so the waits fail nothing...
    healthy = H.scan_failures(HEALTHY_WAITS)
    expect(healthy == [], f"a healthy run with waits reports no failure (got {healthy})")
    # ...but they are still recorded, with the lines that produced them.
    recorded = H.scan_pacing(HEALTHY_WAITS)
    expect([f["line"] for f in recorded] == [3, 5],
           f"scan_pacing records the wait lines 3 and 5 (got {[f['line'] for f in recorded]})")
    expect(all(f["kind"] == "pacing" for f in recorded), "recorded waits are tagged kind='pacing'")

    # 3. Negative control: the same log, and the run failed a gameplay check.
    failed = H.scan_failures(HEALTHY_WAITS, run_failed=True)
    expect([f["line"] for f in failed] == [3, 5],
           f"a failed run still reports both waits (got {[f['line'] for f in failed]})")
    expect(all(f["kind"] == "pacing" for f in failed),
           "the waits reported on a failed run keep kind='pacing'")

    # 4. Both kinds on one failed run: the fatal is reported whether or not the run failed.
    mixed = ["FATAL ERROR: boom\n", "WARNING: Waiting on BotThread\n"]
    expect([f["kind"] for f in H.scan_failures(mixed)] == ["marker"],
           "on a healthy run the fatal is reported and the pacing line is not")
    expect([f["kind"] for f in H.scan_failures(mixed, run_failed=True)] == ["marker", "pacing"],
           "on a failed run both the fatal and the pacing line are reported, in log order")

    # 5. The ERROR banner keeps its classification (common->Error drop, not a fault signal).
    banner = ["********************\n", "ERROR: Thread 'main' script error\n",
              "********************\n", "ERROR: plain drop\n"]
    kinds = [(f["marker"], f["kind"], f["line"]) for f in H.scan_failures(banner)]
    expect(kinds == [("ERROR banner", "script", 2), ("ERROR banner", "error", 4)],
           f"ERROR banners keep script/error tags and line numbers (got {kinds})")

    # 6. The production path in test_all_stock_maps, driven by the same synthetic log:
    #    a healthy map passes with waits recorded, a map that failed a gameplay check
    #    still fails load.failure_markers and names the waits.
    import test_all_stock_maps as M  # noqa: E402  (import only; nothing runs)

    healthy_map = M.MapResult("sewer", False)
    healthy_map.check("bots.gdf.displacement", M.PASS, "max per-life 2D displacement 1520.0u")
    healthy_map.classify_failures(HEALTHY_WAITS)
    expect(healthy_map.passed and healthy_map.metrics["bot_thread_waits"]["count"] == 2,
           f"a healthy map with waits passes and records them (checks {healthy_map.checks})")

    failed_map = M.MapResult("sewer", False)
    failed_map.check("bots.gdf.displacement", M.FAIL, "max per-life 2D displacement 3.0u")
    failed_map.classify_failures(HEALTHY_WAITS)
    marker = next((c for c in failed_map.checks if c["name"] == "load.failure_markers"), None)
    expect(not failed_map.passed and marker is not None and marker["status"] == M.FAIL
           and "Waiting on BotThread" in marker["detail"],
           f"a failed map reports the waits in load.failure_markers (got {marker})")

    # The think.waits gate itself (not just load.failure_markers): a healthy map with
    # waits passes, a map that failed a gameplay check reports them, and no waits never
    # fails. This is the half a bug could invert without the cases above noticing.
    healthy_gate = M.MapResult("sewer", False)
    healthy_gate.check("bots.gdf.displacement", M.PASS, "max per-life 2D displacement 1520.0u")
    expect(healthy_gate.pacing_gate_passes(30),
           "a healthy map with 30 waits passes think.waits")
    failed_gate = M.MapResult("sewer", False)
    failed_gate.check("bots.gdf.displacement", M.FAIL, "max per-life 2D displacement 3.0u")
    expect(not failed_gate.pacing_gate_passes(30),
           "a map that failed a gameplay check reports its 30 waits in think.waits")
    expect(failed_gate.pacing_gate_passes(0),
           "no waits never fails think.waits")

    # A pacing-only failure never counts as the gameplay failure that would gate it.
    pacing_only = M.MapResult("sewer", False)
    pacing_only.check("think.waits", M.FAIL, "waits=30 at end of window")
    expect(not pacing_only.gameplay_failed(),
           "gameplay_failed() ignores the two pacing gates (no circularity)")

    if failures:
        print(f"\n{len(failures)} check(s) failed")
        return 1
    print("\nPASS: pacing warnings are recorded always and fail only a failed run; "
          "the other markers and the ERROR-banner classification are unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
