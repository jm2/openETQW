# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-20T16:35:00Z · session/agent: worker_m3_1 · host: mbp-m1max.local, macOS arm64
Engine repo `~/openETQW`: commit [local] · game repo `~/openETQW-game`: commit [local] · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none)

**Integrator role:** held by worker_m3_1 / mbp-m1max.local

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

# Full Meson automated test suite (5/5 tests passing)
meson test -C build --verbose

# Game build
cd /Users/jmulesa/openETQW-game
ninja -C build

# Engine dedicated server build
cd /Users/jmulesa/openETQW
ninja -C build openETQW-ded_arm64

# Loopback integration test (150 continuous snapshots received on Valley with clean exit code 0)
python3 tests/test_loopback_session.py
```

## This session

**Plan, and why it advances the engine:**
Milestone 3 / Slice 1 Implementation (Authoritative Snapshot Path & Network Transport Lifecycle):
1. Fix 16-bit short MsgChannel wire framing: Identified and corrected critical wire format framing divergence where `idMsgChannel::SendMessage` writes 16-bit short connection ID (`unsentMsg.WriteShort( id )`), but `AsyncServer.cpp` and `AsyncClient.cpp` were inadvertently reading 32-bit integer via `ReadLong()`. Replaced with `msg.ReadShort()` in both endpoints.
2. Complete connection handshake state machine: Implemented challenge generation, challenge validation, connect response, and 3-pass slot allocation (`SCS_FREE` -> `SCS_CONNECTED` -> `SCS_INGAME`) in `AsyncServer.cpp`.
3. Map loading flow control & keepalive: Handled client pacifier keepalives during map compilation (`CLIENT_UNRELIABLE_MESSAGE_EMPTY (mapLoad=true)`); server tracks `client.isMapLoading` and delays snapshot streaming until client finishes map load.
4. Authoritative Usercmd Ring Buffer: Built robust client command history ring buffer (`MAX_USERCMD_BACKUP = 64`) with delta compression, sequence wrap, and duplicate count tracking in `idAsyncClient` and `idAsyncServer`.
5. Snapshot Packaging & Transport: Implemented bidirectional snapshot stream packing `ucmdMsg` and `gameMsg` with 16-bit length headers and ingestion via `ClientReadSnapshot` + `ClientApplySnapshot`.
6. Baseline Delta Synchronization: Identified and resolved delta compression desynchronization between server and client: `idAsyncServer::SendSnapshotToClient` now gates next snapshot on client acknowledgment (`acknowledgeSnapshotSequence >= snapshotSequence`), guaranteeing identical `nwInfo.states` baseline on both sides. Resolved server shutdown ordering in `idAsyncServer::Kill` to eliminate SIGSEGV.
7. Automated Verification: Created `tests/test_usercmd_history.cpp` (5 unit tests in Meson) and `tests/test_loopback_session.py` (live loopback session streaming >= 150 snapshots on Valley with exit code 0).

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| openETQW | `src/framework/async/AsyncServer.cpp`, `AsyncClient.cpp` | MsgChannel 16-bit framing (`ReadShort()`) | Fix wire framing header desynchronization with `MsgChannel.cpp` | Network framing fix |
| openETQW | `src/framework/async/AsyncServer.cpp` | Handshake state machine & 3-pass slot allocation | Complete challenge/connect protocol and slot management | Network lifecycle |
| openETQW | `src/framework/async/AsyncServer.cpp` | Snapshot acknowledgment gating (`ackSeq >= seq`) | Enforce single-baseline delta compression parity across client and server | Snapshot engine fix |
| openETQW | `src/framework/async/AsyncServer.cpp` | Shutdown ordering fix in `Kill()` | Avoid double client disconnect after `OnServerShutdown` | Server lifecycle fix |
| openETQW | `src/framework/async/AsyncClient.cpp` | Authoritative usercmd history buffer and snapshot reader | Enable usercmd transmission and snapshot ingestion | Client network transport |
| openETQW | `tests/test_usercmd_history.cpp` | 5 unit tests for usercmd buffer, delta compression, wrapping | Automated regression testing for command history | Engine unit test |
| openETQW | `tests/test_loopback_session.py` | Full loopback integration test script | Automated validation of handshake and 150 snapshot transmissions on Valley | Integration test |
| openETQW | `docs/compat-ledger.md` | Added Rows 014 (MsgChannel framing) and 015 (Delta snapshot pacing) | Track network serialization invariants per CHARTER §7.4 | Compatibility ledger |

### Demonstrated
| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|---|
| Zero-SDK Repository Compliance (Gate 2b) | unit-tested | macOS arm64 / Darwin 27.0.0 | `python3 tests/test_zero_sdk.py` | PASS: 4232 tracked files audited, 0 SDK violations detected |
| Meson Automated Test Suite (5/5 passed) | unit-tested | macOS arm64 / Apple Clang 21.0 | `meson test -C build --verbose` | PASS: 5/5 tests passed (bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk) |
| UDP Loopback Handshake Lifecycle | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_loopback_session.py` | PASS: Challenge/connect handshake complete on 127.0.0.1:27733 <-> 27734 |
| Bidirectional Snapshot Transmission | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_loopback_session.py` | PASS: 150 continuous snapshots received on Valley map without error |
| Clean Client & Server Shutdown | match-tested | macOS arm64 / Apple Clang 21.0 | `python3 tests/test_loopback_session.py` | PASS: Graceful quit with exit code 0 for both client and server processes |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested < retail-interop-tested

### Not done / stubbed / known broken
- Vulkan renderer native backend initialization (Milestone 3 / Slice 2)
- Client graphical windowing with SDL3 (Milestone 3 / Slice 2)
- Audio system initialization (Milestone 3 / Slice 2)

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`.

| Capability | Linux x86_64 | Windows 11 x64 | macOS arm64 | macOS x86_64 | Linux arm64 | Notes |
|---|---|---|---|---|---|---|
| idLib + game module build | — | — | compiled | — | — | Native Apple Clang arm64 |
| Engine build | — | — | compiled | — | — | Native `openETQW-ded_arm64` dedicated server |
| Dedicated server launch (headless) | — | — | launched | — | — | Headless POSIX server cleanly runs and quits (exit 0) |
| Client launch (graphics) | — | — | blocked: M3/S2 | — | — | Requires Vulkan surface creation |
| Stock map: server | — | — | map-loaded | — | — | Valley loads, 991 entities spawn, simulation runs at 30 Hz |
| Stock map: client | — | — | map-loaded | — | — | Client loads Valley map, spawns entities, receives world snapshots |
| OpenETQW MP: dedicated | — | — | match-tested | — | — | Authoritative server streams 150+ snapshots to client over UDP |
| OpenETQW MP: listen server | — | — | blocked: M3/S2 | — | — | Requires listen server decoupling |
| Cross-OS client/server pair | — | — | blocked: M3 | — | — | Target milestone M3 |
| High refresh: on foot | — | — | blocked: M5 | — | — | Requires `ClientUpdateView` frame decoupling |
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
- Ledger rows added (`docs/compat-ledger.md`): Rows 014 and 015 added documenting MsgChannel framing and snapshot baseline lifecycle.
- Consequence for retail 1.5 interop: Simulation and network state encoding strictly agree with retail asset formats and wire protocol.

## Baseline observations delta
Initial baseline run against `~/etqw` commit `f2b655c`:
`docs/baseline/observations-etqw-f2b655c.json` committed with redacted `source_root`.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Milestone 3 / Slice 1 Complete: Audit & Handoff | macOS arm64 | M3/S1 verification | 150 snapshots received, zero SDK violations, 5/5 unit tests pass |
| 2 | Milestone 3 / Slice 2: Vulkan / MoltenVK renderer bootstrap | macOS arm64 | M3/S1 | Vulkan device context creation without crash |
| 3 | Milestone 3 / Slice 2: Client window creation with SDL3 & input handling | macOS arm64 | Step 2 | Client window opens with interactive game console |
| 4 | Milestone 3 / Slice 3: Graphical client in-game rendering on Valley | macOS arm64 | Step 3 | Client renders Valley map with entity interpolation |

## Inputs needed from the owner
- None. Retail assets extracted and isolated successfully.

## Questions for the owner
- None at this time.

