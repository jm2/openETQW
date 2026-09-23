# Checkpoint: Stock Maps phase, M2 (AAS + 30 Hz bot traversal), 2026-09-22

Written by Claude Code at a quota-forced safe stop. It replaces the earlier Gemini checkpoint, which overstated M1 ("100% verified"): its final M1 review/audit round never produced a verdict.

- Branch: `wip/stock-maps-m2-checkpoint` in both `jm2/openETQW` and `jm2/openETQW-game` (pushed). `main` is untouched (engine `7545679`, game `632aacb`).
- Both repositories are **private** (checked with `gh api`).
- Local-only session notes (gitignored): `/Users/jmulesa/openETQW/.agents/claude_session_2026-09-22/`. They include the transcript-history briefing, code-reader dossier (`code_results.json`), openQ4 triage, follow-up list, probe tool, and harness JSON reports.

## Update, later on 2026-09-22 (current state; supersedes "State at stop" below where they differ)
- **The engine WIP was reviewed and verified:**
  - Headless materials now parse as they do on the client (failed decls went from about 300–370 per map to 0).
  - `spawnServer` really changes maps, and queued stdin survives a map change.
  - Bot slots no longer leak: a console map change releases the bots through the retail spectator timer, and re-added bots reuse the slots. A same-map restart keeps the bots.
  - Printing is thread-safe, and an error in the first map load no longer aborts.
  - New test `tests/test_inprocess_map_change.py`: PASS (quarry→outskirts→ark→ark).
- **Core and bot-AI WIP reviewed** (RSqrt LP64 fix, O(1) `idBlockAlloc::Free`, property lifetime, POSIX threading, `addbot` via CheckRespawns, playerPos/spawn command ownership, the objective-manager `sendNetworkEvent` signature). PLAYER_POS `area=` now reports containment within step height (1/8/16 u above the origin), not the nearest reachable area.
- **M2 result: `test_all_stock_maps.py`, 11/11 maps PASS**, at the retail default think rate (0.500 ratio) and with `--bot-think-delay 0` (1.000 ratio, true 30 Hz AI):
  - AAS areas and reachabilities match the retail files;
  - area containment 96–100%;
  - longest stuck interval ≤ 6.7 s (was 15–30 s);
  - gameHz 30.24–30.31.
  - Reports: `.agents/claude_session_2026-09-22/harness_reports/full11_{a,b,c}.json`.
- Still to run: `tests/test_valley_match.py`. It may depend on the removed EndGame warmup guard, which is restored now.
- **Licensing: jmarshall provenance audit (owner directive).** Report: `.agents/claude_session_2026-09-22/JMARSHALL_PROVENANCE_AUDIT.md`; tooling: `.agents/upstreams/`.
  - Engine: 135 compiled files (about 54.7k lines) are effectively jmarshall-authored, and 34 more are Doom 3 files with his edits.
  - Game: 57 compiled files are SDK files with his edits (48 of them ≥95% identical to the SDK).
  - History: both repos are rooted in jmarshall's 3 commits.
  - Files added after the split are clean.
  - **The owner chose "audit only for now": no remediation code changes or deletions until the owner decides.**
- Owner decisions still open: 30 Hz interpretation, quarry <500 entities, jmarshall remediation strategy.

## State at stop
- `ninja` builds both repos cleanly after `meson setup --reconfigure build` (engine). `meson test --suite unit`: 5/5 OK.
- Smoke at the stop, on this exact tree: `test_all_stock_maps.py --maps volcano,ark` gave **2/2 PASS**. GDF/Strogg displacement: volcano 4356/8794 u, ark 8017/10761 u. gameHz 30.25–30.30. The longest Strogg "stuck" interval fell from 30.5 s (ark, earlier build) to 3.6 s, and volcano's to 0.0 s, which suggests the WIP collision/pathing fixes help.
- All agents were stopped mid-task. Only the **harness** worker finished and verified its work. The **engine**, **bot-AI** and **core** workers were interrupted: their edits compile but have **not** been individually verified or reviewed. Treat them as WIP and review each diff (see below).
- The CI-parity drafting was interrupted after it had written helper scripts only (`tools/ci/` in both repos), not workflows. Those drafts are unvalidated.

## Verified this session (evidence)
- **All 11 maps, dedicated server** (`tests/test_all_stock_maps.py`, strict mode, fresh process per map; run by the harness worker on a mid-session game build that had the new diagnostics):
  - Load and `quit` give exit 0.
  - Spawned + inhibited entities equal the retail `.entities` count.
  - Player and vehicle AAS area and reachability counts equal an independent parse of the retail pk4.
  - `.nav` loads on all maps except volcano, which ships none.
  - GDF and Strogg bots spawn on real team spawns.
  - Per-life displacement is far above 50 u on every map (for example canyon Strogg 12,221 u, volcano GDF 7,621 u).
  - AAS area is valid in 100% of samples, but this check is currently lenient (see below).
  - Server tick measured at 30.17–30.30 Hz; 0 "Waiting on BotThread".
  - Bot AI think rate: `thinkRatio` is 0.500 at the retail default `bot_threadMinFrameDelay 1` (about 15 Hz). It is 0.999–1.000 with `--bot-think-delay 0` (a true 30 Hz), and all 11 maps pass either way.
  - Reports: `.agents/claude_session_2026-09-22/harness_reports/`.
- Skip behaviour: the test exits 77 when `ETQW_DATA_PATH` is unset or invalid. It is registered in `tests/meson.build` (suites integration and stock-maps, serial, 1800 s).

## Work in the tree (by owner area)
- **Harness (done, verified)**
  - New: `tests/etqw_harness.py`, `tests/test_all_stock_maps.py`.
  - Updated: `tests/meson.build`, `test_bot_gameplay.py` (the fake "30 Hz VERIFIED" line is gone; it now measures the tick rate), `test_bot_repeatability.py` (treats 77 as a skip), `test_aas_stress.py` (reads all paks, skips with 77).
- **Engine (WIP, unverified)**: `src/framework/*` (Common, CmdSystem, CVarSystem, FileSystem, async/AsyncNetwork, AsyncServer), `src/sys/*` (posix_main, posix_signal, sys_local, osx/macosx_compat, stub/headless_stubs), `src/renderer/*` (Material, Image_*, image_sequence, new ImageParse.h), `src/decllib/*`, new `src/framework/GameModuleCallbacks.h`. Intended fixes:
  - E1: headless materials default to solid, so water, ladder and nonsolid collision contents diverge on the dedicated server.
  - E2: the stdin line sent right after a map change is dropped.
  - E3: `spawnServer` on a running server only restarts the current map.
  - E4: bot client slots leak across in-process map changes.
  - E5: `Common` printing is not thread-safe.
  - E6: an error in the first map load aborts with SIGABRT.
  - E7: the macOS savepath uses an "openQ4" directory.
  - E8–E11: `globalImages` guards, terminal raw mode, CmdSystem exception safety, the lightProjectionMap hack.
- **Bot AI (WIP, partly exercised by the harness run)**: `botai/*` (AASFile_local TraceFloor/Trace semantics, FindReachabilityByName returning -1, map id detection, validClasses, InitAAS leaks/`aas_types`, PushPointIntoArea, null derefs), `gamesys/SysCmds.cpp` (new `botThreadStats` command; PLAYER_POS gains ` hp= dead= area=`), `rules/*` (addbot synchronous spawn, playerPos/spawn command registration, EndGame warmup guard), `roles/ObjectiveManager.*`.
- **Core (WIP, unverified)**: `src/idlib/*`, plus `physics/Clip.cpp`, `gamesys/{Event,Pvs}.cpp`, `effects/*`, `vehicles/*`, `misc/DefenceTurret.cpp`, `structures/DeployRequest.cpp` and the game `meson.build`. Intended fixes:
  - **`idMath::RSqrt` reads 8 bytes from a 4-byte float on 64-bit targets**, randomly sign-flipping `NormalizeFast`/`LengthFast`. Other 64-bit `long` bugs: rvRandom, MD4/MD5, PVS words.
  - Revert the O(n) `idBlockAlloc::Free` validation that Gemini added and fix the lifetime bug it hid.
  - Restore the strict clip-thread check.
  - Threading: per-thread priority instead of a process-wide renice on macOS, stack size, signal timeout return, `isRunning`.
  - Headless particle surfaces; remove debug printf spam; `g_simHash` defaults to 0.
- Gemini's M1 fixes are included as well (CollisionModel purge/reload, Material `globalImages` guard, precipitation guard, particle `numVerts` reset, shutdown ordering, script/event RAII).

## Findings not yet fixed / open decisions (owner)
1. **"30 Hz" criterion.** The retail SDK bot thread thinks every second game frame (about 15 Hz) by design, while usercmds are applied at 30 Hz. The registered test runs with retail defaults and reports the ratio without asserting it. Should it assert `--bot-think-delay 0` (true 30 Hz AI), or should the default change? (Changing the default is a retail deviation.)
2. **quarry has 415 entities in its retail `.entities`** (414 spawned + 1 inhibited), below the "more than 500" criterion. The loader spawns 100%; the test marks it DATA_LIMITATION. Accept or reword the criterion?
3. **Stuck Strogg bots.** On ark, volcano, salvage, quarry and area22 a Strogg bot holds `fwd=127` at about 0 speed against a flat plane for 15–30 s. Likely cause: the dedicated-server material defaulting (E1) turns passable surfaces solid. Re-measure after E1 is verified; otherwise check doors and movers.
4. PLAYER_POS `area=` uses `PointReachableAreaNum` (nearest area), so the ≥90% validity check is lenient. Switch to strict `PointAreaNum`.
5. Bots always report `lagged=1` (cosmetic; `Player.cpp` SetLagged for bot clients).
6. Pending ports from the openQ4 triage (all 3 of jm2's PRs #176–178 and 13 upstream fixes are NOT applicable to ETQW's code):
   - openQ4-game `841a573`: treat a non-clipping push longer than 4096 u as a teleport (`physics/Push.cpp`).
   - openQ4-game `f5596b6`, part 2 only: a per-snapshot byte budget for unreliable entity events (`Game_network.cpp`).
   - Both are patterns only, as independent implementations; patches are in `openq4_triage.json`.
7. AAS loader bounds checks and CRC validation (`AASFile_local.cpp`, flagged in the M4 review, never fixed).
8. Headless render world traces return no hit, so on the dedicated server radius damage ignores walls. This is a design item.
9. See `.agents/claude_session_2026-09-22/FOLLOWUPS.md` for the full list, including the M3 watch items (no Vulkan validation layers installed, shadow fallback correctness, HiDPI sizes).

## CI (decision recorded)
- The existing CI has never built anything. Its only run failed in the reviewdog/actionlint step ("diff command is empty"), and the build matrix depends on that job.
- The owner **deferred cross-repo CI access until the repos are public**; no token will be added. CI must fetch the companion repo anonymously, and dependent jobs must skip cleanly until then.
- Goal: openQ4 CI parity for both repos, excluding self-hosted and retail-data runners. openQ4 has:
  - Linux x64/ARM64, macOS arm64/x64 and Windows x64/ARM64 builds;
  - a dedicated-server smoke that needs no retail assets;
  - Linux ASan+UBSan;
  - a pinned companion SHA;
  - game-repo CI.
- Known blockers: Windows engine code does not compile (`Common.cpp` POSIX includes; SystemBootstrap SDL3 issues), and Linux has never been compiled.

## How to resume
1. Pre-flight:
   - `pgrep -fl openETQW-` (kill only orphans);
   - `ninja -C ~/openETQW-game/build`;
   - `cd ~/openETQW && meson setup --reconfigure build && ninja -C build && meson test -C build --suite unit`.
2. Review and verify the three WIP worker areas (engine, bot AI, core): read each diff against `.agents/claude_session_2026-09-22/m1_baseline_diffs/` plus this list. Fix anything half-finished, then run:
   - `ETQW_DATA_PATH=/Users/jmulesa/etqw_data python3 tests/test_all_stock_maps.py --jobs 3` (with and without `--bot-think-delay 0`);
   - the M1 regression scenarios (outskirts and ark shutdown ×5, in-process map changes);
   - an adversarial review of each diff.
3. Resolve the owner decisions (30 Hz, quarry), apply the openQ4-pattern ports and the AAS bounds checks, then continue with M3 (Vulkan client on outskirts, ark, canyon, refinery), M4 (CI parity plus test registration) and M5 (STATUS.md, compat-ledger rows from 036, zero-SDK, merge to main, push).
