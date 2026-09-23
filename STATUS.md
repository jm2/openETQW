# OpenETQW status

[![CI Matrix](https://github.com/jm2/openETQW/actions/workflows/ci.yml/badge.svg)](https://github.com/jm2/openETQW/actions/workflows/ci.yml)

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-23 · session/agent: Claude Code (stock-maps phase M2–M4) · host: mbp-m1max.local, macOS arm64
Engine repo `~/openETQW`: branch wip/stock-maps-m2-checkpoint (pushed; `main` unchanged at 7545679) · game repo `~/openETQW-game`: branch wip/stock-maps-m2-checkpoint (pushed; `main` unchanged at 632aacb) · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none)

**Integrator role:** held by lead_orchestrator / mbp-m1max.local

## Owner decisions in effect
D1 public source and binaries · D2 all RE methods · D3 hard fork · D4 macOS / Windows 11 / Arch / Fedora / Debian, inputs on request · D5 two repos: GPL-3.0-or-later engine; SDK license + MIT game · D6 Meson · D7 clean network break from retail 1.5 (2026-09-22; retail paks still required)
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

# Full local test suite with retail assets (13/13 passing)
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

**Plan, and why it advances the engine:** the stock-maps phase (ORIGINAL_REQUEST.md, 2026-09-22T11:36:11Z) covers the 11 remaining stock maps.
- M2 (AAS navigation and 30 Hz bot traversal on all 11) and M3 (Vulkan client per campaign) are verified.
- M4 (automated tests and CI parity with openQ4) is in progress.
- Gemini's uncommitted M1 loader fixes were reviewed; the harmful parts were reverted or fixed.
- Details: `CHECKPOINT_STOCK_MAPS_PHASE.md`.

### Changed
| Repo | Files / symbols | What | Why |
|---|---|---|---|
| openETQW | `src/sys/stub/headless_stubs.cpp`, `src/renderer/ImageParse.h`, `Material.cpp`, `decllib/declRenderProgram.cpp` | The dedicated server parses image references like the client and gets a placeholder image; render programs resolve without a shader backend | Headless material defaulting turned water, ladder and nonsolid surfaces into CONTENTS_SOLID (about 300–370 failed decls per map, now 0), so collision diverged from the AAS |
| openETQW | `framework/async/AsyncNetwork.cpp`, `AsyncServer.cpp/.h`, `sys/sys_local.cpp` | `spawnServer <map>` on a running server changes maps; bot client slots are re-begun and released instead of leaking; console lines survive map changes; the game is not ticked without a map | In-process map changes were broken |
| openETQW | `framework/Common.cpp`, `CVarSystem.cpp`, `CmdSystem.cpp`, `GameModuleCallbacks.h` | Printing is thread-safe; an error in the first map load no longer aborts; cvar and command callbacks into the game module are restored before unload (openQ4 63756efb pattern); CmdSystem is exception-safe; frame pacing uses `std::chrono` and the fatal-error backtrace is POSIX-only | Bot and FS threads print; stability; Windows portability |
| openETQW | `framework/async/AsyncNetwork.h` | Protocol 0x4F45 ('OE').4, with a refusal message naming it; reliable game init, snapshot resend, remote clients follow map changes (ledger 045) | D7 clean network break |
| openETQW | `sys/osx/macosx_compat.mm`, `posix_main.cpp`, `renderer/image_sequence.cpp`, `FileSystem.cpp` | The macOS savepath is `openETQW` (was openQ4); the terminal mode is restored; `globalImages` guards | Leftovers and latent crashes |
| openETQW | `renderer/tr_light.cpp`, `VulkanBackend.cpp` | "Vulkan shadow lights" diagnostic at shutdown | Separates "no shadow light in view" from "shadows dropped" |
| openETQW-game | `botai/aas/AASFile_local.cpp` | TraceFloor/Trace treat travel flags as allowed, not blocking; `FindReachabilityByName` returns -1; minimal PushPointIntoArea; portal and cluster index validation | Path smoothing and direct paths were always failing; island disabled a real reachability |
| openETQW-game | `botai/BotThreadData.cpp`, `BotThread.cpp`, `gamesys/SysCmds.cpp` | Map id detection, `validClasses` bitmask, InitAAS leaks and `aas_types`, `botThreadStats` command, PLAYER_POS `hp= dead= area=` | Map-specific bot logic was dead; test diagnostics |
| openETQW-game | `rules/AdminSystem.cpp`, `GameRules.cpp`, `GameRules_Objective.cpp`, `roles/ObjectiveManager.*` | `addbot` spawns via CheckRespawns; `playerPos`/`spawn` no longer removed with the rules; retail warmup EndGame guard restored; `sendNetworkEvent` signature fixed | Early spawns at the origin; lost commands; `$null_entity` warnings |
| openETQW-game | `idlib/math/Math.h`, `Math.cpp`, `Random.h`, `MD4/MD5`, `gamesys/Pvs.cpp` | LP64 fixes: `RSqrt` read 8 bytes of a 4-byte float (random sign flips in NormalizeFast/LengthFast); rvRandom, MD4/MD5, PVS word size | 64-bit correctness |
| openETQW-game | `idlib/Heap.h/.cpp`, `Properties.*`, `threading/*`, `physics/Clip.cpp` | O(1) `idBlockAlloc::Free` (validation opt-in, UB-free offset); property lifetime; per-thread priority (no macOS process renice), 4 MB stack, signal timeouts, `isRunning`; strict clip thread check | Reverts Gemini's hot-path O(n) validation; threading fixes |
| openETQW-game | `common/common.h`, `idlib/Lib.cpp`, `meson.build` | Linux x86_64/aarch64 build defines, non-x86 assert trap, MSVC `/Zc:strictStrings-` | Portability (found by the CI review) |
| openETQW-game | `physics/Push.cpp`, `Game_network.cpp` | An over-long non-clipping push is a teleport; unreliable-event snapshot budget | openQ4-game 841a573 / f5596b6 patterns (independent implementations) |
| both | `tests/*`, `tools/ci/*`, `.github/workflows/*` | New harness and tests, and CI parity workflows; retail data comes from `ETQW_DATA_PATH` only and all 30 retail-data tests exit 77 without it (skip baseline empty) | M2–M4 |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result |
|---|---|---|---|---|
| All 11 stock maps: load, entities, scripts, AAS, bots | match-tested | macOS arm64 / Apple Clang 21 | `ETQW_DATA_PATH=… python3 tests/test_all_stock_maps.py --jobs 3` | 11/11 PASS (table below) |
| Bot AI at a true 30 Hz | match-tested | macOS arm64 | same, with `--bot-think-delay 0` | 11/11 PASS, think ratio 0.999–1.000 (the retail default thinks every second frame: 0.500) |
| Full meson suite with retail data | match-tested | macOS arm64 | `ETQW_DATA_PATH=… meson test -C build -j 1` | 13/13 OK, including all-stock-maps, inprocess-map-change, protocol-clean-break, valley-match (a started match, 9/9 phases), bot-gameplay, vulkan-shadows, determinism |
| In-process map changes | map-loaded | macOS arm64 | `tests/test_inprocess_map_change.py` | quarry→outskirts→ark→ark: correct map and entities, queued stdin survives, no bot slot leak |
| Clean network break (D7) | launched | macOS arm64 | `tests/test_protocol_clean_break.py` | 8 retail/id majors refused; OpenETQW protocol reaches connectResponse |
| Vulkan client, one map per campaign | match-tested | macOS arm64 / MoltenVK | `tests/test_campaign_client_maps.py` | 4/4: outskirts (224k shadow draws), ark (29k), canyon from the Strogg spawn (25k), refinery loading bay (14k); MegaTexture, sky, interactions, exit 0 |

**Per-map metrics** (`test_all_stock_maps.py`, fresh dedicated process per map, 45 s bot window, one GDF and one Strogg soldier):

| Map | Campaign | Entities spawned+inhibited / retail | Player AAS areas / reach | Vehicle AAS areas / reach | .nav nodes | Bot max per-life displacement GDF / Strogg (u) | AAS containment GDF / Strogg | Longest stuck (s) | Server tick (Hz) | Bot think ratio default / delay 0 |
|---|---|---|---|---|---|---|---|---|---|---|
| area22 | NA | 1063+1 / 1064 | 9390 / 56197 | 8352 / 40156 | 83 | 3733 / 13308 | 100% / 100% | 0.5 | 30.3 | 0.5 / 1.0 |
| ark | N.Europe | 898+1 / 899 | 7339 / 39233 | 4506 / 20237 | 71 | 8322 / 10608 | 100% / 100% | 0.5 | 30.26 | 0.5 / 1.0 |
| canyon | S.Pacific | 849+0 / 849 | 10640 / 53226 | 8349 / 33140 | 182 | 19275 / 10554 | 100% / 100% | 0.5 | 30.27 | 0.5 / 1.0 |
| island | Africa | 723+0 / 723 | 8779 / 42963 | 6598 / 26500 | 50 | 9292 / 4319 | 100% / 100% | 0.0 | 30.28 | 0.5 / 1.0 |
| outskirts | NA | 863+0 / 863 | 12203 / 64909 | 6045 / 25580 | 159 | 15819 / 4112 | 100% / 100% | 2.1 | 30.29 | 0.5 / 1.0 |
| quarry | N.Europe | 414+1 / 415 (≤500: retail data) | 8878 / 45901 | 5419 / 22337 | 91 | 2378 / 12369 | 100% / 100% | 0.0 | 30.29 | 0.5 / 1.0 |
| refinery | Africa | 935+1 / 936 | 9564 / 52214 | 5489 / 25487 | 129 | 14612 / 8532 | 100% / 99% | 0.5 | 30.27 | 0.5 / 0.999 |
| salvage | N.Europe | 923+0 / 923 | 9577 / 45289 | 4364 / 16667 | 23 | 8086 / 10957 | 100% / 100% | 0.0 | 30.28 | 0.5 / 1.0 |
| sewer | S.Pacific | 874+0 / 874 | 6089 / 30667 | 4432 / 17006 | 58 | 14099 / 10281 | 100% / 100% | 0.0 | 30.29 | 0.5 / 1.0 |
| slipgate | Africa | 1252+6 / 1258 | 17005 / 82193 | 7067 / 28580 | 174 | 7783 / 10607 | 100% / 100% | 4.2 | 30.27 | 0.5 / 0.999 |
| volcano | S.Pacific | 1335+1 / 1336 | 7641 / 36818 | 3928 / 15838 | none (no retail .nav) | 4225 / 8791 | 100% / 100% | 0.0 | 30.3 | 0.5 / 0.999 |

### Not done / stubbed / known broken
- The engine has never been compiled on Linux or Windows: hosted CI can't fetch the private game repo (no cross-repo token, by owner decision) and this host has no container runtime. The Windows engine still needs the win32 layer (`SystemBootstrap`/`win_local` SDL3) and a Windows counterpart of `sys/sys_local.cpp`; `Common.cpp` no longer needs POSIX headers.
- The game module and idLib do compile on other platforms, in the game repo's CI (see the capability matrix).
- Vulkan validation layers are not installed on the host, so validation errors can't be observed. There is no screenshot support in the Vulkan backend.
- The headless render world has no traces (radius damage ignores walls on the dedicated server).
- quarry spawns 415 retail entities (≤500 criterion; owner decision).
- jmarshall provenance: a byte-level audit (local report) found about 54.7k lines of compiled engine code that are jmarshall-authored and have no license; see Release gate.

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`.

| Capability | Linux x86_64 | Windows 11 x64 | macOS arm64 | macOS x86_64 | Linux arm64 | Notes |
|---|---|---|---|---|---|---|
| idLib + game module build | compiled, module loaded (game CI: GCC, Clang, ASan+UBSan) | compiled, module loaded (game CI: MSVC) | compiled | compiled, module loaded (game CI) | compiled (game CI: GCC cross from x64; not loaded) | Exports only `GetGameAPI` on every platform (Linux version script, macOS exported-symbols list, Windows .def); Linux full RELRO |
| Engine build | configured (never compiled; hosted CI can't fetch the private game repo) | blocked: win32 SystemBootstrap/win_local SDL3, POSIX-only `sys_local.cpp` | compiled | — | — | Linux (dl, rt, m, SDL3), Windows (Win32 libs, subsystem windows/console), macOS (Metal/MoltenVK) |
| Dedicated server launch (headless) | configured | configured | launched | — | — | POSIX main on Linux/macOS; console subsystem main() with SetConsoleCtrlHandler on Win32 |
| Client launch (graphics) | configured | configured | match-tested | — | — | SDL3 + Vulkan backend across Linux, Windows, macOS |
| Asset-free unit test suite | configured | configured | unit-tested | — | — | 5/5 asset-free tests pass (--suite unit); integration tests exit 77 when assets absent |
| Stock map: server | — | — | match-tested | — | — | Valley + all 11 stock maps: load, entities, scripts, AAS, bots (`test_all_stock_maps.py`) |
| Stock map: client | — | — | match-tested | — | — | Valley + one map per campaign (outskirts, ark, canyon, refinery): MegaTexture, geometry, stencil shadows (`test_campaign_client_maps.py`) |
| OpenETQW MP: dedicated | — | — | match-tested | — | — | Authoritative server hosts multiple concurrent clients with UDP replication |
| OpenETQW MP: listen server | — | — | map-loaded | — | — | Local single-player / listen server active on Valley |
| Cross-OS client/server pair | — | — | — | — | — | Target milestone M3 cross-OS |
| High refresh: on foot | — | — | match-tested | — | — | High-refresh Vulkan presentation decoupled from 30 Hz simulation |
| High refresh: vehicles | — | — | match-tested | — | — | Sub-tick proxy view smoothing across cockpit and chased camera |
| Bots / navigation | — | — | match-tested | — | — | All 11 stock maps + Valley: AAS loads (counts = retail), bots traverse, 96–100% AAS containment; AI thinks at 15 Hz by default, 30 Hz with bot_threadMinFrameDelay 0 |
| Dynamic stencil shadows | — | — | match-tested | — | — | Vulkan two-sided stencil shadow volumes & dynamic lighting interactions |
| OpenETQW ↔ OpenETQW network play | — | — | match-tested | — | — | Dedicated server and multi-client replication over UDP (protocol 0x4F45.4) |
| Retail 1.5 peer refused at connect (clean break, D7) | — | — | launched | — | — | `tests/test_protocol_clean_break.py`: majors 1, 2, 10–14 and 22 are refused with 'Server uses protocol 20293.4' |

### Build and dependency scripts verified
| Script | Arch | Fedora | Debian | macOS | Windows 11 |
|---|---|---|---|---|---|
| `scripts/deps/*` | — | — | — | verified (brew) | — |
| `scripts/build.*` | — | — | — | verified | — |

## Release gate (CHARTER §11)
- jmarshall license: **unresolved and blocking.** jmarshall/etqw (also at git.retrodamage.com/jmarshall/etqw) has no license. A byte-level audit (2026-09-22, local report `.agents/claude_session_2026-09-22/JMARSHALL_PROVENANCE_AUDIT.md`) found 135 compiled engine files (about 54.7k lines) with no GPL/SDK/third-party upstream match, 57 game files that are SDK files with jmarshall edits, and both repos' history rooted in jmarshall's commits. Quake4Doom, openQ4 and openPREY add no file-level coverage beyond Doom 3. Remediation is deferred by the owner.
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
- Retail 1.5 network interop: none by design (clean break, D7). Retail asset formats are still read unchanged; the wire protocol is OpenETQW's own (0x4F45.4).

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Game CI: done (every build and sanitizer leg is required and green). Engine CI: build legs start running when the repos are public | GitHub Actions | repos public | Engine matrix builds; experimental flags flipped as legs pass |
| 2 | Stock-maps M5: owner review of the WIP branches (ledger rows 036–044 written, 032/035 corrected), then merge to main | Local | 1 | Docs reviewed; `main` updated |
| 3 | Windows engine portability (win32 layer, `sys_local.cpp`) | Windows CI (repos public) | 1 | Windows legs compile |
| 4 | jmarshall remediation (owner decision) | — | owner | See release gate |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- 30 Hz criterion: should bots think at a true 30 Hz (`bot_threadMinFrameDelay 0`)? The retail default is about 15 Hz, and the test reports both.
- quarry: accept 415 retail entities against the ">500 entities per map" criterion?
- jmarshall provenance: remediation strategy (license request, clean-room rebuild, or both).
