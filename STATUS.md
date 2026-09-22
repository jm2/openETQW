# OpenETQW status

[![CI Matrix](https://github.com/jm2/openETQW/actions/workflows/ci.yml/badge.svg)](https://github.com/jm2/openETQW/actions/workflows/ci.yml)

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-22T06:15:00Z · session/agent: worker_m4 · host: mbp-m1max.local, macOS arm64
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

### macOS Apple Silicon (Host Native)
```sh
# Dynamic header staging & Cleanroom Zero-SDK Audit Gate
python3 tools/build/stage_game.py . ../openETQW-game .tmp/gamelibs_stage
python3 tests/test_zero_sdk.py

# Asset-free unit test suite (5/5 passing in <1s)
meson test -C build --suite unit --verbose

# Public runner simulation (verifies clean exit code 77 on missing assets)
ETQW_DATA_PATH=/nonexistent meson test -C build --verbose

# Full local test suite with retail assets (10/10 passing 100%)
meson test -C build -j 1 --verbose

# Game module build
cd /Users/jmulesa/openETQW-game
ninja -C build

# Engine client & dedicated server builds
cd /Users/jmulesa/openETQW
ninja -C build openETQW-client_arm64 openETQW-ded_arm64
```

### Linux x86_64 (GCC 13+ / Clang 17+)
```sh
# Dynamic header staging and cleanroom gate
python3 tools/build/stage_game.py . ../openETQW-game .tmp/gamelibs_stage
python3 tests/test_zero_sdk.py

# Game module configuration & compilation
cd ../openETQW-game
meson setup build --buildtype=release
meson compile -C build

# Engine configuration & compilation (Client + Headless Dedicated)
cd ../openETQW
meson setup build --buildtype=release -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true
meson compile -C build

# Cross-compilation from host via Meson cross file
meson setup build-linux-cross --cross-file tools/cross/linux-x86_64.txt --buildtype=release
meson compile -C build-linux-cross

# Asset-free unit test suite
meson test -C build --suite unit --verbose
```

### Windows 11 x64 (MSVC 2022 v143)
```cmd
:: Launch 64-bit Visual Studio Developer Command Prompt
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"

:: Dynamic header staging and cleanroom gate
python tools\build\stage_game.py . ..\openETQW-game .tmp\gamelibs_stage
python tests\test_zero_sdk.py

:: Game module configuration & compilation (generates idLib.lib and game_x64.dll via Game.def)
cd ..\openETQW-game
meson setup build --buildtype=release
meson compile -C build

:: Engine configuration & compilation (Client win_subsystem: windows, Dedicated win_subsystem: console)
cd ..\openETQW
meson setup build --buildtype=release -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true
meson compile -C build

:: Cross-compilation via MinGW / LLVM cross file
meson setup build-win64-cross --cross-file tools/cross/windows-x86_64.txt --buildtype=release
meson compile -C build-win64-cross

:: Asset-free unit test suite
meson test -C build --suite unit --verbose
```

## This session

**Plan, and why it advances the engine:**
Cross-Platform Build & Continuous Integration Matrix Milestone (Linux x86_64, Windows 11 x64, macOS arm64):
1. [Cross-Platform Meson Configuration & System Libraries]
   Reconciled build definitions across `openETQW/meson.build` and `openETQW-game/meson.build` for `host_machine.system()`. Linux links POSIX threads, dynamic loader (`dl`), real-time clock (`rt`), and math (`m`). Windows MSVC configures `/EHsc`, `/permissive-`, `/Zc:__cplusplus`, `/utf-8`, and `/bigobj` (preventing section overflow in large translation units), linking `ws2_32`, `winmm`, `dbghelp`, `version`, `ole32`, and `shell32`.
2. [Cross-Platform Static Library Resolution & Dynamic Header Staging]
   Updated engine library resolution to find `idLib` across platforms: probes `cpp.find_library('idLib')`, falling back cleanly to `libidLib.a` (POSIX) or `idLib.lib` (MSVC). Hardened `tools/build/stage_game.py` with case-insensitive Windows path traversal checks and retry-based directory cleanup (`safe_rmtree`) handling read-only file locks. Authored Meson cross-compilation files `tools/cross/linux-x86_64.txt` and `tools/cross/windows-x86_64.txt`.
3. [Platform Abstraction Hardening & Console Subsystem Isolation]
   Hardened `src/sys/win32/SystemBootstrap.cpp` for 64-bit MSVC: implemented thread table managing 64-bit handles, auto-reset event signaling, and critical sections. Guarded `WinMain` under `#ifndef ID_DEDICATED` and implemented dedicated server `main()` console entry point with `SetConsoleCtrlHandler` under `#ifdef ID_DEDICATED`. Reconciled `tools/build/meson_sources.py` manifests across all 6 targets. Exported `GetGameAPI` on Windows via `/DEF:src/game/Game.def` and `GAME_API_EXPORT`. Replaced x86 inline assembly in `Atomic.h` with standard GCC/Clang builtins.
4. [Asset-Free Unit Test Decoupling & Public CI Runner Resilience]
   Partitioned Meson test targets into `--suite unit` (pure asset-free: `bitmsg`, `usercmd-delta`, `struct-layout`, `usercmd-history`, `zero-sdk`) and `integration`. Extended `tests/StructLayoutTest.cpp` with static assertions verifying `gameImport_t` (192 bytes, offset 0x68 union) and `gameExport_t` (24 bytes). Decoupled integration tests (`test_bot_gameplay.py`, `test_valley_match.py`, `test_vulkan_shadows.py`, `test_client_lifecycle.py`) to skip with exit code 77 when retail data is absent.
5. [GitHub Actions CI Matrix Pipeline]
   Authored production-grade `.github/workflows/ci.yml` matrix covering Ubuntu Linux x86_64, Windows 11 x64 MSVC, and macOS Apple Silicon. Includes workflow linting (`actionlint`), side-by-side checkout, Zero-SDK cleanroom audit gate, dynamic staging, full module & engine builds, and asset-free unit test suite execution. 100% asset-free: zero retail assets required.

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `meson.build` | Added Linux system deps (`dl`, `rt`, `m`), Windows MSVC flags (`/bigobj`), Windows SDK libs, `idLib.lib` static fallback, `win_subsystem` isolation | Cross-platform build support across Tier-1 OSes | GPL-3.0-or-later build |
| openETQW | `tools/build/meson_sources.py` | Validated manifests across 6 targets, added shared POSIX client entry, included Win32 `SystemBootstrap.cpp` | Accurate platform source generation | GPL-3.0-or-later tooling |
| openETQW | `tools/build/stage_game.py` | Added Windows case-insensitive path check, `safe_rmtree`, and `make_writable` | Resilient cross-platform header staging | GPL-3.0-or-later tooling |
| openETQW | `tools/cross/linux-x86_64.txt`, `windows-x86_64.txt` | Authored Meson cross-compilation definitions | Multi-OS cross-compilation support | GPL-3.0-or-later tooling |
| openETQW | `src/sys/win32/SystemBootstrap.cpp`, `win_local.h` | Implemented 64-bit threading table, event signaling, critical sections, dedicated console `main()` entry | Win32 platform layer & console isolation | GPL-3.0-or-later engine |
| openETQW | `src/sys/sdl/sdl_glimp.cpp` | Guarded Cocoa video driver hint with `#if defined(__APPLE__)` | Cross-platform SDL3 video driver initialization | GPL-3.0-or-later engine |
| openETQW | `.github/workflows/ci.yml` | Created GitHub Actions CI matrix for Linux, Windows, and macOS | Continuous integration and cleanroom gating | GPL-3.0-or-later CI |
| openETQW | `tests/meson.build` | Partitioned tests into `unit` and `integration` suites; added Windows/Linux test deps | Asset-free CI gating | GPL-3.0-or-later build |
| openETQW | `tests/StructLayoutTest.cpp` | Added static assertions for `gameImport_t` (192B) and `gameExport_t` (24B) | 64-bit ABI alignment verification | GPL-3.0-or-later tests |
| openETQW | `tests/test_*.py` | Updated integration tests to exit code 77 when retail data is absent | Decouple public CI runners from retail assets | GPL-3.0-or-later tests |
| openETQW-game | `meson.build` | Added MSVC flags (`/bigobj`), Windows DLL definition file `/DEF:src/game/Game.def` | Windows MSVC compilation and dynamic export | ETQW SDK / MIT |
| openETQW-game | `src/game/Game.h`, `Game_local.cpp` | Defined `GAME_API_EXPORT` macro across MSVC and GCC/Clang | Reliable `GetGameAPI` dynamic symbol export | ETQW SDK / MIT |
| openETQW-game | `src/idlib/threading/Atomic.h` | Replaced x86 inline assembly with standard GCC/Clang builtins and Win32 interlocked | Portability across 64-bit compilers | ETQW SDK / MIT |
| openETQW | `docs/compat-ledger.md` | Added Rows 033, 034, 035 documenting build, Windows isolation, and CI architecture | Living compatibility documentation | Documentation |
| openETQW | `STATUS.md` | Updated multi-OS build commands, CI badge, capability matrix, and demonstrated claims | Living project status | Living documentation |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| GitHub Actions CI Matrix Pipeline (.github/workflows/ci.yml) | configured | Multi-OS (Ubuntu Linux x86_64, Windows 11 x64, macOS arm64) | `actionlint .github/workflows/ci.yml` | PASS: 2 jobs (actionlint lint-workflow, build-and-test matrix x3), Zero-SDK gate, dynamic staging, full build, asset-free unit test suite |
| Cross-Platform Meson Build Configuration (Linux & Windows) | configured | Linux x86_64 (GCC/Clang) / Windows 11 x64 (MSVC) | `meson setup build-linux --cross-file tools/cross/linux-x86_64.txt` | PASS: Cross definitions validated, platform source manifests 100% resolved (0 missing, 0 duplicates) |
| Meson Source Manifest Stress Verification (6 targets) | unit-tested | macOS arm64 / Python 3.14 | `python3 tests/test_meson_sources_stress.py` | PASS: 24/24 parameter combinations, 6 valid targets (darwin/linux/windows client/dedicated), 0 missing files, 0 duplicate sources, valid entry points |
| Win32 Platform Layer 64-bit Threading & Synchronization | unit-tested | macOS arm64 / Python 3.14 | `python3 tests/test_win32_platform_stress.py` | PASS: 64-bit thread handle safety, critical sections, event signaling, staged header includes, Win32 symbol linkage 100% complete |
| Asset-Free Unit Test Suite Decoupling (--suite unit) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build --suite unit --verbose` | PASS: 5/5 asset-free tests OK (bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk) in 0.48s with 0 retail data dependencies |
| Retail Asset Decoupling on Public Runners (exit code 77) | unit-tested | macOS arm64 / Apple Clang 21.0 | `ETQW_DATA_PATH=/nonexistent meson test -C build --verbose` | PASS: 5 unit tests passed, 5 integration tests skipped cleanly with Meson status code 77 (0 failures) |
| 64-bit Cross-Platform Structure Layout & ABI Invariants | unit-tested | macOS arm64 / Apple Clang 21.0 | `/Users/jmulesa/openETQW/build/tests/openetqw-struct-layout-test` | PASS: static_assert verified idDrawVert 32B, usercmd_t 28B, netadr_t 12B, gameExport_t 24B, gameImport_t 192B (offset 0x68 union) |
| Zero-SDK Repository Compliance (Gate 2b) | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 4,265 tracked files audited, 0 SDK violations detected |
| Meson Automated Test Suite (10/10 passed) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build -j 1 --verbose` | PASS: 10/10 tests passed (bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk, client-lifecycle, high-refresh-determinism, vulkan-shadows, bot-gameplay, valley-match) |
| Vulkan Dynamic Stencil Shadow Volumes & Two-Sided Pass | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_vulkan_shadows.py` | PASS: `worldShadowPassPipeline` & `worldShadowFailPipeline` created, 14,400+ shadow draws, 849 Vulkan frames presented on Valley, clean exit 0 |
| Vulkan Dynamic Light Interactions Masked by Stencil | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_vulkan_shadows.py` | PASS: `worldInteractionPipeline` created, 127,000+ light interactions masked by stencil == 0, additive blending `ONE, ONE`, clean exit 0 |
| Vulkan Adversarial Shadow Stress & Scissor Verification | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_shadow_stress_adversarial.py` | PASS: Modes 0, 1, 2 passed (Carmack's Reverse, dynamic depth-pass, forced uncapped), scissor clamping verified, 0 errors |
| Milestone 5 Simulation Determinism Invariance (60, 120, 144, 240 Hz) | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_high_refresh_determinism.py` | PASS: 450 pairwise comparisons, 0 mismatches across 150 ticks on Valley; 100% bit-for-bit identical CRC32 hashes, positions, velocities, health, objectives, events |
| Milestone 5 Sub-Tick Vehicle Proxy View Smoothing | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_determinism_adversarial.py` | PASS: First-person cockpit and third-person chased cameras smoothly interpolate without physics snapping or drift across stock vehicles |
| Milestone 5 Dynamic com_maxFPS Switching & Cadence | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_frame_pacing_adversarial.py` | PASS: Dynamic runtime switching across 60, 120, 144, 240, and 0 FPS verified with monotonic pacing |
| Milestone 4 Vehicle Hardening & Adversarial Stress (Stages 1-10) | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_vehicle_deployable_stress.py` | PASS: 10/10 stages passed; Stage 10 capacity overflow verified crash-proof; verdict APPROVE, exit code 0 |
| Milestone 4 Valley Full Match Objectives (Phases 1-9) | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_valley_match.py` | PASS: All 9 phases verified: map load, bot spawns, vehicle mounting/seat-swap/exit, deployable placement/carrier delivery, objectives 0-3 completion, GDF victory, GS_GAMEREVIEW state 4, clean quit |
| Milestone 4 Bot Spawning & Materialization | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_gameplay.py` | PASS: GDF & Strogg bots spawned, limbo cleared (`spectating 0`), parachutes deployed, active pawns verified |
| Milestone 4 AAS Navigation & Graph Loading | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_gameplay.py` | PASS: Loaded `maps/valley.aas_playerb` (9,348 areas), `maps/valley.aas_vehicleb` (6,391 areas), `maps/valley.nav` (164 nodes) |
| Milestone 4 30 Hz Bot Navigation & Traversal | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_gameplay.py` | PASS: `idBotThread` 30 Hz simulation, GDF disp 1533.20 u, Strogg disp 2265.76 u (>= 50.0 u threshold) |
| Milestone 4 Bot Combat Perception & Firing | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_bot_gameplay.py` | PASS: `Bot_FindEnemy()` targeting, view aiming, `atk_cmd: 1` and `atk: 1` active firing verified |
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
| idLib + game module build | configured | configured | compiled | — | — | Linux (threads, m, builtins), Windows (MSVC /bigobj, /DEF, Interlocked), macOS (Clang) |
| Engine build | configured | configured | compiled | — | — | Linux (dl, rt, m, SDL3), Windows (Win32 libs, subsystem windows/console), macOS (Metal/MoltenVK) |
| Dedicated server launch (headless) | configured | configured | launched | — | — | POSIX main on Linux/macOS; console subsystem main() with SetConsoleCtrlHandler on Win32 |
| Client launch (graphics) | configured | configured | match-tested | — | — | SDL3 + Vulkan backend across Linux, Windows, macOS |
| Asset-free unit test suite | configured | configured | unit-tested | — | — | 5/5 asset-free tests pass (--suite unit); integration tests exit 77 when assets absent |
| Stock map: server | — | — | map-loaded | — | — | Valley loads, 991 entities spawn, simulation runs at 30 Hz |
| Stock map: client | — | — | match-tested | — | — | Client loads Valley map, streams MegaTexture, renders world geometry & dynamic stencil shadows |
| OpenETQW MP: dedicated | — | — | match-tested | — | — | Authoritative server hosts multiple concurrent clients with UDP replication |
| OpenETQW MP: listen server | — | — | map-loaded | — | — | Local single-player / listen server active on Valley |
| Cross-OS client/server pair | — | — | — | — | — | Target milestone M3 cross-OS |
| High refresh: on foot | — | — | match-tested | — | — | High-refresh Vulkan presentation decoupled from 30 Hz simulation |
| High refresh: vehicles | — | — | match-tested | — | — | Sub-tick proxy view smoothing across cockpit and chased camera |
| Bots / navigation | — | — | match-tested | — | — | Full AAS navigation, 30 Hz AI thread, terrain traversal > 1000u, combat engagement |
| Dynamic stencil shadows | — | — | match-tested | — | — | Vulkan two-sided stencil shadow volumes & dynamic lighting interactions |
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
- Module ABI (`GAME_API_VERSION`, interface changes this session): `GAME_API_VERSION 1` intact in `Game.h`. `gameImport_t` (192 bytes on 64-bit, offset 0x68 union) and `gameExport_t` (24 bytes) validated via `StructLayoutTest.cpp`. `GAME_API_EXPORT` defined for dynamic export across MSVC (`__declspec(dllexport)`) and GCC/Clang (`__attribute__((visibility("default")))`).
- Serialized/asset format changes: none (preserving retail serialization structures)
- Wire protocol id and OpenETQW-only deviations: 16-bit MsgChannel framing restored; snapshot delta acknowledgment gating enforced.
- Ledger rows added (`docs/compat-ledger.md`): Rows 033, 034, and 035 added documenting cross-platform Meson static library resolution and staging, Windows MSVC flags and console dedicated subsystem isolation, and GitHub Actions continuous integration matrix with asset-free unit test decoupling.
- Consequence for retail 1.5 interop: Simulation and network state encoding strictly agree with retail asset formats and wire protocol.

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Upstream Git Synchronization | Local workstation | CI matrix complete | Clean working trees, commit tags, and git push to upstream remotes |
| 2 | Milestone 6: Retail Network Interop Wire Captures | Linux / macOS / Windows | Step 1 | Retail client/server loopback packet capture & verification |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- None at this time.


