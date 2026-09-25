# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-25 · session/agent: Claude Code integrator session 3a9edf51 · host: mbp-m1max, macOS 27.0 (Darwin 27.0.0), arm64 (Apple M1 Max)
Engine repo `~/openETQW`: `main` c1e6424 (PR #2, the audit follow-ups, merged 2026-09-24) · game repo `~/openETQW-game`: `main` b5c3345 (PR #2) · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none; upstream checked 2026-09-24)

Both histories were rewritten on 2026-09-24 (see "This session"). Every commit ID changed: engine 2104090 → 84a224b and 53d0172 → fef1e53; game 285eaa1 → 24f58bf and 9b3a02b → 19a709a. Engine SHAs quoted in older docs (`CHECKPOINT_STOCK_MAPS_PHASE.md`, ledger rows, earlier STATUS text) are pre-rewrite IDs. The game's historical `tools/ci/engine.ref` pins name engine commits that no longer exist.

**Integrator role:** held by Claude Code integrator session 3a9edf51 on mbp-m1max

## Owner decisions in effect
D1 public source and binaries · D2 all RE methods · D3 hard fork · D4 macOS / Windows 11 / Arch / Fedora / Debian, inputs on request · D5 two repos: GPL-3.0-or-later engine; SDK license + MIT game · D6 Meson · D7 clean network break from retail 1.5 (2026-09-22; deferred 2026-09-24; **dropped 2026-09-25**: OpenETQW does not aim for retail 1.5 network compatibility, matching openQ4; retail 1.5 data stays required and supported)
Also from the owner, 2026-09-24: both repositories stay private; the history rewrite to purge retail content was authorized ("If retail content got accidentally uploaded rewrite the history to purge it").
From the owner, 2026-09-25: retail 1.5 network compatibility is dropped (D7); the jmarshall remediation is deferred; removing GitHub's leftover `refs/pull/1/head` is deferred until the repositories are recreated, which the owner expects to do; the SDK files in the engine's imported root commits stay in history unless that violates the GPL, which the GPL text does not appear to say (`docs/provenance.md` §6).
Environment corrections vs. SESSION_BRIEF: the only host is macOS arm64; there is no Linux or Windows host. GitHub Actions minutes for private repositories ran out on 2026-09-24, so hosted CI does not run. Local checks, local independent reviews and CodeRabbit (about one review an hour) replace it (`CONTRIBUTING.md`). Merge gates are a convention; the free plan has no branch protection.

## Toolchain and commands (this session)
- Host / OS / arch: mbp-m1max, macOS 27.0 (build 26A428), Darwin 27.0.0, arm64, Apple M1 Max
- Compiler, Meson, Ninja, and other tool versions: Apple clang 21.0.0 (clang-2100.3.34.2, target arm64-apple-darwin27.0.0, through ccache) · Meson 1.12.0 · Ninja 1.13.2 · Python 3.14.7 · Git 2.55.0. Both repos build as `buildtype=debug` (-O0). No optimized engine build exists on any platform.
- Setup / compile / test commands, exactly as run (from `~/openETQW`; the game repo is the sibling `../openETQW-game`):
```sh
python3 tools/build/stage_game.py . ../openETQW-game .tmp/gamelibs_stage
meson setup build -Dbuild_engine=true -Dbuild_dedicated=true -Dbuild_tests=true
ninja -C ../openETQW-game/build
ninja -C build
ETQW_DATA_PATH=$HOME/etqw_data meson test -C build -j 1
```
- Notes on those commands:
  - The engine `meson setup` options are the ones recorded in `build/meson-private/cmd_line.txt`. `build_engine` and `build_dedicated` default to `false`, so a plain `meson setup build` builds no engine binary.
  - The game build directory was configured with plain `meson setup build` (default options, per its `meson-private/cmd_line.txt`).
  - Engine configure fails unless `../openETQW-game/build/libidLib.a` already exists, so a fresh checkout builds the game first. Configure also runs `stage_game.py` itself.
  - `ETQW_DATA_PATH` is the retail install root holding `base/pak000.pk4`. Here it holds the English DVD data (`pak000`–`pak004`, `zpak_english000`; their MD5s match the DVD list in the official ETQW 1.5 Linux README) without the 1.5 patch paks (`pak005`–`pak008`, `zpak_english001`–`003`). Without it every retail-data test exits 77 (skip).
  - The full suite uses fixed UDP ports: one suite at a time per host.
- Never run: any Linux or Windows command (native or cross), `scripts/deps/*`, `scripts/build.*` (neither exists).

## This session

**Plan, and why it advances the engine:**
- Merge the stock-maps and network work (PR #1 in both repos) after the final reviews.
- Purge retail content from both repositories' history, with the owner's authorization.
- Audit every charter milestone against code, logs and CI: 5 assessors, each checked by a challenger.
- Fix the small, verifiable code defects the audit found:
  - frame-rate-dependent key and controller turning, whose angles reached the server (M5, fairness);
  - the dedicated server's busy-wait (§7.3; ledger 009);
  - the connect check order that could hide the protocol message from retail peers (§7.4);
  - tests that passed without checking anything.
- Explain the seat-swap report.
- Rewrite this file to match the evidence. The previous STATUS.md overclaimed dedicated MP, the listen server, the release gate, build scripts and the Linux/Windows cells.

The turn-rate fix removes a render-rate dependence from authoritative commands. The unit tests now exercise the engine's own usercmd codec.

### Changed
| Repo | Files / symbols | What | Why | Reuse / provenance |
|---|---|---|---|---|
| both | whole history (git filter-repo); `main` and `wip/stock-maps-m2-checkpoint` force-pushed with `--force-with-lease` | `src/sys/win32/rc/res/etqw_splash.bmp` removed from every commit | It is ET:QW cover art with the game logo: retail content, not in the SDK installer or the Doom 3 lineage | Owner-authorized 2026-09-24. `companion.ref` pins were remapped through the engine history |
| openETQW | `src/sys/win32/rc/etqw.rc` (PR #2) | No longer references the bitmap | The file is gone. `Sys_ShowSplashScreen` shows nothing without it | — |
| openETQW | `src/framework/UsercmdGen.cpp`: `TurnTimeScale`, `AdjustAngles`, `MakeCurrent`; new `+left/-left/+right/-right` (PR #2) | Key and controller turning are scaled by the real time since the previous `MakeCurrent`, clamped to 4 ticks. The timestamp is file-scope, so the class keeps its 32-bit PDB layout. After the game callback only the angles the game changed are copied back (the old per-call `ANGLE2SHORT` round trip truncated small steps) | `GetDirectUsercmd` also runs on every render-only frame (late view-angle sampling). Each call added a whole tick of turning, so the turn rate grew with the frame rate, and those angles went into the commands the server simulates | New code. The old per-call `com_timescale` correction is gone; it is not needed |
| openETQW-game | `src/game/gamesys/SysCmds.cpp` `Cmd_PlayerPos_f` (PR #2) | `playerPos` prints `yaw=`, the yaw of the player's latest usercmd | Lets the server-side test read turning | Game repo, SDK-licensed file (debug output only) |
| openETQW-game | `tools/ci/engine.ref` (PR #2) | Pins engine 84a224b | The old pin named a pre-rewrite commit | — |
| openETQW | `src/framework/Common.cpp` `idCommonLocal::Frame` (PR #2) | A dedicated binary (server, or headless client) sleeps the whole remaining frame time. Graphical clients keep the ~1.5 ms spin tail for exact high-refresh pacing | Charter §7.3: a dedicated server never busy-spins. Ledger row 009 claimed this before it was true | New code |
| openETQW | `src/framework/async/AsyncServer.cpp` `ProcessConnectMessage` (PR #2) | The protocol field is read and checked first, before the length, challenge and checksum checks | A peer whose challenge this server never issued, or whose connect message is shorter than the OpenETQW layout, got "Bad challenge" or silence instead of the protocol message | Doom 3 GPL order (protocol first) |
| openETQW | new `src/framework/async/UsercmdCodec.cpp/.h`; `AsyncNetwork.cpp/.h`; `tools/build/meson_sources.py`; `tests/UserCmdDeltaTest.cpp`, `tests/test_usercmd_history.cpp`, `tests/meson.build` (PR #2) | `Net_WriteUserCmdDelta`, `Net_ReadUserCmdDelta`, `Net_DuplicateUsercmd` and `MAX_USERCMD_DUPLICATION` moved out of `AsyncNetwork.cpp` unchanged; `idAsyncNetwork` forwards to them. `usercmd-delta` and `usercmd-history` now compile and call the engine's code | The tests used pasted copies. The delta test's copy of `DuplicateUsercmd` lacked the duplication clamp | Moved engine code, unchanged |
| openETQW | `tests/test_multiclient_gameplay.py`, `test_valley_match.py`, `test_bot_gameplay.py`, `etqw_harness.py`, new `tests/test_turn_rate.py`, `tests/test_protocol_clean_break.py`, `tests/meson.build` (PR #2) | The weapon step asserts that the server registers the attack (`atk_cmd=1`, `atk: 1`) and the release (`atk_cmd=0`). The three gameplay tests scan all process output with `etqw_harness.scan_failures`. valley-match asserts the `SWAP_SEAT` line. `test_bot_gameplay.py`: the combat window grows from 40 s to 90 s (the two bots met after 13.5–42.8 s in 8 runs, past 40 s in about one run in four); the loop still stops as soon as every criterion is met. New meson test `turn-rate`. Protocol test adds two retail connects: one with a challenge the server never issued, one holding only the protocol field | The weapon step printed PASSED without checking anything. Three gameplay tests never scanned logs. Seat-swap report (see Demonstrated). bot-gameplay failed in suite 18 when the bots had not met within 40 s | New test code |
| openETQW | 20 `#include`s in `src/renderer`, `src/renderer/megatexture`, `src/decllib`, `src/bse`, `src/framework` (and the unbuilt megatexture compiler); new `tools/ci/check_include_case.py` (compiler search order from `build/compile_commands.json`), run in CI script smoke and listed as a local gate | The includes now match the files' letter case (`megatexture.h`, `declManager.h`, …). Four `src/decllib` files now name the GPL `../framework/Decl*.h` they have always compiled against | They built only because macOS ignores case: a Linux build would have failed, or compiled four decl files against the SDK `decllib/decl*.h` instead. Found during the docs pass and the code review | New check |
| both | PR #1, merged earlier today (now 84a224b / 24f58bf) | Retail script event signatures (`sendNetworkEvent`, `broadcastToolTip`, `setPlayerPickupRequestTime`, objective `OnWriteInitialReliableMessages`). Five handler/definition mismatches (`teamSuicideIfNotNearAction`, `disableAASAreaInLocation`, `enable/disableRouteGroup`, client `bindToJoint`). Game `tools/ci/check_event_handlers.py`. Reconcile warning as a harness failure marker (ledger 050). Command capture, posix_net latency queue, terminal restore, macOS bundle validation, smoke script fixes. CI pushes build only `main` | Stock-maps phase and network review follow-ups | See the PR descriptions and ledger 050 |
| openETQW | `STATUS.md` (PR #2) | Rewritten against the charter audit | It was stale and overclaimed | — |
| both | Docs pass (merged in PR #2, fact-checked by two independent checkers): engine `readme.md`, game `README.md` (new), `CONTRIBUTING.md` and PR templates in both repos, `docs/compat-ledger.md` (header, rows 003, 008, 009, 013, 014, 026, 027, 028, 029, 033, 034, 035, 036, 045, 050, new 051), `docs/provenance.md`, `docs/provenance-manifest.json`, `walkthrough.md`, `CHECKPOINT_STOCK_MAPS_PHASE.md`, game `tools/ci/spdx_allowlist.txt` and `check_spdx.py` wording | README text on the repo split, the SDK license and the retail-data requirement. Merge gates without hosted CI. History-rewrite notes. Provenance relabels, import method and the full SDK exception registry (Release gates 2 and 3) | Audit findings: release gates 3 and 4; stale process docs | — |

### Demonstrated
All rows: host mbp-m1max, macOS 27 arm64, Apple Clang 21, debug build, retail data at `$HOME/etqw_data`: the English DVD paks without the 1.5 patch paks (found 2026-09-25; every result below comes from the SDK 1.5 game code on that unpatched data). Network tests use loopback (127.0.0.1) only.

| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|
| Full retail-data suite, fix branch (22 tests) | map-loaded (highest level any test in it reaches) | as above; the `fix/audit-followups` trees, merged as engine c1e6424 and game b5c3345 | `ETQW_DATA_PATH=… meson test -C build -j 1` (suites 18 and 19) | Suite 18: 21/22 OK; bot-gameplay failed (the bots had not met within the 40 s window). A/B of the old and new dedicated pacing at 40 s: 1/6 and 2/6 failures, server tick rate 30.3 Hz either way, so not a pacing regression. With the 90 s window: 8/8 solo runs passed (first combat after 13.5–42.8 s). Suite 19 (before the code review's fixes): 22/22 OK. Suite 20, final tree: 22/22 OK (2026-09-24, 0 script-event mismatch warnings; `build/meson-logs/testlog.txt` holds this run) |
| Full retail-data suite, before the fix branch (21 tests) | map-loaded (highest level any test in it reaches) | as above; binaries built 12:26 from the uncommitted tree later committed as fef1e53 / 19a709a. Trees are identical to `main` 84a224b / 24f58bf. This is inferred from file times; the binaries carry no commit stamp | `ETQW_DATA_PATH=… meson test -C build -j 1` (suite 17, 2026-09-24 12:27–12:46 EDT) | 21/21 OK. `build/meson-logs/testlog.txt` has been overwritten by suite 18 |
| Key turning no longer depends on frame rate | map-loaded (quarry; dedicated server + one headless client) | as above, fix tree | `tests/test_turn_rate.py` (meson `turn-rate`), `in_yawspeed 20`, `in_anglespeedkey 1` | 19.6 °/s at a 30 fps cap, 19.3 °/s at 240 fps. With the old code restored: 122 °/s at 240 fps (the test failed, as intended). After the review found the per-call `ANGLE2SHORT` truncation (direction-dependent rate at small steps) and it was fixed: 19.9–20.1 °/s for +left at 30 and 240 fps and +right at 240 fps, 10% tolerance, three runs. Under meson the test writes its logs to `build/meson-logs/turn-rate/`. Controller turning uses the same scale but is untested |
| Dedicated server no longer busy-waits | launched | as above, fix tree | Integrator's scratch script (not in the repo): process CPU time from `ps` over 20 s, idle server on quarry, no clients | 1.5% of one core before, 1.1% after |
| Test added: a retail connect without a valid challenge, or holding only the protocol field, must get the protocol message | launched | as above, fix tree | `tests/test_protocol_clean_break.py` (meson `protocol-clean-break`) | OK in suite 18. The peer is simulated: raw UDP with retail majors, not a real retail 1.5 client |
| Unit tests exercise the engine's usercmd codec | unit-tested | as above, fix tree | meson `usercmd-delta`, `usercmd-history` | OK in suite 18. These are self-consistency tests (the engine's encoder against its own decoder), not fidelity tests |
| Seat swap works; the bot moves itself back | map-loaded (Valley) | as above | Integrator's instrumentation (scratch); `valley-match` now asserts `SWAP_SEAT` | The swap reports seat 1 in the same frame. The bot's own vehicle AI (`becomeDriver`: a bot alone in a gunner seat takes the empty driver seat) then returns it to seat 0. Not a seat bug. `bot_pause` cannot be changed in multiplayer |
| All 11 stock maps on the dedicated server | map-loaded (warmup only) | as above, suite 17 | meson `all-stock-maps` | 11/11 PASS in suites 17 and 18. The table below has suite 17's values; its JSON was overwritten by later runs (`build/meson-logs/all-stock-maps.json` always holds the latest run) |
| OpenETQW network play | map-loaded | as above, suite 17 | meson `multiclient-gameplay` (+`-latency`), `remote-client-map-change` (+`-latency`, `-loss`), `userinfo` (+`-loss`) | All OK. Dedicated server plus two headless clients (the dedicated binary with `+connect`). One-way movement (suite 17; both ways since 2026-09-25, next rows): 404.93 u seen by the peer (398.17 u at 80 ms + 0–40 ms jitter, 5% loss). Map change at 0%, 10% loss + 60 ms + 0–40 ms, and 20% loss |
| Full retail-data suite, M3 tests branch (26 tests) | map-loaded (highest level any test in it reaches) | as above; `test/m3-network` before the review fixes, engine binaries of `main` c1e6424 | `ETQW_DATA_PATH=… meson test -C build --no-rebuild -j 1` (2026-09-25) | 25/26 OK. `all-stock-maps` failed on slipgate (Strogg AAS area validity 67.1% < 90%); three reruns of slipgate alone: pass, pass, fail (74.1%). The Strogg samples outside an area were a bot riding a vehicle (hundreds of units per sample, up to z 3600, 'grounded'); fixed by the `vehicle=` field and leaving riding samples out (game PR #4, engine `fix/aas-validity-vehicles`). Not caused by this branch (tests and docs only) |
| Each client sees the other walk; reconnect, client restart, map restart | map-loaded (valley) | as above; the reviewed `test/m3-network` tests | meson `multiclient-gameplay` (+`-latency`), `reconnect-restart` | Both ways: 414.68 u and 320.76 u seen by the peer, ending within 0.04 u of the server (same at 80 + 0–40 ms, 5% loss). `reconnect`: new game init, same slot, both see each other. Killed client: the new process got slot 2 while dead slot 0 timed out (`net_serverClientTimeout` 20), 2 slots in use. `serverMapRestart`: both respawned, snapshots continued, positions within 0.03 u. Logs: the session scratchpad (not kept) |
| 10 minutes at 2% loss and 20–60 ms delay without a disconnect | map-loaded (valley) | as above; the reviewed `lossy-session` | meson `lossy-session` (suite `soak`) | 33 checks: each client's copy within 0.04 u of the server; 33 walks, each seen by the other client moving 170–305 u; longest snapshot gap 0.24 s; no drop; 2 slots in game at the end; all exit 0. An earlier version (walks not checked) also passed, twice |
| The Vulkan client on a network | map-loaded (valley) | as above, MoltenVK | meson `client-network-join`, `client-network-listen` | Join: each client saw the other walk (423.14 u, 320.76 u). Listen host (`+spawnServer`): the peer saw the host walk 423.14 u; the host moved the peer's player 320.76 u from its usercmds. 1,248–1,428 frames presented with world draws; no Vulkan error markers. What the client draws of remote players is not checked |
| Vulkan client loads and renders stock maps | map-loaded | as above, MoltenVK, suite 17 | meson `client-lifecycle`, `campaign-client-maps`, `vulkan-shadows` | 5 maps (valley, outskirts, ark, canyon, refinery) in `+devmap` sessions. Evidence is backend draw counters only; 0 skinned and 0 GUI draws |
| Render-cap replay determinism | map-loaded (offline `+devmap`, one player) | as above, suite 17 | meson `high-refresh-determinism` | Same hashes at caps 60/120/144/240, but presented frames were 387/645/647/647, so caps above ~120 did not take effect here (cause unverified) |
| History purge scan | inspected | as above | Integrator scans (session scratchpad): every blob in every ref hashed against the retail data (raw, LF and CRLF forms); same-named text files compared line by line; binaries/resources checked against the Doom 3 GPL, dhewm3, RBDOOM-3-BFG, DOOM-3-BFG and openQ4 trees; name/content sweep for disassembly, IDA databases, PDBs and captures | One retail file found (the splash bitmap) and purged. The 204 files in `src/compiledscript/scripts/` (202 `.script`, 2 `.include`) are SDK text media, not retail content: 195 distinct script blobs in the game history (194 in the engine history) are byte-identical to the SDK 1.5 installer's Game Text Media copies; the others are small edits and jmarshall's rework of `main.script` (`docs/provenance.md` §2.2) |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested

**Per-map results** (suite 17, `all-stock-maps`: a fresh dedicated server per map, 45 s bot window, one GDF and one Strogg soldier bot):

| Map | Entities spawned + not spawned / retail | Player AAS areas / reach | Vehicle AAS areas / reach | .nav nodes | Bot max per-life displacement GDF / Strogg (u) | Bot samples in a valid AAS area GDF / Strogg | Longest stuck (s) | Server tick (Hz) |
|---|---|---|---|---|---|---|---|---|
| area22 | 1063 + 1 / 1064 | 9390 / 56197 | 8352 / 40156 | 83 | 3729 / 13899 | 100% / 100% | 0.0 | 30.26 |
| ark | 898 + 1 / 899 | 7339 / 39233 | 4506 / 20237 | 71 | 8016 / 10610 | 98.0% / 100% | 0.0 | 30.25 |
| canyon | 849 + 0 / 849 | 10640 / 53226 | 8349 / 33140 | 182 | 7700 / 10890 | 100% / 100% | 0.51 | 30.25 |
| island | 723 + 0 / 723 | 8779 / 42963 | 6598 / 26500 | 50 | 9364 / 4336 | 100% / 98.7% | 0.0 | 30.29 |
| outskirts | 863 + 0 / 863 | 12203 / 64909 | 6045 / 25580 | 159 | 12431 / 5766 | 100% / 100% | 0.0 | 30.25 |
| quarry | 414 + 1 / 415 | 8878 / 45901 | 5419 / 22337 | 91 | 2380 / 11177 | 100% / 95.3% | 0.53 | 30.27 |
| refinery | 935 + 1 / 936 | 9564 / 52214 | 5489 / 25487 | 129 | 11380 / 5540 | 100% / 100% | 0.52 | 30.24 |
| salvage | 923 + 0 / 923 | 9577 / 45289 | 4364 / 16667 | 23 | 6595 / 11031 | 100% / 100% | 0.0 | 30.3 |
| sewer | 874 + 0 / 874 | 6089 / 30667 | 4432 / 17006 | 58 | 15005 / 10989 | 100% / 100% | 0.52 | 30.27 |
| slipgate | 1252 + 6 / 1258 | 17005 / 82193 | 7067 / 28580 | 174 | 12137 / 14203 | 100% / 100% | 0.53 | 30.26 |
| volcano | 1335 + 1 / 1336 | 7641 / 36818 | 3928 / 15838 | none (no retail .nav) | 4330 / 7727 | 100% / 100% | 0.0 | 30.29 |

Notes on the per-map results:
- AAS and .nav counts equal the retail file headers on every map.
- The game stays in warmup (state 1) for the whole run. No bot death was seen, though ark's Strogg bot shows 2 lives (unexplained).
- The bot AI thinks on every second game frame (ratio 0.5, about 15 Hz, the retail default).
- The entity check proves parsing, not spawning: every map entity counts as spawned or not spawned. Each nonzero "not spawned" count is a `Class idPathCorner not found` failure: 11 entities on 6 maps.
- canyon GDF's area figure rests on 7 grounded samples of 86.
- 30 Hz bot AI (`bot_threadMinFrameDelay 0`) passed 11/11 once, on 2026-09-22 with an older binary. A later delay-0 run failed slipgate (AAS area validity 88.6% < 90%). Not rerun since.
- slipgate failed AAS area validity intermittently at the default rate (2 of 4 runs on 2026-09-25: 67.1%, 74.1%). Cause: bots riding vehicles, whose samples report the vehicle's origin and ground state. `playerPos` now prints `vehicle=` (game), and `all-stock-maps` leaves riding samples out: 11/11 PASS, slipgate 5/5. On six maps both bots spend most of the 45 s window in vehicles, so the on-foot check there rests on 4–9 samples per bot.
- Bot-behavior flakes that passed on rerun are recorded in the local `.agents/claude_session_2026-09-22/FOLLOWUPS.md`: valley-match vehicle entry and salvage AAS validity. The bot-gameplay combat flake (about 1 in 4) was the 40 s window, now 90 s.

### Not done / stubbed / known broken
- **No Linux or Windows engine.** The engine has never been configured or compiled on Linux or Windows: there is no host, Actions minutes are out, and the engine CI legs skip while `openETQW-game` is private. Those legs are also `experimental: true` with `continue-on-error` (`.github/workflows/ci.yml:164`).
- **Windows engine link gaps (inspected).** SystemBootstrap's `idSysLocal` lacks 23 virtual methods that only the POSIX `src/sys/sys_local.cpp` implements (DLL_*, FPU_*, GetClockTicks, callstack, DebugPrintf and others). `Sys_InitInput` and `Sys_ShutdownInput` (`Common.cpp:1298`, `:1408`; defined in `src/sys/win32/win_input.cpp`) and `Sys_ShowSplashScreen` (`Common.cpp:1282`; defined in `src/sys/win32/splashscreen.cpp`) come from files that are not in the Windows source list. The Windows network layer (`SystemBootstrap.cpp`) has no loss or latency simulation.
- **No build or dependency scripts.** `scripts/deps/*` and `scripts/build.*` do not exist in either repo. The only dependency installer is `tools/ci/linux_deps.sh` (CI-only, apt).
- **No retail data discovery.** No platform probes common retail install locations. Linux and Windows `Sys_DefaultBasePath` return the current directory. `fs_basepath` works and is what the tests use.
- **The Vulkan client on a network is tested only by automation (2026-09-25).** It joined a dedicated server and hosted a listen server (`+spawnServer`; `+devmap` starts no async server), each with one headless peer, and moved under `clientMove`. Nobody has played it, and nothing checks what it draws of remote players.
- **The listen host's view is expected to be stepped.** `idAsyncServer::RunFrame` never calls `ClientUpdateView`, so a listen host would draw remote players at the tick rate (M5).
- **High-refresh gaps (openETQW-game):**
  - Remote-entity interpolation producer compiled out: `src/game/Entity.cpp:1736` (`#if 0 // jmarshall - unlock fps`).
  - The vehicle sub-tick path is unreachable: `idPlayer::IsFPSUnlock` returns false with a proxy (`src/game/Player.cpp:8794`).
  - `com_unlock_avgFrames`, `com_unlock_safetyMargin` and `com_unlock_timingMethod` are registered (`src/framework/Common.cpp:78–83`) and read nowhere.
  - `docs/frame-path.md` does not exist.
  - Other per-frame effects of late view-angle sampling that the audit listed (the mouse smoothing history, `game->UsercommandCallback`) were not reviewed today.
- **Silent engine no-ops** in `src/framework/async/NetworkSystem.cpp`:
  - `ServerGetClientNetId` (:245);
  - `ServerKickClient` (:255), empty, so kicks the game requests do nothing;
  - VOIP (:350);
  - demos (`CanPlayDemo`, :377);
  - sound test (:391);
  - `HTTPEnable` (:414);
  - `Repeater*` setters (:420 on).
- **Render-world stubs.**
  - Client portal API: `src/renderer/RenderWorld_portals.cpp`. `NumPortals` returns 0 and `SetPortalState` does nothing. Interarea portal data is discarded at load.
  - Headless render world: `src/sys/stub/headless_stubs.cpp`. One area, and traces never hit.
  - The dedicated server therefore gets no render-model combat hits (players while dead, prone or leaning use render-model combat models). `RadiusPush` ignores walls there. `RadiusDamage` uses collision traces and is not affected.
  - Game PVS falls back to "all areas visible".
  - Ledger row 013 records all of this as of today's docs pass (at 84a224b it did not).
- **Skipped map chunks:** ledger 013 records `shadowModel` and `mapEnvBounds` (every stock map has both), the discarded portal data and the headless render world (today's docs pass; at 84a224b it named valley only).
- **Untriaged warnings.**
  - `salvage.script(338)`: "Event call on a NULL object", after `func_door_gdf_rail_1` is not found.
  - `sdVehiclePart::AddSurface`: "Invalid Surface", on the headless server.
  - Client logs: renderprog parse and "cannot upload ARB" warnings.
- **Navigation data checks.** The AAS map CRC is never compared. The .nav loader does not bounds-check node links (`BotAI_Navigation.cpp` `LoadNodes`). The AAS loader reads packed disk structs raw, with no layout assertions (`AASFile_local.cpp`).
- **Vulkan.** Validation layers are not installed on the host. The backend has no screenshot or image capture.
- **macOS dedicated binary** links the AppKit, Metal, OpenGL and related frameworks (`ded_deps` in `meson.build`). It has never run on a host without a GPU or display.
- **Retail-install check.** The retail-install requirement (EULA §2 mitigation) is not enforced anywhere.
- **Leftover CMake.** Engine `src/CMakeLists.txt` and game `src/compiledscript/CMakeLists.txt` are still tracked.
- **Include letter case in the Radiant editor.** `src/tools/radiant` (Windows-only MFC, not built by Meson) has about 80 includes whose case does not match the file; `check_include_case.py` excludes it.

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`. All engine evidence is from mbp-m1max, macOS 27 arm64, Apple Clang 21, debug. "No engine build" means the engine has never been configured on that OS: there is no host, Actions minutes ran out 2026-09-24, and the engine CI legs skip while `openETQW-game` is private.

| Capability | Linux x86_64 | Windows 11 x64 | macOS arm64 | macOS x86_64 | Linux arm64 | Notes |
|---|---|---|---|---|---|---|
| idLib + game module build | compiled (hosted CI, Ubuntu 24.04: GCC 13.3, Clang 18.1.3) | compiled (hosted CI, windows-2025 runner: MSVC 19.51) | compiled (local) | compiled (hosted CI) | compiled (hosted CI, GCC cross from x64) | CI last green at game 9a929f6 (pre-rewrite ID), before the last game commits. CI resolves `GetGameAPI` after loading the module (not the arm64 cross build) and runs no unit tests. The Windows runner is not Windows 11. The Linux runner is Ubuntu, not Arch, Fedora or Debian |
| Engine build | blocked: never configured (no host, no CI) | blocked: never configured (no host, no CI); link gaps at inspection | compiled | — | — | macOS x64 and Linux ARM64 engine CI legs exist but skip. A docstring in `tools/ci/dedicated_server_smoke.py` claims a Rosetta x86_64 run; no log supports it |
| Dedicated server launch (headless) | blocked: no engine build | blocked: no engine build | map-loaded | — | — | macOS: on a Mac with GPU and display. Stdin console commands and exit 0 |
| Client launch (graphics) | blocked: no engine build | blocked: no engine build | map-loaded | — | — | SDL3 + Vulkan via MoltenVK. `+devmap` sessions, and since 2026-09-25 a network join and a listen host (`client-network-*`) |
| Asset-free unit tests | blocked: no engine build | blocked: no engine build | unit-tested | — | — | bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk. The codec tests are self-consistency tests |
| Stock map: server | blocked: no engine build | blocked: no engine build | map-loaded | — | — | 11 stock maps + Valley, warmup. One Valley match with forced objectives |
| Stock map: client | blocked: no engine build | blocked: no engine build | map-loaded | — | — | valley, outskirts, ark, canyon, refinery. Draw counters only |
| OpenETQW MP: dedicated | blocked: no engine build | blocked: no engine build | map-loaded | — | — | Loopback, one host. Up to 2 clients (headless, or the Vulkan client and a headless one) + 1 bot. Movement checked both ways; reconnect, client restart, map restart, 10-minute lossy session |
| OpenETQW MP: listen server | blocked: no engine build | blocked: no engine build | map-loaded | — | — | The Vulkan client with `+spawnServer` (`+devmap` starts no async server) and one headless peer (2026-09-25): the peer sees the host walk, and the host moves the peer's player. What the host draws of remote players is not checked and is expected to be stepped (no `ClientUpdateView`) |
| Cross-OS client/server pair | blocked: no engine build | blocked: no engine build | blocked: needs an engine on a second OS | — | — | M3 requires one |
| High refresh: on foot | blocked: no engine build | blocked: no engine build | map-loaded | — | — | Offline replay at 60 and ~120 fps. Key turning per second (turn-rate, headless networked client). The Vulkan client has joined a server (`client-network-join`), but nothing captures per-frame poses |
| High refresh: vehicles | blocked: no engine build | blocked: no engine build | blocked: path unreachable (`IsFPSUnlock` proxy gate) | — | — | The sub-tick vehicle code added 2026-09-21 has never run |
| Bots / navigation | blocked: no engine build | blocked: no engine build | map-loaded | — | — | Retail AAS on all 12 maps. Warmup traversal, soldier class only, much of it in vehicles (on six maps most of the 45 s window). ~15 Hz AI at the retail default |
| Retail 1.5 peer rejected at connect (clean break, D7) | blocked: no engine build | blocked: no engine build | launched | — | — | Simulated peer (id/Splash Damage majors 1, 2, 10–14, 22) gets "Server uses protocol 20293.5". No real retail client tried |

### Build and dependency scripts verified
| Script | Arch | Fedora | Debian | macOS | Windows 11 |
|---|---|---|---|---|---|
| `scripts/deps/*` | — | — | — | — | — |
| `scripts/build.*` | — | — | — | — | — |

Neither script exists; no `scripts/` directory exists in either repo's history. The previous "verified" cells were wrong.

## Milestones (CHARTER §8)
Charter milestone numbers. `CHECKPOINT_STOCK_MAPS_PHASE.md` and older notes use the stock-maps phase's own M2–M5, which are different. Each track is reported separately; no percentages.

**M0 (bootstrap and baseline): partial.**
- Met on macOS arm64:
  - The import from `~/etqw` f2b655c is recorded in each repository's first own commit (engine 9043b91, game 9600d7a) and in `docs/provenance.md`, with history preserved.
  - Engine HEAD holds no SDK files (`zero-sdk` test).
  - The baseline JSON is committed.
  - The asset-free unit suite passes.
  - Engine and game compile.
- Gaps:
  - The engine was never configured on Linux or Windows; M0 needs the other two OSes to at least configure.
  - At 84a224b, `docs/provenance.md` does not state the import method. The docs pass (PR #2) adds it.
  - SDK code in the engine build is under-registered (see Release gate 2), and there are no decision records (`docs/decisions/` does not exist).
  - Provenance labels are wrong at 84a224b for the compiledscript tree (SDK, labeled MIT), and two minizip entries are stale (`.cpp` → `.c`). The docs pass (PR #2) corrects both (Release gate 3).
  - openQ4-derived files (`URLPolicy.h`, `macosx_common.h`, `macosx_sys.h`, `macosx_local.h`, `macosx_compat.mm`, `macosx_misc.mm`) have no donor commit and no SPDX line.
  - At 84a224b the codec unit tests were not labeled as self-consistency tests; ledger rows 003/014 and this file now label them.
  - The charter asks for a `LicenseRef-ETQW-SDK` SPDX line on SDK-derived files; none has one (the game's `spdx_allowlist.txt` stands in).

**M1 (native execution): macOS only.**
- macOS arm64:
  - Client, dedicated server and game module compile.
  - The dedicated server mounts retail data through `fs_basepath`, initializes decls and the game, loads maps, accepts stdin console commands and exits 0.
- Linux x86_64 and Windows 11: not met. The engine was never configured or compiled; only the game module compiled in hosted CI.
- Gaps: the Windows link gaps and missing scripts under "Not done", retail data discovery, and the `false` defaults of `build_engine` and `build_dedicated`.

**M2 (stock map): server side met on macOS arm64; client side partial.**
- Server: all 11 stock maps and Valley load on the dedicated server with map scripts and retail AAS, and bots move (map-loaded, warmup only).
- Client: the Vulkan client renders 5 maps, backed by draw counters only.
- Gaps:
  - In the graphical client, spawn and movement are asserted only by the network tests (`client-network-*`, 2026-09-25), from the server's and the peer's side; collision is not asserted.
  - The skipped-chunk list was incomplete: portal data is discarded, the client portal API is stubbed, and the headless render world is stubbed. Ledger 013 records them as of today's docs pass; their gameplay effects are not measured.

**M3 (OpenETQW multiplayer): partial; one Mac, loopback; the cross-OS pair is missing.**
- Shown (map-loaded):
  - A dedicated server and two headless clients connect, spawn on opposite teams and replicate positions.
  - Movement replicates in both directions: each client sees the other walk, and its copy ends within 2 units of the server's position once the walker is at rest (2026-09-25).
  - Reconnect: a client's `reconnect` gets a new game init, it spawns again and both clients see each other; a killed client replaced by a new process gets a new slot while the dead one times out (`net_serverClientTimeout`); `serverMapRestart` with both clients connected respawns both, and they see each other (`reconnect-restart`, 2026-09-25).
  - A 10-minute session at 2% loss each way and 20–60 ms delay (40 ± 20 ms, order kept) completed without a disconnect: 33 replication checks within 0.04 units, 33 walks each seen by the other client, longest snapshot gap 0.24 s (`lossy-session`, final version, 2026-09-25; the evidence table has the details).
  - The Vulkan client joined a dedicated server, with a headless peer: each client saw the other walk (`client-network-join`, 2026-09-25). It also hosted a listen server: the peer saw the host walk, and the host moved the peer's player from its usercmds (`client-network-listen`). The client presented frames and drew the world in both. Neither involved a human at the controls; movement came from `clientMove`.
  - Remote clients follow map changes, also at 10% loss with 60 ms + 0–40 ms latency and at 20% loss.
  - The two-client test also passes at 80 ms + 0–40 ms latency with 5% loss.
  - User info reaches both clients.
  - The server registers a client's attack and its release; this assertion was added today.
- Not tested:
  - peer-observed weapon fire (a headless peer cannot observe it);
  - a cross-OS pair (no second OS host);
  - the Vulkan client with a human at the controls, and the listen host's view of remote players over time (M5).
- Loss and jitter simulation is POSIX-only and keeps packet order, so reordering is never tested.

**M4 (full match): mostly not met.**
- One started Valley objective match, bots only (one soldier per team):
  - objectives forced with `completeObjective`;
  - a Titan entered with `enterVehicle` (driver seat), and the seat swap asserted;
  - a deployable requested with `g_skipDeployChecks 1`, never delivered ("DEPLOYABLE GONE" at the forced match end);
  - the attackers' win reaches game review.
- Not shown: other classes, damage or kills, deployable delivery, a defender or time-limit win, stopwatch or campaign transitions, and a started match on any other map.
- Bots / navigation row: partial. Retail AAS is on all 12 maps and bots traverse in warmup. Bots play no objectives.

**M5 (high-refresh multiplayer): not met.**
- Only an offline single-player replay exists (`high-refresh-determinism`). Caps above ~120 did not take effect on this host. Its hash leaves out view angles, other players and projectiles.
- Today's turn-rate fix removed one render-rate dependence of authoritative state (key turning).
- Open:
  - the `Entity::Present` producer;
  - the vehicle proxy gate;
  - `ClientUpdateView` on the listen host;
  - a mixed-cap live session;
  - per-frame pose capture;
  - `docs/frame-path.md`;
  - the dead `com_unlock_*` CVars;
  - a cap change during play on the current code.

**M7 (public release): not met.** None of the six release-gate items is cleared (next section). Also missing:
- packages and packaging scripts;
- a release workflow;
- `docs/platform-matrix.md`;
- retail data discovery.

The macOS bundle check expects the module in `Contents/Frameworks`, but `FindDLL` never looks there.

## Release gate (CHARTER §11)
Both repositories are private and have no releases.
1. **jmarshall license: unresolved, blocking.**
   - `~/etqw` and upstream (still f2b655c) have no license.
   - A local byte-level audit (2026-09-22, gitignored `.agents/claude_session_2026-09-22/JMARSHALL_PROVENANCE_AUDIT.md`) found about 135 compiled engine files (about 54.7k lines) that are jmarshall-authored, and 57 game files that are SDK files with his edits. Both histories start from his commits.
   - Some engine sources carry Hex-Rays/IDA-style markers. They are his reconstructed code (the `retail_RE` provenance tier), not raw decompiler output, and fall under the same open question.
   - Remediation deferred by the owner (2026-09-25). This gate stays blocking until it is resolved.
2. **SDK-licensed code linked into engine binaries: not cleared.**
   - At 84a224b, `docs/provenance.md` registers only 7 staged headers (`Game.h`, `NetworkSystem.h`, `UsercmdGen.h`, `BuildDefines.h`, `BuildDefines.inc`, `BotAI_Debug.h`, `Bot_Common.h`).
   - Not registered at 84a224b:
     - the game repo's SDK idLib (`libidLib.a`, 72 objects), linked into the client, the dedicated server and the unit tests;
     - the staged SDK `src/decllib/DeclType.cpp`, compiled into both;
     - the rest of the staged SDK files the build reads: 192 distinct files (193 path spellings in the ninja deps, because `declManager.h` was also included as `DeclManager.h`);
     - the staged SDK `BitMsg.cpp`, compiled into three unit tests.
   - Today's docs pass rewrites the registry (`docs/provenance.md` §4) to list all of this (merged in PR #2).
   - There are no decision records and no owner approvals.
   - Engine HEAD contains no SDK files, but engine history does, through jmarshall's root commits. The owner left that history in place (2026-09-25). Before publishing, SDK EULA §5 (the EULA must accompany each copy) still has to be handled for those commits (`docs/provenance.md` §6).
3. **`docs/provenance.md` coverage: not cleared.**
   - At 84a224b, `docs/provenance-manifest.json` classified 5,275 imported files; after today's edit (splash entry dropped) it classifies 5,274.
   - About 103 engine files and 29 game files added after the split are not in it.
   - At 84a224b / 24f58bf, the manifest and the game's `tools/ci/spdx_allowlist.txt` label the 204 files in `src/compiledscript/scripts/` (202 `.script`, 2 `.include`) and 13 compiledscript runtime files (217 in all) Reconstruction_original/MIT. They are SDK files (`LicenseRef-ETQW-SDK`).
   - Today's docs pass is correcting this. The manifest relabels 217 compiledscript files as SDK, drops the purged splash entry and fixes the two minizip entries. The allowlist is updated to match (merged in PR #2).
   - jmarshall files are labeled GPL-3.0-or-later although their license is the open question.
   - Other defects are listed under M0.
4. **LICENSE and README explain the split and the retail-data requirement: not cleared.**
   - At the audit, the engine `readme.md` was jmarshall's two lines, the game repo had no README, and the engine `LICENSE` did not mention the split. The game `LICENSE` explains the SDK EULA/MIT split and the retail requirement.
   - Today's docs pass drafted an engine `readme.md` and a game `README.md` covering these points. They are merged in PR #2 and await owner review.
5. **No retail binaries, PDBs, raw RE output, captures or assets in either repo's history: not cleared.**
   - The one retail file found, the splash bitmap, was purged from both repos' history on 2026-09-24 (`main` and `wip/stock-maps-m2-checkpoint` force-pushed).
   - GitHub still keeps `refs/pull/1/head` in each repo, pointing at the old commits (engine 53d0172, game 9b3a02b), whose history still contains it. Removing them needs GitHub Support or recreating the repositories. The owner deferred this on 2026-09-25 and expects to recreate the repositories; check that the old refs are gone when that happens.
   - Local backup bundles of the pre-purge repos exist outside the repos (session scratchpad).
   - No `.exe`, `.pdb`, `.pk4` or capture files and no raw decompiler output were found.
   - Third-party binary blobs remain: `src/openal/lib/openal32.lib`, `eaxguid.lib` (2 copies) and zlib contrib `.obj` files. They are not retail content, but they are unreviewed restricted dependencies (§11).
6. **SDK EULA with the game repo and every package; retail-install check enforced; nothing sold: not cleared.**
   - The EULA ships verbatim in the game repo (`legal/EULA SDK ETQW.rtf`, `EULA_SDK_ETQW.txt`).
   - No packages exist.
   - The game's `meson install` and the past CI artifacts carry no EULA.
   - The retail-install requirement is not enforced.
   - Nothing is sold.

## Compatibility record
- **Module ABI:** `GAME_API_VERSION` is 8 (1008 with `SD_PUBLIC_TOOLS`), in openETQW-game `src/game/Game.h:467–471`. The engine checks it at load (`Common.cpp:1184`). The previous STATUS said 1, which was wrong. No interface change today. `struct-layout` asserts `gameImport_t` 192 bytes and `gameExport_t` 24 bytes on arm64 (unit-tested).
- **Serialized/asset format changes:** none today. The usercmd codec moved to `UsercmdCodec.cpp` unchanged.
- **Wire protocol id and OpenETQW-only deviations:** major `0x4F45` ('OE' = 20293), minor 5 (`src/framework/async/AsyncNetwork.h`):
  - minor 1: 16-bit channel framing, acked baselines;
  - minor 2: reliable game init;
  - minor 3: user info;
  - minor 4: streamed snapshots with APPLYSNAPSHOT;
  - minor 5: client prediction in usercmd messages.

  Retail 1.5 peers are refused at connect with "Server uses protocol 20293.5". As of today (PR #2), the protocol is checked first. That assumes a retail connect starts with the protocol field, as Doom 3's does; no real retail client has been tried.
- **Retail 1.5 network compatibility:** dropped (owner decision, 2026-09-25, matching openQ4). It is not claimed. Retail 1.5 data is required and supported.
- **Protocol id coverage:** game-side codecs (snapshots, user info, network events) are outside the protocol id. The game's `ServerAllowClient` has no version check, so minor bumps are kept by convention only.
- **Ledger rows (`docs/compat-ledger.md`) this work bears on** (updated in today's docs pass):
  - 050: added in PR #1, merged today.
  - 009: dedicated pacing. Its "no busy-spin" claim was wrong until today's change; the row now describes the `std::chrono` sleep, the client spin tail and the 1.5% → 1.1% measurement.
  - 036: clean break. The rejection is now checked before the challenge. D7 was deferred on 2026-09-24 and dropped on 2026-09-25.
  - 014: the 16-bit wire parity claim is withdrawn; the usercmd tests now compile the engine codec and are labeled self-consistency tests.
  - 051 (new): turn rate, connect check order, seat-swap finding.
  - 008: aim/camera; relevant to the turn-rate fix.
  - 028 and 029: the audit found overclaims (stale pacing description; the vehicle smoothing is unreachable).
- **Protocol version bumped (incompatible wire change)?** No. Today's changes alter no bytes on the wire. A connect message holding only the protocol field now gets a print instead of silence; that is server behavior, not format.

## Baseline observations delta
None. `~/etqw` is still at f2b655c (upstream checked 2026-09-24). `docs/baseline/observations-etqw-f2b655c.json` is unchanged.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Provenance and license records (today's corrections are merged: compiledscript relabel, import method, full exception registry). Write decision records in `docs/decisions/` for each SDK exception. Add post-split files and openQ4 donor commits to the manifest; add SPDX lines | any | owner approval for exceptions | Manifest covers every tracked file; game `python3 tools/ci/check_spdx.py` passes; the registry matches ninja deps |
| 2 | Linux x86_64 and Windows 11 engine builds. Close the Windows link gaps; run the M1 dedicated checklist with retail data | Linux x86_64 and Windows 11 hosts, or a private runner | owner input | Engine compiles on both; dedicated server loads a stock map, takes console commands, exits 0 |
| 3 | M5 groundwork: `docs/frame-path.md`; the `Entity::Present` producer; listen-host `ClientUpdateView`; a decision on the vehicle proxy gate; implement or remove the dead `com_unlock_*` CVars; per-frame pose capture | macOS arm64 | none | Frame-path doc exists; a capture test shows remote-entity poses changing on every rendered frame |
| 4 | Retail-install check that still allows the asset-free CI smoke (release gate 6) | any | owner decision on the design | Engine refuses to run without a full retail install; `tools/ci/dedicated_server_smoke.py` still passes |
| 5 | M4 gameplay-driven tests: classes, damage and kills, deployable delivery, stopwatch and campaign transitions | macOS arm64 | none | New meson tests pass on the tested maps |

## Inputs needed from the owner
- A Linux x86_64 host (Arch, Fedora or Debian) and a Windows 11 x64 host, each with the retail 1.5 paks. A private or self-hosted runner, or restored Actions minutes, would also do. Needed for M0 (configure on the other OSes), M1 and the cross-OS pair in M3.
- The ET:QW 1.5 patch data: `pak005`–`pak008.pk4` and `zpak_english001`–`003.pk4` (from the official 1.5 patch or a full 1.5 client install). The local data is the unpatched DVD set, so the SDK 1.5 game code has only ever run on pre-1.5 scripts and assets; some of the retail script mismatches the game now reconciles may come from that.
- Optional: a retail ET:QW 1.5 client binary (Linux or Windows), used locally as a behavior reference. It would confirm what a real retail client sees at connect; the current test uses a simulated peer.

## Questions for the owner
- SDK EULA scope (EULA §2): does it cover a module built for OpenETQW?
- SDK code in engine binaries: approve `libidLib.a`, `DeclType.cpp` and the staged headers as exceptions, or replace them (for example with a Doom 3 GPL idLib)?
- Retail-install check: which design? It must still allow the asset-free CI smoke.
- 30 Hz criterion: should bots think at a true 30 Hz (`bot_threadMinFrameDelay 0`)? The retail default is about 15 Hz.
- quarry: accept 415 retail entities against the ">500 entities per map" criterion?
- Ledger 047: keep reading bot `validClasses` as a bitmask, or restore the retail `GetBool`?
