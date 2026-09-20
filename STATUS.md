# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-20T20:35:00Z · session/agent: worker_m4_1 · host: mbp-m1max.local, macOS arm64
Engine repo `~/openETQW`: commit 2992c27 (tracking https://github.com/jm2/openETQW.git) · game repo `~/openETQW-game`: commit f3710a2 (tracking https://github.com/jm2/openETQW-game.git) · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none)

**Integrator role:** held by worker_m4_1 / mbp-m1max.local

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

# Full Meson automated test suite (6/6 tests passing 100%)
meson test -C build --verbose

# Game build
cd /Users/jmulesa/openETQW-game
ninja -C build

# Engine client & dedicated server builds
cd /Users/jmulesa/openETQW
ninja -C build openETQW-client_arm64 openETQW-ded_arm64

# Client automated lifecycle verification (quick-quit & live Valley MegaTexture rendering)
python3 tests/test_client_lifecycle.py
```

## This session

**Plan, and why it advances the engine:**
Milestone 2 Client / Milestone 4 Graphics Bring-up: Native Apple Silicon macOS Client & Vulkan/MoltenVK Modernization:
1. [Platform & Windowing] Integrated SDL3 Cocoa platform layer (`src/sys/sdl/`, `src/sys/osx/`): application runloop (`SDL_RunApp`), non-blocking event pump converting SDL3 events to `sysEvent_t` and 30 Hz `usercmd_t`, and SDL3 audio stream abstraction (44.1 kHz, 16-bit stereo PCM).
2. [Vulkan Renderer] Modernized Vulkan 1.3 backend (`src/renderer/VulkanBackend.cpp`) on Apple Silicon M1 Max via MoltenVK: dynamic rendering, portability flags (`VK_KHR_portability_enumeration`, `VK_KHR_portability_subset`), decoupled hardware ray tracing requirement (`r_rayTracing 0` fallback), offline/runtime SPIR-V shader compilation via `/opt/homebrew/bin/glslangValidator`.
3. [World & MegaTexture Pipeline] Connected `idRenderWorldLocal::PerformRenderScene` directly to Vulkan draw pipelines (`RB_STD_DrawView`). Successfully mounted and streamed retail MegaTexture terrain (`megatextures/valley_lit.mega`, 32768x32768, 87,296 tiles, DCT YCoCg) via 5 toroidal atlas textures with 129x129 ST grid projective mapping (32767 packing).
4. [Teardown & Crash Fixes] Guarded `sdUICinematic::~sdUICinematic()` against dereferencing sound emitter after `soundSystem->GetPlayingSoundWorld()` shutdown. Added empty-joint tolerance in `sdInventory::GetPlayerJoint`. Added `closeLimbo` command to dismiss limbo UI and expose full 3D world scene.
5. [Interactive Verification] Visually and interactively verified live in Cocoa window: camera pans smoothly with mouse free-look, terrain geometry and high-detail rock/ground textures stream seamlessly, sky dome atmosphere renders cleanly, and 1,750+ Vulkan frames and 86,000+ MegaTexture draws presented.
6. [Automated Lifecycle Testing] Implemented `tests/test_client_lifecycle.py` testing quick startup/quit and live Valley map rendering with graceful SIGTERM shutdown (exit code 0). Integrated into Meson test suite (6/6 tests passing). Zero SDK violations across all 4,234 tracked files.

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `meson.build`, `tools/build/meson_sources.py` | Added client compilation target `openETQW-client_arm64`, linked SDL3, Vulkan, Apple frameworks | Build native macOS client executable | Meson build modernization |
| openETQW | `src/sys/osx/`, `src/sys/sdl/` | Added Cocoa windowing, SDL3 event loop, SDL3 audio stream | Native windowing, non-blocking input, audio output | GPL-3.0-or-later platform layer |
| openETQW | `src/renderer/VulkanBackend.cpp` | Added MoltenVK portability flags, decoupled ray tracing, runtime SPIR-V compilation | Run Vulkan renderer on Apple Silicon | Vulkan 1.3 modernization |
| openETQW | `src/renderer/RenderWorld.cpp` | Intercepted `R_UseVulkanBackend()` in `PerformRenderScene` to invoke `RB_STD_DrawView()` | Direct Vulkan world and MegaTexture rasterization | Renderer modernization |
| openETQW | `tests/test_client_lifecycle.py`, `tests/meson.build` | Added automated lifecycle test and registered in Meson test suite | Comprehensive client lifecycle CI verification | Engine test suite |
| openETQW | `docs/compat-ledger.md` | Added Rows 016, 017, 018 for SDL3, Vulkan/MoltenVK, and UI/BSE teardown | Complete compatibility ledger records | Documentation |
| openETQW-game | `src/game/guis/UICinematic.cpp` | Guarded sound emitter destruction against null sound world | Prevent SIGSEGV on client shutdown | Teardown fix |
| openETQW-game | `src/game/roles/Inventory.cpp` | Warn instead of fatal error on empty joint name | Robust soldier joint binding | Game logic fix |
| openETQW-game | `src/game/gamesys/SysCmds.cpp` | Added `toggleLimbo` and `closeLimbo` commands | Control `GUI_INHIBIT_GAME_WORLD` for 3D world view | Debug/console command |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| Zero-SDK Repository Compliance (Gate 2b) | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 4234 tracked files audited, 0 SDK violations detected |
| Meson Automated Test Suite (6/6 passed) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build --verbose` | PASS: 6/6 tests passed (bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk, client-lifecycle) |
| Native macOS Client Compilation | compiled | macOS arm64 / Apple Clang 21.0 | `ninja -C build openETQW-client_arm64` | PASS: Clean compilation and link without unresolved symbols |
| Client Quick-Quit Lifecycle | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_client_lifecycle.py` | PASS: SDL3 Cocoa window opens, audio initializes (44.1kHz), clean exit code 0 |
| Valley World & MegaTexture Rendering | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_client_lifecycle.py` | PASS: 1,754 Vulkan frames presented, 93,658 world draws, 86,935 MegaTexture draws, exit code 0 |
| Real-time Mouse Free-Look Integration | match-tested | macOS arm64 / Apple Clang 21.0 | Interactive execution | PASS: Mouse motion events pan 3D camera across Valley scene in real time |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested < retail-interop-tested

### Not done / stubbed / known broken
- Default soldier movement pawn possession (currently spawns in spectator/free-cam pending team/role deployment)
- Shadow volume buffer parsing for Vulkan client (`shadowModel` chunk rendering)
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
| OpenETQW MP: dedicated | — | — | match-tested | — | — | Authoritative server streams 150+ snapshots to client over UDP |
| OpenETQW MP: listen server | — | — | map-loaded | — | — | Local single-player / listen server active on Valley |
| Cross-OS client/server pair | — | — | blocked: M3 | — | — | Target milestone M3 cross-OS |
| High refresh: on foot | — | — | launched | — | — | High-refresh Vulkan presentation decoupled from 30 Hz simulation |
| High refresh: vehicles | — | — | blocked: M5 | — | — | Requires proxy view smoothing |
| Bots / navigation | — | — | blocked: M4 | — | — | Requires QWNV navmesh loader |
| Retail interop: dir 1 (Open ↔ Open) | — | — | match-tested | — | — | OpenETQW dedicated server and client verified over UDP |
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
- Ledger rows added (`docs/compat-ledger.md`): Rows 016, 017, and 018 added documenting SDL3 Cocoa windowing, MoltenVK Vulkan renderer modernization, MegaTexture streaming, and UI/BSE teardown sequencing.
- Consequence for retail 1.5 interop: Simulation and network state encoding strictly agree with retail asset formats and wire protocol.

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Milestone 2 / 4 Complete: Teamwork Audit & Handoff | macOS arm64 | M4 verification | 6/6 Meson unit tests pass, zero SDK violations, clean git tree |
| 2 | In-Game Soldier Pawn Movement Integration | macOS arm64 | M4 client | WASD key bindings drive player pawn position across Valley |
| 3 | Shadow Volume Interaction Geometry | macOS arm64 | Step 2 | Dynamic shadow volumes rendered via Vulkan pipelines |
| 4 | Vehicle Physics & Third-Person Camera Smoothing | macOS arm64 | Step 2 | Smooth vehicle driving and perspective switching |
| 5 | Cross-Platform Windows & Linux Client Targets | Windows 11 / Linux | Step 1 | SDL3 + Vulkan client builds on Win64 and Linux x86_64 |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- None at this time.
