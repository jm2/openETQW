# OpenETQW Provenance & Licensing Classification

> This document classifies every file imported from the upstream baseline (`~/etqw` at commit `f2b655c992b47e0eaca7c178b0dae490abc1544e`) and the ETQW 1.5 SDK, details the two-repository partition under Owner Decision D5, and keeps the Tracked Exceptions Registry for SDK code that the engine build uses (CHARTER §11).
>
> Updated 2026-10-07: the files added after the split and the openQ4 donor recorded in the manifest (§2, §6). Updated 2026-10-25: the §2/§3/§5 counts and the §6 label rows after the minizip, EAX GUID and retail-RE relabel. Updated 2026-09-25: the owner's decisions on the SDK exceptions and the EULA risk (§4, §6; `docs/decisions/0001`, `0002`), the scripts compared with the 1.5 data (§2.2), the retail-content scan (§7). Updated 2026-09-24: `src/compiledscript` reclassified as SDK (§2.2), exceptions registry corrected (§4), open items (§6). Inspected at engine `main` `d15bdea` and game `main` `f00f4d3`. This document records facts and open questions; it draws no legal conclusions (CHARTER §11).

---

## 1. Executive Summary & Licensing Architecture

Under **Owner Decision D5**, OpenETQW separates the GPL-licensed engine from the SDK-licensed game module:

1. **Engine Repository (`.`)**:
   - **License**: GNU General Public License v3 (`GPL-3.0-or-later`) with Id Software Additional Terms. jmarshall's reconstruction code has no license yet (open owner item, §6).
   - **Scope**: Platform abstraction, Vulkan renderer, audio system, collision models, particles, navigation, async networking, framework, developer tools, unit tests, and build system.
   - **Invariant**: **No SDK-licensed files are tracked at HEAD** (checked by `tests/test_zero_sdk.py`). The imported history (jmarshall's three upstream commits) still contains the SDK tree.
   - **SDK code in the build**: The engine build is not free of SDK code. `tools/build/stage_game.py` copies 317 files from `openETQW-game` into `.tmp/gamelibs_stage/` (git-ignored) at configure time, the build uses 192 of them, and the engine links the game repo's SDK idLib (`libidLib.a`). §4 lists all of it.

2. **Game Module Repository (`../openETQW-game`)**:
   - **License**: ETQW SDK EULA (`LicenseRef-ETQW-SDK`) for SDK source files and SDK text media (including `src/compiledscript/scripts`); MIT License for new game-side files and build infrastructure.
   - **Scope**: Entity logic, player movement, weapons, vehicles, bot AI, rules, declarations, UI system, SDK idLib, and the `compiledscript` tree (SDK script text and runtime files, plus jmarshall's DoomScript native code generator).
   - **Invariant**: No GPL-only files reside in this repository.

**Import method.** Both repositories keep jmarshall's three upstream commits (`6de0697`, `961cb21`, `f2b655c` in `~/etqw`) as their root history. Each repository's first own commit then deleted the files that belong to the other repository: engine `9043b91`, game `9600d7a`. Both commit messages record the source commit `f2b655c`. In our repositories the three root commits have different IDs (`b8f61ab`, `d131a72`, `e1407bc`); this document and the commit messages keep citing the upstream IDs, which identify jmarshall's commits in `~/etqw`.

---

## 2. Five-Tier Provenance Classification

All imported files are classified into five origin tiers. The manifest covers 5,262 files of the upstream baseline at `f2b655c`. An additional 12 supplemental AAS navigation files were imported from the ETQW 1.5 SDK into `openETQW-game` in commit `288052c` to enable ARM64 game module compilation. All 5,274 files are classified in `docs/provenance-manifest.json`. Files added after the split are in it too, marked as such (after the table).

| Tier | Origin Description | License | Baseline (`~/etqw`) | Supplemental (`openETQW-game`) | Reconciled Total | Target Repository |
|---|---|---|---|---|---|---|
| **Tier 1: SDK** | Original ETQW 1.5 SDK files (matching the extracted `ETQW-SDK-1.5.exe`). Includes 773 CRLF-normalized, 63 reconstructed/modified and 5 byte-identical baseline source files, 217 `src/compiledscript` files reclassified on 2026-09-24 (§2.2), and 12 AAS navigation files. | `LicenseRef-ETQW-SDK` | 1,058 | 12 | **1,070** | `openETQW-game` |
| **Tier 2: Doom 3 GPL** | Doom 3 GPL engine and tool source files carrying the id Software GPL-3.0 header and additional terms (`dmap`, `Radiant`, legacy GL renderer, idLib base). | `GPL-3.0-or-later` | 309 | 0 | **309** | `openETQW` |
| **Tier 3: Third-Party** | Permissively licensed or copyleft third-party libraries vendored in upstream (`curl`, `imgui`, `recast`, `openal`, `zlib`, `ogg`, `libvorbis`, `speex`, `sdl2`). | Various (MIT, zlib, BSD, LGPL) | 3,436 | 0 | **3,436** | `openETQW` |
| **Tier 4: Retail-RE** | Reconstruction engine implementations that describe themselves as derived from retail binaries, carrying decompiler or PDB markers (`AsyncClient`, `AsyncServer`, `Common`, `RenderSystemBackend`, `DeclManager`, `FileSystem`, `tr_render.h`, `tr_rendertools.cpp`). These markers are in jmarshall's reconstructed code; they are not raw decompiler output. | `GPL-3.0-or-later` (see note) | 65 | 0 | **65** | `openETQW` |
| **Tier 5: Recon-Original** | Code created for the reconstruction (Vulkan SPIR-V shaders, the `compiledscript` code generator and build files, CMake presets, `readme.md`). | `GPL-3.0-or-later` (engine, see note) / `MIT` (game) | 394 | 0 | **394** | Split (`openETQW`: 390 / `openETQW-game`: 4) |
| **Total Source Census** | **Imported files in both repositories** | | **5,262** | **12** | **5,274** | |

The 2026-10-25 §6 relabel moved the five `src/framework/minizip/` files and
`src/sys/win32/eaxguid.lib` (deleted 2026-10-26, §6) to Tier 3, and four PDB/reconstruction-marked engine files
(`DeclManager.cpp`, `FileSystem.cpp`, `tr_render.h`, `tr_rendertools.cpp`) to Tier 4.
The table and `docs/provenance-manifest.json` carry the resulting counts.

The manifest's imported section is a **census of the split import**, not a listing of the
checked-out tree: a file deleted since keeps its row, so `totalFiles` (5,554) counts
manifest entries, not present files. That is deliberate — do not "fix" the number by
pruning rows (§6).

**Files added after the split** (2026-10-07) are in the manifest too: 213 entries (176 engine, 37 game) at the time, 230 at engine b318c87 (193 engine, 37 game), marked `"addedAfterSplit": true`, each with the commit that added the path (`addedIn`), the SPDX tag the file carries (`spdx`, null without one) and, for a file taken from openQ4, its `donor` (repository, commit, path, `identical` or `adapted`). Their tiers (`postSplitCounts`): 208 `Reconstruction_original` then (225 at b318c87), 2 `Doom3_GPL` (the two macOS files that keep id's Doom 3 notice), 2 `SDK` (the game's EULA texts) and 1 `ThirdParty` (`LICENSES/third-party/minizip.txt`, the zlib licence text). At this manifest's head there are 280 post-split entries (243 engine, 37 game), 275 of them `Reconstruction_original`; the 230 and 225 figures above are the b318c87 snapshot. An untagged file takes its repository's licence (engine documentation and records `GPL-3.0-or-later`, game files `MIT`) unless a rule in `tools/build/provenance_post_split.py` says otherwise. Their hashes describe the engine files at the commit that last changed the manifest and the game files at the commit in `postSplitAsOf`; the imported entries keep the imported contents. `python3 tools/build/provenance_post_split.py --game-repo ../openETQW-game` lists tracked files missing from the manifest (exit 1 if any), and `--update` adds them and refreshes the post-split entries; a new file with neither an SPDX tag nor a rule stops it, so that file needs a decision.

Note on Tiers 4 and 5: `tools/build/classify_provenance.py` assigned the tiers on 2026-09-20 from SDK path matches and header text. They were not reconciled with the stricter local byte-level audit of jmarshall's code (2026-09-22, gitignored session notes), which counts about 135 compiled engine files as effectively jmarshall-authored. The `GPL-3.0-or-later` label on these engine files states the license this project intends for its own contributions. jmarshall's code carries no license; that is an open owner item and release gate item 1 (§6).

### 2.1 Supplemental SDK Navigation Imports (`openETQW-game`)

During Milestone 0 ARM64 compilation, 12 bot navigation (AAS) files from the ETQW 1.5 SDK were imported into `openETQW-game` in commit `288052c` to resolve missing symbols in `idAASLocal` (`RouteToGoalArea`, `WalkPathToGoal`) and `AASFileManager`. These files are byte-identical to the SDK 1.5 source and are kept in `openETQW-game` under `LicenseRef-ETQW-SDK`:

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

### 2.2 `src/compiledscript` reclassified as SDK (2026-09-24)

Until 2026-09-24 the manifest and the game's `tools/ci/spdx_allowlist.txt` labeled all 221 `src/compiledscript` files `Reconstruction_original` / MIT: the classifier gave the whole directory MIT. That was wrong for 217 of them. Both files now label those 217 as tier SDK, `LicenseRef-ETQW-SDK`:

| Files in `openETQW-game` | Count | Evidence (inspected, macOS arm64) |
|---|---|---|
| `src/compiledscript/scripts/**` (202 `.script`, 2 `.include`) | 204 | Script text distributed with the SDK. The ETQW SDK 1.5 installer's "Game Text Media" section holds 3,134 files, including 202 `.script` files under `base/script`. A blob-level comparison over each repository's full history found 195 distinct script blobs in the game history (194 in the engine history) byte-identical to files in that section. The rest are small edits (1 to 5 lines each) and jmarshall's rework of `main.script` (193 changed lines). `map.script` (empty) and `maps/etqwmap.script` (371 lines; its closest retail script is `maps/valley.script` at 37% line similarity) have no counterpart in retail `pak000.pk4`; they are labeled SDK with the rest of the directory. Retail `base/pak000.pk4` holds versions of the same scripts (90 identical, 110 different), which is why the audit first reported them as retail content. The SDK installer distributes them, so they are SDK text media, not retail-only content (§7). Against the 1.5 data (2026-09-25): 197 of the 204 files equal the effective 1.5 script tree. Six more (`main.script`, `util.script`, `player/player.script`, `deployables/base.script`, `items/carryable_item.script`, `maps/quarry.script`) differ only in the `scripts/` versus `script/` include prefix, which is the whole of `main.script`'s 193 changed lines. `map.script` matches the empty file in `pak007`/`pak008`; only `maps/etqwmap.script` has no retail counterpart. The 90/110 figures above compare against the DVD `pak000`. |
| `CompiledScriptInterface.h`, `CompiledScript_Base.cpp`, `CompiledScript_Class.cpp/.h`, `CompiledScript_Event.cpp/.h`, `CompiledScript_TypeInfo.cpp/.h`, `CompiledScript_Types.h`, `CompiledScripts_Types.cpp`, `Precompiled.cpp/.h`, `scripts.def` | 13 | Compared with the same names under the extracted SDK's `base/`: 9 are identical after CRLF normalization; `CompiledScriptInterface.h` (99% line similarity), `CompiledScript_Base.cpp` (97%), `CompiledScript_Types.h` (97%) and `Precompiled.h` (94%) are modified copies. |

The other 4 files stay `Reconstruction_original` / MIT: `.gitignore`, `CMakeLists.txt`, `README.md` and `tools/CompiledScriptCodegen.cpp` (no SDK counterpart). No Meson target builds or reads `src/compiledscript`; it is jmarshall's CMake project. `tools/ci/check_spdx.py` in the game repo passes with the corrected allowlist (1,070 `LicenseRef-ETQW-SDK`, 4 MIT entries).

---

## 3. Subsystem & Directory Partition Ledger

| Subsystem / Directory | Primary Content | Provenance Tier | Assigned Repository |
|---|---|---|---|
| `src/game/` | Game entity system, physics, weapons, vehicles, bot AI, rules, UI (`game/guis/`) | Tier 1 (SDK) | `openETQW-game` |
| `src/idlib/` (SDK files) | Math, containers, geometry, text, threading utilities | Tier 1 (SDK) | `openETQW-game` |
| `src/idlib/` (GPL files) | 15 Doom 3 GPL files (`Base64.*`, `CmdArgs.*`, `Lexer.*`, `Parser.*`, `Str.*`, `Token.*`, etc.) | Tier 2 (Doom 3 GPL) | `openETQW` |
| `src/compiledscript/scripts/` | SDK script text (204 files, §2.2) | Tier 1 (SDK) | `openETQW-game` |
| `src/compiledscript/` (runtime) | 13 compiled-script runtime files matching the SDK's `base/` (§2.2) | Tier 1 (SDK) | `openETQW-game` |
| `src/compiledscript/` (other) | DoomScript native code generator (`tools/CompiledScriptCodegen.cpp`), CMake file, README, `.gitignore` (4 files) | Tier 5 (Recon-Original) | `openETQW-game` |
| `src/compiledscript.xcodeproj/`| Xcode project bundle for compiledscript | Tier 1 (SDK) | `openETQW-game` |
| `src/decllib/DeclType.cpp` | Game declaration registry implementation (compiled into the engine binaries, §4) | Tier 1 (SDK) | `openETQW-game` |
| `src/decllib/` (engine) | Declaration manager, surface types, table parser | Tier 2 / 5 (GPL) | `openETQW` |
| `src/framework/` (engine) | Common engine framework, session, event loop, file system, async networking | Tier 2 / 4 / 5 (GPL) | `openETQW` |
| `src/framework/minizip/` | Five files (`crypt.h`, `ioapi.c/.h`, `unzip.c/.h`) are zlib's `contrib/minizip` (Gilles Vollant headers), relabeled Tier 3 on 2026-10-25 (§6); `unzip_internal.h` is Tier 4 (its header says it holds layouts from the retail PDB) | Tier 3 / Tier 4 | `openETQW` |
| `src/renderer/` | Vulkan backend, MegaTexture streaming, materials, models | Tier 2 / 4 / 5 (GPL) | `openETQW` |
| `base/vkprogs/` | Vulkan SPIR-V shader source files | Tier 5 (Recon-Original) | `openETQW` |
| `src/cm/` | Collision model core and trace pipeline | Tier 2 / 5 (GPL) | `openETQW` |
| `src/sound/` | Audio system core, sound shaders, sound world | Tier 2 / 5 (GPL) | `openETQW` |
| `src/bse/` | Special effects / particle system engine | Tier 2 / 5 (GPL) | `openETQW` |
| `src/navigation/`, `src/recast/` | QWNV container, Detour & Recast pathfinding | Tier 3 / 5 (Third-Party / GPL) | `openETQW` |
| `src/sdnet/` | Splash Damage network account / session implementations | Tier 4 / 5 (GPL) | `openETQW` |
| `src/sys/` | macOS, POSIX, Windows platform layers, vendored SDL2 and the Tier 3 `src/sys/win32/eaxguid.lib` EAX GUID import library (§6; deleted 2026-10-26); six files added later come from openQ4: `URLPolicy.h` and five macOS files (§6) | Tier 2 / 3 / 5 (GPL / Third-Party) | `openETQW` |
| `src/libs/`, `src/curl/`, `src/openal/` | Vendored third-party dependencies | Tier 3 (Third-Party) | `openETQW` |
| `src/imgui/` | Dear ImGui tooling | Tier 3 (Third-Party) | `openETQW` |
| `src/tools/` | Radiant editor, `dmap`, `megatexture`, `navbuild` compilers | Tier 2 (Doom 3 GPL) | `openETQW` |
| SDK interface files staged for the engine build | `stage_game.py` copies 317 files (314 headers, `BuildDefines.inc`, `idlib/BitMsg.cpp`, `decllib/DeclType.cpp`); the build uses 192 (§4) | Tier 1 (SDK) | `openETQW-game` (staged to `openETQW/.tmp/`) |

---

## 4. Tracked Exceptions Registry

The engine repository tracks no SDK files at HEAD, but the engine binaries and unit tests are built with SDK code from `openETQW-game`. CHARTER §11 makes each such use a tracked exception: "the repo is clean, but the engine binary is not yet". Until 2026-09-24 this section listed only 7 staged headers (§4.3). The full list below comes from the macOS arm64 build directory (`build.ninja` link lines and `ninja -C build -t deps`, read on 2026-09-24; the build was configured from the working trees of engine `84a224b` and game `24f58bf` on `fix/audit-followups`). Evidence level: inspected.

### 4.1 SDK code in the engine build

| # | SDK code | How the build gets it | Built into |
|---|---|---|---|
| **E1** | SDK idLib: `libidLib.a` (72 objects) from `openETQW-game/build` (`idLib.lib` on Windows) | `meson.build` finds it with `cpp.find_library` and adds it to `engine_common_deps` (a direct path in `engine_common_link_args` is the fallback); `tests/meson.build` links it into the unit tests too | `openETQW-client_<arch>`, `openETQW-ded_<arch>`, unit tests |
| **E2** | `src/decllib/DeclType.cpp`, an SDK source file | staged, then listed in `tools/build/meson_sources.py` | both engine binaries |
| **E3** | `src/idlib/BitMsg.cpp`, an SDK source file | staged, then listed in `tests/meson.build` | unit tests `bitmsg`, `usercmd-delta`, `usercmd-history` |
| **E4** | 189 staged SDK headers and `framework/BuildDefines.inc` | `tools/build/stage_game.py` copies them to `.tmp/gamelibs_stage/` at configure time; engine sources include them | both engine binaries and the unit tests |

### 4.2 Staged files the build uses, by directory

192 distinct staged files appear in the ninja deps (191 in the engine binaries; `BitMsg.cpp` only in the unit tests). Every one is tier SDK in the manifest.

| Staged directory | Files | Notes |
|---|---|---|
| `src/idlib/` | 110 | top level 23, `containers/` 24, `math/` 22, `text/` 13, `geometry/` 11, `bv/` 6, `hashing/` 6, `threading/` 5; includes `BitMsg.cpp` (E3) |
| `src/framework/` | 19 | includes `BuildDefines.h`, `BuildDefines.inc`, `UsercmdGen.h`, `Licensee.h`, `async/NetworkSystem.h` |
| `src/decllib/` | 16 | includes `DeclType.cpp` (E2) |
| `src/sdnet/` | 12 | SDNet interface headers |
| `src/renderer/` | 10 | `Material.h`, `Model.h`, `RenderWorld.h`, `RenderSystem.h`, ... |
| `src/libs/` | 6 | `filelib/` 2, `qglLib/` 4 |
| `src/bse/` | 5 | BSE interface headers |
| `src/sys/` | 5 | `sys_public.h`, `sys_local.h`, `sys_input.h`, `sys_render.h`, `keynum.h` |
| `src/sound/` | 4 | sound system interface headers |
| `src/game/` | 3 | `Game.h`, `botai/BotAI_Debug.h`, `botai/Bot_Common.h` |
| `src/cm/` | 1 | `CollisionModel.h` |
| `src/common/` | 1 | `common.h` |
| **Total** | **192** | |

The deps log holds 193 path spellings: engine sources include `framework/DeclManager.h`, and the staged file is `declManager.h`. On the case-insensitive macOS file system both names are one file. On a case-sensitive file system (Linux) the include would not find the staged file (inspected only; the engine has never been built on Linux).

**Resolution: approved by the owner on 2026-09-25 ("openQ4 precedent").** E1–E4 stay as tracked exceptions, stated openly here; no code is replaced. CHARTER §11 listed the alternatives: a GPL replacement from Doom 3 or dhewm3 extended to the ETQW API (idLib has a Doom 3 GPL counterpart), keeping interface headers only, or new GPL code. openQ4 reached the same outcome without saying so: its engine repository compiles its own idLib, 121 of whose 146 files are near-identical to the Quake 4 SDK copy (50 carry Raven markers) under a GPL label, plus 94 SDK game headers. OpenETQW keeps each file's own licence text instead of relabelling (CHARTER §11). The conditions that go with the approval: engine packages ship the SDK EULA as well as the GPL (EULA §5 requires the EULA to accompany each copy; see §6), and the exceptions are listed here. With E1–E4 approved, release gate item 2 is cleared for the engine binaries now that the condition above is met (since PR #19 `meson install` carries the SDK EULA, checked by the `install-licenses` test). The SDK files in the engine's early history still need EULA §5 handling before publishing (§6). The decision record is `docs/decisions/0001-sdk-code-in-the-engine-build.md`.

### 4.3 Headers registered on 2026-09-19

This was the original registry. The seven headers are among the 192 files above. The roadmap column is the 2026-09-19 plan and has not been carried out: `src/framework/Usercmd.h` and a GPL `NetworkSystem.h` do not exist, `meson.build` still passes `-DSD_SDK_BUILD`, and `Game.h` still includes `botai/BotAI_Debug.h`.

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

The provenance classification and zero-SDK compliance can be re-checked with the following commands:

1. **Full Provenance Classification Audit (Baseline + Supplemental SDK)**:
   ```bash
   # the first argument is the tree to classify (~/etqw); the SDK file list comes from the bundle.
   # Write to a scratch file: writing over docs/provenance-manifest.json would undo the corrections below.
   python3 tools/build/classify_provenance.py ~/etqw openetqw_handoff_v3/reference/etqw_sdk_comparison_bundle.zip --game-repo ../openETQW-game --output-json /tmp/provenance-check.json
   ```
   *Expectation* (run 2026-09-24): `Classified 5277 files: {'SDK': 853, 'Doom3_GPL': 311, 'ThirdParty': 3430, 'Reconstruction_retail_RE': 61, 'Reconstruction_original': 622}`, exit code 0. The two files beyond the manifest's baseline are the game's `src/game/botai/aas/AASFile_local.cpp/.h`, new MIT files added after the import, which the classifier picks up from `--game-repo` and labels Reconstruction_original. This is the classifier's view of `~/etqw` and does not include the 2026-09-24 corrections. The committed manifest differs from it by those corrections: one baseline entry dropped, 217 `src/compiledscript` files relabeled SDK (§2.2), and `src/framework/minizip/ioapi.cpp` and `unzip.cpp` listed as `ioapi.c` and `unzip.c` (renamed without content change in engine commit `1223dc5`). The committed counts are `{'SDK': 1070, 'Doom3_GPL': 309, 'ThirdParty': 3436, 'Reconstruction_retail_RE': 65, 'Reconstruction_original': 394}`, 5,274 files. The 2026-10-25 §6 relabel moved ten of those files (six to ThirdParty, four to Reconstruction_retail_RE); the rest of the difference from the classifier's view is the 2026-09-24 corrections above. Writing the classifier output over `docs/provenance-manifest.json` would undo the corrections; compare against it instead.

2. **Automated Zero-SDK Verification**:
   ```bash
   python3 tests/test_zero_sdk.py
   ```
   *Expectation*: Outputs `[PASSED] Zero SDK-licensed files detected in repository tracking.` with exit code 0. It matches exact hashes and paths only; the SDK path list comes from the gitignored handoff bundle.

3. **Game repository SPDX allowlist** (in `../openETQW-game`):
   ```bash
   python3 tools/ci/check_spdx.py
   ```
   *Expectation*: `PASSED: every in-scope file is SPDX-tagged or covered by the provenance allowlist.` The allowlist entries match this manifest's `openETQW-game` entries (1,074 files).

---

## 6. Open provenance items (2026-09-24, updated 2026-10-25)

These come from the charter audit of 2026-09-24 and the checks noted. The owner resolved the SDK rows on 2026-09-25; the remaining rows are resolved or explicitly deferred below.

| Item | Facts | Needed |
|---|---|---|
| jmarshall's license | `~/etqw` and the upstream (HEAD still `f2b655c`, checked 2026-09-24) have no license file. Both histories are rooted in his commits. | **Deferred** (owner, 2026-09-25): the remediation (a licence from him or replacement code) is not decided; release gate item 1 stays blocking. |
| SDK code in the engine build | §4: `libidLib.a`, `DeclType.cpp`, `BitMsg.cpp` (tests) and 189 headers plus one `.inc`. | Resolved 2026-09-25: the owner approved them as exceptions on the openQ4 precedent (§4). Engine packages must carry the SDK EULA. |
| SDK EULA scope | EULA §2 allows modifications "that shall operate only with ENEMY TERRITORY: QUAKE WARS (but not any demo, test, or other version)"; the game module runs on OpenETQW, not id's executable. openQ4's licence has the identical clause; it publishes, checks for real Quake 4 data and has not asked id. | Decided 2026-09-25: accept the risk as openQ4 does, with two safeguards, both in place since 2026-09-25 (engine PR #19, game PR #12): the engine refuses to run without the retail 1.5 data (openQ4's check, `fs_validateOfficialPaks`), and every package carries the licence texts (GPL, SDK EULA, third-party notices). `docs/decisions/0002-sdk-eula-risk-and-retail-install-check.md`. |
| openQ4-derived engine files without a donor commit | Added in engine commit `ed2de0d` (2026-09-20), whose message names no donor. The donor is `jm2/openQ4` `b37b1456f3e400fd4b523090402a6dd1fae2af1b` (2026-09-18), openQ4's HEAD when `ed2de0d` was made and the commit the tooling row below names. Byte-identical to it: `src/sys/URLPolicy.h`, `src/sys/osx/macosx_common.h`, `src/sys/osx/macosx_sys.h`; adapted from it: `src/sys/osx/macosx_local.h`, `macosx_misc.mm`, `macosx_compat.mm` (no identical blob anywhere in openQ4's history). Licences: `URLPolicy.h` carries openQ4's own grant of version 3 or later; `macosx_misc.mm` and `macosx_compat.mm` keep id's Doom 3 notice (version 3 or later) with openQ4's changes; `macosx_common.h`, `macosx_sys.h` and `macosx_local.h` carry no notice. openQ4's `LICENSE` is the GPLv3 text and its own SPDX-tagged sources declare `GPL-3.0-or-later`, and the owner (who controls jm2/openQ4) decided on 2026-10-07 that openQ4-derived files are `GPL-3.0-or-later`. | Done 2026-10-07: each file starts with `SPDX-License-Identifier: GPL-3.0-or-later` and a note naming the donor file and commit, and the manifest records the donor (`donor`). |
| openQ4-derived build and release tooling (2026-09-29 on) | Adapted, with an SPDX line and a note naming the donor, from openQ4 at `jm2/openQ4` `b37b1456`. All of them are `GPL-3.0-or-later` (owner, 2026-10-07: openQ4-derived files take the engine's licence; openQ4's own SPDX-tagged sources declare `GPL-3.0-or-later` too, and its `LICENSE` is the GPLv3 text). Ports of a donor file: `tools/build/openetqw_version.py` (`tools/build/openq4_version.py`), `tools/build/openetqw_release_version.py` (`openq4_release_version.py`), `tools/build/generate_release_changelog.py`, `tools/build/verify_release_asset_set.py`, `tools/build/verify_release_source_provenance.py`, `tools/build/build_windows_installer.py` and `tools/build/openETQWInstaller.iss.in` (`openQ4Installer.iss.in`), `tools/build/package_linux_appimage.py` (reduced: no linuxdeploy), `.github/workflows/manual-release.yml`. Written for openETQW after a donor file's design: `tools/validation/openetqw_validate.py` (`tools/validation/openq4_validate.py`), `tools/build/package_release.py` (`package_nightly.py`), `tools/build/prepare_macos_deps.py` (`prepare_macos_moltenvk.sh`), and the workflows `commit-validation.yml`, `push-verification.yml`, `linux-arm64-cross.yml`, `macos-debug.yml`, and `macos-sanitizer.yml`. The appimagetool pins (1.9.1 and its SHA-256s) and the MoltenVK 1.4.1 pin are the donor's. `tools/build/linux/openetqw.svg` is an original placeholder mark, not ET:QW artwork. | **Resolved** 2026-10-07: recorded; each adapted file keeps its donor note. |
| openQ4-derived engine input code (2026-10-19 on) | `src/sys/sdl/MouseDelta.h` (#141) is adapted from one function of `jm2/openQ4` `b37b1456`, `src/sys/sdl3/sdl3_backend.cpp` `SDL3_ConsumeMouseDelta` (the sub-unit mouse remainder), without `idMath`. `GPL-3.0-or-later` (owner, 2026-10-07: openQ4-derived files take the engine's licence). | **Resolved** 2026-10-19: recorded; the file carries its SPDX line and donor note, and the manifest its `donor`. |
| `src/framework/minizip/` labels | `crypt.h`, `ioapi.c` and `ioapi.h` are byte-identical to the vendored zlib `contrib/minizip` copies (ThirdParty tier); `unzip.c` and `unzip.h` are modified copies whose headers name Gilles Vollant. The classifier labeled all five `Reconstruction_original` / GPL. The two stale `.cpp` paths were corrected on 2026-09-24; the tier was not. `crypt.h` and the password path of `unzip.c` are Info-ZIP decryption code (Info-ZIP license); since PR #19 the engine builds with `NOUNCRYPT`, so neither binary contains it (`LICENSES/third-party/minizip.txt`). | **Resolved** 2026-10-25: the five manifest entries are `ThirdParty`/`Upstream` (the §2 and §5 counts are updated). `crypt.h`, `ioapi.c` and `ioapi.h` are byte-identical to `src/libs/zlib/contrib/minizip/` (SHA-256 matches); `unzip.c` and `unzip.h` are the modified Vollant copies. The exact terms stay in `LICENSES/third-party/minizip.txt` (zlib for the Vollant files; Info-ZIP for `crypt.h` and the `NOUNCRYPT`-excluded decryption path). `provenance_post_split.py` reports 0 gaps. |
| Other label questions | `src/sys/win32/eaxguid.lib` (a binary import library, not identical to `src/openal/lib/eaxguid.lib`) is labeled `Reconstruction_original` / GPL. Four engine files with self-described RE markers are not tagged `Reconstruction_retail_RE`: `src/framework/DeclManager.cpp`, `src/framework/FileSystem.cpp`, `src/renderer/tr_render.h`, `src/renderer/tr_rendertools.cpp`. | **Resolved** 2026-10-25: `src/sys/win32/eaxguid.lib` is relabeled `ThirdParty`/`Upstream`: it is an `ar` archive with one `eaxguid.obj` member whose debug path is `D:\MyProjects\eaxguid\eaxguid.cpp`, exporting Creative EAX GUID symbols, not reconstructed engine code; its licence and whether to replace it stay with release gate 5 (queue §3 item 7). `DeclManager.cpp` (line 3 "declaration manager reconstruction"; line 1079 PDB-owned decl types), `FileSystem.cpp` (lines 437-438 and 670 PDB layout assertions), `tr_render.h` (lines 38-39 reconstructed front end) and `tr_rendertools.cpp` (line 34 reconstructed functions; line 2402 PDB) are relabeled `Reconstruction_retail_RE`; their licence stays `GPL-3.0-or-later`. |
| Files added after the split | At `84a224b`, 101 tracked engine files and 29 game files were not in the manifest; at engine `e3244b2` / game `81f43a5`, 175 and 37. | **Resolved** 2026-10-07: all are in the manifest (§2, after the tier table), with `tools/build/provenance_post_split.py` to report and add later ones. **Resolved** 2026-10-25: the report passes against game `7fbdf80` (0 tracked files missing). Making it a CI gate is **deferred**: that changes merge/CI policy, is not required to close this item, and is tracked by release gate 3 in `STATUS.md`. |
| History | The imported root commits (`b8f61ab`, `d131a72`, `e1407bc`) contain the full SDK tree; our first commit, `9043b91`, removed it from the tree (1,065 files changed). The owner decided on 2026-09-25 to leave that history in place unless it violates the GPL. Read as text (not a legal opinion), the GPL sets conditions on conveying covered works and says it does not extend to separate works stored alongside them in an aggregate (GPL-3.0 §5); nothing in it limits unrelated files in older commits, and the root commits never labeled the SDK tree GPL (no LICENSE, no SPDX lines, id's notices kept). | **Deferred** to repository recreation/publishing: the SDK EULA §5 condition that the EULA accompany each copy also reaches the SDK files in old commits. Handle it when the repositories are recreated (drop or squash the root commits) or by tracking the EULA text with a note naming those commits. The owner's 2026-09-25 decision to leave the history stands; this item changes no history. |
| `1223dc5` label | Commit `1223dc5` (2026-09-20) added `src/framework/BuildVersion.cpp` and `.h` with id's 2007 copyright line and `SPDX-License-Identifier: GPL-3.0-or-later`. Their content is the SDK's two engine revision numbers (`ENGINE_SRC_REVISION = 12658`, `ENGINE_MEDIA_REVISION = 33244`, as in the game repo's SDK `src/framework/BuildVersion.cpp`). `src/framework/EngineVersion.cpp` at HEAD keeps the two numbers without id's line. It is the one place where our own commit put a GPL label on SDK content (CHARTER §11 forbids relabeling). Whether two numbers are copyrightable is not concluded. | **Deferred** to repository recreation: recorded here; only a history rewrite at recreation can drop it, and the owner's 2026-09-25 decision left the history in place. |
| Prebuilt code binaries in the tree (release gate 5) | Seven Windows import libraries and zlib MASM objects (`src/openal/lib/openal32.lib`, `src/openal/lib/eaxguid.lib`, `src/sys/win32/eaxguid.lib`, `src/libs/zlib/contrib/masmx64/{gvmat64,inffasx64}.obj`, `src/libs/zlib/contrib/masmx86/{gvmat32,inffas32}.obj`) and a Mach-O **ppc** dylib (`src/openal/osx/OpenAL.framework/Versions/A/OpenAL`, 256,696 B). No build target references any of them: OpenAL is compiled out on every platform (`ID_OPENAL` is defined nowhere, so all seven `#if ID_OPENAL` sites are 0 and the cvars are `CVAR_ROM` "not supported in this build", `src/sound/snd_system.cpp:72-86`); zlib comes from `dependency('zlib')` (`meson.build:280`), not from `src/libs/zlib`; `src/sys/win32/win_snd.cpp` is in no target; and no source names the framework. | **Deferred** on the owner's approval of the deletion, which is [#301](https://github.com/jm2/openETQW/pull/301) (2026-10-26); `docs/queue.md` §1 had listed the same deletion as owner-gated, so this row must not read as settled until that approval is on `main` (§9 or a `STATUS.md` owner-decision line). Deleted rather than replaced: nothing links them, they are Windows/MSVC and PowerPC build artifacts of the retail-era tree, and removing them answers the restricted-dependency question instead of documenting around it. `src/openal/include/` and `src/openal/idal.cpp` stay (included by `precompiled.h` and `win_snd.cpp`). What remains binary in the tree is documentation (40 PDFs under `src/curl/docs/`, 18 under the libtheora tree, 62 repo-wide), vendored third-party UI/media assets (`bmp`/`png`/`ico`/`cur`/`gif`/`wav`) and SDL2's Android tooling (`src/sys/sdl2/android-project/gradle/wrapper/gradle-wrapper.jar`); no prebuilt code in any engine build target. The manifest keeps its 21 rows for them: the imported section records the split import as a census, not the present tree (so `totalFiles` 5,554 counts manifest entries, not checked-out files, and `provenance_post_split.py` reports 0 gaps against the tree). |

Status legend: **Resolved**, **Done**, **Decided** and **Recorded** rows are closed; **Deferred** rows state the reason and the owner or gate that holds them.

The SDK EULA (`legal/EULA SDK ETQW.rtf` in the game repo) is shipped on purpose (CHARTER §11); it is not retail content.

---

## 7. Retail-content scan

**Method.** Every blob in every ref of both repositories was hashed against every file in the retail 1.5 data, base and patch paks, megatextures and video (raw, LF and CRLF forms). Same-named text files were also compared line by line, every binary and resource file was checked against the Doom 3 GPL, dhewm3, RBDOOM-3-BFG, DOOM-3-BFG and openQ4 trees, and names and contents were swept for disassembly, IDA databases, PDBs and packet captures.

**Result.** The only matches are SDK script text at `src/compiledscript/scripts` (§2.2), found in both histories (the engine's only in its early commits). Those files are SDK-distributed text, not retail-only content.

**Why.** Retail assets must not be in either repository's history (CHARTER §11, release gate item 5). Before publishing, check that no ref on the host, including GitHub's pull-request refs, reaches retail content.

**Older commit IDs.** Engine SHAs quoted in older engine documents (for example `CHECKPOINT_STOCK_MAPS_PHASE.md`) may not resolve in the current history, and neither do some of the game's historical `tools/ci/engine.ref` pins. The engine's historical `tools/ci/companion.ref` pins all resolve.
