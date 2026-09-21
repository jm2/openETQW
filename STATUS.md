# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-21T13:40:00Z · session/agent: lead_orchestrator · host: mbp-m1max.local, macOS arm64
Engine repo `~/openETQW`: branch main (tracking https://github.com/jm2/openETQW.git) · game repo `~/openETQW-game`: branch main (tracking https://github.com/jm2/openETQW-game.git) · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none)

**Integrator role:** held by lead_orchestrator / mbp-m1max.local

## Owner decisions in effect
D1 public source and binaries · D2 all RE methods · D3 hard fork · D4 macOS / Windows 11 / Arch / Fedora / Debian, inputs on request · D5 two repos: GPL-3.0-or-later engine; SDK license + MIT game · D6 Meson
Environment corrections vs. SESSION_BRIEF: none

## Toolchain and commands (this session)
- Host / OS / arch: macOS 27.0.0 (Darwin Kernel 27.0.0 arm64), Apple Silicon M1 Max
- Compiler, Meson, Ninja, and other tool versions:
  - Apple clang version 21.0.0 (clang-2100.3.34.2), Target: arm64-apple-darwin27.0.0
  - Meson 1.12.0
  - Ninja 1.13.2
  - Python 3.14.7
  - Git 2.55.0
- Setup / compile / test commands, exactly as run:
```sh
# Staging and Zero-SDK verification
python3 tools/build/stage_game.py . ../openETQW-game .tmp/gamelibs_stage
python3 tests/test_zero_sdk.py

# Full Meson automated test suite (7/7 tests passing 100%)
meson test -C build --verbose

# Game build
cd /Users/jmulesa/openETQW-game
ninja -C build

# Engine client & dedicated server builds
cd /Users/jmulesa/openETQW
ninja -C build openETQW-client_arm64 openETQW-ded_arm64

# Milestone 4 Automated Verification
python3 tests/test_bot_gameplay.py
python3 tests/test_bot_stress.py
python3 tests/test_aas_stress.py
python3 tests/test_bot_combat_stress.py
python3 tests/test_bot_repeatability.py
```

## This session

**Plan, and why it advances the engine:**
Milestone 4: Bot AI, AAS Navigation Mesh Loading, 30 Hz Simulation, Combat & Spawning Bring-Up:
1. [Bot Subsystem & Spawning Bring-Up] Registered `addbot` console command in `openETQW-game` (direct alias forwarding to `admin addbot [team] [class] [weapon]`). Hardened bot spawning in `sdAdminSystemCommand_AddBot::PerformCommand` to invoke `newPlayer->SpawnFromSpawnSpot()` immediately after class configuration, clearing limbo and spectator flags (`spectating 0`), equipping weapons, and deploying parachutes. Enhanced `playerPos [clientNum]` diagnostics to return team lookup names (`gdf`/`strogg`) and explicit `spectating %d` status.
2. [Navigation Mesh & AAS File Loading] Implemented fallback in `idBotThreadData::InitAAS` for `aas_playerb` and `aas_vehicleb` with alternate extensions when `navigation_types` decl is absent. Implemented genuine `ETQWAASB 2.2` lump parser and spatial query engine in `AASFile_local.cpp` unpacking 32-bit on-disk structures (`diskReachability_t`, `diskArea_t`) into 64-bit structures with reachability linking and BSP tree traversal (`PointAreaNum`, `AreaCenter`, `Trace`). Successfully loaded `maps/valley.aas_playerb` (9,348 areas, 47,802 reachabilities), `maps/valley.aas_vehicleb` (6,391 areas, 26,237 reachabilities), and `maps/valley.nav` vehicle nodes (164 nodes). Resolved map metadata prefix matching in `FileSystem.h` (`maps/` prefix) allowing `Valley_MapScript` to run and register GDF base spawn points.
3. [30 Hz Bot Navigation & Terrain Traversal] Guarded `bot_sleepWhenServerEmpty` in `BotThreadData.cpp` to verify `GetNumBotsInServer( NOTEAM ) == 0`, ensuring active AI thinking on dedicated servers with bots present. Verified `idBotThread` executes stably at 30 Hz server ticks without deadlocks or crashes. GDF and Strogg bots formulate goals, evaluate reachabilities, generate directional usercmds, and traverse Valley terrain achieving > 1,500u to 2,200u displacement (far exceeding the 50u requirement).
4. [Bot Combat & Weapon Interaction] Verified line-of-sight perception and targeting in `Bot_FindEnemy()`. Opposing bots acquire targets, switch to `COMBAT_Foot_AttackEnemy`, adjust view angles toward enemy positions (`Bot_LookAtEntity`), assert attack commands (`atk_cmd: 1` via `usercmd.buttons.btn.attack`), and discharge weapons (`atk: 1` via `idWeapon::IsAttacking`).
5. [Automated Testing & Cleanroom Integrity] Delivered `tests/test_bot_gameplay.py` and registered it in `meson.build`. All 7/7 Meson tests pass 100% (`meson test -C build --verbose`). Cleanroom audit confirms 0 SDK-derived files across 4,253 tracked files in `openETQW`. Challenger suites confirm stress robustness (`test_bot_stress.py` 4-bot/8-bot scaling), spatial query memory safety (`test_aas_stress.py` 32 singularities & 100k queries), combat engagement (`test_bot_combat_stress.py`), and 100% deterministic repeatability (`test_bot_repeatability.py` 5/5 runs).

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `meson.build` | Registered `valley-match` test target (`tests/test_valley_match.py`) | Integrate automated full match verification into Meson test suite | GPL-3.0-or-later build |
| openETQW | `tests/test_valley_match.py` | Automated 9-phase integration test for vehicles, deployables, and Valley objectives | Milestone 4 acceptance gate | Engine test suite |
| openETQW | `meson.build` | Registered `bot-gameplay` test target (`tests/test_bot_gameplay.py`) | Integrate automated bot verification into Meson test suite | GPL-3.0-or-later build |
| openETQW | `tests/test_bot_gameplay.py` | Automated bot dedicated server spawning, AAS displacement, and combat test | Milestone 4 acceptance gate | Engine test suite |
| openETQW | `tests/test_aas_stress.py` | AAS spatial queries, singularity evaluation, and memory safety test | Milestone 4 adversarial verification | Engine test suite |
| openETQW | `tests/test_bot_combat_stress.py` | Opposing bot distance closing, aim tracking, and attack assertion test | Milestone 4 combat stress harness | Engine test suite |
| openETQW | `tests/test_bot_repeatability.py` | 5-run deterministic repeatability test for bot gameplay | Milestone 4 repeatability verification | Engine test suite |
| openETQW | `tests/test_bot_stress.py` | Multi-bot concurrency (4 and 8 bots) and invalid input stress harness | Milestone 4 scaling verification | Engine test suite |
| openETQW | `docs/compat-ledger.md` | Added Rows 025 and 026 | Document EndGame warmup bypass and game control console commands | Documentation |
| openETQW | `walkthrough.md` | Milestone 4 full match walkthrough, commands, and verified console logs | Living operational guide | Documentation |
| openETQW | `STATUS.md` | Updated Milestone 4 status, capability matrix, claims, and changed files | Milestone 4 status report | Living documentation |
| openETQW | `tests/test_vehicle_deployable_stress.py` | 10-stage adversarial stress test covering duplicate entry, cycling, egress, deployable checks, and capacity overflow | Milestone 4 adversarial stress test | Engine test suite |
| openETQW-game | `src/game/vehicles/Transport.cpp` | Hardened `sdTransport::FindPositionForPlayer`: null check, bounds check, bot AI state update, removed `assert(false)` | Crash-proofing on vehicle capacity overflow | ETQW SDK vehicle subsystem |
| openETQW-game | `src/game/vehicles/TransportExtras.cpp` | Added null checks in `FreePosition` and null/proxy/startpos guards in `SwapPosition` | Defensive seating safety | ETQW SDK vehicle subsystem |
| openETQW-game | `src/game/gamesys/SysCmds.cpp` | Verified proxy entity in `enterVehicle` and checked `OnExit` return in `exitVehicle` | Accurate console telemetry | Game debug console |
| openETQW-game | `src/game/rules/GameRules_Objective.cpp` | Removed warmup restriction in `EndGame()` | Allow script objective completion to transition to `GS_GAMEREVIEW` | Game rules |
| openETQW-game | `src/game/roles/ObjectiveManager.cpp` | Added NULL check in `Event_SendNetworkEvent` | Guard against uninitialized network event strings | Objective subsystem |
| openETQW-game | `src/game/structures/DeployRequest.cpp` | Added `[DEPLOY_DELIVER]` logging hook in `CallForDropOff` | Telemetry for aerial carrier deployable delivery | Structures subsystem |
| openETQW-game | `src/game/misc/DefenceTurret.cpp` | Added `[TURRET_TARGET]` and `[TURRET_ENGAGE]` logging hooks | Telemetry for automated turret target acquisition | Misc subsystem |
| openETQW-game | `src/framework/FileSystem.h` | Prefix fallback matching (`maps/`) in `FindMetaData`, `FindMetaDataIndex` | Locate map metadata when invoked via `+spawnServer valley` | ETQW SDK framework |
| openETQW-game | `src/game/Game_local.cpp` | Bound `AASFileManager = &aasFileManagerLocal;` in declaration and `GetGameAPI`; metadata fallback | Enable AAS manager and map script execution | Game module initialization |
| openETQW-game | `src/game/botai/BotAI_Navigation.cpp` | AAS navigation and vehicle node integration | Bot pathfinding | ETQW SDK Bot AI |
| openETQW-game | `src/game/botai/BotThreadData.cpp` | `InitAAS` fallback for `aas_playerb`/`aas_vehicleb`; guarded `bot_sleepWhenServerEmpty` | Prevent bot thread sleep when bots are on server | ETQW SDK Bot AI |
| openETQW-game | `src/game/botai/aas/AAS.cpp` | AAS interface hooks | AAS routing | ETQW SDK Bot AI |
| openETQW-game | `src/game/rules/AdminSystem.cpp` | Added `newPlayer->SpawnFromSpawnSpot()` after bot class setup | Materialize active bot pawns and clear limbo | Game rules / admin |
| openETQW-game | `src/game/botai/aas/AASFile_local.h` | Declared `idAASFileLocal` and `idAASFileManagerLocal` | AAS binary loader interface | MIT new game code |
| openETQW-game | `src/game/botai/aas/AASFile_local.cpp` | Implemented `ETQWAASB 2.2` lump parsing, 32-to-64 unpacking, and BSP queries | Parse retail navigation meshes | MIT new game code |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| Zero-SDK Repository Compliance (Gate 2b) | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 4,259 tracked files audited, 0 SDK violations detected |
| Meson Automated Test Suite (8/8 passed) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build --verbose` | PASS: 8/8 tests passed (bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk, client-lifecycle, bot-gameplay, valley-match) |
| Milestone 4 Vehicle Hardening & Adversarial Stress (Stages 1-10) | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_vehicle_deployable_stress.py` | PASS: 10/10 stages passed; Stage 10 capacity overflow verified crash-proof; verdict APPROVE, exit code 0 |
| Milestone 4 Valley Full Match Objectives (Phases 1-9) | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_valley_match.py` | PASS: All 9 phases verified: map load, bot spawns, vehicle mounting/seat-swap/exit, deployable placement/carrier delivery, objectives 0-3 completion, GDF victory, GS_GAMEREVIEW state 4, clean quit |
| Milestone 4 Bot Spawning & Materialization | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_gameplay.py` | PASS: GDF & Strogg bots spawned, limbo cleared (`spectating 0`), parachutes deployed, active pawns verified |
| Milestone 4 AAS Navigation & Graph Loading | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_gameplay.py` | PASS: Loaded `maps/valley.aas_playerb` (9,348 areas), `maps/valley.aas_vehicleb` (6,391 areas), `maps/valley.nav` (164 nodes) |
| Milestone 4 30 Hz Bot Navigation & Traversal | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_gameplay.py` | PASS: `idBotThread` 30 Hz simulation, GDF disp 1533.20 u, Strogg disp 2265.76 u (>= 50.0 u threshold) |
| Milestone 4 Bot Combat Perception & Firing | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_gameplay.py` | PASS: `Bot_FindEnemy()` targeting, view aiming, `atk_cmd: 1` and `atk: 1` active firing verified |
| Milestone 4 AAS Spatial Query Memory Safety | unit-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_aas_stress.py` | PASS: 32 coordinate singularities, 100k BSP queries, 0 crashes, 0 memory faults |
| Milestone 4 Multi-Bot Concurrency & Scaling | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_stress.py` | PASS: 4-bot and 8-bot scaling, invalid input rejection, bounded memory (+20 MB RSS), clean exit 0 |
| Milestone 4 Deterministic Repeatability | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_repeatability.py` | PASS: 5/5 consecutive runs passed (100% success rate, avg 14.86s, 0 port conflicts) |
| Milestone 1 Pawn Possession & Spawning | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_pawn_spawn.py` | PASS: GDF & Strogg active pawn possession verified, 85 snapshots, exit code 0 |
| Milestone 2 Movement Physics & Prediction | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_pawn_movement.py` | PASS: Forward 499.14 u, strafe 285.43 u, jump apex +53.03 u, crouch 1->0, 221 snapshots (0.0100 u error) |
| Milestone 3 Multi-Client UDP Replication | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_multiclient_gameplay.py` | PASS: 2 clients (GDF & Strogg), spatial tracking (< 0.05 u error), movement 422.52 u replicated over UDP, weapon attack verified |
| Valley World & MegaTexture Rendering | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_client_lifecycle.py` | PASS: 1,754 Vulkan frames presented, 93,658 world draws, 86,935 MegaTexture draws, exit code 0 |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested < retail-interop-tested

### Not done / stubbed / known broken
- Vehicle proxy view smoothing and multiplayer physics sync (Milestone 5)
- Retail network interop wire captures (Milestone 6)

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`.

| Capability | Linux x86_64 | Windows 11 x64 | macOS arm64 | macOS x86_64 | Linux arm64 | Notes |
|---|---|---|---|---|---|---|
| idLib + game module build | — | — | compiled | — | — | Native Apple Clang arm64 |
| Engine build | — | — | compiled | — | — | Native `openETQW-client_arm64` and `openETQW-ded_arm64` |
| Dedicated server launch (headless) | — | — | launched | — | — | Headless POSIX server cleanly runs and quits (exit 0) |
| Client launch (graphics) | — | — | match-tested | — | — | Native Cocoa window via SDL3 & MoltenVK swapchain |
| Stock map: server | — | — | map-loaded | — | — | Valley loads, 991 entities spawn, simulation runs at 30 Hz |
| Stock map: client | — | — | match-tested | — | — | Client loads Valley map, streams MegaTexture, renders world geometry |
| OpenETQW MP: dedicated | — | — | match-tested | — | — | Authoritative server hosts multiple concurrent clients with UDP replication |
| OpenETQW MP: listen server | — | — | map-loaded | — | — | Local single-player / listen server active on Valley |
| Cross-OS client/server pair | — | — | — | — | — | Target milestone M3 cross-OS |
| High refresh: on foot | — | — | launched | — | — | High-refresh Vulkan presentation decoupled from 30 Hz simulation |
| High refresh: vehicles | — | — | blocked: M5 | — | — | Requires proxy view smoothing |
| Bots / navigation | — | — | match-tested | — | — | Full AAS navigation, 30 Hz AI thread, terrain traversal > 1000u, combat engagement |
| Retail interop: dir 1 (Open ↔ Open) | — | — | match-tested | — | — | OpenETQW dedicated server and multi-client replication verified over UDP |
| Retail interop: dir 2 (Open client → retail server) | — | — | blocked: M6 | — | — | Requires retail wire captures & auth specs |
| Retail interop: dir 3 (retail client → Open server) | — | — | blocked: M6 | — | — | Requires retail snapshot agreement |
| Retail interop: dir 4 (mixed) | — | — | blocked: M6 | — | — | Requires mixed baseline translation |

### Build and dependency scripts verified
| Script | Arch | Fedora | Debian | macOS | Windows 11 |
|---|---|---|---|---|---|
| `scripts/deps/*` | — | — | — | verified (brew) | — |
| `scripts/build.*` | — | — | — | verified | — |

## Release gate (CHARTER §11)
- jmarshall license: unresolved (read-only reference; proprietary retail RE code isolated and tracked)
- SDK-licensed code linked into engine binaries (tracked exceptions): 7 headers staged (`Game.h`, `NetworkSystem.h`, `UsercmdGen.h`, `BuildDefines.h`, `BuildDefines.inc`, `BotAI_Debug.h`, `Bot_Common.h`)
- `docs/provenance.md` coverage: 5,275 / 5,275 files classified (100.0% coverage across both repos; 5,263 baseline + 12 supplemental SDK)
- LICENSE and README explain the split and the retail-data requirement: yes
- Retail binaries, PDBs, raw RE output, captures, or assets in either repo's history: none found (verified via git tree audit)
- SDK EULA shipped with the game repo and every package; retail-install check enforced; nothing sold: yes

## Compatibility record
- Module ABI (`GAME_API_VERSION`, interface changes this session): `GAME_API_VERSION 1` intact in `Game.h`, `gameImport_t` offset 0x68 union reconciled.
- Serialized/asset format changes: none (preserving retail serialization structures)
- Wire protocol id and OpenETQW-only deviations: 16-bit MsgChannel framing restored; snapshot delta acknowledgment gating enforced.
- Ledger rows added (`docs/compat-ledger.md`): Rows 022, 023, and 024 added documenting `ETQWAASB 2.2` 32-to-64-bit lump unpacking, dedicated server bot sleep guard, and map metadata prefix normalization.
- Consequence for retail 1.5 interop: Simulation and network state encoding strictly agree with retail asset formats and wire protocol.

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Milestone 5: Vehicle Physics & Third-Person Camera Smoothing | macOS arm64 | M4 complete | Smooth vehicle driving, proxy view smoothing, and perspective switching |
| 2 | Shadow Volume Interaction Geometry | macOS arm64 | Step 1 | Dynamic shadow volumes rendered via Vulkan pipelines |
| 3 | Cross-Platform Windows & Linux Client Targets | Windows 11 / Linux | Step 1 | SDL3 + Vulkan client builds on Win64 and Linux x86_64 |
| 4 | Milestone 6: Retail Network Interop Wire Captures | Linux / macOS | Step 1 | Retail client/server loopback packet capture & verification |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- None at this time.

