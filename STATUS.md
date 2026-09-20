# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-20T06:56:00Z · session/agent: worker_m1_2 · host: mbp-m1max.local, macOS arm64
Engine repo `~/openETQW`: commit [local] · game repo `~/openETQW-game`: commit [local] · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none)

**Integrator role:** held by worker_m1_2 / mbp-m1max.local

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

# Full Meson automated test suite
meson test -C build --verbose

# Game build
cd /Users/jmulesa/openETQW-game
meson compile -C build idLib libGameDecl game_arm64

# Engine dedicated server build
cd /Users/jmulesa/openETQW
meson compile -C build openETQW-ded_arm64

# Verification of zero GPU / audio / Cocoa linkage
otool -L build/openETQW-ded_arm64

# End-to-end headless dedicated execution test (authentic retail pak data)
printf "status\nlistDecls\nhelp\nquit\n" | ./build/openETQW-ded_arm64 +set dedicated 1 +set fs_basepath /Users/jmulesa/etqw_data +set fs_game base
```

## This session

**Plan, and why it advances the engine:**
Milestone 1 Iteration 2 Remediation:
1. Zero-SDK Policy Remediation: Remove 3 tracked SDK files (`src/framework/BuildVersion.cpp`, `src/framework/BuildVersion.h`, `src/idlib/precompiled.h`) from openETQW git tracking, resolving Gate 2b violation.
2. Clean Engine Version & Header Staging: Provide clean GPL-3.0-or-later replacement files `src/framework/EngineVersion.h` and `src/framework/EngineVersion.cpp` defining `ENGINE_SRC_REVISION = 12658` and `ENGINE_MEDIA_REVISION = 33244`. Inject `<string>` and `<vector>` into `src/framework/precompiled.h` before `#include "idlib/precompiled.h"` to prevent libc++ `<locale>` macro poisoning. Ensure `#include "idlib/precompiled.h"` resolves via `.tmp/gamelibs_stage/src/idlib/precompiled.h`.
3. POSIX Platform Layer Bug Fixes: Apply and verify `posix_fixes.patch` across `src/sys/posix/` and `src/sys/sys_local.cpp`:
   - Eliminate 64-bit thread handle truncation via static `g_threadTable[MAX_THREADS]` with 1-based integer handles and native `pthread_t` storage.
   - Fix buffer transposition in `posix_net.cpp:207` (`static char s[4][64]`).
   - Fix non-blocking console EOF detection using `read() == 0` in `posix_main.cpp:517`.
   - Implement strictly monotonic timing in `Sys_Milliseconds` via `clock_gettime(CLOCK_MONOTONIC)`.
   - Implement async-signal-safe error logging via unbuffered `write(STDERR_FILENO, ...)` in `posix_signal.cpp`.
   - Restore terminal canonical/echo mode on shutdown/exit via `Posix_ShutdownConsoleInput()` and `atexit()`.
   - Parameterize architecture suffix `SYS_ARCH_SUFFIX` in `idSysLocal::DLL_GetFileName`.
4. Workspace Hygiene: Remove synthetic `/Users/jmulesa/etqw_data/base/default.cfg` and cleaned test directory. Verify dedicated server boots gracefully without synthetic configuration.
5. Rigorous Empirical Verification: Live execution of `test_zero_sdk.py`, `meson test` 4/4 suite, `openETQW-ded_arm64` headless dedicated console loop, and `otool -L` linkage audit.

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `src/framework/BuildVersion.*`, `src/idlib/precompiled.h` | Removed from git tracking | Zero-SDK Gate 2b compliance | Proprietary SDK files untracked |
| openETQW | `src/framework/EngineVersion.h`, `EngineVersion.cpp` | Clean GPL-3.0-or-later version module | Provide wire/console revision constants (12658/33244) | Clean GPL-3.0-or-later replacement |
| openETQW | `src/framework/precompiled.h` | Include stdlib `<string>` & `<vector>`, include `idlib/precompiled.h` & `EngineVersion.h` | Prevent libc++ macro poisoning, resolve staged idLib PCH | Engine PCH fix |
| openETQW | `tools/build/meson_sources.py` | Reference `EngineVersion.cpp` instead of `BuildVersion.cpp` | Build clean GPL engine version module | Meson source list |
| openETQW | `src/sys/posix/posix_threads.cpp` | Static thread slot table `g_threadTable[MAX_THREADS]` | Prevent 64-bit pointer truncation in 32-bit threadHandle | POSIX bug fix |
| openETQW | `src/sys/posix/posix_net.cpp` | `static char s[4][64]` rotating buffers | Prevent IP/port string truncation | POSIX bug fix |
| openETQW | `src/sys/posix/posix_main.cpp` | Robust EOF detection (`read() == 0`), monotonic clock, terminal restoration | Safe non-blocking input, accurate frame delta, clean tty exit | POSIX bug fix |
| openETQW | `src/sys/posix/posix_public.h` | Declare `Posix_ShutdownConsoleInput()` | Allow graceful terminal cleanup | POSIX header |
| openETQW | `src/sys/posix/posix_signal.cpp` | Async-signal-safe `write()` in fatal handler | Prevent deadlock in fatal signal reporting | POSIX bug fix |
| openETQW | `src/sys/posix/posix_syscon.cpp` | Resolve `idlib/precompiled.h` via staging | Header staging resolution | POSIX include fix |
| openETQW | `src/sys/stub/headless_stubs.cpp` | Resolve `idlib/precompiled.h` via staging | Header staging resolution | POSIX include fix |
| openETQW | `src/sys/sys_local.cpp` | Parameterize `SYS_ARCH_SUFFIX`, resolve staged `idlib/precompiled.h` | Multi-arch DLL naming & staged header | POSIX bug fix |
| etqw_data | `base/default.cfg` | Deleted synthetic config | Clean room verification on pure retail paks | Hygiene cleanup |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| Zero-SDK Repository Compliance (Gate 2b) | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 4231 tracked files audited, 0 SDK violations detected |
| Meson Automated Test Suite (4/4 passed) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build --verbose` | PASS: 4/4 tests passed (bitmsg, usercmd-delta, struct-layout, zero-sdk) |
| Dedicated Server Compilation Cleanliness | compiled | macOS arm64 / Apple Clang 21.0 | `meson compile -C build openETQW-ded_arm64` | PASS: Compiled cleanly with zero errors |
| Zero-Dependency Dedicated Server Linkage | inspected | macOS arm64 / Apple Clang 21.0 | `otool -L build/openETQW-ded_arm64` | PASS: Strictly links only libz, libc++, libSystem (0 GPU/audio/Cocoa/SDL) |
| Retail Asset Purity & Packaging Isolation | inspected | macOS arm64 / Darwin 27.0.0 | `ls -la /Users/jmulesa/etqw_data/base` | PASS: Pure retail paks pak000-pak004, zpak_english000, 0 synthetic configs |
| Git Isolation of Retail Data | unit-tested | macOS arm64 / Darwin 27.0.0 | `git status` (both repos) | PASS: 0 retail files or pk4s staged or tracked; etqw_data outside git |
| File System Mounting & Decl Parsing | launched | macOS arm64 / Apple Clang 21.0 | `./build/openETQW-ded_arm64 ...` | PASS: 6 paks mounted (15k+ files), 20 builtin decl types registered, 13,634 decls parsed |
| Game DLL Dynamic Loading via GetGameAPI | launched | macOS arm64 / Apple Clang 21.0 | `./build/openETQW-ded_arm64 ...` | PASS: game_arm64.dylib loaded, event system & class hierarchy initialized |
| Dedicated Console Lifecycle & Clean Exit | launched | macOS arm64 / Apple Clang 21.0 | `printf "status\nlistDecls\nhelp\nquit\n" \| ./build/openETQW-ded_arm64 ...` | PASS: processed status, listDecls, help, quit; clean shutdown, exit code 0 |

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
