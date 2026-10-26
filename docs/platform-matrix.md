<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Platform matrix (M7 release gate)

What has actually been verified on each platform, with the host, the evidence level and the
exact commits. `STATUS.md` § Capability matrix is the broader grid of what is known to work;
this file is the release-gate evidence register. A row is added only by a session that read
the raw log or the PR it points at. Historical rows stay in their own table and are not
evidence for `main` today.

Last updated: 2026-10-26 (UTC). `main` at the time of writing: engine `6361385` (#283 merged).

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
| M3 `weapon-fire-observer`: PASS, exit 0, 8/8/8 tracers, `playerPos=24`, 24 samples, 3D drift 0.00 u | macOS arm64 (macOS 27.0.1, arm64) | `mbp16-m1max` | runtime (map-loaded); SDL offscreen software raster, so not GPU/display/pacing evidence | `a3b0db1` (test-only; binaries `be49e04`, test blob `c2e3061d`) / `7fbdf80` | retail 1.5 overlay | PR #283; `mbp16-m1max:/private/tmp/oe-m3-observer-20261025/m3fix-console.log` (sha256 `1aa32b8c`) |
| M4 six existing tests (`damage-kills`, `classes`, `deployable`, `match-end`, `stopwatch`, `campaign`): 6/6 OK, exit 0 | macOS arm64 | `mbp16-m1max` | runtime (match-tested) | `a38e311` / `bb8e0c5` | retail 1.5 overlay | `mbp16-m1max:/private/tmp/oe-m2/engine-m4/build/meson-logs/m4-mac-arm64.txt` (Ok 6, Fail 0) |
| M2 `m2-client-collision`: 1/1 OK (52.03 s), control leg 550.0 u, 6-sample spread 0.030 u, client's own copy 0.01 u from the server's, final speed 0.00 u/s, exits 0/0 | macOS arm64 | `mbp16-m1max` | runtime (map-loaded) | `6dc1124` (engine head, base `a38e311`; test commit `75f4bd9`) / `bb8e0c5` | retail 1.5 overlay | `mbp16-m1max:/private/tmp/oe-m2/engine-m2c2/build/meson-logs/m2-client-collision-mac-final.txt` (sha256 `2428af63`) |
| M2 `m2-render-world-parity`: 1/1 OK (26.10 s), both processes exit 0 | Linux x86_64 | `antec` | runtime (map-loaded; trace parity only, not GPU raster) | `146e44e` / `8a13028` | retail 1.5, valley | PR #178 and game #60; the earlier evidence is recorded in `docs/queue.md` §3 item 8. Merged run: OK in 22 s inside a 69/70 retail suite on antec's RX 6400 (2026-10-22) |
| M3 `weapon-fire-observer`: PASS, exit 0, 8/8/8 tracers, `playerPos=24`, 24 samples, 3D drift 0.00 u, exits 0/0/0 | Windows 11 x64 (10.0.26300, MSVC 19.51) | `antec-win` | runtime (map-loaded) | `9818c52` / `7fbdf80` | retail 1.5 | PR #283; `antec-win:.tmp\swk\engine-m3\builddir-swk\meson-logs\weapon-fire-observer\` (server/shooter/observer). Default ninja backend, built by running the exact command lines `ninja -t commands all` prints (ninja itself stalls after one object in that sandbox); each test was then run as the exact python command line Meson registers. `meson test` aborts before running anything (its `testlog.txt` records Ok 0 / Fail 0), so the result is the test\'s own output, with no Meson timeout accounting |
| M2 `m2-render-world-parity`: PASS, exit 0, probe/prone/radiuspush/exit relationships as recorded, exits 0/0 | Windows 11 x64 | `antec-win` | runtime (map-loaded); listen side is a real Vulkan client (swapchain 1280x720), not llvmpipe | `9818c52` / `7fbdf80` | retail 1.5 | PR #283; `antec-win:.tmp\swk\engine-m3\builddir-swk\meson-logs\m2-render-world-parity\` (dedicated, listen). Same build/run caveat as the M3 row |
| M4 `m4-bot-match` + `m4-offcampaign-match`: 2/2 PASS, exit 0 (30.0 s time limit, no objectives, gamereview reached) | Windows 11 x64 | `antec-win` | runtime (match-tested) | `9818c52` / `7fbdf80` | retail 1.5 | PR #283; `antec-win:.tmp\swk\engine-m3\builddir-swk\meson-logs\{m4-bot-match,m4-offcampaign-match}\`. Same build/run caveat as the M3 row |
| M3 `weapon-fire-observer` 8/8/8 tracers, 3D drift 0.00 u; M2 `m2-render-world-parity`; M4 bot + offcampaign match: 4/4 PASS, exit 0, zero skips | Windows 11 arm64 (10.0.26200, Snapdragon X / Adreno X1-85) | `yoga-slim7x` | runtime (match-tested for the M4 pair, map-loaded for M3/M2); M2 listen side is a real Adreno Vulkan window and render world (raster; no RT), so M2 is GPU evidence here | tree `bada9b03` = the published tree of `9818c52` / `7fbdf80` | retail 1.5 | PR #283; `yoga-slim7x:.tmp\swk-work\openETQW\builddir-vs\meson-logs\{weapon-fire-observer,m2-render-world-parity,m4-bot-match,m4-offcampaign-match}\`. Built with Meson's VS backend (`meson setup --backend=vs`) and `msbuild /m:1 /p:Configuration=debug /p:Platform=arm64`; each test was then run as the registered python command line, because ninja hangs and `meson test` cannot open its pipe in that sandbox (so there is no `testlog.txt` result and no Meson timeout accounting) |

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
| M4 `m4-bot-match` + `m4-offcampaign-match` (merged as #199): 2/2 OK | macOS arm64 | `mbp16-m1max` | runtime (match-tested) | first run `6d43788` / `7fbdf80`; review-fix rerun `5e12932` / `7fbdf80` | retail 1.5 | PR #199 and `docs/queue.md` §5:234 |

## Not a pass

These runs are recorded so they are not mistaken for gate evidence.

| Run | Platform | Host | Result | Why it is not a pass |
|---|---|---|---|---|
| Full retail suite, 2026-10-21, engine `0205686` | Linux arm64 | `macmini-m1` | 51 OK, 16 FAIL, 1 SKIP of 68 | The sandbox saw only llvmpipe (no `/dev/dri`). The 16 failures are the graphics/timing and client tests: `high-refresh-determinism`, `-listen`, `turn-rate-listen`, `seat-gate`, `campaign-client-maps`, `client-network-join`, `client-network-listen`, `frame-poses-join`/`-listen`/`-devmap`, `seat-poses-devmap`/`-join`/`-listen`, `vehicle-view-net`, `aircraft-reprediction`, `mixed-caps`. The skip is `cross-os` (exit 77, no server). Log: `macmini-m1:build/agent-work/engine/.tmp/suite-0205686/final/retail-0205686.txt` |
| Full retail suite, 2026-10-22, engine `99261aa` (code; `fb45cea` adds only the manifest) / game `e4c6d3f` | Linux arm64 | `macmini-m1` | 64 OK, 4 FAIL, 1 SKIP of 69 | Real GPU under `swk-gpu-run`, but not clean: `frame-poses-join`/`-listen`/`-devmap` fail only the cap-240 A1 turn phase (219.6 / 218.7 / 220.4 fps against 235.2; 60 and 120 pass), and `client-lifecycle` hit ALSA "Host is down" (passes 1/1 with `SDL_AUDIODRIVER=dummy`). The skip is `cross-os`. Logs: tests 1–61 `oe-full-suite-fb45cea-GPU3.log`, tests 62–69 `oe-suite-tail2-GPU.log` |
| Full suite attempt at `bb82aef` / `e4c6d3f`, 2026-10-22 | Linux arm64 | `macmini-m1` | 43/69 completed, then the exec session was killed | No final result; every completed test was OK or the `cross-os` SKIP, including both GUI tests. Log: `oe-suite-bb82aef-GPU.log`. Do not cite as a suite result |
| Full suite attempt at `fb45cea`, 2026-10-21 | Linux arm64 | `macmini-m1` | 27/69 completed, then interrupted | No final result. Log: `oe-full-suite-fb45cea-INTERRUPTED-27of69.log` |
| M3 `weapon-fire-observer`, macOS arm64, engine `be49e04` / game `7fbdf80` | macOS arm64 | `mbp16-m1max` | 1/1 FAIL, exit 1, 48.09 s | The test's equipment check still required the whole kit to equal `{inventory/weapons/sniperrifle}`, and GDF covertops also carried `inventory/tools/gdf_parachute` on this spawn. The check was fixed in `a3b0db1`; this is the failure that found the bug. Log: `mbp16-m1max:/private/tmp/oe-m3-fresh-20261021/engine-equipped/build/meson-logs/m3-weapon-fire-mac-37e3c8c.txt` (sha256 `0c5a9ef6`) |
| M3 `weapon-fire-observer` at test revision `ed6735b7`, two runs, engine `1ff001f` / game `bb8e0c5` (withdrawn) | macOS arm64 | `mbp16-m1max` | PASS lines, exit 0, but withdrawn as evidence | The revision hardcoded client 0 (the observer, not the shooter) and measured 2D drift; the revised test identifies the shooter by team and needs 3D drift. Receipt counts 6/6/6 and `playerPos=48` stand only as history. Logs: `mbp16-m1max:/private/tmp/oe-162.YFvqlt/m3-mac.out` and `m3-mac-run2.out` (sha256 `57e45a8d`) |

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
