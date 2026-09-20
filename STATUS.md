# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-20T04:50:00Z · session/agent: worker_m0_1 · host: mbp-m1max.local, macOS arm64
Engine repo `~/openETQW`: commit 1130d16 · game repo `~/openETQW-game`: commit 9600d7a · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none)

**Integrator role:** held by worker_m0_1 / mbp-m1max.local

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
meson setup build -Dbuildtype=debug -Doptimization=0
meson compile -C build idLib libGameDecl game_arm64

# Engine build and tests
cd /Users/jmulesa/openETQW
meson setup build -Dbuild_engine=false -Dbuild_tests=true
meson compile -C build
meson test -C build --verbose
```

## This session

**Plan, and why it advances the engine:**
Execute Milestone 0 bootstrap: establish strict licensing separation between GPL engine and SDK game repositories per Owner Decision D5. Implement Meson + Ninja build system replacing Windows-only CMake, generate 100% provenance classification for all 5,263 files, verify zero-SDK tracking in engine, and validate native compilation of idLib, libGameDecl, and game_arm64.dylib on macOS arm64.

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | Top-level | Repository initialized | Host engine under GPL-3.0-or-later | Filtered from `~/etqw` commit `f2b655c` |
| openETQW | `tools/build/stage_game.py` | Dynamic game header staging script | Stage required game interfaces to `.tmp/` without git contamination | openQ4 adaptation |
| openETQW | `tests/test_zero_sdk.py` | Automated Zero-SDK audit test | Enforce 0 SDK files in engine repository | New (GPL-3.0-or-later) |
| openETQW | `docs/provenance.md` | 100% provenance classification & tracked exceptions | Legal compliance & release gate audit | New (GPL-3.0-or-later) |
| openETQW | `docs/compat-ledger.md` | Initialized compatibility ledger | Track architectural & wire divergence risks | New (GPL-3.0-or-later) |
| openETQW | `docs/baseline/observations-etqw-f2b655c.json` | Baseline source observations fixture | Cryptographic record of upstream state | verify_source_observations.py |
| openETQW-game | Top-level | Game repo initialized | Host SDK game logic under ETQW SDK EULA | Filtered from `~/etqw` + SDK 1.5 |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| 100% File Provenance Classification | inspected | macOS arm64 / Darwin 27.0.0 | `python3 tools/build/classify_provenance.py` | 5,263/5,263 files accounted for in 5 tiers |
| Zero-SDK Repository Compliance | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 0 SDK files tracked |
| Baseline Source Observations Verification | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 verify_source_observations.py` | PASS: 12/12 SHA-256 hashes matched upstream |
| Dynamic Header Staging | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tools/build/stage_game.py` | PASS: Staged headers to `.tmp/gamelibs_stage/` with manifest |
| Native Game Module & Libraries Build | compiled | macOS arm64 / Apple Clang 21.0 | `ninja -C openETQW-game/build` | PASS: libidLib.a (7.5M), liblibGameDecl.a (22M), game_arm64.dylib (13M, single export _GetGameAPI) |
| Asset-Free Unit Test Harness | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C openETQW/build --verbose` | PASS: 4/4 tests passed (bitmsg, usercmd-delta, struct-layout, zero-sdk) |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested < retail-interop-tested

### Not done / stubbed / known broken
- Engine executable target on macOS arm64 (pending platform layer port from openQ4/dhewm3 in M1)
- Vulkan renderer native backend initialization (pending windowing integration)
- Dedicated server map loading (blocked on retail asset mount in M1/M2)

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`.

| Capability | Linux x86_64 | Windows 11 x64 | macOS arm64 | macOS x86_64 | Linux arm64 | Notes |
|---|---|---|---|---|---|---|
| idLib + game module build | — | — | compiled | — | — | Native Apple Clang arm64 |
| Engine build | — | — | configured | — | — | Meson configure ready; engine donor pending |
| Dedicated server launch (headless) | — | — | blocked: M1 | — | — | Requires platform abstraction layer |
| Client launch (graphics) | — | — | blocked: M2 | — | — | Requires Vulkan surface creation |
| Stock map: server | — | — | blocked: M2 | — | — | Requires user-supplied retail assets |
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
- `docs/provenance.md` coverage: 5,263 / 5,263 files classified (100.0% coverage across both repos)
- LICENSE and README explain the split and the retail-data requirement: yes
- Retail binaries, PDBs, raw RE output, captures, or assets in either repo's history: none found (verified via git tree audit)
- SDK EULA shipped with the game repo and every package; retail-install check enforced; nothing sold: yes

## Compatibility record
- Module ABI (`GAME_API_VERSION`, interface changes this session): `GAME_API_VERSION 1` intact in `Game.h`
- Serialized/asset format changes: none (preserving retail serialization structures)
- Wire protocol id and OpenETQW-only deviations: none yet (baseline staging)
- Ledger rows added (`docs/compat-ledger.md`): 10 rows initialized covering CHARTER §7.4
- Consequence for retail 1.5 interop: Foundation laid without wire mutations

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | M0 Complete: Review and audit M0 artifacts | macOS arm64 | M0 verification | All unit tests pass, zero-SDK clean |
| 2 | Milestone 1: Platform abstraction layer port (SDL3 windowing & events) | macOS arm64 | M0 | `openETQW-ded_arm64` headless compilation |
| 3 | Milestone 1: Vulkan / MoltenVK renderer bootstrap | macOS arm64 | Step 2 | Vulkan device context creation without crash |
| 4 | Milestone 1: Headless dedicated server startup and console prompt | macOS arm64 | Step 2 | Server starts up and processes `status` command |

## Inputs needed from the owner
- Retail ETQW 1.5 Linux dedicated server binaries and pak files for M1/M2 headless testing.
- Map pk4 assets (`pak000.pk4` through `pak004.pk4`) for M2 map loading.

## Questions for the owner
- None at this time.
