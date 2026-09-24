# Checkpoint: Stock Maps phase, M2 (AAS + 30 Hz bot traversal), 2026-09-22

Written by Claude Code at a quota-forced safe stop. It replaces the earlier Gemini checkpoint, which overstated M1 ("100% verified"): its final M1 review/audit round never produced a verdict.

- Branch: `wip/stock-maps-m2-checkpoint` in both `jm2/openETQW` and `jm2/openETQW-game` (pushed). `main` is untouched (engine `7545679`, game `93f939f`).
- Both repositories are **private** (checked with `gh api`).
- Local-only session notes (gitignored): `/Users/jmulesa/openETQW/.agents/claude_session_2026-09-22/`. They include the transcript-history briefing, code-reader dossier (`code_results.json`), openQ4 triage, follow-up list, probe tool, and harness JSON reports.

## Latest: network play over real links (2026-09-23)
- **User info (protocol .3, ledger 049).** Players' names, clan tags and flags reach the server and every client, validated and bounded. Before this, every remote player was "Player N". New tests: `userinfo`, `userinfo-loss`.
- **Streamed snapshots (protocol .4, ledger 015 and 045).** Replaced stop-and-wait with the retail/Doom 3 flow. The server streams snapshots against the base it has applied. It applies on the client's ack and sends `SERVER_RELIABLE_MESSAGE_APPLYSNAPSHOT`, and the client makes a snapshot its base only on that message.
  - Game side: pending game states are purged when a rules object is replaced, and `ApplySnapshot` skips an entity with no base.
- **Client prediction time sync (ledger 045).** The client runs ahead of the server by about the latency, as in Doom 3. Before this, under any real latency a remote player's input reached the server too late to move it.
- **Test tooling:**
  - POSIX `net_forceLatency` and `net_forceJitter` (receive-side delay; jitter keeps the order: independent per-packet delays reordered the fragments of every large message, which the channel drops) plus `net_forceDrop`.
  - A game-side debug check, `net_debugSnapshotBase`, compares the shape of the client's and server's bases and the applied counters on every snapshot.
  - It found that state values legitimately differ: the server keeps exact values and the client keeps quantized ones.
- **New meson tests:**
  - `remote-client-map-change-latency` (60+40 ms, 10% loss, base check);
  - `multiclient-gameplay` and `multiclient-gameplay-latency` (80–120 ms, 5% loss, movement replication).
- **Verified:** the base check held up to 150 ms latency, 100 ms jitter and 20% loss.
- **Flakes seen once each (both pass on rerun):** valley-match phase 3 (bot vehicle entry) and all-stock-maps salvage (GDF bot AAS-area validity 87% < 90%).

## Latest: pre-merge review (2026-09-23)
Three adversarial review rounds over the WIP branches, run as multi-agent workflows (local notes: `.agents/claude_session_2026-09-22/FOLLOWUPS.md`). Every confirmed finding is fixed except two: the APPLYSNAPSHOT redesign (follow-up) and ledger 047 (owner decision).

**Round 1 (whole diff vs main), 11 confirmed:**
- **High:** a server map change (`spawnServer`, admin changemap, campaign) left every remote human frozen on the old map until they timed out. Fixed, with the new test `remote-client-map-change`.
- **Medium:** a same-map `spawnServer` after an `si_rules` change kept the old rules.
- **Lows:**
  - AAS validation int overflow and the 0/1-node bounds search;
  - event budget headroom;
  - campaign test binary;
  - CI companion policy and pin;
  - protocol test positive half;
  - ledger 045–047 (047 `validClasses` needs an owner decision).

**Rounds 2–3 (on the fixes), reworked network delivery (ledger 045, protocol 0x4F45.2):**
- **Reliability:** the game init is reliable. The client waits for its first snapshot before running the game, and the server enters it after its first usercmd of the new game. Client messages carry the game init id.
- **Snapshots and local client:** snapshots are resent after 250 ms, with reliable messages held back while one is unacked. The listen-server local client gets no channel traffic, and repeater double-sends are gone.
- **Loss tests:** POSIX `net_forceDrop` makes loss testable. Verified up to 30% loss each way. New meson test `remote-client-map-change-loss`.
- **Game:** AAS cluster, portal and area routing-index validation, including cross-references. All constraints hold for the 24 retail files. An earlier version of the check rejected all 24; it was caught before it was pushed.
- **FTZ/DAZ:** tried and reverted (ledger 048). The script VM keeps integers in float slots, and a client then ran server-only map script.

**Evidence:** full meson with retail data 16/16. Multiclient 3/3 and loopback OK, before and after the final round.

## Latest: M4 CI parity (2026-09-23)
- **Engine CI** (`ci.yml`): lint and script smoke are green. All 7 build legs plus the sanitizer leg skip cleanly while `openETQW-game` is private, so hosted CI has never built the engine.
- **Game CI** (`commit-validation.yml`, round 3 at `3b6e1b9`): the module builds and `GetGameAPI` resolves on macOS arm64/x64, Linux x64 GCC/Clang and Windows x64 MSVC. The Linux x64 and macOS ASan+UBSan legs load and unload the module cleanly. The Linux ARM64 cross build failed on `raise`/`SIGTRAP` in `Lib.cpp` (fixed in `2e4b1ce`).
- Game `79d6166` and `5f2e7eb`: the Linux module exports only `GetGameAPI` (version script; it used to export about 22,800 symbols) and links with full RELRO. **Game CI run 35858568611 is green on every leg.** Every leg now checks exports strictly and none is experimental: macOS arm64/x64, Linux x64 GCC/Clang, Linux ARM64 cross, Windows x64 MSVC, and the ASan+UBSan legs on Linux x64 and macOS arm64 (load and unload clean). Only the native-ARM64-runner legs remain experimental, and they don't run while the repo is private.
- Hosted-runner queueing stalled both repos for more than 30 minutes around 03:15Z. GitHub status said Actions was operational; a private-repo minutes quota is a possible cause (billing can't be read without the `user` scope).
- **Tests:** retail data comes from `ETQW_DATA_PATH` only. All 30 retail-data tests exit 77 without data (9 meson-registered, 21 standalone), the skip baseline is empty, and no absolute paths remain.
- Ledger 032 and 035 are corrected to MEDIUM (Partial), stating what was actually verified.
- `Common.cpp`: the frame pacer uses `std::chrono`, and the backtrace is POSIX-only. Windows still needs the win32 layer and a Windows `sys_local.cpp`, which can't be verified here: there is no container runtime, and mingw has `unistd.h`, so it can't stand in for MSVC.

## Latest (M2 closed, M3 done; D7 clean network break)
- **The full `meson test -C build -j 1` with retail data passes 13/13**: the unit tests; client-lifecycle, high-refresh-determinism and vulkan-shadows; all-stock-maps (561 s, serial); protocol-clean-break and inprocess-map-change; bot-gameplay and valley-match. The Valley match now plays a started match (the retail warmup EndGame guard is restored).
- **Game follow-ups:**
  - Push teleport (openQ4-game 841a573 idea).
  - Unreliable-event snapshot budget (f5596b6, second half).
  - AAS validation of portals and clusters (entry 0 of those lists is a dummy).
- **D7 (owner, 2026-09-22):**
  - Clean network break from retail 1.5, mirroring openQ4. Protocol 0x4F45.1 (0x4F45.2 since the pre-merge review fixes); retail majors 1, 2, 10–14 and 22 are refused at connect (`tests/test_protocol_clean_break.py`).
  - The charter, README, session brief and templates in the gitignored `openetqw_handoff_v3/` are updated, as are STATUS.md and ledger row 036. Retail paks are still required.
- **M3: `tests/test_campaign_client_maps.py` 4/4.** Frames, world, MegaTexture, sky and interaction draws, stencil shadows, exit 0, no Vulkan errors:
  - outskirts (NA, GDF) 224k shadow draws;
  - ark (NE, GDF) 29k;
  - canyon (SP, Strogg spawn) 25k;
  - refinery (Africa, loading bay via setviewpos) 14k.
  - The GDF spawn views of canyon, refinery, slipgate and island contain no shadow-casting light (new diagnostic line "Vulkan shadow lights").
  - Vulkan validation layers are not installed, so validation errors could not be observed. There is no screenshot support in the Vulkan backend; visual confirmation is the owner's.
- **Next: M4, CI parity** (drafts in `tools/ci/` of both repos and `.github/workflows/` of the game repo; the cross-repo token is deferred until the repos are public). Then M5 docs.

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
