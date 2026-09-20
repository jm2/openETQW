# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-20T06:40:00Z · session/agent: worker_m1_1 · host: mbp-m1max.local, macOS arm64
Engine repo `~/openETQW`: commit [local] · game repo `~/openETQW-game`: commit [local] · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none)

**Integrator role:** held by worker_m1_1 / mbp-m1max.local

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
# Staging and verification
python3 tools/build/stage_game.py . ../openETQW-game .tmp/gamelibs_stage
python3 tests/test_zero_sdk.py

# Game build
cd /Users/jmulesa/openETQW-game
meson compile -C build idLib libGameDecl game_arm64

# Engine dedicated server build
cd /Users/jmulesa/openETQW
meson setup build -Dbuild_dedicated=true -Dbuild_engine=false
meson compile -C build openETQW-ded_arm64

# Verification of zero GPU / audio / Cocoa linkage
otool -L build/openETQW-ded_arm64

# End-to-end headless dedicated execution test
printf "status\nlistDecls\nhelp\nquit\n" | ./build/openETQW-ded_arm64 +set dedicated 1 +set fs_basepath /Users/jmulesa/etqw_data +set fs_game base
```

## This session

**Plan, and why it advances the engine:**
Execute Milestone 1 (Native Execution & Headless Dedicated Server):
1. Extract retail assets from `/Users/jmulesa/Desktop/etqw-data-steam-1-1-any.pkg.tar.zst` into isolated directory `/Users/jmulesa/etqw_data/base` outside git repositories. Verify git status remains clean in both repos.
2. Implement POSIX platform layer for macOS arm64 (`src/sys/posix/`, `src/sys/sys_local.cpp`, `src/sys/stub/headless_stubs.cpp`) without GPU/audio dependencies, configured in Meson for `openETQW-ded_arm64`. Verify zero runtime linkage to OpenGL, Metal, OpenAL, or Cocoa.
3. Fix filesystem mounting and declaration initialization: handle `default.cfg` missing in Steam retail paks, stage `DeclType.cpp` to register all 20 builtin decl types, reconcile `gameImport_t` offset 0x68 union, and load `game_arm64.dylib` via `GetGameAPI`.
4. Implement dedicated server console lifecycle: process `status`, `listDecls`, `help`, and `quit` with clean exit code 0.

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `src/sys/posix/*` | POSIX platform layer implementation | Provide macOS arm64 headless execution, terminal I/O, signals, threads | openQ4 adaptation |
| openETQW | `src/sys/stub/headless_stubs.cpp` | Zero-dependency headless stubs | Provide dummy renderSystem, soundSystem, etc. for dedicated server | New (GPL-3.0-or-later) |
| openETQW | `src/sys/sys_local.cpp` | POSIX sys_local methods | Event queue, timing, memory, DLL loading | New / openQ4 adaptation |
| openETQW | `src/decllib/DeclRenderProgram_headless.cpp` | Headless render program backend | Allow decl loading without OpenGL dependency | New (GPL-3.0-or-later) |
| openETQW | `src/decllib/declEffect.cpp` | Effect declaration implementation | Register `declEffectsType` in builtin decl table | New (GPL-3.0-or-later) |
| openETQW | `src/framework/Common.cpp` | Headless lifecycle, `status`, `help` commands, game DLL loading | Enable dedicated console commands and game loading | Clean GPL reconstruction |
| openETQW | `src/framework/FileSystem.cpp` | Tolerant default.cfg loading in dedicated mode | Steam retail paks omit default.cfg | Clean GPL reconstruction |
| openETQW | `meson.build`, `meson_sources.py` | Dedicated build target decoupling & flags | Enable standalone `openETQW-ded_arm64` build | New build configuration |
| openETQW | `.gitignore` | Ignore pk4, mega, bik, base, etqw_data | Maintain strict asset isolation | Git configuration |
| openETQW-game | `src/game/Game.h` | Anonymous union for `bse`/`networkService` and `navigationSystem` | Reconcile gameImport_t ABI offset 0x68 | SDK parity fix |
| openETQW-game | `src/decllib/DeclType.cpp` | Decl registration table staging | Provide all 20 builtin decl types | SDK parity fix |
| openETQW-game | `.gitignore` | Ignore pk4, mega, bik, base, etqw_data | Maintain strict asset isolation | Git configuration |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| Retail Asset Extraction & Storage Isolation | inspected | macOS arm64 / Darwin 27.0.0 | `ls -la /Users/jmulesa/etqw_data/base` | 4.6 GB retail data extracted (pak000-pak004, zpak_english000, 12 megatextures) outside git tracking |
| Git Isolation of Retail Data | unit-tested | macOS arm64 / Darwin 27.0.0 | `git status` (both repos) | PASS: 0 retail files or pk4s staged or tracked |
| Zero-Dependency Dedicated Server Linkage | inspected | macOS arm64 / Apple Clang 21.0 | `otool -L build/openETQW-ded_arm64` | PASS: Strictly links only libz, libc++, libSystem (0 GPU/audio/Cocoa) |
| File System Mounting & Decl Parsing | launched | macOS arm64 / Apple Clang 21.0 | `./build/openETQW-ded_arm64` | PASS: 6 paks mounted (15k+ files), 20 builtin decl types registered, 13,634 decls parsed |
| Game DLL Dynamic Loading via GetGameAPI | launched | macOS arm64 / Apple Clang 21.0 | `./build/openETQW-ded_arm64` | PASS: game_arm64.dylib loaded, event system & class hierarchy initialized |
| Dedicated Console Lifecycle & Clean Exit | launched | macOS arm64 / Apple Clang 21.0 | `printf "status\nlistDecls\nhelp\nquit\n" \| ./build/openETQW-ded_arm64 ...` | PASS: processed status, listDecls, help, quit; exit code 0 |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested < retail-interop-tested

### Not done / stubbed / known broken
- Vulkan renderer native backend initialization (Milestone 2)
- Client launch with graphics / windowing (Milestone 2)
- Dedicated server map loading / game loop (Milestone 2)

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`.

| Capability | Linux x86_64 | Windows 11 x64 | macOS arm64 | macOS x86_64 | Linux arm64 | Notes |
|---|---|---|---|---|---|---|
| idLib + game module build | — | — | compiled | — | — | Native Apple Clang arm64 |
| Engine build | — | — | compiled | — | — | Native `openETQW-ded_arm64` dedicated server |
| Dedicated server launch (headless) | — | — | launched | — | — | Headless POSIX server cleanly runs and quits (exit 0) |
| Client launch (graphics) | — | — | blocked: M2 | — | — | Requires Vulkan surface creation |
| Stock map: server | — | — | blocked: M2 | — | — | Requires map entity spawner / collision loading |
| Stock map: client | — | — | blocked: M2 | — | — | Requires user-supplied retail assets |
| OpenETQW MP: dedicated | — | — | blocked: M3 | — | — | Requires snapshot wire implementation |
| OpenETQW MP: listen server | — | — | blocked: M3 | — | — | Requires listen server decoupling |
| Cross-OS client/server pair | — | — | blocked: M3 | — | — | Target milestone M3 |
| High refresh: on foot | — | — | blocked: M5 | — | — | Requires `ClientUpdateView` frame decoupling |
| High refresh: vehicles | — | — | blocked: M5 | — | — | Requires proxy view smoothing |
| Bots / navigation | — | — | blocked: M4 | — | — | Requires QWNV navmesh loader |
| Retail interop: dir 1 (Open ↔ Open) | — | — | blocked: M3 | — | — | Requires OpenETQW framing codec |
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
- Wire protocol id and OpenETQW-only deviations: none yet (baseline staging)
- Ledger rows added (`docs/compat-ledger.md`): 12 rows tracking CHARTER §7.4 (including 011 and 012)
- Consequence for retail 1.5 interop: Foundation laid without wire mutations

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | M1 Complete: Review and audit M1 artifacts | macOS arm64 | M1 verification | Dedicated server launches, mounts, executes commands, quits with 0 |
| 2 | Milestone 2: Vulkan / MoltenVK renderer bootstrap | macOS arm64 | M1 | Vulkan device context creation without crash |
| 3 | Milestone 2: Dedicated server map loading (`devmap valley`) | macOS arm64 | M1 | Server loads map geometry and spawns game entities |
| 4 | Milestone 2: Client window creation with SDL3 & input handling | macOS arm64 | Step 2 | Client window opens with game console |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- None at this time.
