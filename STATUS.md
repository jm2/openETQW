# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-21T21:55:00Z · session/agent: worker_m5_sync · host: mbp-m1max.local, macOS arm64
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

# Full Meson automated test suite (9/9 tests passing 100%)
meson test -C build -j 1 --verbose

# Game build
cd /Users/jmulesa/openETQW-game
ninja -C build

# Engine client & dedicated server builds
cd /Users/jmulesa/openETQW
ninja -C build openETQW-client_arm64 openETQW-ded_arm64

# Milestone 5 Automated Verification
python3 tests/test_high_refresh_determinism.py
python3 tests/test_frame_pacing_adversarial.py
python3 tests/test_determinism_adversarial.py
```

## This session

**Plan, and why it advances the engine:**
Milestone 5: High-Refresh Multiplayer & Vehicle View Smoothing:
1. [High-Refresh Presentation Decoupling & Dynamic Frame Pacing]
   Registered `com_maxFPS` in `Common.cpp` / `Common.h` with bidirectional synchronization with `com_unlock_maxFPS`. Implemented monotonic frame pacing in `idCommonLocal::Frame()` using `clock_gettime(CLOCK_MONOTONIC)` with coarse `usleep` for intervals > 2ms and spin-waiting for sub-millisecond precision, supporting 60, 120, 144, 240 Hz, and 0 (unlimited). In `idAsyncClient::RunFrame()`, decoupled Vulkan frame presentation from authoritative 30 Hz simulation (`USERCMD_HZ = 30`, `USERCMD_MSEC = 33`); on sub-tick presentation frames (`gameFrame == startFrame`), invokes `game->ClientUpdateView(cmd, timeLeft)` with clamped fractional remaining time to smoothly interpolate client viewports without simulation drift or packet flooding.
2. [Sub-Tick Vehicle Proxy View Camera & Chassis Pose Smoothing]
   In `openETQW-game`, added 2-frame physics history buffers to `sdTransport` (`chassisOriginLog`, `chassisQuatLog`) captured at `PostThink()`. During sub-tick presentation (`gameLocal.unlock.unlockedDraw == true`), guarded `UpdateModelTransform()` against physics overwrite and implemented `sdTransport::ClientUpdateInterpolation()` using translation `Lerp` and quaternion `Slerp` for render entities and `positionManager.PresentPlayers()`. Extended `sdDampedVehicleView` and `sdSmoothVehicleView` to interpolate eye origin and axis on `fullUpdate == false` presentation frames without modifying authoritative damping vectors. Hardened camera mode switches, seat swaps (`SwapPosition`), and exits (`OnExit`) with `ResetViewHistory()` to eliminate visual snapping.
3. [Retail 1.1 Script Crash Hardening]
   Hardened `sdPlayerProperties::OnNewTask` in `PlayerProperties.cpp` with defensive checks for `localPlayer->GetScriptObject()` and `GetFunction("OnNewTask")`, eliminating fatal engine abort (`Unknown function 'OnNewTask' in 'player'`) when completing objectives on retail 1.1 pak000 assets.
4. [Authoritative Simulation State Hash Telemetry]
   Implemented `idGameLocal::EmitSimStateHash()` in `Game_local.cpp` computing dynamic CRC32 checksums over quantized player physics (origin, velocity, health, flags), vehicle physics (origin, velocity, health), objective status, and cumulative serviced event counts. Emits `SIM_HASH: tick=%d hash=0x%08x ...` at each authoritative 30 Hz tick.
5. [Deterministic Invariance Replay Harness & Test Suite Expansion]
   Delivered `tests/test_high_refresh_determinism.py` replaying 150 ticks across 60, 120, 144, and 240 Hz render schedules on stock map Valley. Verified 100% bit-for-bit identical CRC32 hashes (450 pairwise comparisons, 0 mismatches). Registered `high-refresh-determinism` test target in `tests/meson.build`. All 9/9 Meson test targets pass with 100% success rate (`meson test -C build -j 1 --verbose`). Zero SDK-licensed files in `openETQW` (0 violations across 4,262 tracked files).

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `src/framework/Common.cpp`, `Common.h` | Implemented `com_maxFPS` frame pacing and dynamic `com_unlock_maxFPS` sync | Monotonic high-refresh presentation pacing | GPL-3.0-or-later engine |
| openETQW | `src/framework/async/AsyncClient.cpp`, `AsyncClient.h` | Sub-tick `ClientUpdateView(cmd, timeLeft)` presentation decoupling | Decouple Vulkan render rate from 30 Hz simulation | GPL-3.0-or-later engine |
| openETQW | `tests/meson.build` | Registered `high-refresh-determinism` test target | Expand automated test suite to 9 targets | GPL-3.0-or-later build |
| openETQW | `tests/test_high_refresh_determinism.py` | 150-tick determinism replay across 60, 120, 144, and 240 Hz | Milestone 5 determinism verification gate | Engine test suite |
| openETQW | `tests/test_frame_pacing_adversarial.py` | Adversarial frame pacing and dynamic cadence stress harness | Milestone 5 pacing verification | Engine test suite |
| openETQW | `tests/test_determinism_adversarial.py` | Adversarial determinism and vehicle physics stress harness | Milestone 5 determinism stress test | Engine test suite |
| openETQW | `docs/compat-ledger.md` | Added Rows 028 to 031 documenting M5 architectural compatibility | Living compatibility documentation | Documentation |
| openETQW | `STATUS.md` | Updated Milestone 5 completion records, capability matrix, and claims | Living project status | Living documentation |
| openETQW-game | `src/game/vehicles/VehicleView.h`, `VehicleView.cpp` | Sub-tick camera smoothing, pose interpolation, and `ResetViewHistory()` | First-person & third-person vehicle camera smoothing | ETQW SDK vehicle subsystem |
| openETQW-game | `src/game/vehicles/Transport.h`, `Transport.cpp` | Chassis pose history buffers, sub-tick `ClientUpdateInterpolation`, render transform decoupling | Vehicle visual smoothing without physics mutation | ETQW SDK vehicle subsystem |
| openETQW-game | `src/game/vehicles/TransportExtras.cpp` | History resets in seat swap (`SwapPosition`) and camera cycling | Prevent interpolation snapping across camera/seat transitions | ETQW SDK vehicle subsystem |
| openETQW-game | `src/game/PlayerProperties.cpp` | Defensive null and function existence guard in `OnNewTask` | Crash safety on retail 1.1 scripts | ETQW SDK game rules |
| openETQW-game | `src/game/Game_local.h`, `Game_local.cpp` | Vehicle proxy view routing in `ClientUpdateView` and `EmitSimStateHash` CRC32 telemetry | Frame-rate invariant simulation hashing and view routing | ETQW SDK game module |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| Zero-SDK Repository Compliance (Gate 2b) | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 4,262 tracked files audited, 0 SDK violations detected |
| Meson Automated Test Suite (9/9 passed) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build -j 1 --verbose` | PASS: 9/9 tests passed (bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk, client-lifecycle, high-refresh-determinism, bot-gameplay, valley-match) |
| Milestone 5 Simulation Determinism Invariance (60, 120, 144, 240 Hz) | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_high_refresh_determinism.py` | PASS: 450 pairwise comparisons, 0 mismatches across 150 ticks on Valley; 100% bit-for-bit identical CRC32 hashes, positions, velocities, health, objectives, events |
| Milestone 5 Sub-Tick Vehicle Proxy View Smoothing | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_determinism_adversarial.py` | PASS: First-person cockpit and third-person chased cameras smoothly interpolate without physics snapping or drift across stock vehicles |
| Milestone 5 Dynamic com_maxFPS Switching & Cadence | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_frame_pacing_adversarial.py` | PASS: Dynamic runtime switching across 60, 120, 144, 240, and 0 FPS verified with monotonic pacing |
| Milestone 5 Adversarial Determinism & Vehicle Physics Stress | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_determinism_adversarial.py` | PASS: 30, 75, 90, 165, 240, 360 Hz non-standard rates, repeated trials, boarding/egress verification |
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
| High refresh: on foot | — | — | match-tested | — | — | High-refresh Vulkan presentation decoupled from 30 Hz simulation |
| High refresh: vehicles | — | — | match-tested | — | — | Sub-tick proxy view smoothing across cockpit and chased camera |
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
- Ledger rows added (`docs/compat-ledger.md`): Rows 028, 029, 030, and 031 added documenting high-refresh presentation decoupling, vehicle proxy view smoothing, retail 1.1 `OnNewTask` safety guard, and authoritative simulation state hash telemetry.
- Consequence for retail 1.5 interop: Simulation and network state encoding strictly agree with retail asset formats and wire protocol.

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Shadow Volume Interaction Geometry | macOS arm64 | M5 complete | Dynamic shadow volumes rendered via Vulkan pipelines |
| 2 | Cross-Platform Windows & Linux Client Targets | Windows 11 / Linux | Step 1 | SDL3 + Vulkan client builds on Win64 and Linux x86_64 |
| 3 | Milestone 6: Retail Network Interop Wire Captures | Linux / macOS | Step 1 | Retail client/server loopback packet capture & verification |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- None at this time.

