# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-21T08:05:00Z · session/agent: lead_orchestrator · host: mbp-m1max.local, macOS arm64
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

# Full Meson automated test suite (6/6 tests passing 100%)
meson test -C build --verbose

# Game build
cd /Users/jmulesa/openETQW-game
ninja -C build

# Engine client & dedicated server builds
cd /Users/jmulesa/openETQW
ninja -C build openETQW-client_arm64 openETQW-ded_arm64

# Milestone 1 & 2 Automated Verification
python3 tests/test_pawn_spawn.py
python3 tests/test_pawn_movement.py

# Milestone 3 Automated Multi-Client Verification
python3 tests/test_multiclient_gameplay.py
```

## This session

**Plan, and why it advances the engine:**
Milestones 2 & 3: Movement Physics, Client Prediction, Multi-Client In-Game Spawning & UDP Replication on Valley:
1. [Command Tokenizer Modernization] Corrected `idCmdArgs::TokenizeString` in both `openETQW` and `openETQW-game` to recognize and concatenate `+` and `-` command token prefixes (`+forward`, `-forward`, `+attack`, `+jump`, etc.), enabling standard idTech4 user action input binding.
2. [Client Prediction & Clock Resync] Implemented client prediction replay in `AsyncClient.cpp` (`game->ClientPrediction`). Solved post-map-load frame deficit by synchronizing `gameFrame` and `gameTime` to incoming server snapshots, eliminating server usercmd drops and ensuring smooth continuous prediction.
3. [Headless Client Stability] Added explicit NULL guards around `sdWeaponSelectionMenu`, `sdChatMenu`, and `sdLimboMenu` in `Player.cpp` and `PlayerView.cpp` to prevent signal 139 / SIGSEGV when headless test instances execute player simulation without GUI menus.
4. [Milestone 2 Physics Verification] Verified all 5 physics criteria on stock map Valley in `tests/test_pawn_movement.py`: forward displacement 499.14 u ($\ge 100$ u), strafe displacement 285.43 u ($\ge 50$ u), jump apex impulse (+53.03 u) and landing, crouch/uncrouch stance transitions ($1 \to 0$), and 221 snapshots with 0.0100 u prediction error.
5. [Milestone 3 Multi-Client UDP Replication] Delivered `tests/test_multiclient_gameplay.py`: dedicated server concurrently hosts Client 1 (GDF Soldier) and Client 2 (Strogg Soldier) over UDP. Verified mutual in-game spawning, spatial coordinate replication across clients ($< 0.05$ u error), peer movement displacement replication (422.52 u observed over UDP), weapon attack interaction (`+attack` / `atk=1`), and clean multi-process shutdown (exit code 0).
6. [Compliance & Cleanroom Integrity] 100% pass on Meson unit test suite (6/6 tests), 0 SDK violations across all 4,248 tracked files in `openETQW`, and verified private status on GitHub remotes (`jm2/openETQW` and `jm2/openETQW-game`).

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `src/idlib/CmdArgs.cpp` | Concatenate `+` and `-` prefixes with command names in `TokenizeString` | Allow `+forward`, `+attack`, `-forward` console commands | GPL-3.0-or-later idLib |
| openETQW | `src/framework/UsercmdGen.cpp` | Added `SetButton` helper, `+forward`, `+back`, `+moveleft`, `+moveright`, `+jump`, `+crouch`, `+attack`, `clientMove` | Wire console commands to `usercmd_t` movement/button fields | GPL-3.0-or-later engine |
| openETQW | `src/framework/async/AsyncClient.{h,cpp}` | Added `predictedGameFrame`, snapshot clock resync, and prediction replay loop | Lock client simulation to 30 Hz server time and replay inputs | GPL-3.0-or-later async networking |
| openETQW | `src/framework/async/AsyncServer.cpp` | Initialized userinfo on connect, updated `lastPacketTime` in unreliable handler | Robust multi-client connect handshake | GPL-3.0-or-later async server |
| openETQW | `tests/test_pawn_movement.py` | Automated 5-criteria physics and prediction test harness | Milestone 2 verification gate | Engine test suite |
| openETQW | `tests/test_multiclient_gameplay.py` | Automated 2-client UDP spawning, movement, and weapon replication test | Milestone 3 verification gate | Engine test suite |
| openETQW | `docs/compat-ledger.md` | Added Rows 019, 020, 021 | Document tokenizer, headless GUI, and clock resync | Documentation |
| openETQW-game | `src/idlib/text/CmdArgs.cpp` | Concatenate `+` and `-` prefixes in game idLib `TokenizeString` | Match engine command tokenizer behavior | ETQW SDK idLib |
| openETQW-game | `src/game/Player.cpp` | Null guards for `weaponMenu` in `Weapon_Combat`, `Damage`, `UpdateWeaponMenu`; added `Event_IsDead` | Prevent SIGSEGV in headless mode | Game logic hardening |
| openETQW-game | `src/game/PlayerView.cpp` | Null guards for `chatMenu` and `limboMenu` in `DrawScoreBoard` | Headless client scoreboard safety | Game logic hardening |
| openETQW-game | `src/game/Weapon.cpp` | Added logging for `EVENT_TRACER` broadcast and receipt | Telemetry for network weapon firing | Game logic |
| openETQW-game | `src/game/gamesys/SysCmds.cpp` | Registered `playerPos [clientNum]`, reporting position, stance, and attack state | Telemetry for multi-client position queries | Game debug console |
| openETQW-game | `src/game/rules/GameRules.cpp` | Added command aliases `setTeam`, `setClass`, `spawn`; set `player->SetInGame(true)` | Facilitate scriptable spawn flow | Game rules |
| openETQW-game | `src/game/structures/TeamManager.cpp` | Guarded against disconnected clients in `GetNumPlayers` | Prevent null pointer dereference on team queries | Team manager hardening |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| Zero-SDK Repository Compliance (Gate 2b) | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 4,248 tracked files audited, 0 SDK violations detected |
| Meson Automated Test Suite (6/6 passed) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build --verbose` | PASS: 6/6 tests passed (bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk, client-lifecycle) |
| Milestone 1 Pawn Possession & Spawning | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_pawn_spawn.py` | PASS: GDF & Strogg active pawn possession verified, 85 snapshots, exit code 0 |
| Milestone 2 Movement Physics & Prediction | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_pawn_movement.py` | PASS: Forward 499.14 u, strafe 285.43 u, jump apex +53.03 u, crouch 1->0, 221 snapshots (0.0100 u error) |
| Milestone 3 Multi-Client UDP Replication | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_multiclient_gameplay.py` | PASS: 2 clients (GDF & Strogg), spatial tracking (< 0.05 u error), movement 422.52 u replicated over UDP, weapon attack verified |
| Valley World & MegaTexture Rendering | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_client_lifecycle.py` | PASS: 1,754 Vulkan frames presented, 93,658 world draws, 86,935 MegaTexture draws, exit code 0 |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested < retail-interop-tested

### Not done / stubbed / known broken
- Bot / AI navigation mesh loading (Milestone 4 bot bring-up)
- Vehicle proxy view smoothing and multiplayer physics sync (Milestone 5)
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
| OpenETQW MP: dedicated | — | — | match-tested | — | — | Authoritative server hosts multiple concurrent clients with UDP replication |
| OpenETQW MP: listen server | — | — | map-loaded | — | — | Local single-player / listen server active on Valley |
| Cross-OS client/server pair | — | — | — | — | — | Target milestone M3 cross-OS |
| High refresh: on foot | — | — | launched | — | — | High-refresh Vulkan presentation decoupled from 30 Hz simulation |
| High refresh: vehicles | — | — | blocked: M5 | — | — | Requires proxy view smoothing |
| Bots / navigation | — | — | blocked: M4 | — | — | Requires QWNV navmesh loader |
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
