# OpenETQW Provenance & Licensing Classification

> This document classifies 100% of files imported from the upstream baseline (`~/etqw` at commit `f2b655c992b47e0eaca7c178b0dae490abc1544e`) and the ETQW 1.5 SDK, details the two-repository partition under Owner Decision D5, and maintains the Tracked Exceptions Registry for engine-staged game headers per CHARTER §11.

---

## 1. Executive Summary & Licensing Architecture

Under **Owner Decision D5**, OpenETQW establishes strict licensing and repository separation between the GPL-licensed engine and the SDK-licensed game module:

1. **Engine Repository (`.`)**:
   - **License**: GNU General Public License v3 (`GPL-3.0-or-later`) with Id Software Additional Terms.
   - **Scope**: Platform abstraction, Vulkan renderer, audio system, collision models, particles, navigation, async networking, framework, developer tools, unit tests, and build system.
   - **Invariant**: Strictly **zero SDK-licensed files** are tracked in this repository.
   - **Header Bridge**: Required game interface definitions are staged dynamically at configure time from `openETQW-game` into `.tmp/gamelibs_stage/` (git-ignored) via `tools/build/stage_game.py`. Every staged header is cataloged in the Tracked Exceptions Registry below.

2. **Game Module Repository (`../openETQW-game`)**:
   - **License**: ETQW SDK EULA (`LicenseRef-ETQW-SDK`) for SDK source files; MIT License for new game-side files and build infrastructure.
   - **Scope**: Entity logic, player movement, weapons, vehicles, bot AI, rules, declarations, UI system, SDK idLib, and reconstructed DoomScript native codegen.
   - **Invariant**: Strictly **zero GPL-only files** reside in this repository.

---

## 2. Five-Tier Provenance Classification

All files imported across both repositories are classified into five canonical origin tiers. The upstream baseline at `f2b655c` comprises 5,263 files; an additional 12 supplemental AAS navigation files were imported from the ETQW 1.5 SDK into `openETQW-game` in commit `288052c` to enable ARM64 game module compilation. 100% coverage across all 5,275 source files is verified in `docs/provenance-manifest.json`:

| Tier | Origin Description | License | Baseline (`~/etqw`) | Supplemental (`openETQW-game`) | Reconciled Total | Target Repository |
|---|---|---|---|---|---|---|
| **Tier 1: SDK** | Original ETQW 1.5 SDK source files (matching extracted `ETQW-SDK-1.5.exe`). Includes 773 CRLF-normalized, 63 reconstructed/modified, 5 byte-identical baseline files, and 12 AAS navigation files. | `LicenseRef-ETQW-SDK` | 841 | 12 | **853** | `openETQW-game` |
| **Tier 2: Doom 3 GPL** | Doom 3 GPL engine and tool source files carrying the id Software GPL-3.0 header and additional terms (`dmap`, `Radiant`, legacy GL renderer, idLib base). | `GPL-3.0-or-later` | 311 | 0 | **311** | `openETQW` |
| **Tier 3: Third-Party** | Permissively licensed or copyleft third-party libraries vendored in upstream (`curl`, `imgui`, `recast`, `openal`, `zlib`, `ogg`, `libvorbis`, `speex`, `sdl2`). | Various (MIT, zlib, BSD, LGPL) | 3,430 | 0 | **3,430** | `openETQW` |
| **Tier 4: Retail-RE** | Reconstruction engine implementations reverse-engineered from retail binaries, carrying decompiler or PDB markers (`AsyncClient`, `AsyncServer`, `Common`, `RenderSystemBackend`). | `GPL-3.0-or-later` | 61 | 0 | **61** | `openETQW` |
| **Tier 5: Recon-Original** | New code created specifically for the ETQW modern reconstruction (Vulkan SPIR-V shaders, `compiledscript` DoomScript runtime, CMake presets, `readme.md`). | `GPL-3.0-or-later` (engine) / `MIT` (game) | 620 | 0 | **620** | Split (`openETQW`: 399 / `openETQW-game`: 221) |
| **Total Source Census** | **Complete active source file inventory across both repositories** | | **5,263** | **12** | **5,275** | |

### 2.1 Supplemental SDK Navigation Imports (`openETQW-game`)

During Milestone 0 ARM64 compilation, 12 bot navigation (AAS) files from the ETQW 1.5 SDK were imported into `openETQW-game` in commit `288052c` to resolve missing symbols in `idAASLocal` (`RouteToGoalArea`, `WalkPathToGoal`) and `AASFileManager`. These files are 100% byte-identical to the official SDK 1.5 source and reside legally in `openETQW-game` under `LicenseRef-ETQW-SDK`:

| Relative Path in `openETQW-game` | License | SHA-256 Digest | Bytes | Function / Technical Justification |
|---|---|---|---|---|
| `src/game/botai/aas/AASCallback_AvoidLocation.cpp` | `LicenseRef-ETQW-SDK` | `614356ad149eba35181a900f2572f20e786c2119919d800a358ceaf6ecba4f02` | 2,802 | Area avoidance callback for bot routing |
| `src/game/botai/aas/AASCallback_AvoidLocation.h` | `LicenseRef-ETQW-SDK` | `88707b30710234c2d6313c61ef3d683fe2238f91d41b043c12e0e28dcbc90560` | 1,262 | Header declaration for avoid location callback |
| `src/game/botai/aas/AASCallback_FindAreaOutOfRange.cpp` | `LicenseRef-ETQW-SDK` | `de8a7c9b3e45aee124fa15c11c460dbe98d9b202e0fd0a27da2893081c9e3d63` | 1,096 | Area search callback outside specified range |
| `src/game/botai/aas/AASCallback_FindAreaOutOfRange.h` | `LicenseRef-ETQW-SDK` | `4406037e76515c2a363683f1bbc85f7f41cbd7474748a3270b96d616bd42201c` | 748 | Header declaration for range callback |
| `src/game/botai/aas/AASCallback_FindCoverArea.cpp` | `LicenseRef-ETQW-SDK` | `1a6f864a00229fe8024e5a9a705dab6b19d2d78aa0cc171eafe4019e01603f6e` | 1,360 | Cover area search callback for combat tactics |
| `src/game/botai/aas/AASCallback_FindCoverArea.h` | `LicenseRef-ETQW-SDK` | `419ae4bbe86c923d6b668d0c03a7efa3cd6d03436167ef8f1632df572406b577` | 802 | Header declaration for cover callback |
| `src/game/botai/aas/AASCallback_FindFlaggedArea.cpp` | `LicenseRef-ETQW-SDK` | `9fe7f82b1e5ac44deaad9783b2736154931219edb549cb0a245e3dafb4ada5ed` | 819 | Flagged area evaluation callback |
| `src/game/botai/aas/AASCallback_FindFlaggedArea.h` | `LicenseRef-ETQW-SDK` | `53b645e4ce8e61b107d35594a6adb61f6db4d5c280a2ec91af591a6118b3c38e` | 752 | Header declaration for flagged area callback |
| `src/game/botai/aas/AAS_debug.cpp` | `LicenseRef-ETQW-SDK` | `0073db004fd25196848871cd5ef5fa1735277261a465cf48ae60999c59468c8e` | 30,067 | AAS routing visualization and debug logging |
| `src/game/botai/aas/AAS_pathing.cpp` | `LicenseRef-ETQW-SDK` | `fc733a65e6794c9871d93c7fffab8a9734277bc1495e733347de774a49750e9f` | 21,371 | Obstacle avoidance path synthesis (`WalkPathToGoal`) |
| `src/game/botai/aas/AAS_routing.cpp` | `LicenseRef-ETQW-SDK` | `9abe74f329d847b59c134ef91836949ad38b4b54109dbdb40cccf80d82c19c23` | 35,911 | Core AAS routing algorithm (`RouteToGoalArea`) |
| `src/libs/AASLib/AASFileManager.h` | `LicenseRef-ETQW-SDK` | `742961d5f28c7a9f47550ca615548efb1655b5f05fcb339bafb88b980673d2d1` | 631 | AAS binary file management interface |

---

## 3. Subsystem & Directory Partition Ledger

| Subsystem / Directory | Primary Content | Provenance Tier | Assigned Repository |
|---|---|---|---|
| `src/game/` | Game entity system, physics, weapons, vehicles, bot AI, rules, UI (`game/guis/`) | Tier 1 (SDK) | `openETQW-game` |
| `src/idlib/` (SDK files) | Math, containers, geometry, text, threading utilities | Tier 1 (SDK) | `openETQW-game` |
| `src/idlib/` (GPL files) | 15 Doom 3 GPL files (`Base64.*`, `CmdArgs.*`, `Lexer.*`, `Parser.*`, `Str.*`, `Token.*`, etc.) | Tier 2 (Doom 3 GPL) | `openETQW` |
| `src/compiledscript/` | Reconstructed DoomScript native compiler and runtime (221 files) | Tier 5 (Recon-Original) | `openETQW-game` |
| `src/compiledscript.xcodeproj/`| Xcode project bundle for compiledscript | Tier 1 (SDK) | `openETQW-game` |
| `src/decllib/DeclType.cpp` | Game declaration registry implementation | Tier 1 (SDK) | `openETQW-game` |
| `src/decllib/` (engine) | Declaration manager, surface types, table parser | Tier 2 / 5 (GPL) | `openETQW` |
| `src/framework/` (engine) | Common engine framework, session, event loop, file system, async networking | Tier 2 / 4 / 5 (GPL) | `openETQW` |
| `src/renderer/` | Vulkan backend, MegaTexture streaming, materials, models | Tier 2 / 4 / 5 (GPL) | `openETQW` |
| `base/vkprogs/` | Vulkan SPIR-V shader source files | Tier 5 (Recon-Original) | `openETQW` |
| `src/cm/` | Collision model core and trace pipeline | Tier 2 / 5 (GPL) | `openETQW` |
| `src/sound/` | Audio system core, sound shaders, sound world | Tier 2 / 5 (GPL) | `openETQW` |
| `src/bse/` | Special effects / particle system engine | Tier 2 / 5 (GPL) | `openETQW` |
| `src/navigation/`, `src/recast/` | QWNV container, Detour & Recast pathfinding | Tier 3 / 5 (Third-Party / GPL) | `openETQW` |
| `src/sdnet/` | Splash Damage network account / session implementations | Tier 4 / 5 (GPL) | `openETQW` |
| `src/sys/` | macOS, POSIX, Windows platform layers and vendored SDL2 | Tier 2 / 3 / 5 (GPL / Third-Party) | `openETQW` |
| `src/libs/`, `src/curl/`, `src/openal/` | Vendored third-party dependencies | Tier 3 (Third-Party) | `openETQW` |
| `src/imgui/` | Dear ImGui tooling | Tier 3 (Third-Party) | `openETQW` |
| `src/tools/` | Radiant editor, `dmap`, `megatexture`, `navbuild` compilers | Tier 2 (Doom 3 GPL) | `openETQW` |
| SDK Interface Headers (96 files) | Public boundary headers (`Game.h`, `NetworkSystem.h`, `UsercmdGen.h`, `BuildDefines.h`, etc.) | Tier 1 (SDK) | `openETQW-game` (staged to `openETQW/.tmp/`) |

---

## 4. Tracked Exceptions Registry

The engine build dynamically stages required game interface headers into `.tmp/gamelibs_stage/src/game/` via `tools/build/stage_game.py`. While `.tmp/` is strictly git-ignored and never committed to the repository, staging SDK headers into the build artifact compiles SDK interface definitions into the binary.

Per **CHARTER §11**, each staged header is a tracked exception that blocks the public release gate until replaced with native GPL definitions.

| Staged Relative Path | Source Path in `openETQW-game` | Canonical SHA-256 | Consuming Engine Files | Technical Justification | Release Gate Impact | GPL Replacement Roadmap |
|---|---|---|---|---|---|---|
| `Game.h` | `src/game/Game.h` | `a2c408c8d01e8ee8ddf96c5a9328406a4471d2ede324d4ca5ef6974ee5e174df` | `framework/Session.cpp`, `framework/precompiled.h` | Declares `idGame` abstract virtual interface, `gameImport_t`, `gameExport_t`, `GAME_API_VERSION`, and `GetGameAPI` entry point. | Blocks public binary release if linked without exception. | **M1**: Refactor into a clean GPL abstract interface (`idGameGPL.h`) mirroring OpenQ4 engine/game ABI decoupling. |
| `NetworkSystem.h` | `src/framework/async/NetworkSystem.h` | `f3695fb2f6c5f411af847700d7e80358d78167319ec1730f6d9b2f2565c68f22` | `framework/async/`, `game/` | Declares abstract `idNetworkSystem` interface used by both engine and game module for packet transport and snapshots. | Blocks public binary release. | **M1**: Replace with native GPL engine network interface (`src/framework/async/NetworkSystem.h`) under GPL-3.0. |
| `UsercmdGen.h` | `src/framework/UsercmdGen.h` | `25a1fd43ab023599b6f2b02eae7488e9c2efe7668ef150597bd600c6acf40296` | `framework/async/AsyncClient.cpp`, `tests/UserCmdDeltaTest.cpp` | Declares `usercmd_t` client command structure, sequence constants, and `idUsercmdGen`. | Blocks public binary release. | **M0.4**: Implement explicit-width GPL `usercmd_t` with compile-time layout assertions in `src/framework/Usercmd.h`. |
| `BuildDefines.h` | `src/framework/BuildDefines.h` | `f4fa484450895cad97a8ed4cec35eb099338096313c9bae40849f31065e8dae6` | `framework/`, `decllib/` | Defines cross-module build switches (`SD_SDK_BUILD`, `SD_PUBLIC_BUILD`, `SD_USE_DRAWVERT_SIZE_32`, `SD_USE_INDEX_SIZE_16`). | Blocks public binary release. | **M0.3**: Moved to Meson project options and compiler defines. |
| `BuildDefines.inc` | `src/framework/BuildDefines.inc` | `83073d1864a29e98bda738b62d9bcb1bd9f25ab19dd32f779f226e8d03d110c0` | Included by `BuildDefines.h` | Macro sanity validation guards. | Blocks public binary release. | **M0.3**: Consolidated into Meson build definitions. |
| `botai/BotAI_Debug.h` | `src/game/botai/BotAI_Debug.h` | `c52d8309eb7bdc7d7e26438d27e06b872116af2a1e69309208207d3c885b5a94` | Transitive include from `Game.h:24` | Declares `botDebugInfo_t` referenced by `idGame::GetBotDebugInfo()`. | Transitive SDK include. Blocks release. | **M1**: Sever transitive include by forward-declaring `botDebugInfo_t` in `idGame`. |
| `botai/Bot_Common.h` | `src/game/botai/Bot_Common.h` | `c3798a8f2a0103212e6ebdc57bed9ba44ff3445f7ed4abb576335b8c382ea6c3` | Transitive include from `BotAI_Debug.h:7` | Declares weapon and vehicle enumeration tables. | Transitive SDK include. Blocks release. | **M1**: Severed when `BotAI_Debug.h` is removed from `Game.h`. |

---

## 5. Audit & Verification Procedure

The provenance classification and zero-SDK compliance can be independently re-verified at any time using the following commands:

1. **Full Provenance Classification Audit (Baseline + Supplemental SDK)**:
   ```bash
   ETQW_SDK_SRC=/path/to/etqw-sdk-source   # the original ETQW SDK source tree
   python3 tools/build/classify_provenance.py "$ETQW_SDK_SRC" openetqw_handoff_v3/reference/etqw_sdk_comparison_bundle.zip --game-repo ../openETQW-game --output-json docs/provenance-manifest.json
   ```
   *Expectation*: Outputs `Classified 5275 files: {'SDK': 853, 'Doom3_GPL': 311, 'ThirdParty': 3430, 'Reconstruction_retail_RE': 61, 'Reconstruction_original': 620}` with exit code 0.

2. **Automated Zero-SDK Verification**:
   ```bash
   python3 tests/test_zero_sdk.py
   ```
   *Expectation*: Outputs `[PASSED] Zero SDK-licensed files detected in repository tracking.` with exit code 0.
