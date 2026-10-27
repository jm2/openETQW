<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# Work queue

The shared to-do list for every session. Coordinator log: [#133](https://github.com/jm2/openETQW/issues/133); see its latest comment for who coordinates now (coordination §2). Take items from the top. The coordinator keeps it
in order, and every PR that changes an item's state updates its row. `owner` names the
session that holds the item; `—` means nobody does. Before taking an item, check its PR or
issue for anything newer than this file.

**Parked (2026-10-25).** Owner, 2026-10-24: "burn down all open issues and in-progress PRs across all sessions" (coordination §9). That burn-down is done: #220–#276 merged (§5), and no PR is open in either repository. Owner, 2026-10-25: finish the open issues and PRs, update the docs and park every session. The open issues left (§2) are unowned or wait on the owner. Owner, 2026-10-25: "focus specifically on bug fixes rather than large feature requests from the issues"; "small features are ok if they're easy wins, just not long/large feature efforts".

**Resumption point (2026-10-25, all openETQW sessions stopped for quota).** Any session,
including Switchworks or DeepSeek ones, can resume from this file and `main` alone:
- **State:** `main` has everything. No PR is open in either repository, no session holds a
  host claim, and no openETQW process is running. The last coordinator was
  `openETQW-linux`; pick the next one by `docs/coordination.md` §2, and log on #133.
- **Unmerged work on branches:** only `fix/instance-distance-cull-246` (openETQW-win). It adds
  a per-instance distance cull for OpenGL and ray tracing, and is part of the deferred #246
  feature, so leave it until the owner reopens #246. Every other non-`main` branch in both
  repositories is older and either merged or abandoned; don't resume from it.
- **Superseded in part, 2026-10-26:** one PR is open ([#287](https://github.com/jm2/openETQW/pull/287), §1) and §2
  now lists eleven issues in nine rows; the tree state below is otherwise as described.
- **Next work, in order:**
  1. the combined run below — its retail suite has been run once as the M7 register run but
     no leg of it is a clean pass, and what is left is a fresh pass at current `main` plus the
     screenshot sets;
  2. #197, the owner's check;
  3. §3's milestone items.
  Owner direction (2026-10-25): bug fixes first; small easy features are fine; no large
  feature efforts.
- **Before touching rendering,** read `docs/render-path.md`; before comparing with retail,
  `docs/retail-comparison.md`. Per-platform traps are in `docs/coordination.md` §5.
- **The merge procedure** is `docs/coordination.md` §8. The coordinator's helper that ran
  it was a local script, not in the repository. Its steps:
  1. merge `main` into the PR branch, taking `main`'s `docs/queue.md` and
     `docs/provenance-manifest.json` on conflict, and resolving any code conflict by hand;
  2. add the PR's line to §5;
  3. regenerate the manifest with
     `tools/build/provenance_post_split.py --game-repo <game checkout at the pin> --update`
     and check that it reports 0 files missing;
  4. push, wait about 5 s, then `gh pr merge N --merge --match-head-commit <head>`;
  5. delete the branch.

**Outstanding gate work: the combined run.** Owner, 2026-10-23: full retail suites and screenshot sets wait for one combined run at the tail end, so the kisakcod sessions keep priority on shared hosts. The retail suite has been run: the five-platform 77-test run behind `docs/platform-matrix.md` (§3 item 6) is that run, made at engine `0a44150` / game `7fbdf80`, and **none of its five legs is a clean pass** (6, 3, 5, 32 and 17 failures), each attributed row by row. Still outstanding, and not implied by it: the **screenshot sets from all five platforms** (never taken), and a **fresh suite at current `main`** — `main` has carried source and test changes since `0a44150` (#292 `51f7449`, #297, #301), so the M7 rows are evidence for `0a44150` only. Per-platform legs at current `main` are the next runs; file an issue for any regression.

Updated: 2026-10-27 by `openETQW-linux` (coordinator): #301, #297, #303, #304, #307 and #287 merged (§5), so `main` is `ce40c41`; §1 is empty; §2 lists the eleven open issues in nine rows; §3 items 4, 6 and 7 updated; §4 gains the BotThread-marker rule (#294); the combined run and screenshot work re-stated at the top.
Updated: 2026-10-25 by `openETQW-linux` (coordinator): §1 empty; §2 is #197 and #246 (#270 fixed by #275); #220–#276 in §5, #268 added.
Updated: 2026-10-23 by `openETQW-linux` (coordinator): the combined run merged #202, #204, #206, #207, #210, #213 and #215 (§5). Earlier: #180 (merged 2026-10-22), #198, #199 and #200 are in §5; M4's last item is done.
Updated: 2026-10-22 by `openETQW-linux` (coordinator): the game #51 row with the Linux arm64
evidence and the open owner ask, and the review state of §3 items 3, 8 and 9 and of #162.
Updated 2026-10-21 after the overnight work (§5); earlier that day, merged since 2026-10-18
were #145, #146, #149, #152, #153, #155–#158 and game #53–#56 (§5), and #159 and #160 were
filed. Before that, 2026-10-18, after a pass over every doc.

## 1. Open PRs, in merge order

| # | State | What |
|---|---|---|
| [#287](https://github.com/jm2/openETQW/pull/287) | open, no review yet | Test harness only: a Windows child's exit status is decoded into something readable (NTSTATUS, exception code) instead of a generic non-zero, and a fixed-port test pre-flights its port and any leftover `ded` before it starts. Addresses #288 and #289; the residual in #290 (27 test files spawn with bare `Popen`) is not in this PR. Owner-requested; nothing in it changes a test's assertions. |

Closed 2026-10-26 as superseded by #301, on the owner's word: [#282](https://github.com/jm2/openETQW/pull/282) (earlier re-cut: the same seven binaries, no framework, and it prunes manifest rows) and [#281](https://github.com/jm2/openETQW/pull/281) (the original branch). Their branches stay; #301 is the PR that carries the deletion.

## 2. Open issues, in priority order

| # | What | Owner | Next step |
|---|---|---|---|
| [#294](https://github.com/jm2/openETQW/issues/294) | `Waiting on BotThread` is a hard failure marker, so a slow host fails a healthy match (`m4-campaign`, and `all-stock-maps` on Windows arm64: sewer 5 marker lines / 30 waits, slipgate 2 / 2) | owner (§4) | The marker cannot tell a starved bot thread from a wedged one — waits seen are 2, 4 and 30 and all three matches finished normally. Owner picks: drop the marker, keep it but gate it on outcome, or keep the count bound. No session may edit `FAILURE_MARKERS` first. |
| [#197](https://github.com/jm2/openETQW/issues/197) | Fresh profile goes straight to the menu as "Player" (owner, 2026-10-23: fine if the name can be changed in-game) | owner | `ui_name` persists across restarts (checked on macOS). Left: the owner's 30-second real-keyboard check: Options > Game Settings > Name, type a name, Apply, restart, look at the field. |
| [#302](https://github.com/jm2/openETQW/issues/302) | Linux x64 client frame work looked 3-5x Windows on the same GPU | `openETQW-linux` | Measured 2026-10-26: it was not a 3-5x OS gap, and no test bound fails. Three differences, in order of size: `tests/test_client_network.py:62` starts the client with `r_vkValidation 1` (engine default 0), and the Khronos layer costs Linux +17.5 ms a frame (`work_p50` 4.0 to 21.5 ms at `-O2`) against Windows' +3.3; with the layer still on, `client-network-join` at `-O2` passes at 46 GUI/s against the 40/s floor (15% headroom, so a slower Linux host would be close to it); the Linux perf tree was `buildtype=debug` (`-O0`), which alone is 9.2 ms against 3.9 ms at `-O2`; and Windows x64 defaults `r_rayTracing 1` while Linux has no ray-query path at all (#306), so the first rows compared two lighting paths. Like-for-like (layer off, both `debugoptimized`, Windows `r_rayTracing 0`): Linux 240 GUI/s (245 uncapped) / `work_p50` 3.9-4.0 ms, Windows raster 232/s at cap 240 / 2.6 ms, with the same per-frame draw mix across the two validation-off runs (world 1,626 vs 1,721, shadow 100 vs 105, light 528 vs 548; MegaTex 141 vs 180 is the one mismatch and is not recomputed with the load window excluded). A **~1.5x `work_p50` residual survives** — Linux work-bound, Windows cap-bound at 240 — and it is unfiled until a `-O2` profile of the frame body exists on both sides; the `-O0` `eu-stack` profile (186 samples, 103 in `DrawView`, 8 in driver entry points, hot in `idVec3::operator-`, `memcpy`, `FindSilEdges`, `SetMappingForSurface`) only shows that `-O0` measures inline-heavy code. Driver split (RADV versus AMD 32.0.21045.5002) is not separable by these runs. |
| [#296](https://github.com/jm2/openETQW/issues/296) | Linux had no retail-data folder picker | — | Done in #297 (§3 item 4). Left: the interactive checklist on a desktop — start with no retail data, Cancel stores nothing, a refused folder re-asks, five refusals fail, an accepted one is written. Unrun: no agent here can open a dialog. Close when the owner has tried it. |
| [#298](https://github.com/jm2/openETQW/issues/298) | A failed `basepath.txt` write truncated the previous choice | — | Fixed in #297: `RetailDiscovery::WriteStoredPath` writes `basepath.txt.tmp` and renames it over the old file (`MoveFileExA` with `MOVEFILE_REPLACE_EXISTING` on Windows); `RetailDiscoveryTest.cpp:259-283` makes the folder read-only and asserts the write is refused, the previous content still reads back and no `.tmp` is left (skipped as root; Windows covers only the replace direction). Left: close it once its filer or the owner agrees the fix is what the issue asked for. |
| [#288](https://github.com/jm2/openETQW/issues/288), [#289](https://github.com/jm2/openETQW/issues/289), [#290](https://github.com/jm2/openETQW/issues/290) | Three harness diagnostics gaps from the Windows arm64 suite: no port/leftover-process pre-flight (#288), a hard crash reads as a generic non-zero exit (#289), and 27 test files spawn processes with bare `Popen` and bypass the pre-flight (#290) | `openETQW-win` | #287 addresses #288 and #289; #290 is the residual and follows it. |
| [#246](https://github.com/jm2/openETQW/issues/246) | Instanced trees and shrubs: they draw on both APIs since #248 (Vulkan) and #252 (OpenGL) | — | Deferred by the owner (2026-10-25) as a large feature: distant-tree imposters and the per-instance distance cull. Not to be taken until the owner reopens it. |
| [#133](https://github.com/jm2/openETQW/issues/133) | Coordinator log | `openETQW-linux` | Stays open. |
| [#306](https://github.com/jm2/openETQW/issues/306) | Linux and macOS clients have no ray-query lighting path; Windows x64 gets it by default | owner | `R_RayTracingRequested()` returns false on non-`_WIN64` (`src/renderer/draw_raytracing.cpp:683-688`) and `R_RayTracingInit()` returns before reading anything, so a Linux `+set r_rayTracing 1` logs `Vulkan ray tracing unavailable: r_rayTracing is disabled` and draws stencil shadow volumes instead. Owner picks: port the path behind a runtime capability check, or ledger the difference as a platform limit. Until then no Linux-vs-Windows frame comparison is like-for-like with Windows defaults. |

## 3. Milestone work, in order

Each item names its acceptance test. Details are in the linked plan.

1. **M5 step 14b, the hull lead** (`docs/hull-lead.md`). The code is in, off by default
   (ledger 067). A network client's and a listen host's `VEH_PRED` numbers per class are in
   §6 (owner: `openETQW-win`; merged as #157 with game #56). Left: the owner's review on a
   high-refresh display (owner-gated, §4).
2. **M5 step 14, what is left** (owner: `openETQW-win`): late look for `freepivot` views and the damped
   first-person `clampDamped*` view is merged (#152 with game #54). Left: the owner decides
   which seats join the default list (§4).
3. **M4, what is left**: done (#199, §5). Every M4 item listed in `STATUS.md` § Milestones now has a meson test.
4. **Retail data discovery** (`STATUS.md` § Next steps, item 7): the Linux folder picker
   is done, merged as `03d160c` (#297, issue #296): `Sys_PromptForRetailData` on Linux asks
   through the first helper of zenity, kdialog and yad that is installed, with the
   macOS/Windows rules — a Cancel stores nothing, an absent helper is skipped, and the
   folder is written once it is accepted. One difference is deliberate: Linux's refusal
   is a message dialog and then the folder dialog again, so only that dialog's Cancel
   ends the sequence, where macOS offers Quit and Windows Cancel at the refusal. The
   `retail-discovery` unit test covers each route's arguments, the helper's outcome, the
   printed answer and the stored path's replace, and the same PR fixed #298 by writing
   `basepath.txt` through a temporary file and a rename. The interactive check needs a
   desktop and is a five-step checklist on the PR; **it has not been run** — nobody in the
   fleet has a display. Left: a person trying the Linux, macOS and Windows pickers (owner),
   the yad refusal dialog included, which no host here can open.
5. **Provenance labels** (`docs/provenance.md` §6): done, merged as `a9aadd4` (#285,
   `docs/provenance-labels`). It relabels the five minizip files and `eaxguid.lib` as
   ThirdParty/Upstream and the four PDB/reconstruction-marked engine files as
   Reconstruction_retail_RE; the openQ4 and post-split rows are resolved. Deferred with
   reasons: jmarshall's licence (owner, release gate 1), a post-split CI gate
   (release gate 3), and the history/`1223dc5` labels (repository recreation). Acceptance:
   `provenance_post_split.py` reports no gap, the game's `check_spdx.py` passes, and the
   §6 rows are resolved or explicitly deferred.
6. **`docs/platform-matrix.md`** (release gate, M7): the evidence register exists
   (merged as `d3a4d5b`, #284; per row the verified thing, platform, host, evidence level, engine/game
   commits, data set and raw log or PR, with historical rows kept apart). A combined
   five-platform run of the 77 non-soak tests was made on 2026-10-25/26 at engine
   `0a44150` / game `7fbdf80`. Those rows are evidence for `0a44150` and nothing later:
   `main` is now `527c1ef` and has carried source and test changes since (#292's test fix
   `51f7449`, #297's picker, #301's deletions), so a fresh per-platform pass at current
   `main` is a new leg, not a re-read of these numbers. The run: Linux x64
   70 OK / 6 FAIL / 1 SKIP (real RX 6400), Linux arm64 73 / 3 / 1 (real GPU), Windows x64
   70 / 5 / 2, Windows arm64 43 / 32 / 2 (mostly sandbox and display/frame-pacing; 11 of the 12
   sandbox tests pass when re-run with `mkdtemp` replaced by `os.makedirs`, tests
   unmodified, and the twelfth `all-stock-maps` is #294), macOS arm64
   59 / 17 / 1 (software raster only, no GPU evidence). M7 stays open until the macOS
   leg is re-run and each platform's session has checked its own row: the owner's GPU
   broker is live on mbp16-m1max and `gpu-run metal-probe` returns `metal-device: OK
   name=Apple M1 Max headless=false` (2026-10-26), so that leg waits on allowlisting the
   suite probe, not on a decision, and the 59/17/1 software-raster row is what it replaces. The #292
   failures are fixed in `51f7449`; the M7 pacing failures were host load and pass on
   quiet reruns (#293 closed); #299 was closed as a measurement artifact — `test_client_network.py:62` turns on the
   Khronos validation layer and the perf tree `build/` was `buildtype=debug` (`-O0`) — so the 30-35 GUI/s
   was neither the product nor a reason to move the 40/s floor (closed 2026-10-26 with no test or
   floor change). #302 is measured: layer off and both
   sides `debugoptimized`, Linux 240 GUI/s / `work_p50` 3.9-4.0 ms against Windows raster 232/s at cap
   240 / 2.6 ms, same draw mix, so a ~1.5x frame-work residual survives and fails nothing. Left: a
   `-O2` profile of the frame body on both sides, and #306 (Linux and macOS have no ray-query path,
   Windows defaults to it), which no test change addresses either way. Any perf row in this register
   must name its build directory: `build/` on antec is `buildtype=debug`. #294 (the Windows arm64 BotThread marker) is an
   owner decision in §4, and the
   rest of the Windows arm64 failures are environment. Linux arm64 has since been re-run at `527c1ef` (post-merge, `-O2`, real GPU):
   76 OK / 0 FAIL / 1 SKIP, the skip `cross-os`. That is a separate row in `platform-matrix.md`
   and does not close the M7 arm64 row at `0a44150`.
7. **Release gate 5, third-party binaries:** done, merged as `493bed2` (#301, the re-cut of
   #282/#281) — the seven unreferenced Windows binaries (`openal32.lib`, both `eaxguid.lib`,
   zlib's four `contrib/masmx*/*.obj`) and the PowerPC `src/openal/osx/OpenAL.framework`
   binary are deleted, not replaced: nothing links them. The owner approved the deletion of
   all eight, including the previously owner-gated `src/sys/win32/eaxguid.lib`, on
   2026-10-26. `docs/provenance.md` §6 holds the evidence and `STATUS.md` § Release gate
   item 5 the record. The imported manifest rows stay as the census (deleted paths are not
   pruned), so the post-split count is unchanged.
8. **The M1 and M2 gaps** (`STATUS.md` § Milestones):
   - ~~M1: the `false` defaults of `build_engine` and `build_dedicated`~~: both default to `true` (#173);
   - M2: collision in the graphical client is never asserted;
   - M2: the headless render world is still stubbed. Its effects are recorded in ledger 013,
     and the parity test that measures them is merged: [#178](https://github.com/jm2/openETQW/pull/178)
     with game [#60](https://github.com/jm2/openETQW-game/pull/60) (2026-10-22; meson `m2-render-world-parity`,
     OK in 22 s in a 69/70 retail suite on antec's RX 6400). Earlier evidence, at `146e44e`: `m2-render-world-parity` 1/1 OK 26.10 s on antec
     x86_64 (llvmpipe/offscreen, not GPU raster), both processes exit 0, `openetqw_validate.py pr`
     PASSED, provenance 0 missing. Reviewed at
     Windows with a full assertion replay against the raw log and at Linux arm64 (no blocker;
     its one should-fix, the manifest entry, is fixed in `04b2610`).
     **It does not close the client-collision gap.** Both render worlds' `ModelTrace`/`Trace`/
     `FastWorldTrace` are stubs that answer false (`src/renderer/RenderWorld.cpp:787-799`,
     `src/sys/stub/headless_stubs.cpp:120-133`), so the prone-bot and `RadiusPush` scenes are
     stub parity by construction and only the vehicle-exit `PointInArea` scene can differ today.
     Render-model collision needs collisionModelManager/client-prediction coverage, not
     render-world traces. Its result is the evidence for the owner question in §4.

   Each gap gets a test or a recorded decision.
9. **M3, what is left** (`STATUS.md` § Milestones, M3):
    - ~~cross-OS pairs that include an arm64 host~~: done (#144). Linux arm64 (`macmini-m1`)
      passes X1–X4 both ways with Linux x64, Windows x64 and macOS arm64
      (issuecomment-6281426367, -6282183468, -6303053162, -6303056403);
    - ~~weapon fire seen by the other player~~: a headless client does receive it. Game
      main prints `EVENT_TRACER: server broadcast` on the server and `client received`
      on each client, and `weapon-fire-observer` requires the shooter and a second
      headless client to receive every broadcast with the same start and end.
      `clientClass covertops 1` selects the sniper through the class data; the test
      asserts the GDF SPAWN line's class=covertops, that the server's
      `g_debugWeaponState` diagnostic names `inventory/weapons/sniperrifle` for that
      client (item presence in `SPAWN_ITEMS` does not prove the option took), and that
      the shooter's own client number (client 1 on Linux arm64, not always 0) stays
      within 2 u in 3D. The equipped check compares items under `inventory/weapons/`
      only: GDF covertops also carries `inventory/tools/gdf_parachute` on some spawns
      (seen on macOS, not on either Linux run), and exact set equality failed on it.
      PR #283, branch `feat/m3-weapon-fire-observer` at a3b0db1 (after be49e04; base
      a38e311, which predates #178/#199), published; the a3b0db1 delta is 8/-2 in the
      equipped predicate. Test file 12,399 B, sha256 66ee9048.
      Reviewed: macmini on the e2070be2/534c375c text diffs and mbp16 on the a3b0db1
      delta, neither with a blocker (the one nit was the unconfirmed
      `set g_debugWeaponState -1`); yoga read it as a Windows program and found no
      path where a crashed or misbound run reports PASS.
      Passing legs: antec, Linux x86_64, game a5fdfaf (older than the pin), valley,
      retail 1.5 - shooter client 0, equipped sniper only, 8 broadcasts received by
      both clients, 24 `playerPos` samples, drift 0.00 u, exits 0
      (`/var/tmp/oe-m3-linux/run.log`); macmini-m1, Linux arm64 Fedora 44 Asahi, game
      7fbdf80 - shooter client 1, sniper only, 8/8/8, 24 samples, drift 0.00 u,
      exits 0 (`build/agent-work/logs/m3-weapon-fire-a3b0db1-arm64.log`); mbp16-m1max,
      macOS arm64, game 7fbdf80, SDL offscreen software raster (outcome evidence only,
      not GPU raster) - shooter client 1, sniper + parachute, 8/8/8, 24 samples,
      drift 0.00 u, exits 0. The earlier ed6735b7 runs (6/6/6 receipts) hardcoded
      client 0 and measured 2D drift on the observer; their receipt counts stand,
      their drift evidence does not.
      Windows x64 (antec-win, Windows 11 26300, MSVC 19.51, real Vulkan client) and
      Windows arm64 (yoga-slim7x, Windows 11 ARM64, Adreno X1-85 raster) both pass:
      8/8/8, 24 samples, drift 0.00 u, exits 0/0/0. Neither Windows host can run
      ninja or Meson's test runner in its sandbox, so both built with msbuild and
      ran the exact python command lines Meson registers: no testlog.txt and no
      Meson timeout/skip accounting (recorded, not glossed).
      Windows run procedure: check no leftover openETQW-ded process and that
      29974-29976 are free before each test. Meson's TerminateProcess on timeout
      skips the harness cleanup, so leftovers from one run poison the next; Windows
      prints "FATAL ERROR", not the POSIX "FATAL SIGNAL CAUGHT" the test greps for,
      so a hard crash shows only as an exit code (NTSTATUS). Both diagnostics are
      implemented in PR #287, `tests/etqw_harness.py` only: exit-status messages
      name the NTSTATUS or signal, and a pre-flight refuses to spawn when the port
      is bound (by a process this run does not hold) or a leftover `openETQW-ded`
      is alive. A refusal is a failure (exit 1), not a skip, and it names the port.
      Test verdicts are otherwise unchanged.
      Merged as #283 (`6361385`) on 2026-10-25 with game `7fbdf80`; the five legs are
      Linux x64 (game `a5fdfaf`, older than the pin), Linux arm64, macOS arm64,
      Windows x64 and Windows arm64. `docs/platform-matrix.md` carries the two Windows
      rows from these runs.

    Acceptance: `cross-os` X1–X4 each way per pair, recorded like the evidence rows of
    2026-10-16, with the server's `playerPos` polled every 0.25 s (coordination §5).

**Before you add a row:** compare `STATUS.md` § Milestones and § Release gate with this
list. Anything open there and missing here belongs in this section or in §4.

## 4. Owner-gated

No session can finish these alone. Prepare the evidence, then ask the owner
(`fleet_ask` to the owner's address, coordination §3).

- **Before the repositories go public: less detail, no personal data.** Owner, 2026-10-23: "sooner or later this level of detail and any PII (including hostnames, etc.) should stop being published and then eventually scrubbed from the repo: it doesn't make sense to include all this if and when the repo goes public." Until then, new docs, PRs and comments keep host names, local paths and per-session narration to what the evidence rules need, and never include IPs, user names or home paths. Changing the evidence rules (AGENTS.md rule 6, `CONTRIBUTING.md` § Records, which name the host) and the scrub itself (a history rewrite or fresh repositories) are the owner's decisions.
- **Release gate 1, the jmarshall licence.** Blocking for any public release. The owner
  deferred it on 2026-09-25. Nothing to do until the owner reopens it.
- **Publishing.** Both repositories stay private until the owner decides otherwise; the
  owner expects to recreate them first.
- **The BotThread marker as a failure (#294).** `tests/etqw_harness.py:551-562` counts
  `WARNING: Waiting on BotThread` as a failure; the game emits it from
  `src/game/botai/BotThreadData.cpp:1085-1087` when the bot thread is more than
  `bot_threadMaxFrameDelay` game frames behind. Seen with 4 waits in `m4-campaign`, and on
  Windows arm64 in `all-stock-maps` (sewer 30, slipgate 2) — and every one of those matches
  ran to its time limit and finished normally, so a wait does not by itself mean a wedged
  bot thread. Options and their costs: **drop the marker** (loses the only signal a genuinely
  stuck bot thread gives); **keep it but gate it on the match's outcome**, so a run that
  also fails a gameplay check reports it and a healthy match does not (recommended by the
  coordinator — the tests already assert match end, so the change is small); **keep it** as
  a hard marker and accept slow-host noise. A count threshold has nothing to calibrate
  against: the three sightings are 2, 4 and 30 waits and all three were normal runs, with
  no wedged example to bound the other side. No session edits `FAILURE_MARKERS` before the
  owner picks.
- **The hull lead:** the p95 bound and each class's L, and which driver and pilot seats
  join the default list. Needs a review on a high-refresh display.
- **Render portals, stage 2:** the network decision (`docs/render-portals.md`).
- **A Windows run on an NVIDIA or Intel GPU** (`STATUS.md` § Next steps, item 3). No such
  host is in the fleet; the owner would have to provide one.
- **Hosted CI:** comes back only with Actions minutes. Until then, local checks replace it.
- **The headless render world (M2):** keep `idRenderWorldHeadless` as it is, or load the
  `.procb` areas and nodes on the dedicated server (`PointInArea`, `BoundsInAreas`;
  moderate), with render-model traces still out (they need render models; heavy).
  Recommended by `openETQW-linux-arm64` (2026-10-20): keep the stub for M2 as a recorded
  decision (ledger 013) until the §3 item 8 parity test measures the effects.
  Measured 2026-10-21 by `openETQW-linux (Switchworks)` on antec (Linux x86_64, engine
  `fix/m2-render-world-parity` on a38e311, game bb8e0c5 plus the test-only `m2Parity`
  command, retail 1.5, `valley`). The listen host is a Vulkan client on SDL's offscreen
  driver with llvmpipe (software Vulkan, Mesa 26.2.4), so it exercises the client's real
  `idRenderWorldLocal` but no GPU raster path. Raw logs
  `/var/tmp/oe-swk-linux/handoff/m2-parity-afab6d7`, reproducible with meson
  `m2-render-world-parity`:
  - `probe`: dedicated `numAreas=1`, `PointInArea` 0 inside and 0 outside; listen
    `numAreas=54`, 0 inside and -1 outside;
  - `prone`: both `modelHit=0`, `modelFraction=1.0`; both `clipHit=1`,
    `clipFraction=0.8708`. The combat model has real bounds on both (about
    96x54x86 dedicated, 94x49x86 listen; the pose varies a little);
  - `RadiusPush` behind a wall: identical on both (`wallFound=1`,
    `wallDistance=281.78`, `listed=8`, `pushed=1`, `speed=68.5902`,
    `velocity=(59.3801 34.2830 1.8150)`), because `FastWorldTrace` answers false on
    both;
  - vehicle exit outside the map: dedicated accepts (`exitAccepted=1`,
    `stillInVehicle=0`, `vehicleArea=0`); listen rejects (`exitAccepted=0`,
    `stillInVehicle=1`, `vehicleArea=-1`). This is ledger 013's effect, now measured.
- **A person at the controls.** Smooth motion at high caps, and M3's "Vulkan client with a
  human at the controls", are judged by people on real displays (M5 D3), not by a test.

## 5. Recently done

2026-10-27 (`main` `ce40c41`):
- #308 Docs: post-merge Linux arm64 leg at `527c1ef` — 76 OK / 0 FAIL / 1 SKIP (`cross-os`); its own row, not M7 evidence
- #307 Docs: #302 measured (the validation layer, the `-O0` perf tree, two lighting paths), #299 closed as a measurement artifact, and the worktree recipe now says `-Dbuildtype=debugoptimized` (merged ea215a5)
- #287 Test harness: readable child exit statuses (NTSTATUS and exception decode) and a port/leftover-process pre-flight; addresses #288 and #289, #290 remains (merged ce40c41)

2026-10-26 (`main` `527c1ef`):
- #304 Docs: `docs/platform-matrix.md`, the Windows arm64 row records the sandbox workaround — 11 of the 12 temp-directory failures re-run with `mkdtemp` replaced by `os.makedirs` and all 11 pass, tests unmodified; the twelfth, `all-stock-maps`, moves to the #294 bucket (merged 527c1ef)
- #303 Docs: the #299 failure cites #302 as its cause (merged 7c223f7)
- #301 Release gate 5: the seven unreferenced Windows binaries and the PowerPC OpenAL framework deleted, on the owner's approval (merged 493bed2)
- #297 Linux: the retail-data folder picker (zenity, kdialog, yad, ledger 068), and `basepath.txt` written through a temporary file and a rename, which fixes #298 (merged 03d160c)
- Closed with them: #293 (the M7 cap-60 pacing failures were host load; quiet reruns hold 60.0 fps and A10 about -1 ms)
- Closed with them: #299 (the 30-35 GUI/s on Linux x64 was the Khronos validation layer that `test_client_network.py` turns on, on a `debug` perf tree — not a product floor; `client-network-join` passes at 46 GUI/s at `-O2` with the layer on, and no test or bound changed)

2026-10-24/25 (the burn-down):
- #285 Docs: provenance labels (minizip and eaxguid.lib -> ThirdParty/Upstream, four engine files -> Reconstruction_retail_RE; merged a9aadd4)
- #284 Docs: `docs/platform-matrix.md`, the M7 evidence register (merged d3a4d5b)
- #283 M3: `weapon-fire-observer`, a headless peer sees the other player's weapon tracers (five platforms; merged 6361385)
- #280 Docs: resumption point for any session (sessions stopped for quota)
- #279 Docs: the sessions' tribal knowledge (docs/render-path.md, docs/retail-comparison.md, coordination §5 platform notes)
- #278 Docs: STATUS summary no longer lists #270 as open
- #277 Docs: STATUS and the queue as of #276
- #276 Queue: #270 fixed by #275
- #275 Vulkan stuff grass draws after the raster lighting block, not under its flat ambient (fixes #270)
- #274 Docs: state as of 2026-10-25 (burn-down done; sessions parked)
- Closed since 2026-10-24: #267, #265, #262, #258, #256, #255, #253, #246 (reopened for the deferred part), #239, #233, #232, #219, #218, #216, #212, #211, #196, #193, #186, #185, #162, #148, #141, #122. #270 was split from #256.
- #273 Vulkan: no interaction specular in GUI render worlds, as OpenGL since #251 (ledger 079)
- #272 Vulkan draws guis/postprocess's glow natively, with retail's scene alpha (fixes #233 with #263)
- #271 Vulkan shadow stencil counts around 128 and lights where 128 >= stencil, as OpenGL (fixes #267)
- #268 Raster Vulkan: shadowed amblit surfaces end as (ambient + sun) × minSpecShadowColor, as retail's shadow/multiplitive pass (fixes #265)
- #269 Stuff grass fades with distance on both APIs: _diffusionMask is a 16x16 ordered dither; Vulkan fades per instance (fixes #256; colour gap in #270)
- #263 OpenGL draws retail's guis/postprocess glow (ARB ports of downsample, glare, blur, glow), the scene lift #233 measured
- #266 Vulkan: image descriptors use the evicting allocator; currentRender material sets purged on swapchain recreate (#264 review)
- #264 Vulkan: material descriptor sets freed with purged images and evicted when the pool is full (fixes #262)
- #261 Vulkan: front-end descriptor sets in their own pool, hashed, freed with purged images, evicted when full (fixes #258)
- #260 OpenGL forceClear follows glConfig.samples; clearer multisample fallback warning (#255 review)
- #259 OpenGL: retry without multisampling when the requested r_multiSamples fails (fixes #255 on SDL3)
- #257 Vulkan: sunlit opaque surfaces take retail's amblit ambient (minSpecAmbientColor) via ambSurf; others the cube plus its specular term (fixes #122's ambient gap)
- #254 OpenGL: load qglSampleCoverageARB; the SDL3 window requests r_multiSamples (fixes #253)
- #249 Raster Vulkan: retail's ambient cube (multiplicative, before the flat ambient), self-illumination, interaction specular; generated cubes no longer black (part of #122)
- #252 OpenGL: load qglMultiTexCoord4fARB, so instanced trees and shrubs draw (#246 OpenGL half)
- #251 No interaction specular in GUI render worlds (limbo model), matched to measured retail; ledger row 079 (owner accepted 2026-10-25; fixes #212 with #250)
- #250 Light colours clamped to [0,1] before r_lightScale (GL, Vulkan, RT), as retail (limbo and canyon bunker light measured; part of #212)
- #248 Vulkan draws instanced models (valley's trees, shrubs) per instance; front-end scissor from instanced bounds; foliage vertex colour (part of #246)
- #247 MegaTexture: bit-identical IDCT over nonzero coefficients; the finest level completes about 2x sooner after a teleport (part of #233)
- #245 seat-poses-listen: one re-run of a host-contended phase, then exit 1 'host contended' (§9 2026-10-24; closes #162, refs #196)
- #242 Vulkan terrain: retail's notlit_lit under the atmosphere sun, with minSpecShadowColor in shadow (part of #233)
- #243 Owner decisions of 2026-10-24 in coordination §9 (a contended re-run fails on a second failure; burn-down; #141 undeferred) and ledger row 078. Closed with evidence: #186 (macOS globe renders), #148 (no repro).
- #244 Mouse: carry SDL's sub-pixel motion between events (fixes #141)
- #241 Vulkan megatexture detail texture, with the CPU mipmap-state levels (part of #233)

2026-10-23/24 (after the 2026-10-23 combined run; these wait on the next one too):
- #236 (openETQW-win-arm64): Vulkan applies retail's linear atmosphere fog to world surfaces, raster (fog-once stencil) and RT (part of #233)
- #238 (openETQW-win): Vulkan front-end pipelines gated on the stage cull, MoltenVK swizzle fallback, comment and dead-code cleanup (#229 and #234 follow-ups)
- #240 (openETQW-linux): the default image is black outside developer mode, as Doom 3; missing terrain spec masks no longer light map edges (fixes #239)
- #237 (openETQW-linux): Vulkan scales the terrain's light by the bakedamb stage's parameters.w, as retail; valley ground loses its doubled sun (part of #233)
- #235 (openETQW-win): the ambient cube is no longer pre-scaled by brightness, which every consumer applies; the menu globe lands on retail's brightness (fixes #232)
- #234 (openETQW-win): Vulkan ports retail's sfx/atmos for the menu globe's atmosphere, with the stage's back-face cull (#186 lead 2)
- #231 (openETQW-win): the ambient cube bakes the directional ambient lights on black, not on ambientColor; the menu globe gets retail's terminator and dark limb (#186 lead 1)
- #229 (openETQW-win): Vulkan reads retail's 3Dc normal maps with X and Y the right way round (BC5 view swizzle, decode fallback, heat haze and water)
- #230 (openETQW-linux): the ambient specular cube is baked on black, not minSpecAmbientColor, ending OpenGL's silvery overbright on lit models (#212)
- #227 (openETQW-win-arm64): the client re-challenges on Bad challenge, takes challenge replies only from the server's address and port, and still times out; new test client-bad-challenge (may fix #216)
- #226 (openETQW-win): OpenGL material stages also leave the environment cube, gradient map and dither mask to the renderer, re-set per surface (follow-up to #224, #186)
- #228 (openETQW-win): OpenGL copies red into alpha for uncompressed TD_BUMP images and _flat gets alpha 128, as retail's programs read X from alpha (OpenGL menu globe, #186)
- #225 (openETQW-win-arm64): the client takes a challengeResponse only while challenging; new test client-stray-challenge (side finding of #216)
- #224 (openETQW-win): OpenGL material stages no longer overwrite the entity's ambient, sun and specular cubes with _normalCubeMap (#186 OpenGL globe, part)
- #223 (openETQW-linux): OpenGL loads ARB ports from base/glprogs for retail's Cg sky, atmosphere and grass programs; fixes #219
- #222 (openETQW-linux): OpenGL unbinds stale element and array buffers before client-pointer draws; fixes #218 and drops #221's glthread stopgap
- #221 (openETQW-linux): Linux OpenGL turns off Mesa glthread (stopgap for #218's crash and GPU reset)
- #220 (openETQW-mac): OpenGL decodes 3Dc (ATI1/ATI2) normal maps when the driver has no LATC; fixes macOS's part of #212

2026-10-22/23:
- #215 (part of #186): Vulkan ports retail's sfx/feAmbDecal and sfx/feAmbDecal_cloud, so the menu globe is lit by its ambient cube map, with night lights only on the dark side. Still brighter than retail, no visible terminator, atmosphere rim unchanged (#186 open). Combined run 2026-10-23 (as #202).
- #213 (#159): all-stock-maps leaves area-0 samples inside a measured gap in retail's own AAS (salvage) out of aas_area_validity and reports them (owner: match retail). Combined run 2026-10-23 (as #202).
- #210 (#208, #211): surfaces of equal sort draw in submission order (a stable merge sort, view entities first as the SDK does), so the menu's star field no longer covers the nebula; the game's sky-layer sortOffset is honoured. Combined run 2026-10-23 (as #202); smoke and vehicle glass were not in the sets.
- #207 (#203): the OpenGL GUI path resyncs the GL state cache each frame after direct state calls. Combined run 2026-10-23 (as #202).
- #206 (#187): Vulkan's stand-in sky gradient runs only for render worlds loaded from a map, so the limbo player model no longer sits in a black box (ledger 076). Combined run 2026-10-23 (as #202).
- #204 (#185): OpenGL skips any stage whose render program is defaulted (retail's Cg programs, which have no runtime on x64 or arm64), in the world and the GUI, instead of drawing it solid orange; OpenGL is a documented degraded fallback (ledger 077). Combined run 2026-10-23 (as #202).
- #202 (#193): the OpenGL GUI state cache follows direct state changes, so the menu's and limbo's buttons draw (Mods, Replay, Credits; team buttons). Combined run 2026-10-23 (integration e9df517): antec suite 71/2 flakes (#162, #216; both OK alone)/1 skip; screenshot sets from five platforms, no regressions.
- The owner's answers of 2026-10-23 (`STATUS.md` § Owner decisions): #209 merged (ledger row 076 kept); #192 and game #51 closed; #147 decided, so sizes stay points; #185's OpenGL is a documented degraded fallback (`readme.md` § Renderers, ledger row 077); #159 fixed by #213; #197 is waiting on a check that the in-game Name field works.
- #205 (#188): `openetqw_validate.py` links the installed game module beside the build's binaries (copies it on Windows), so `meson test` on a validate tree loads the PR's module rather than one in `<data>/base/`. The by-hand link step in coordination §5 is gone.
- #180: build helpers, one command per platform (`scripts/build-*`, `docs/build-scripts.md`; the owner's request), merged 2026-10-22.
- #200 (#189): the Vulkan GUI honours retail's `trivialMakeAlpha*` programs, which use brightness as alpha and draw white. The compass ring's black background no longer covers the minimap, and the menu and HUD sheets and the health cross draw translucent, as GL (which runs the real program) does. Found by skipping GUI stages one material at a time. Screenshot sets on `screenshots-pr200` from all five platforms (Windows x64 with RT on and off). Antec suite: 70 OK / 1 FAIL / 1 SKIP. The failure was `all-stock-maps` outskirts GDF `aas_area_validity` at 85.4% (#159's class); it was OK when rerun alone.
- #199 (§3 item 3, M4): `m4-bot-match` (two live bots play a time-limited match and complete no objective; movement is measured within one life) and `m4-offcampaign-match` (an objective match on quarry and island runs to its time limit, and the map's defenders win). They pass on macOS arm64 at 6d43788 and 5e12932, and on antec Linux x64 at 5e12932 (34.8 s and 69.4 s). The antec full suite at c19f7ff gave 73 OK / 0 FAIL / 1 SKIP (cross-os).
- #198 (part of #193): a single vertically centred line of GUI text centres its ink (capitals to descenders) as retail 1.5 does, and the bottom of a text rectangle gives way by the font's descent, so the main menu banner keeps its descenders. Screenshot sets on `screenshots-pr198` (Linux x64, Windows x64, Windows arm64; macOS banner check). Antec retail suite at the merge head: 71 OK / 0 FAIL / 1 SKIP (cross-os). #193 stays open for the blank OpenGL buttons.
- #195 (part of #189): masked GUI draws are cut to where their mask coordinates are 0..1, so the command map's territory outlines no longer cover the 3D view under Vulkan. Screenshot sets on `screenshots-pr195` from all five platforms. #189's black minimap was fixed by #200.
- #194 (#190): a Bink movie named by a binary sound decl is found, so the first-start intro ends (here, at once, with no movie installed) and the main menu draws on a fresh profile. The menu it uncovered shows #193 (clipped descenders, blank OpenGL buttons) and #186's dark OpenGL globe.
- #191 (#184): Vulkan submits the GUI at the end of the frame, so the HUD and menus queued after the last scene draw. Screenshot sets: Linux x64, Windows x64 (RT on and off), Windows arm64 and macOS on `screenshots-pr191`. Found once the HUD drew: #189 (minimap image, unmasked command-map lines), #190 (black fresh-profile main menu) and #192 (limbo name clipped on Windows arm64).
- #182 (#181): GUI text drawn from the game's fonts on every platform and both renderers (`docs/gui-text.md`). Screenshot sets for all five platforms are on `screenshots-pr182`; follow-ups #184-#188.
- #177 with game #59 (game #51): `sdUserInterfaceLocal::Load`'s catch no longer touches `this` after a failed load; the GUI tests cover it. Merge evidence: antec retail suite 70 OK / 0 FAIL / 1 SKIP (cross-os) at the merge head, which settles the macmini cap-240 question.
- #178 with game #60: the M2 render-world parity test (`m2-render-world-parity`).

2026-10-20/21, overnight:
- #169 (#134): retail's per-entity shadow limits.
- #174 (#170): a light's `shadowSpec` and `dontCastFromAtmosLight`.
- #171 with game #57 (#154): L7, a gun at its limit while the hull rocks.
- #175 with game #58 (#151): the jetpack flies from open ground.
- #172 (#160): a stalled H3 phase is driven once more.
- #173: `build_engine` and `build_dedicated` default to true, closing the M1 gap.
- #168: ledger 013's vehicle-exit effect, and the M2 render-world question.
- #135 closed, not reproduced.

2026-10-20, the evening sprint:
- #165 (#151, in part): a stalled jetpack flight in `seat-poses` I1 is reported and flown once more.
- #144's last pair, macOS arm64 ↔ Linux arm64: X1–X4 pass both ways at 318b638.
- #163 closed: no other `;`-chained checks.
- #164: what the sessions knew but the docs didn't.

2026-10-20:
- #149 (#128): off-view static casters cast stencil shadows. The review fix scales the caster box about the light, so the cull is conservative. Screenshot sets on all five platforms.
- #157 with game #56: the hull lead's `VEH_PRED` per class on a network client and a listen host (`lead-measure-join`, `-listen`).
- #158 (#136): `bot-gameplay`'s encounter is repeatable (inline bot AI, command-line join).
- #156: `screenshot_set.py`'s canyon anchor is outdoors, and the hangar is an extra view.
- #155 with game #55 (#126): samples in teleport transit leave the AAS tally.
- #153 (#150): the determinism replays turn tooltips off.
- #152 with game #54: late look for `freepivot` and the damped view.

#145 (2026-10-20; #127): `+set r_mode` was never ignored; the window cap now says when it shrinks a window, Linux and macOS cap at the real display, and the headless drivers don't cap. M5 step 3's seat part (2026-10-19, #146): `seat-poses-*` are the seat capture, and the new S5 checks the cockpit (`docs/m5-plan.md` step 3).

Closed on re-check (2026-10-18), with evidence:
- the mixed-caps producer gap: M5 step 8 removed it (`docs/m5-plan.md` step 8, ledger 059: A7/A7o pass, known gaps removed);
- the vehicle proxy gate: steps 13–14 replaced it for listed seats, so all that's left is the owner's default seat list (§4).

M3 cross-OS with the Mac, all four directions (2026-10-16, `STATUS.md` evidence), #140 (the other three), #139 (the first of them), #138 (the Mac Local Network notes), #132 (this runbook), #137 (STATUS current), #130 (dropped clients are told; listen-host `disconnect` ends its server), #129 (raster shadow volumes; #124 fixed), #131 (removed the Discord announcement), #125 (the screenshot tool and rule), #123 (the
raster sky), #118 (Stop disconnects the client), #117 (the vehicle-pop tolerance),
#112 (the Windows retail-data folder picker). Older work is in `STATUS.md` and the merged
PRs.
