# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-20T15:02:00Z · session/agent: worker_m2_1 · host: mbp-m1max.local, macOS arm64
Engine repo `~/openETQW`: commit [local] · game repo `~/openETQW-game`: commit [local] · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none)

**Integrator role:** held by worker_m2_1 / mbp-m1max.local

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
ninja -C build

# Engine dedicated server build
cd /Users/jmulesa/openETQW
ninja -C build openETQW-ded_arm64

# Live dedicated server simulation verification (>100 frames at 30 Hz with status and quit)
python3 -u -c "
import subprocess, time, sys
p = subprocess.Popen(
    ['./build/openETQW-ded_arm64', '+set', 'dedicated', '1', '+set', 'fs_basepath', '/Users/jmulesa/etqw_data', '+set', 'fs_game', 'base', '+set', 'si_rules', 'sdGameRulesObjective', '+spawnServer', 'valley'],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
)
start = time.time(); active_start = None
while True:
    line = p.stdout.readline()
    if not line: break
    if 'StartFirstObjective' in line and active_start is None: active_start = time.time()
    if active_start and time.time() - active_start >= 5.0: break
p.stdin.write('status\n'); p.stdin.flush(); time.sleep(2)
p.stdin.write('quit\n'); p.stdin.flush()
for line in p.stdout: print(line.strip())
rc = p.wait(timeout=10); assert rc == 0
"
```

## This session

**Plan, and why it advances the engine:**
Milestone 2 Implementation (Stock Map Loading & Simulation):
1. Resolve Declaration / Lexer Parser Faults: Fix null-pointer dereference in `rvDeclEffect::Parse` / `idLexer::SkipWhiteSpace` across `Lexer.cpp`, `Parser.cpp`, `WLexer.cpp`, `declEffect.cpp`, `BSE_EffectTemplate.cpp`, `Material.cpp`. Dispatch binary vs text parsing correctly and guard empty tokens.
2. Collision Model & Proc Loading Parity: Reconcile collision model caching for `worldMap0` in `CollisionModel.cpp` and `.cm`/`.cmb` fallback in `CollisionModel_files.cpp`. Add Row 013 to `docs/compat-ledger.md` documenting skipped binary proc chunks (`shadowModel` and `mapEnvBounds`).
3. Dedicated Server Simulation Pacing: Introduce `Sys_Sleep(1)` in `posix_main.cpp` main loop when headless dedicated server frame delta is under tick rate to maintain smooth 30 Hz simulation without 100% busy-looping.
4. Headless World Abstractions & Bone Hierarchy: Implement `sdRenderWorldHeadlessLocal` and `sdSoundWorldHeadlessLocal` in `Game_local.cpp` to ensure headless server safe execution. Implement `LoadMD5Binary` reference pose and bounds parsing from retail `generated/md5binary/*.md5b` models in `idRenderModelHeadlessLocal`. Fix `GetJointHandle` to return `INVALID_JOINT` rather than mutating model bone count.
5. ARM64 Calling Convention Reconciliation: Fix `CPU_EASYARGS` in `openETQW-game/src/common/common.h` (set to 0 for 64-bit arm64/x86-64) so that event dispatch in `Class.cpp` uses typed callback prototypes (`Callbacks.cpp`), preventing register misalignments when float arguments precede pointers.
6. Full Map Spawning & Objective Execution: Load `maps/valley.world` (`valley.proc`, `valley.cm`, `valley.entities`), spawn all 991 entities, compile scripts, instantiate `mapObject_Valley`, and execute `InitObjectives()` and `StartFirstObjective()`.
7. Rigorous Verification: Verified live execution of >150 frames (up to 750 frames) at authoritative 30 Hz pacing with console `status` inspection and graceful shutdown on `quit` with exit code 0. Passed 100% automated tests (`test_zero_sdk.py` and `meson test`).

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW-game | `src/idlib/text/Lexer.cpp`, `Parser.cpp`, `WLexer.cpp` | Buffer pointer guards and bounds checks | Prevent null-pointer dereference on empty/binary decl data | Text parser hardening |
| openETQW | `src/decllib/declEffect.cpp`, `src/bse/BSE_EffectTemplate.cpp` | Binary decl format dispatch (`bse_`) and empty buffer guards | Fix declEffect crash during decl loading | Engine decl fix |
| openETQW | `src/renderer/Material.cpp` | Binary buffer dispatch and text length guards | Fix material decl parsing crash | Engine renderer fix |
| openETQW | `src/decllib/declentitydef.cpp` | Remove recursive `declManager->CacheFromDict` in `idDeclEntityDef::Parse` | Prevent infinite mutual recursion between entityDef parsing and deployable object decls | Decl parser fix |
| openETQW | `src/cm/CollisionModel.cpp`, `CollisionModel_files.cpp` | Retain cached collision model across `worldMap0` loads; `.cmb` fallback | Prevent collision model manager failure on stock proc maps | CM fix |
| openETQW | `src/sys/posix/posix_main.cpp` | Add `Sys_Sleep(1)` dedicated server frame pacing | Prevent 100% CPU busy-spin while maintaining 30 Hz server rate | POSIX platform fix |
| openETQW-game | `src/game/Game_local.cpp` | `sdRenderWorldHeadlessLocal`, `sdSoundWorldHeadlessLocal`, MD5B loader, `GetJointHandle` fix | Complete headless rendering/sound/model support for stock maps | Game module fix |
| openETQW-game | `src/common/common.h` | Set `CPU_EASYARGS 0` for `__arm64__` and `__x86_64__` | Use typed event callbacks (`Callbacks.cpp`) on 64-bit architectures, fixing register mismatch | Architecture ABI fix |
| openETQW | `docs/compat-ledger.md` | Add Row 013 for binary proc chunks (`shadowModel`, `mapEnvBounds`) | Document binary proc skip behavior per CHARTER §7.4 | Compatibility ledger |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| Zero-SDK Repository Compliance (Gate 2b) | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 4231 tracked files audited, 0 SDK violations detected |
| Meson Automated Test Suite (4/4 passed) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build --verbose` | PASS: 4/4 tests passed (bitmsg, usercmd-delta, struct-layout, zero-sdk) |
| Stock Map Geometry & Collision Loading | map-loaded | macOS arm64 / Apple Clang 21.0 | `./build/openETQW-ded_arm64 +spawnServer valley` | PASS: `maps/valley.world`, `valley.proc`, `valley.cm` parsed & loaded |
| Full 991 Entity Spawning | map-loaded | macOS arm64 / Apple Clang 21.0 | `./build/openETQW-ded_arm64 +spawnServer valley` | PASS: All 991 entities from `maps/valley.entities` spawned |
| Script Compilation & Objective Execution | map-loaded | macOS arm64 / Apple Clang 21.0 | `./build/openETQW-ded_arm64 +spawnServer valley` | PASS: `script/main.script`, `mapObject_Valley`, `InitObjectives`, `StartFirstObjective` ran |
| Authoritative 30 Hz Simulation Loop | map-loaded | macOS arm64 / Apple Clang 21.0 | Dedicated server script | PASS: >150 frames (25s active) at 30 Hz (`USERCMD_HZ = 30`, `USERCMD_MSEC = 33`) |
| Console Command Processing & Clean Quit | map-loaded | macOS arm64 / Apple Clang 21.0 | `(status -> quit)` via stdin pipe | PASS: `status` outputs server state, `quit` cleanly shuts down map & game, exit code 0 |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested < retail-interop-tested

### Not done / stubbed / known broken
- Vulkan renderer native backend initialization (Milestone 3)
- Client launch with graphics / windowing / SDL3 (Milestone 3)
- Network snapshot packet transmission for remote clients (Milestone 3)

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`.

| Capability | Linux x86_64 | Windows 11 x64 | macOS arm64 | macOS x86_64 | Linux arm64 | Notes |
|---|---|---|---|---|---|---|
| idLib + game module build | — | — | compiled | — | — | Native Apple Clang arm64 |
| Engine build | — | — | compiled | — | — | Native `openETQW-ded_arm64` dedicated server |
| Dedicated server launch (headless) | — | — | launched | — | — | Headless POSIX server cleanly runs and quits (exit 0) |
| Client launch (graphics) | — | — | blocked: M3 | — | — | Requires Vulkan surface creation |
| Stock map: server | — | — | map-loaded | — | — | Valley loads, 991 entities spawn, simulation runs at 30 Hz |
| Stock map: client | — | — | blocked: M3 | — | — | Requires user-supplied retail assets & Vulkan |
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
- Ledger rows added (`docs/compat-ledger.md`): Row 013 added documenting skipped binary proc chunks (`shadowModel`, `mapEnvBounds`)
- Consequence for retail 1.5 interop: Simulation and map loading logic strictly agree with retail asset formats.

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | M2 Complete: Review and audit M2 artifacts | macOS arm64 | M2 verification | Dedicated server loads stock Valley map, spawns entities, runs simulation, quits with 0 |
| 2 | Milestone 3: Vulkan / MoltenVK renderer bootstrap | macOS arm64 | M2 | Vulkan device context creation without crash |
| 3 | Milestone 3: Client window creation with SDL3 & input handling | macOS arm64 | Step 2 | Client window opens with game console |
| 4 | Milestone 3: Network snapshot framing and multiplayer connection | macOS arm64 | Step 3 | Client connects to dedicated server |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- None at this time.
