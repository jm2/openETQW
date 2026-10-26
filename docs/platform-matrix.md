<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Platform matrix (M7 release gate)

What has actually been verified on each platform, with the host, the evidence level and the
exact commits. `STATUS.md` § Capability matrix is the broader grid of what is known to work;
this file is the release-gate evidence register. A row is added only by a session that read
the raw log or the PR it points at. Historical rows stay in their own table and are not
evidence for `main` today.

Last updated: 2026-10-25. `main` at the time of writing: engine `f4d3d74`.

## Evidence levels

- **runtime**: the thing ran on that host and its own assertions or checks were read. The
  `CONTRIBUTING.md` level is given in parentheses when it is narrower (`map-loaded`,
  `match-tested`).
- **outcome-only**: an outcome was observed but not asserted by a test (a manual session, a
  screenshot review, a one-off measurement).
- **static**: no game execution; source, build, scan or validation checks only.

A skip (exit 77) is not a pass. A run on llvmpipe with no `/dev/dri` is not GPU evidence;
real-GPU work on the Linux hosts uses `swk-gpu-run` (`AGENTS.md` § GPU runs).

## Current rows

| Verified thing | Platform (OS/arch) | Host | Evidence | Engine / game | Data set | Where the raw log or PR lives |
|---|---|---|---|---|---|---|
| `gui-load-error` + `gui-hud-reload-error`, 2/2 OK (20.88 s, 18.92 s) | Linux arm64 (Fedora 44 Asahi) | `macmini-m1` | runtime (map-loaded) | `99261aa` / `e4c6d3f` | retail 1.5, `~/etqw_data` | PR #177 and game #59; `macmini-m1:build/agent-work/logs/51-fb45cea/oe-gui-both-final5.log` |
| `openetqw_validate.py pr` PASSED (linux arm64, debugoptimized) | Linux arm64 | `macmini-m1` | static | `bb82aef` / `e4c6d3f` | n/a (staged payload and smoke) | `macmini-m1:build/agent-work/logs/oe-validate-pr-bb82aef.log`; the same check PASSED at `99261aa` in `logs/51-fb45cea/oe-validate-pr-final5.log` |
| `check_python.py` (pyflakes) | Linux arm64 | `macmini-m1` | static | `bb82aef` | n/a | `macmini-m1:build/agent-work/logs/oe-check-python-bb82aef.log`: 94 files, 0 blocking, 86 advisory findings |
| `check_include_case.py` | Linux arm64 | `macmini-m1` | static | `bb82aef` | n/a | `macmini-m1:build/agent-work/logs/oe-checks-bb82aef.log`: 0 case-mismatched includes |
| `check_integration_skips.py` | Linux arm64 | `macmini-m1` | static | `bb82aef` | n/a | `macmini-m1:build/agent-work/logs/oe-checks-bb82aef.log`: 37/38 Meson integration tests and 20/20 standalone scripts skip cleanly; 1 violation, `tests/test_client_server_drop.py` (exit 2 instead of 77 without data) |
| M3 `weapon-fire-observer`: PASS, exit 0 (42 s), 8/8/8 tracers, 3D drift 0.00 u | Linux arm64 | `macmini-m1` | runtime (map-loaded) | `a3b0db1` / `7fbdf80` | retail 1.5, `~/etqw_data` | PR #283; `macmini-m1:build/agent-work/logs/m3-weapon-fire-a3b0db1-arm64.log` and `build/agent-work/tmp/m3-standalone-logs-3/` |
| M2 `m2-render-world-parity`: 1/1 OK (26.10 s), both processes exit 0 | Linux x86_64 | `antec` | runtime (map-loaded; trace parity only, not GPU raster) | `146e44e` / `8a13028` | retail 1.5, valley | PR #178 and game #60; the earlier evidence is recorded in `docs/queue.md` §3 item 8. Merged run: OK in 22 s inside a 69/70 retail suite on antec's RX 6400 (2026-10-22) |

## Historical rows

| Verified thing | Platform (OS/arch) | Host | Evidence | Engine / game | Data set | Where the raw log or PR lives |
|---|---|---|---|---|---|---|
| Full retail suite, 44/44 OK, nothing skipped (first arm64 build and run) | Linux arm64 | `macmini-m1` | runtime (map-loaded) | `00c5fc9` / `5815431` | retail 1.5 | `STATUS.md` § Verification, "Full retail-data suite, Linux arm64, the first runs (44 tests)" (2026-10-04); the second run's `build/meson-logs/testlog.txt` is on `macmini-m1` |
| Full retail suite, 34/34 OK | macOS arm64 (macOS 27, MoltenVK) | `mbp16-m1max` | runtime (map-loaded) | `cba54bb` plus a test-only include-order fix / `2485998` | retail 1.5 | `STATUS.md` § Verification, "Full suite, 1.5 data, macOS on main after the Windows merge (34 tests)" (2026-09-29) |
| Full retail suite, 34/34 OK | Linux x86_64 | `antec` | runtime (map-loaded) | `cba54bb` / `c4d1619` | retail 1.5 | `STATUS.md` § Verification, "Full retail-data suite, Linux x86_64, the installable engine and CI/release parity" (2026-09-30) |
| Full retail suite, 42/43 OK optimized | Windows 11 x64 | `antec-win` | runtime (map-loaded) | `a1428cf` / `8710165` | retail 1.5 | `STATUS.md` § Verification, "Full retail-data suite, Windows 11 x64, main 506fb29 (debug, 40 tests) and a1428cf (optimized, 43 tests)" (2026-10-01); the one failure is `mixed-caps`, whose spawn-spot assertion named the Mac's valley spots, not this host's |
| Full retail suite, 48/49 OK | Windows 11 arm64 | `yoga-slim7x` | runtime (map-loaded) | `e3244b2` plus #87 `8de6f8e` / `81f43a5` | retail 1.5 | `STATUS.md` § Verification, "Full retail-data suite, Windows 11 arm64, main after #59, #65, #71 and #72 (49 tests)" (2026-10-07); the failure is `all-stock-maps`' `salvage` AAS-area flake under a shared host |
| X1–X4 cross-OS, macOS arm64 ↔ Linux arm64, both ways | macOS arm64 and Linux arm64 | `mbp16-m1max`, `macmini-m1` | runtime (map-loaded) | `318b638` / `a5fdfaf` | retail 1.5, valley then outskirts | Issue #144 comment (2026-10-20, 18:15–18:19 EDT): `mbp16:/var/tmp/xos144/legB-client.out`, `mbp16:/var/tmp/xos144/legB-logs/`, and `macmini-m1:~/openetqw-evidence/m3arm-2026-10-20/mac/legA-clients/`; one X3 client-1 walk reads 397.3 u on the server's `playerPos` copy against 423.2 u on the client (first walk after the reconnect); the other five walks agree within 0.1 u |
| X1–X4 cross-OS, three desktop pairs, both ways | Linux x86_64, Windows 11 x64, macOS arm64 | `antec`, `antec-win`, `mbp16-m1max` | runtime (map-loaded) | `33d2018` / `28be6b6` | retail 1.5, valley then outskirts | `STATUS.md` § Capability matrix, cross-OS rows (2026-10-16): join, walks both ways agreeing to 0.00 u with the server's `playerPos`, reconnect, and the map change to outskirts |

## Not a pass

These runs are recorded so they are not mistaken for gate evidence.

| Run | Platform | Host | Result | Why it is not a pass |
|---|---|---|---|---|
| Full retail suite, 2026-10-21, engine `0205686` | Linux arm64 | `macmini-m1` | 51 OK, 16 FAIL, 1 SKIP of 68 | The sandbox saw only llvmpipe (no `/dev/dri`). The 16 failures are the graphics/timing and client tests: `high-refresh-determinism`, `-listen`, `turn-rate-listen`, `seat-gate`, `campaign-client-maps`, `client-network-join`, `client-network-listen`, `frame-poses-join`/`-listen`/`-devmap`, `seat-poses-devmap`/`-join`/`-listen`, `vehicle-view-net`, `aircraft-reprediction`, `mixed-caps`. The skip is `cross-os` (exit 77, no server). Log: `macmini-m1:build/agent-work/engine/.tmp/suite-0205686/final/retail-0205686.txt` |
| Full retail suite, 2026-10-22, engine `99261aa` (code; `fb45cea` adds only the manifest) / game `e4c6d3f` | Linux arm64 | `macmini-m1` | 64 OK, 4 FAIL, 1 SKIP of 69 | Real GPU under `swk-gpu-run`, but not clean: `frame-poses-join`/`-listen`/`-devmap` fail only the cap-240 A1 turn phase (219.6 / 218.7 / 220.4 fps against 235.2; 60 and 120 pass), and `client-lifecycle` hit ALSA "Host is down" (passes 1/1 with `SDL_AUDIODRIVER=dummy`). The skip is `cross-os`. Logs: tests 1–61 `oe-full-suite-fb45cea-GPU3.log`, tests 62–69 `oe-suite-tail2-GPU.log` |
| Full suite attempt at `bb82aef` / `e4c6d3f`, 2026-10-22 | Linux arm64 | `macmini-m1` | 43/69 completed, then the exec session was killed | No final result; every completed test was OK or the `cross-os` SKIP, including both GUI tests. Log: `oe-suite-bb82aef-GPU.log`. Do not cite as a suite result |
| Full suite attempt at `fb45cea`, 2026-10-21 | Linux arm64 | `macmini-m1` | 27/69 completed, then interrupted | No final result. Log: `oe-full-suite-fb45cea-INTERRUPTED-27of69.log` |

## Platform coverage

| Platform | Latest full retail suite on record | Date | Commits | Result |
|---|---|---|---|---|
| Linux arm64 | 69 tests | 2026-10-22 | `99261aa` / `e4c6d3f` | 64 OK, 4 FAIL, 1 SKIP; not clean (see above) |
| Linux x86_64 | 69/70 with M2 (#178) | 2026-10-22 | merged #178 / game #60 | 69 OK, 1 FAIL (details in #178) |
| Windows 11 x64 | 43 tests, optimized | 2026-10-01 | `a1428cf` / `8710165` | 42 OK, 1 FAIL (`mixed-caps` spawn-spot assertion) |
| Windows 11 arm64 | 49 tests | 2026-10-07 | `e3244b2` + `8de6f8e` / `81f43a5` | 48 OK, 1 FAIL (`all-stock-maps` AAS flake) |
| macOS arm64 | 34 tests | 2026-09-29 | `cba54bb` / `2485998` | 34 OK |

The M7 gate stays open: there has been no combined five-platform run at current `main`
(`f4d3d74`) covering the merged work from #220 to #276. Every row above is host-local
evidence for the commit named in it, not for `main` today.

## Adding a row

1. Run the thing on your own host and read the raw log; do not add a row from a report.
2. Put the exact engine and game commits, the data set, the host and the raw-log path or PR
   in the row. A skip, an aborted run and an llvmpipe run go in "Not a pass", never here.
3. If the run is at a commit that is no longer `main`, put it under "Historical rows" and
   give the date.
4. Sign the pull request with your session name and `(Switchworks)`.
