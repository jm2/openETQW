#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The simulation does not depend on what is in freshly allocated memory (#427).

high-refresh-determinism-listen failed now and then at tick 113 (two game events
fewer or more, from then on). The cause was not the frame rate: the titan the replay
drops in at map start reads its physics' ground normal before its first ground contact
(sdWalkerControl::UpdateSlidingFoot), and idPhysics_Monster never initialised it, so
the walker's foot-dust effect at tick 19 played or not as the heap's leftovers said,
and with it the effect's removal at tick 113. Meson's random MALLOC_PERTURB_ made that
look intermittent; a fixed fill makes it repeatable. The game module now starts the
ground trace empty.

The listen-host replay (test_high_refresh_determinism.py's schedule, g_determinismTest
1, com_maxFPS 60) runs twice, with glibc filling new allocations with two different
bytes (MALLOC_PERTURB_ 85 and 170; before the fix 85 gave 1874 events at tick 113 and
170 gave 1876), each on a fresh profile:
  H1 both runs print SIM_HASH on the same 150 or more ticks
  H2 every tick's SIM_HASH (positions, health, objectives and the serviced event count)
     is the same in both

glibc only: elsewhere MALLOC_PERTURB_ does nothing, and the test skips (exit 77); so
does an AddressSanitizer build, whose allocator ignores it.

Usage: test_determinism_heap_fill.py [--port N] [--log-dir DIR]
Exit status: 0 pass, 1 fail, 77 skip (not glibc, no client binary or no retail data).
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as h  # noqa: E402
import test_high_refresh_determinism as replay  # noqa: E402

FILLS = (85, 170)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=29968)
    ap.add_argument("--log-dir", type=Path)
    args = ap.parse_args()

    if not sys.platform.startswith("linux") or platform.libc_ver()[0] != "glibc":
        print("[SKIP] MALLOC_PERTURB_ is a glibc feature; this host has no glibc")
        return h.EXIT_SKIP
    if not replay.CLIENT_BIN.is_file():
        print(f"[SKIP] {replay.CLIENT_BIN} does not exist (the listen host needs the client binary)")
        return h.EXIT_SKIP
    # ASan's allocator ignores MALLOC_PERTURB_: both runs would see the same memory
    if b"__asan_init" in replay.CLIENT_BIN.read_bytes():
        print("[SKIP] an AddressSanitizer build: its allocator ignores MALLOC_PERTURB_")
        return h.EXIT_SKIP
    missing = h.missing_required_paks(replay.DATA_PATH)
    if missing:
        print(f"[SKIP] Retail 1.5 data incomplete at {replay.DATA_PATH / 'base'} (missing: {', '.join(missing)})")
        return h.EXIT_SKIP
    h.install_signal_handlers()

    runs = {}
    old_fill = os.environ.get("MALLOC_PERTURB_")
    try:
        for fill in FILLS:
            os.environ["MALLOC_PERTURB_"] = str(fill)
            # a fresh profile for each run, so the two differ only in the heap's fill
            replay.SAVEPATH = h.private_savepath(f"etqw-heap-fill-{fill}-")
            lines: list[str] = []
            try:
                hashes, _, lines = replay.run_determinism_schedule(
                    fps=60, bin_path=replay.CLIENT_BIN, expected_min_ticks=150, timeout_sec=50.0,
                    listen_port=args.port, output_lines=lines)
            finally:
                if args.log_dir is not None:
                    args.log_dir.mkdir(parents=True, exist_ok=True)
                    (args.log_dir / f"heap-fill-{fill}.log").write_text("\n".join(lines) + "\n")
            runs[fill] = hashes
    except Exception as e:  # a schedule that raised (too few ticks, a timeout, a crash)
        print(f"FAIL: {type(e).__name__}: {e}")
        return 1
    finally:
        if old_fill is None:
            os.environ.pop("MALLOC_PERTURB_", None)
        else:
            os.environ["MALLOC_PERTURB_"] = old_fill

    failures: list[str] = []
    a, b = (runs[fill] for fill in FILLS)
    if sorted(a) != sorted(b) or len(a) < 150:
        failures.append(f"H1 the runs logged different ticks ({len(a)} and {len(b)})")
    differing = [t for t in sorted(set(a) & set(b)) if (a[t].crc_hash, a[t].ev) != (b[t].crc_hash, b[t].ev)]
    for t in differing[:5]:
        print(f"  tick {t}: fill {FILLS[0]} hash=0x{a[t].crc_hash} ev={a[t].ev}; "
              f"fill {FILLS[1]} hash=0x{b[t].crc_hash} ev={b[t].ev}")
    if differing:
        failures.append(f"H2 SIM_HASH differs on {len(differing)} ticks, from tick {differing[0]}")

    print(f"  {'ok  ' if not failures else 'FAIL'} SIM_HASH on {len(set(a) & set(b))} ticks under fills {FILLS}")
    for f in failures:
        print(f"FAIL: {f}")
    if failures:
        return 1
    print("PASS: the replay is the same whatever new memory holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
