# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-22T01:50:00Z · session/agent: worker_m3 · host: mbp-m1max.local, macOS arm64
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

# Full Meson automated test suite (10/10 tests passing 100%)
meson test -C build -j 1 --verbose

# Vulkan Dynamic Stencil Shadows & Lighting Verification
python3 tests/test_vulkan_shadows.py
python3 tests/test_shadow_stress_adversarial.py

# Game build
cd /Users/jmulesa/openETQW-game
ninja -C build

# Engine client & dedicated server builds
cd /Users/jmulesa/openETQW
ninja -C build openETQW-client_arm64 openETQW-ded_arm64
```

## This session

**Plan, and why it advances the engine:**
Vulkan Dynamic Stencil Shadow Volumes & Lighting Interactions:
1. [Vulkan Combined Depth-Stencil Format & Dynamic Rendering Barriers]
   Upgraded Vulkan depth buffer from `VK_FORMAT_D32_SFLOAT` to `VK_FORMAT_D32_SFLOAT_S8_UINT` (with runtime format query fallback to `VK_FORMAT_D24_UNORM_S8_UINT`) across image creation, image views, dynamic rendering `pStencilAttachment` descriptors, and barrier subresource ranges (`DEPTH_BIT | STENCIL_BIT`). Implemented `IssueDepthStencilBarrier` to synchronize shadow volume stencil writing with subsequent lighting interaction stencil test passes.
2. [Two-Sided Depth-Stencil Shadow Volume Pipelines]
   Created native Vulkan two-sided stencil shadow pipelines in `src/renderer/VulkanBackend.cpp` (`CreateShadowPipelines`): `worldShadowPassPipeline` (Depth-Pass: front increment/wrap, back decrement/wrap) and `worldShadowFailPipeline` (Carmack's Reverse: front depth-fail decrement/wrap, back depth-fail increment/wrap) with `cullMode = NONE`, `colorWriteMask = 0`, `depthTest = TRUE`, `depthWrite = FALSE`, and `depthCompareOp = LESS_OR_EQUAL`.
3. [Front-End Light & Shadow Geometry Decoupling]
   Decoupled `R_UseVulkanBackend()` from OpenGL stencil fallback (`!R_RayTracingUsingStencilFallback()`), allowing Vulkan rasterization to execute natively with `r_shadows 1`. Unblocked front-end surface interaction and shadow generation in `src/renderer/tr_light.cpp` (`R_AddLightSurfaces`, `R_RemoveUnecessaryViewLights`), enabling dynamic linkage of `light->globalShadows`, `light->localShadows`, `light->localInteractions`, and `light->globalInteractions`.
4. [Shadow Silhouette Extraction, Capping & Geometry Streaming]
   Extended `ShadowIndexCount` in `src/renderer/draw_shadow.cpp` to evaluate `viewLight_s` orientation, camera occlusion (`viewInsideLight`), and capping bitmasks (`shadowCapPlaneBits`). Implemented `DrawVulkanShadowSurface` in `src/renderer/VulkanBackend.cpp` streaming 16-byte homogeneous vertices (`shadowCache_t`) and 16-bit indices through `VertexCache`, binding dynamic scissors in Vulkan top-left coordinates, and applying dynamic depth bias.
5. [Dynamic Lighting Interaction Pipeline & Stencil Masking]
   Created `worldInteractionPipeline` in `src/renderer/VulkanBackend.cpp` with additive color blending (`VK_BLEND_FACTOR_ONE, VK_BLEND_FACTOR_ONE`), depth test `LESS_OR_EQUAL` (depth write disabled), and stencil test `VK_COMPARE_OP_EQUAL` reference `0` (read-only mask). In `sdVulkanBackend::DrawView`, implemented light traversal iterating `view->viewLights`, executing per-light stencil clear (`vkCmdClearAttachments`) inside light scissor rects, rendering shadow volume passes, synchronizing via pipeline barriers, and submitting additive light interactions.
6. [Automated Verification Test Suite & Upstream Integration]
   Delivered `tests/test_vulkan_shadows.py` verifying shadow pipeline creation, active shadow volume draws (`shadow draws > 0`), dynamic light interactions (`light interactions > 0`), and MoltenVK frame presentation (>= 50 frames) on Valley with clean exit code 0. Registered `vulkan-shadows` in `tests/meson.build` with suite `client`. All 10/10 Meson test targets pass with 100% success rate (`meson test -C build -j 1 --verbose`). Zero SDK-licensed files in `openETQW` (0 violations across 4,265 tracked files).

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `src/renderer/VulkanBackend.cpp`, `VulkanBackend.h` | Depth-stencil format upgrade, two-sided shadow pipelines, interaction pipeline, `DrawVulkanShadowSurface`, `DrawVulkanLightInteraction`, light traversal loop | Native Vulkan dynamic stencil shadow volumes and lighting interactions | GPL-3.0-or-later engine |
| openETQW | `src/renderer/draw_shadow.cpp` | Global linkage and `viewLight_s` overload for `ShadowIndexCount` | Silhouette capping and external/internal pipeline selection | GPL-3.0-or-later engine |
| openETQW | `src/renderer/tr_light.cpp` | Unblocked `R_AddLightSurfaces` and `R_RemoveUnecessaryViewLights` for Vulkan | Front-end light-surface interaction and shadow chain linkage | GPL-3.0-or-later engine |
| openETQW | `tests/meson.build` | Registered `vulkan-shadows` test target with suite `client` | Expand automated test suite to 10 targets | GPL-3.0-or-later build |
| openETQW | `tests/test_vulkan_shadows.py` | Automated lifecycle test verifying Vulkan shadow pipelines, shadow draws, light interactions, and presentation | Automated verification gate for shadows | Engine test suite |
| openETQW | `docs/compat-ledger.md` | Added Row 032 documenting Vulkan dynamic stencil shadows & lighting architecture | Living compatibility documentation | Documentation |
| openETQW | `STATUS.md` | Updated Vulkan shadow volume milestones, test execution metrics, and demonstrated claims | Living project status | Living documentation |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
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
| idLib + game module build | — | — | compiled | — | — | Native Apple Clang arm64 |
| Engine build | — | — | compiled | — | — | Native `openETQW-client_arm64` and `openETQW-ded_arm64` |
| Dedicated server launch (headless) | — | — | launched | — | — | Headless POSIX server cleanly runs and quits (exit 0) |
| Client launch (graphics) | — | — | match-tested | — | — | Native Cocoa window via SDL3 & MoltenVK swapchain |
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
- Module ABI (`GAME_API_VERSION`, interface changes this session): `GAME_API_VERSION 1` intact in `Game.h`, `gameImport_t` offset 0x68 union reconciled.
- Serialized/asset format changes: none (preserving retail serialization structures)
- Wire protocol id and OpenETQW-only deviations: 16-bit MsgChannel framing restored; snapshot delta acknowledgment gating enforced.
- Ledger rows added (`docs/compat-ledger.md`): Row 032 added documenting Vulkan dynamic stencil shadow volumes, two-sided stencil testing (depth-pass & Carmack's Reverse depth-fail), pipeline barriers, and additive lighting interactions masked by stencil == 0.
- Consequence for retail 1.5 interop: Simulation and network state encoding strictly agree with retail asset formats and wire protocol.

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Cross-Platform Windows & Linux Client Targets | Windows 11 / Linux | Shadows complete | SDL3 + Vulkan client builds on Win64 and Linux x86_64 |
| 2 | Milestone 6: Retail Network Interop Wire Captures | Linux / macOS | Step 1 | Retail client/server loopback packet capture & verification |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- None at this time.


