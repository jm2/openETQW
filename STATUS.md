# OpenETQW status

> This file lives at `~/openETQW/STATUS.md` and covers both repositories. Rewrite "This session" every session. Keep the rest current. It is the next session's starting point.

Updated: 2026-09-25 (evening) · session/agent: Claude Code integrator session 3a9edf51 · host: mbp-m1max, macOS 27.0 (Darwin 27.0.0), arm64 (Apple M1 Max)
Engine repo `~/openETQW`: `main` d15bdea (PR #14, merged 2026-09-25; `tools/ci/companion.ref` pins game f00f4d3) · game repo `~/openETQW-game`: `main` f00f4d3 (PR #10, merged 2026-09-25) · `~/etqw` reference: f2b655c992b47e0eaca7c178b0dae490abc1544e (new jmarshall commits since last session: none; upstream checked 2026-09-24)

Both histories were rewritten on 2026-09-24 (`docs/provenance.md` §7). Every commit ID changed: engine 2104090 → 84a224b and 53d0172 → fef1e53; game 285eaa1 → 24f58bf and 9b3a02b → 19a709a. Engine SHAs quoted in older docs (`CHECKPOINT_STOCK_MAPS_PHASE.md`, ledger rows, earlier STATUS text) are pre-rewrite IDs. The game's historical `tools/ci/engine.ref` pins name engine commits that no longer exist.

**Integrator role:** held by Claude Code integrator session 3a9edf51 on mbp-m1max

## Owner decisions in effect
D1 public source and binaries · D2 all RE methods · D3 hard fork · D4 macOS / Windows 11 / Arch / Fedora / Debian, inputs on request · D5 two repos: GPL-3.0-or-later engine; SDK license + MIT game · D6 Meson · D7 clean network break from retail 1.5 (2026-09-22; deferred 2026-09-24; **dropped 2026-09-25**: OpenETQW does not aim for retail 1.5 network compatibility, matching openQ4; retail 1.5 data stays required and supported)
Also from the owner, 2026-09-24: both repositories stay private; the history rewrite to purge retail content was authorized ("If retail content got accidentally uploaded rewrite the history to purge it").
From the owner, 2026-09-25: retail 1.5 network compatibility is dropped (D7); the jmarshall remediation is deferred; removing GitHub's leftover `refs/pull/1/head` is deferred until the repositories are recreated, which the owner expects to do; the SDK files in the engine's imported root commits stay in history unless that violates the GPL, which the GPL text does not appear to say (`docs/provenance.md` §6).
From the owner, 2026-09-25, answers to the open-question list (the letters and numbers are that list's; "M5 plan D1–D10" are `docs/m5-plan.md`'s decisions, not the charter's D1–D7 above):
- A3 SDK EULA §2 scope: accept the risk as openQ4 does, with two safeguards: a retail-install check, and the licence texts in every package.
- A6 SDK code in the engine build (`libidLib.a`, `DeclType.cpp`, `BitMsg.cpp` for the unit tests, the 189 staged headers and `BuildDefines.inc`): approved as tracked exceptions, on openQ4's precedent; no rewrite (`docs/decisions/0001-sdk-code-in-the-engine-build.md`).
- A7 retail-install check: "exactly how openQ4 does" (`docs/decisions/0002-sdk-eula-risk-and-retail-install-check.md`).
- B8 bot think rate: keep the retail default (about 15 Hz decisions, `bot_threadMinFrameDelay 1`); the criterion means the bot thread keeps up with the 30 Hz server.
- B9 quarry's 415 entities: the ">500 entities per map" criterion becomes "every entity in each map file is accounted for".
- B10 bot `validClasses`: do what retail 1.5 does. Retail 1.5 reads it as a bool in all three game modules (`docs/specs/retail-1.5-reference.md`), so `GetBool` is restored (game PR #11).
- C11 data: everything targets the retail data patched to 1.5: `pak000`–`pak008` (the required set) plus the edition's language paks (here `zpak_english000`–`003`); the unpatched DVD set alone is not supported.
- C12 other OSes: no Linux or Windows retail-data automation. Do what is possible without retail data (for example a Windows cross-compile); the owner tests retail data on other OSes by hand.
- M5 plan D1–D10: all decided (`docs/m5-plan.md`). D10 (asked again with context): vehicles feel "as modern/responsive as possible without breaking game physics": the seat view is on by default and grows to every seat as each is made safe, and moving vehicles further off the tick rate is to be explored.
- F16: the merged branches were deleted (13 engine, 8 game).
- Charter §11: the retail-install check is the engine's alone, as in openQ4 ("mirroring openQ4 here sounds reasonable"); the local charter now says so, and release gate 6 names engine packages too.
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
  - `ETQW_DATA_PATH` is the retail install root holding `base/`. Here it holds the retail data patched to 1.5: `pak000`–`pak004` and `zpak_english000` (their MD5s match the DVD list in the official ETQW 1.5 Linux README) plus `pak005`–`pak008` and `zpak_english001`–`003`, added on 2026-09-25 from the official Linux 1.5 client installer (byte-identical). Results recorded before 2026-09-25 14:49 come from the DVD paks alone. Without it every retail-data test exits 77 (skip).
  - The full suite uses fixed UDP ports: one suite at a time per host.
- Never run: `scripts/deps/*`, `scripts/build.*` (neither exists). A Windows x64 cross-compile with mingw-w64 (`tools/cross/windows-x86_64.txt`) is allowed (owner C12, 2026-09-25); nothing built for Linux or Windows can run here (no Wine, no Docker), and retail-data testing on those OSes is manual, by the owner.

## This session

2026-09-25. The 2026-09-24 session (PR #1/#2 merges, the history purge, the charter audit and its fixes) is in git history and those PR descriptions.

**Plan, and why it advances the engine:**
- M3: finish the single-Mac network items (movement both ways, reconnect and restart, a lossy soak, the Vulkan client as a network client and a listen host).
- M5: measure first (`docs/frame-path.md`, `docs/m5-plan.md`): frame timing, per-frame pose capture, a per-tick view-state replay; fix what the measurements find.
- Get the owner's answers to the open questions, then act on them: target the retail 1.5 data, match retail 1.5 behaviour where asked, build the release-gate safeguards.
- Keep the docs matched to the evidence (a docs audit found 54 gaps; this update works through them, and the feature PRs close the rest as they merge).

### Merged on 2026-09-25
| Repo | PR (merge) | What |
|---|---|---|
| openETQW | #3 ff39170 | STATUS refresh after the audit follow-ups |
| openETQW / game | #4 84ad1bb / game #3 7f0e275 | Owner decisions of 2026-09-25 (morning): D7 dropped, deferrals; the data found to be the unpatched DVD set |
| openETQW | #5 a6dbde0 | M3 tests: movement both ways, `reconnect-restart`, the 10-minute `lossy-session` soak, `client-network-join`/`-listen` |
| openETQW / game | #6 85879e3 / game #4 d8e5b3d | `all-stock-maps` leaves vehicle riders out of the AAS check (slipgate flake); `playerPos` prints `vehicle=` |
| openETQW | #7 c5b73a6 | `docs/frame-path.md` (one host frame, traced from code) and `docs/m5-plan.md` |
| openETQW | #8 e8aca6c | M5 step 2: `com_logFrameTiming`, adaptive pacer margin (a 60 cap ran at 58–59 fps); the ~120 fps ceiling is the 120 Hz display |
| openETQW | #9 5cc280c | `all-stock-maps` bots walk (`bot_useVehicles 0`) so the AAS check has samples |
| openETQW / game | #10 976ab58, #11 6b53d24 / game #5 1e1aaa3, #6 5d23c81 | M5 step 3 on foot: `g_logFramePoses`, `frame-poses-join`/`-listen` with known-gap tables; the viewmodel repeat after re-prediction fixed (`PresentUnlocked`) |
| openETQW / game | #12 a0e4535 / game #7 05fd612 | The presented sub-tick fraction no longer steps back within a tick |
| openETQW | #13 c6345bb | Network sessions update the GUIs every frame (none did) |
| openETQW | #14 d15bdea | M5 step 5, single player: `high-refresh-determinism` compares `SIM_VIEW` across caps in full precision; one weapon-log divergence after entering the vehicle is allowed in its measured shape (45–59 ticks from tick 92) until plan step 6 |
| game | #8 ea14841, #9 54d18d8, #10 f00f4d3 | `SIM_VIEW`: per-tick view state for the replay, with the vehicle view's last returned axis, in full precision |
| game / openETQW | #11 / this PR | Target the retail 1.5 data: the SDK script-event signatures back, `validClasses` read as retail 1.5 reads it (B10), a data-free `events.script` check; the engine tests for the 1.5 data and B9 |

### Open and pending
- **Pushed, no PR yet.** Engine `feat/retail-install-check` was adversarially reviewed (its 7 confirmed findings fixed in 9f3b4d6); game `feat/install-licenses` was not. Neither has passed the full suite yet:
  - engine `feat/retail-install-check` (ff66385, 0b3386c, 9f3b4d6): openQ4's retail-install check (A7, `fs_validateOfficialPaks`), licence texts in the engine's install set (A3), meson tests `retail-install-check` and `install-licenses`.
  - game `feat/install-licenses` (9eba2d9, 9a5f751; on 54d18d8): licence texts in the game's install set and CI artifact, plus the game README/CONTRIBUTING licensing docs.
- **Merge order:** `feat/retail-install-check` with game `feat/install-licenses`, now that the 1.5 fix has merged (the check refuses the DVD set).

### Found this session
- **The data was not 1.5.** `~/etqw_data` held the unpatched English DVD paks, so every result before 2026-09-25 14:49 ran the SDK 1.5 game code on 1.0 scripts. The 1.5 paks were added from the official Linux 1.5 client installer on the owner's drive. On them, `main` failed 20 of 28 tests: PR #1's "retail" script-event signatures had been fitted to the DVD scripts, and the harness counts the reconcile warnings as failures (details under Demonstrated and ledger 050). With game PR #11 and this engine PR, the full suite passes on the 1.5 data (28/28).
- **Retail 1.5 behaviour is now readable.** The same installer holds the retail 1.5 game modules; the macOS one keeps its symbols. `docs/specs/retail-1.5-reference.md` records what was read, with addresses: `validClasses` is a bool (B10), no vehicle seat gets per-frame views and remote entities interpolate by origin only (M5 plan D2), render frames reach tick state only through side channels (D4), the `Entity::Present` producer is active (D5), the retail CVar defaults, and the 1.5 script-event signatures.
- **M5 measurements** (Demonstrated): caps 60 and 120 held exactly after the pacer fix; the ~120 fps ceiling is the display; the network client's viewmodel repeated on a fifth of draws (fixed); no GUI updated in network sessions (fixed); the replay's view state matches at every cap except one bounded weapon-log divergence after entering a vehicle (PR #14; plan step 6 fixes it).
- **Kept outside the repositories** (gitignored `~/openETQW/.agents/`): the research notes and scripts (`research-2026-09-25/`), the docs audit, the branch-review findings, and the verified pre-purge backup bundles (`backups-2026-09-24-prepurge/`; they contain the purged bitmap and must never be pushed).

### Demonstrated
All rows: host mbp-m1max, macOS 27 arm64, Apple Clang 21, debug build, retail data at `$HOME/etqw_data`. Rows before 2026-09-25 14:49 ran on the English DVD paks alone (the SDK 1.5 game code on 1.0 scripts and maps); the rows marked **1.5 data** ran with `pak005`–`pak008` and `zpak_english001`–`003` added. Network tests use loopback (127.0.0.1) only.

| Claim | Evidence level | Host / OS / arch / compiler | Command | Result (log path) |
|---|---|---|---|---|
| Full suite, `main` binaries, DVD data (28 tests) | map-loaded (highest level any test in it reaches) | as above; engine `main` c6345bb with game ea14841, then game 54d18d8 (`vret`) | `ETQW_DATA_PATH=… meson test -C build --no-rebuild -j 1` (2026-09-25; the second run used a DVD-only view of the data) | 28/28 OK both times |
| Full suite, `main` binaries, **1.5 data** (28 tests) | map-loaded | as above; engine c6345bb (test tree of `m5/sim-view` 32f4cb1), game ea14841 | as above, 2026-09-25 14:49 | 8 OK, 20 FAIL: client-lifecycle, vulkan-shadows, all-stock-maps, turn-rate, inprocess-map-change, remote-client-map-change (+`-latency`, `-loss`), multiclient-gameplay (+`-latency`), userinfo (+`-loss`), reconnect-restart, lossy-session, client-network-join/-listen, frame-poses-join/-listen, bot-gameplay, valley-match. Cause, in the logs: `script event 'sendNetworkEvent' ( fbs ) in script/events.script does not match its native definition ( Es )` and the same for `broadcastToolTip`, which the harness counts as failures (ledger 050). high-refresh-determinism passed. Also in inprocess-map-change: `sdVehiclePart::AddSurface` "Invalid Surface" warnings, as on the DVD data (not new with 1.5) |
| Full suite, **1.5 data**, game `fix/target-1.5-data` | map-loaded | as above; engine c6345bb binaries (test tree of `m5/sim-view`), game a5af70b | as above, 2026-09-25 16:58 | 26 OK, 2 FAIL. No reconcile warnings. all-stock-maps 11/11 on the 1.5 maps, with every map entity spawned (the DVD data left 11 path corners unspawned) and ark's and volcano's new counts (897, 1332). client-lifecycle and vulkan-shadows waited out their 90 s: their "player active" marker was a debug print that only the DVD scripts make (`parachute::Idle`), and their map marker is never printed. Fixed in the tests on engine `fix/target-1.5-data` (5a5b672), not yet re-run. An earlier run of this row loaded the old module: the engine loads `game_arm64.dylib` from its own directory first, not from `fs_basepath`. |
| Full suite, **1.5 data**, both `fix/target-1.5-data` branches | map-loaded | as above; engine `fix/target-1.5-data` 9575b21 (the tests for 1.5) with game 358b2aa | as above, 2026-09-25 18:30 | **28/28 OK.** all-stock-maps 11/11 (table below), campaign-client-maps 4/4, client-lifecycle and vulkan-shadows pass with the new markers, the replay passed with the known divergence in its allowed shape (56 ticks from tick 92) |
| Replay on the **1.5 data** | map-loaded (offline `+devmap`) | as above; game ea14841 to 54d18d8 | meson `high-refresh-determinism` (five runs, 2026-09-25) | Pass, 5/5. The known weapon-log divergence after entering the vehicle appeared in 3 of 5 runs, always in its allowed shape: from tick 92, 31–37 ticks, any cap against 60 Hz (on the DVD data about 2 runs in 3, 31–40 ticks) |
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
| Render-cap replay determinism, with measured frame rates | map-loaded (offline `+devmap`, one player) | as above; branch `m5/frame-timing` | meson `high-refresh-determinism` (caps 60/120/144/240 and uncapped, `com_logFrameTiming 1`) | Same per-tick hashes in all five schedules (150 ticks), and since 2026-09-25 the same `SIM_VIEW` (tick-produced view state) on every tick, except one found leak: after entering the vehicle the logged weapon pose can differ between one schedule and the 60 Hz one from tick 92 to the end of the replay. At full precision (game PR #10, `%.9g`) it covers 55–56 of the 59 ticks 92–150 when it appears (7 of 10 runs, DVD and 1.5 data); two decimals had shown only 31–40 of them. Allowed by the test in that shape until plan step 6 (likely the entering tick's render frames writing the weapon pose; inferred). Median-interval frame rate during play: 60.0, 120.0, 122.0, 121.8 and 121.2 fps; caps 144 and 240 reported unreachable here (display-bound: each frame's work takes the full 8.3 ms). Longest frame 413–462 ms in every schedule, p99 about 62–76 ms. Before the pacer fix, cap 60 ran at 58–59 fps (17.19 ms frames). The earlier run's 387/645/647/647 'presented frames' counted the whole process lifetime |
| What the Vulkan client draws, frame by frame (on foot; render-world poses) | map-loaded (valley) | as above; game `m5/frame-poses` (`g_logFramePoses`) | meson `frame-poses-join`, `frame-poses-listen` (caps 60/120/240) | Join: camera changes on 99.8–100% of draw pairs, remote player on 50.5% (60) / 25.2% (120, 240), viewmodel on 79–82% (100% after the `PresentUnlocked` fix); every draw between ticks unlocked; the fraction rose except on 1 of 1005 pairs, where the client's prediction-clock correction after a snapshot stepped it back by 1–2 ms (since fixed: the game holds the fraction within a tick; 0 backward steps in about 4,900 draws). Listen host: camera, viewmodel and remote player 50.5% (60) / ~25–29% (120, 240), exactly once per tick; no draw between ticks is unlocked (251–743 per cap). Negative control (unlock CVars off): the client camera changes on 51% at 60. Both tests pass against their known-gap tables |
| History purge scan | inspected | as above | Integrator scans (session scratchpad): every blob in every ref hashed against the retail data (raw, LF and CRLF forms); same-named text files compared line by line; binaries/resources checked against the Doom 3 GPL, dhewm3, RBDOOM-3-BFG, DOOM-3-BFG and openQ4 trees; name/content sweep for disassembly, IDA databases, PDBs and captures | One retail file found (the splash bitmap) and purged. The 204 files in `src/compiledscript/scripts/` (202 `.script`, 2 `.include`) are SDK text media, not retail content: 195 distinct script blobs in the game history (194 in the engine history) are byte-identical to the SDK 1.5 installer's Game Text Media copies; the others are small edits and jmarshall's rework of `main.script` (`docs/provenance.md` §2.2) |

Evidence levels: inspected < configured < compiled < unit-tested < launched < map-loaded < match-tested

**Per-map results** (`all-stock-maps` on the **1.5 data** with game `fix/target-1.5-data`, 2026-09-25 18:30: a fresh dedicated server per map, 45 s bot window, one GDF and one Strogg soldier bot, bots on foot). Every map entity now spawns (the DVD data left 11 path corners unspawned on 6 maps), and ark and volcano have the 1.5 entity counts. The DVD-data table (suite 17) is in git history.

| Map | Entities spawned + not spawned / retail | Player AAS areas / reach | Vehicle AAS areas / reach | .nav nodes | Bot max per-life displacement GDF / Strogg (u) | Bot samples in a valid AAS area GDF / Strogg | Longest stuck (s) | Server tick (Hz) |
|---|---|---|---|---|---|---|---|---|
| area22 | 1064 + 0 / 1064 | 9390 / 56197 | 8352 / 40156 | 93 | 3722 / 14202 | 100% / 100% | 0.0 | 30.29 |
| ark | 897 + 0 / 897 | 7207 / 38159 | 4489 / 20305 | 72 | 7540 / 11598 | 100% / 100% | 0.0 | 30.28 |
| canyon | 849 + 0 / 849 | 10673 / 53160 | 8349 / 33140 | 182 | 12079 / 16230 | 100% / 100% | 0.52 | 30.29 |
| island | 723 + 0 / 723 | 8779 / 42963 | 6598 / 26500 | 68 | 12070 / 4364 | 100% / 100% | 0.0 | 30.3 |
| outskirts | 863 + 0 / 863 | 12203 / 64909 | 6045 / 25580 | 159 | 11297 / 5134 | 100% / 100% | 0.0 | 30.28 |
| quarry | 415 + 0 / 415 | 8848 / 45839 | 5417 / 22084 | 91 | 4844 / 12544 | 96.3% / 100% | 0.0 | 30.3 |
| refinery | 936 + 0 / 936 | 9564 / 52214 | 5489 / 25487 | 129 | 9963 / 8336 | 100% / 100% | 3.15 | 30.28 |
| salvage | 923 + 0 / 923 | 9577 / 45289 | 4364 / 16667 | 23 | 8020 / 9358 | 98.2% / 100% | 0.0 | 30.28 |
| sewer | 874 + 0 / 874 | 6089 / 30667 | 4432 / 17006 | 58 | 12597 / 11128 | 100% / 100% | 0.0 | 30.29 |
| slipgate | 1258 + 0 / 1258 | 17005 / 82193 | 7067 / 28580 | 174 | 14411 / 10914 | 95.7% / 97.8% | 0.0 | 30.29 |
| volcano | 1332 + 0 / 1332 | 7641 / 36818 | 3928 / 15838 | 14 | 4280 / 7434 | 98.5% / 100% | 0.0 | 30.31 |

Notes on the per-map results:
- AAS and .nav counts equal the retail file headers on every map (1.5 files where the patch overrides them).
- The game stays in warmup (state 1) for the whole run. No bot death was seen, though ark's Strogg bot shows 2 lives (unexplained).
- The bot AI thinks on every second game frame (ratio 0.5, about 15 Hz, the retail default, which the owner kept: B8).
- The entity check proves parsing, not spawning: every map entity counts as spawned or not spawned. On the DVD data, 11 entities on 6 maps were `Class idPathCorner not found` failures; on the 1.5 data none is.
- canyon GDF's area figure rests on 7 grounded samples of 86.
- 30 Hz bot AI (`bot_threadMinFrameDelay 0`) passed 11/11 once, on 2026-09-22 with an older binary. A later delay-0 run failed slipgate (AAS area validity 88.6% < 90%). Not rerun since.
- slipgate failed AAS area validity intermittently at the default rate (2 of 4 runs on 2026-09-25: 67.1%, 74.1%). Cause: bots riding vehicles, whose samples report the vehicle's origin and ground state. `playerPos` now prints `vehicle=` (game), and `all-stock-maps` leaves riding samples out: 11/11 PASS, slipgate 5/5. On six maps both bots spent most of the 45 s window in vehicles, so the on-foot check rested on 4–9 samples per bot, and one bad sample failed refinery (80%, 4 of 5). `all-stock-maps` now runs the bots with `bot_useVehicles 0`: 11/11 PASS with 27–84 on-foot samples per bot (96.6–100% in an AAS area).
- Bot-behavior flakes that passed on rerun are recorded in the local `.agents/claude_session_2026-09-22/FOLLOWUPS.md`: valley-match vehicle entry and salvage AAS validity. The bot-gameplay combat flake (about 1 in 4) was the 40 s window, now 90 s.

### Not done / stubbed / known broken
- **Cleanups.** The idParser skip of `jointOffset(...)` (game `src/idlib/text/Parser.cpp`, OpenETQW commit d9ba1db) matches no script or decl in either data set; remove it. `ReconcileEventsWithScript` could become warn-only (optional).
- **First runs on 1.5 to watch.** Five script callbacks that only the 1.5 scripts define (`GetSpectateEntity`, `OnDeployableDeployed`, `OnNetworkTracer`, `OnNewTask`, `OnSendingVoice`) and 48 events that only 1.5 declares now run. Retail ran the scripts as compiled-script modules; OpenETQW interprets them. Differences not examined.
- **No Linux or Windows engine.** The engine has never been configured or compiled on Linux or Windows: there is no host (owner C12: none planned), Actions minutes are out, and the engine CI legs skip while `openETQW-game` is private. Next: a Windows x64 cross-compile with mingw-w64 (`tools/cross/windows-x86_64.txt`); Linux needs a sysroot (no Docker here). Those legs are also `experimental: true` with `continue-on-error` (`.github/workflows/ci.yml:164`).
- **Windows engine link gaps (inspected).** SystemBootstrap's `idSysLocal` lacks 23 virtual methods that only the POSIX `src/sys/sys_local.cpp` implements (DLL_*, FPU_*, GetClockTicks, callstack, DebugPrintf and others). `Sys_InitInput` and `Sys_ShutdownInput` (`Common.cpp:1400`, `:1510`; defined in `src/sys/win32/win_input.cpp`) and `Sys_ShowSplashScreen` (`Common.cpp:1384`; defined in `src/sys/win32/splashscreen.cpp`) come from files that are not in the Windows source list. The Windows network layer (`SystemBootstrap.cpp`) has no loss or latency simulation. `Sys_Error` in `SystemBootstrap.cpp` opens a message box, which would stall a dedicated server's fatal error (and `retail-install-check`) on Windows.
- **No build or dependency scripts.** `scripts/deps/*` and `scripts/build.*` do not exist in either repo. The only dependency installer is `tools/ci/linux_deps.sh` (CI-only, apt).
- **No retail data discovery.** No platform probes common retail install locations. Linux and Windows `Sys_DefaultBasePath` return the current directory. `fs_basepath` works and is what the tests use.
- **The Vulkan client on a network is tested only by automation (2026-09-25).** It joined a dedicated server and hosted a listen server (`+spawnServer`; `+devmap` starts no async server), each with one headless peer, and moved under `clientMove`. Nobody has played it. `frame-poses-join` captures what it draws: remote players change once per tick (plan step 8).
- **The listen host's view is stepped** (measured by `frame-poses-listen`): camera, viewmodel and remote players change once per tick, because `idAsyncServer::RunFrame` never calls `ClientUpdateView` (M5 plan step 7).
- **High-refresh gaps (openETQW-game):**
  - Remote-entity interpolation producer compiled out: `src/game/Entity.cpp:1736` (`#if 0 // jmarshall - unlock fps`).
  - Vehicle seats draw at the tick rate: `idPlayer::IsFPSUnlock` returns false with a proxy (`src/game/Player.cpp:8794`), as retail 1.5 does (M5 plan D2). OpenETQW's vehicle sub-tick code still runs on the render frames of the tick in which a player enters, and through `GetAORView` (AoR, antilag), where it writes presentation values into tick state (plan step 6). D10 adds a seat view, on by default (steps 13–14).
  - `com_unlock_avgFrames`, `com_unlock_safetyMargin` and `com_unlock_timingMethod` are registered (`src/framework/Common.cpp:80–85`) and read nowhere (D9: remove them, plan step 12).
  - Other per-frame effects of late view-angle sampling that the audit listed (the mouse smoothing history, `game->UsercommandCallback`) were not reviewed (2026-09-24 audit).
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
  - Ledger row 013 records all of this since the 2026-09-24 docs pass (at 84a224b it did not).
- **Skipped map chunks:** ledger 013 records `shadowModel` and `mapEnvBounds` (every stock map has both), the discarded portal data and the headless render world (the 2026-09-24 docs pass; at 84a224b it named valley only).
- **Untriaged warnings.**
  - `salvage.script(338)`: "Event call on a NULL object", after `func_door_gdf_rail_1` is not found.
  - `sdVehiclePart::AddSurface`: "Invalid Surface", on the headless server (inprocess-map-change and all-stock-maps), on the DVD and the 1.5 data alike; not new with 1.5.
  - Client logs: renderprog parse and "cannot upload ARB" warnings.
- **Navigation data checks.** The AAS map CRC is never compared. The .nav loader does not bounds-check node links (`BotAI_Navigation.cpp` `LoadNodes`). The AAS loader reads packed disk structs raw, with no layout assertions (`AASFile_local.cpp`).
- **Vulkan.** Validation layers are not installed on the host. The backend has no screenshot or image capture.
- **macOS dedicated binary** links the AppKit, Metal, OpenGL and related frameworks (`ded_deps` in `meson.build`). It has never run on a host without a GPU or display.
- **Retail-install check.** Not enforced on `main`. Built on engine `feat/retail-install-check` (openQ4's check, `fs_validateOfficialPaks`; A7), not merged.
- **Leftover CMake.** Engine `src/CMakeLists.txt` and game `src/compiledscript/CMakeLists.txt` are still tracked.
- **Include letter case in the Radiant editor.** `src/tools/radiant` (Windows-only MFC, not built by Meson) has about 80 includes whose case does not match the file; `check_include_case.py` excludes it.

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`. All engine evidence is from mbp-m1max, macOS 27 arm64, Apple Clang 21, debug. "No engine build" means the engine has never been configured on that OS: there is no host, Actions minutes ran out 2026-09-24, and the engine CI legs skip while `openETQW-game` is private.

| Capability | Linux x86_64 | Windows 11 x64 | macOS arm64 | macOS x86_64 | Linux arm64 | Notes |
|---|---|---|---|---|---|---|
| idLib + game module build | compiled (hosted CI, Ubuntu 24.04: GCC 13.3, Clang 18.1.3) | compiled (hosted CI, windows-2025 runner: MSVC 19.51) | compiled (local) | compiled (hosted CI) | compiled (hosted CI, GCC cross from x64) | CI last green at game 9a929f6 (pre-rewrite ID), before the last game commits. CI resolves `GetGameAPI` after loading the module (not the arm64 cross build) and runs no unit tests. The Windows runner is not Windows 11. The Linux runner is Ubuntu, not Arch, Fedora or Debian |
| Engine build | blocked: never configured (no host, no CI) | blocked: never configured (no host, no CI); link gaps at inspection; a mingw-w64 cross-compile is next (C12) | compiled | — | — | macOS x64 and Linux ARM64 engine CI legs exist but skip. A docstring in `tools/ci/dedicated_server_smoke.py` claims a Rosetta x86_64 run; no log supports it |
| Dedicated server launch (headless) | blocked: no engine build | blocked: no engine build | map-loaded | — | — | macOS: on a Mac with GPU and display. Stdin console commands and exit 0 |
| Client launch (graphics) | blocked: no engine build | blocked: no engine build | map-loaded | — | — | SDL3 + Vulkan via MoltenVK. `+devmap` sessions, and since 2026-09-25 a network join and a listen host (`client-network-*`) |
| Asset-free unit tests | blocked: no engine build | blocked: no engine build | unit-tested | — | — | bitmsg, usercmd-delta, struct-layout, usercmd-history, zero-sdk. The codec tests are self-consistency tests |
| Stock map: server | blocked: no engine build | blocked: no engine build | map-loaded | — | — | 11 stock maps + Valley, warmup. One Valley match with forced objectives |
| Stock map: client | blocked: no engine build | blocked: no engine build | map-loaded | — | — | valley, outskirts, ark, canyon, refinery. Draw counters only |
| OpenETQW MP: dedicated | blocked: no engine build | blocked: no engine build | map-loaded | — | — | Loopback, one host. Up to 2 clients (headless, or the Vulkan client and a headless one) + 1 bot. Movement checked both ways; reconnect, client restart, map restart, 10-minute lossy session |
| OpenETQW MP: listen server | blocked: no engine build | blocked: no engine build | map-loaded | — | — | The Vulkan client with `+spawnServer` (`+devmap` starts no async server) and one headless peer (2026-09-25): the peer sees the host walk, and the host moves the peer's player. What the host draws is stepped, measured by `frame-poses-listen`: camera, viewmodel and remote players change once per tick (no `ClientUpdateView`) |
| Cross-OS client/server pair | blocked: no engine build | blocked: no engine build | blocked: needs an engine on a second OS | — | — | M3 requires one |
| High refresh: on foot | blocked: no engine build | blocked: no engine build | map-loaded | — | — | Offline replay at caps 60/120 (held exactly) and 144/240 (display-bound at ~120 here). Key turning per second (turn-rate, headless networked client). Per-frame poses are captured on foot (`frame-poses-join`/`-listen`, caps 60/120/240) |
| High refresh: vehicles | blocked: no engine build | blocked: no engine build | blocked: `IsFPSUnlock` refuses any proxy, as retail 1.5 does (D2) | — | — | Seats draw at the tick rate. OpenETQW's vehicle sub-tick code runs only on the entering tick's render frames and via AoR, where it leaks (plan step 6); the seat view, on by default, is plan steps 13–14 (D10) |
| Bots / navigation | blocked: no engine build | blocked: no engine build | map-loaded (DVD data) | — | — | Retail AAS on all 12 maps. Warmup traversal on foot (`bot_useVehicles 0` in `all-stock-maps` since 2026-09-25; with vehicles, bots rode for most of the window on six maps), soldier class only. ~15 Hz AI at the retail default |
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
  - SDK code in the engine build: registered (`docs/provenance.md` §4) and approved as exceptions on 2026-09-25 (`docs/decisions/0001-sdk-code-in-the-engine-build.md`).
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
  - The skipped-chunk list was incomplete: portal data is discarded, the client portal API is stubbed, and the headless render world is stubbed. Ledger 013 records them since the 2026-09-24 docs pass; their gameplay effects are not measured.

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
  - The server registers a client's attack and its release (asserted since 2026-09-24).
- Not tested:
  - peer-observed weapon fire (a headless peer cannot observe it);
  - a cross-OS pair (no second OS host);
  - the Vulkan client with a human at the controls.
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
- Only an offline single-player replay exists (`high-refresh-determinism`). `SIM_HASH` leaves out view angles, other players and projectiles; since PR #14 the replay also compares `SIM_VIEW` each tick at full precision (view angles, the logged view origin and weapon pose, the vehicle's pose and its view's last returned axis). Other players and projectiles are still not compared.
- In network games (client or listen host) no GUI updated: the engine never called the per-frame GUI update outside single player (0 updates in 5 s of play). Fixed 2026-09-25; `client-network-*` require it (60 updates/s at cap 60).
- What the Vulkan client draws is now captured per frame (`g_logFramePoses`, meson `frame-poses-join`/`-listen`, 2026-09-25, on foot, caps 60/120/240): on a network client the camera changes on every draw, remote players only once per tick (the `Entity::Present` producer is off) and, since the `PresentUnlocked` fix, the viewmodel changes on every draw (before it, it repeated on about a fifth of draws: after a snapshot re-ran prediction, `isNewFrame` was false and the weapon's `Present()` returned early); on a listen host camera, viewmodel and remote players all change only once per tick.
- Frame rate is now measured during play (`com_logFrameTiming`, 2026-09-25): caps 60 and 120 are held exactly (16.67 and 8.33 ms frames) after a pacer fix; caps 144 and 240 run at about 120 because presentation waits for this host's 120 Hz display. Every replay schedule has one ~410–540 ms stall right after spawn: first-use material and render-program loading (with debug dumps), not pacing.
- The 2026-09-24 turn-rate fix removed one render-rate dependence of authoritative state (key turning).
- `docs/frame-path.md` (2026-09-25) traces one host frame in each mode, from code; its pacing section (§7.1, §7.5, §7.6) also has the frame rates measured with `com_logFrameTiming`. `docs/m5-plan.md` orders the remaining work in 17 steps; the owner made its ten decisions (D1–D10) on 2026-09-25; D10's seat view is on by default.
- Open (details and file:line in `docs/frame-path.md` §9):
  - the `Entity::Present` producer (compiled out by jmarshall; SDK code);
  - the vehicle proxy gate;
  - the listen host: no `ClientUpdateView`, and it samples view input only once per tick, so its camera turns at 30 Hz;
  - OpenETQW's vehicle-view interpolation writes presentation values into state the next tick reads (`unlockedDraw` stays set until the next tick or snapshot);
  - tick frames are drawn at the tick boundary (a 30 Hz judder at any cap);
  - a mixed-cap live session and a cap change during play;
  - `com_unlockFPS` is ignored, and `com_unlock_avgFrames`/`safetyMargin`/`timingMethod` do nothing (D9: remove them).

**M7 (public release): not met.** None of the six release-gate items is cleared yet. Item 2's SDK exceptions are approved (A6), and the item clears for the engine binaries when the licence install (`feat/retail-install-check` 0b3386c) merges (next section). Also missing:
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
2. **SDK-licensed code linked into engine binaries: not cleared; exceptions approved by the owner (2026-09-25).**
   - The engine links the game repo's SDK idLib (`libidLib.a`, 72 objects) and compiles the staged SDK `DeclType.cpp`, 189 staged SDK headers and `BuildDefines.inc`; three unit tests also compile the SDK `BitMsg.cpp`. `docs/provenance.md` §4 lists them (E1–E4).
   - The owner approved them as tracked exceptions on openQ4's precedent (A6; `docs/decisions/0001-sdk-code-in-the-engine-build.md`). Condition: every engine package carries the SDK EULA as well as the GPL (built on `feat/retail-install-check` 0b3386c, not merged). The item is cleared for the engine binaries once that merges.
   - Still open before publishing: engine history holds SDK files through jmarshall's root commits (the owner left them in place); SDK EULA §5 (the EULA must accompany each copy) still has to be handled for those commits (`docs/provenance.md` §6).
3. **`docs/provenance.md` coverage: not cleared.**
   - At 84a224b, `docs/provenance-manifest.json` classified 5,275 imported files; after the 2026-09-24 edit (splash entry dropped) it classifies 5,274.
   - About 103 engine files and 29 game files added after the split are not in it.
   - At 84a224b / 24f58bf, the manifest and the game's `tools/ci/spdx_allowlist.txt` label the 204 files in `src/compiledscript/scripts/` (202 `.script`, 2 `.include`) and 13 compiledscript runtime files (217 in all) Reconstruction_original/MIT. They are SDK files (`LicenseRef-ETQW-SDK`).
   - The 2026-09-24 docs pass corrected this. The manifest relabels 217 compiledscript files as SDK, drops the purged splash entry and fixes the two minizip entries. The allowlist is updated to match (merged in PR #2).
   - jmarshall files are labeled GPL-3.0-or-later although their license is the open question.
   - Other defects are listed under M0.
4. **LICENSE and README explain the split and the retail-data requirement: not cleared.**
   - At the audit, the engine `readme.md` was jmarshall's two lines, the game repo had no README, and the engine `LICENSE` did not mention the split. The game `LICENSE` explains the SDK EULA/MIT split and the retail requirement.
   - The 2026-09-24 docs pass drafted an engine `readme.md` and a game `README.md` covering these points. They are merged in PR #2 and await owner review.
5. **No retail binaries, PDBs, raw RE output, captures or assets in either repo's history: not cleared.**
   - The one retail file found, the splash bitmap, was purged from both repos' history on 2026-09-24 (`main` and `wip/stock-maps-m2-checkpoint` force-pushed). On 2026-09-25 the 1.5 patch paks (4,531 files) were scanned the same way: the only matches are the SDK script text in `src/compiledscript/scripts/` (`docs/provenance.md` §7).
   - GitHub still keeps `refs/pull/1/head` in each repo, pointing at the old commits (engine 53d0172, game 9b3a02b), whose history still contains it. Removing them needs GitHub Support or recreating the repositories. The owner deferred this on 2026-09-25 and expects to recreate the repositories; check that the old refs are gone when that happens.
   - Local backup bundles of the pre-purge repos (verified) are kept outside the repositories, in the gitignored `~/openETQW/.agents/backups-2026-09-24-prepurge/`. They hold the purged bitmap and must never be pushed.
   - No `.exe`, `.pdb`, `.pk4` or capture files and no raw decompiler output were found.
   - Third-party binary blobs remain: `src/openal/lib/openal32.lib`, `eaxguid.lib` (2 copies) and zlib contrib `.obj` files. They are not retail content, but they are unreviewed restricted dependencies (§11).
6. **SDK EULA with the game repo and every package; retail-install check enforced; nothing sold: not cleared.**
   - The owner accepted the EULA §2 risk as openQ4 does (A3, 2026-09-25), with two safeguards (`docs/decisions/0002-sdk-eula-risk-and-retail-install-check.md`):
     - the retail-install check, openQ4's design (A7): built on engine `feat/retail-install-check`, not merged;
     - the licence texts in every package: the engine and game install sets carry them on `feat/retail-install-check` (0b3386c, checked by meson `install-licenses`) and game `feat/install-licenses`, not merged.
   - The EULA ships verbatim in the game repo (`legal/EULA SDK ETQW.rtf`, `EULA_SDK_ETQW.txt`).
   - No packages exist. Nothing is sold.

## Compatibility record
- **Module ABI:** `GAME_API_VERSION` is 8 (1008 with `SD_PUBLIC_TOOLS`), in openETQW-game `src/game/Game.h:467–471`. The engine checks it at load (`Common.cpp:1286`). The previous STATUS said 1, which was wrong. No interface change on 2026-09-24 or 2026-09-25. `struct-layout` asserts `gameImport_t` 192 bytes and `gameExport_t` 24 bytes on arm64 (unit-tested).
- **Serialized/asset format changes:** none. The usercmd codec moved to `UsercmdCodec.cpp` unchanged (2026-09-24).
- **Wire protocol id and OpenETQW-only deviations:** major `0x4F45` ('OE' = 20293), minor 5 (`src/framework/async/AsyncNetwork.h`):
  - minor 1: 16-bit channel framing, acked baselines;
  - minor 2: reliable game init;
  - minor 3: user info;
  - minor 4: streamed snapshots with APPLYSNAPSHOT;
  - minor 5: client prediction in usercmd messages.

  Retail 1.5 peers are refused at connect with "Server uses protocol 20293.5". Since PR #2 (2026-09-24), the protocol is checked first. That assumes a retail connect starts with the protocol field, as Doom 3's does; no real retail client has been tried.
- **Retail 1.5 network compatibility:** dropped (owner decision, 2026-09-25, matching openQ4). It is not claimed. Retail 1.5 data is required and supported.
- **Protocol id coverage:** game-side codecs (snapshots, user info, network events) are outside the protocol id. The game's `ServerAllowClient` has no version check, so minor bumps are kept by convention only.
- **Ledger rows (`docs/compat-ledger.md`) this work bears on:**
  - 050: added in PR #1 (2026-09-24). Its "retail" signatures were the 1.0 DVD scripts'; retail 1.5 uses the SDK ones, which game `fix/target-1.5-data` restores (pending).
  - 047: `validClasses` read as retail 1.5 reads it, `GetBool` (B10; pending the same branch). 030: resolved by the 1.5 data. 040: B8. 052 (new): the data target is retail 1.5.
  - 009: dedicated pacing (2026-09-24: no busy-spin; the client margin is adaptive since 2026-09-25).
  - 036: clean break. The rejection is now checked before the challenge. D7 was deferred on 2026-09-24 and dropped on 2026-09-25.
  - 014: the 16-bit wire parity claim is withdrawn; the usercmd tests now compile the engine codec and are labeled self-consistency tests.
  - 051 (new): turn rate, connect check order, seat-swap finding.
  - 008: aim/camera; relevant to the turn-rate fix.
  - 028 and 029: the audit found overclaims (stale pacing description; vehicle smoothing that never ran). The 029 finding was corrected on 2026-09-25: the vehicle sub-tick code runs on the render frames of the tick in which a player enters a vehicle, and through `GetAORView` (AoR, antilag), where it writes into tick state (plan step 6).
- **Protocol version bumped (incompatible wire change)?** No. No change on 2026-09-24 or 2026-09-25 alters bytes on the wire. A connect message holding only the protocol field now gets a print instead of silence; that is server behavior, not format.

## Baseline observations delta
None. `~/etqw` is still at f2b655c (upstream checked 2026-09-24). `docs/baseline/observations-etqw-f2b655c.json` is unchanged.

## Next steps (7 or fewer, ordered)
| # | Step | Host / OS needed | Depends on | Acceptance test |
|---|---|---|---|---|
| 1 | Release gate 6 safeguards: engine `feat/retail-install-check` with game `feat/install-licenses` (rebased on 54d18d8); readme, CONTRIBUTING, ledger 035/052 and this file updated when they merge | macOS arm64 | none (the 1.5 fix has merged) | Full suite on 1.5 plus `retail-install-check` and `install-licenses` |
| 2 | M5 in the order of `docs/m5-plan.md`: the D1 test change (the reach bounds become reports), then steps 6–15 and 17 | macOS arm64 | none | Each step's test (see the plan) |
| 3 | Windows x64 cross-compile with mingw-w64 (C12); then a Linux sysroot | macOS arm64 (cross) | none | The engine configures and compiles for Windows x64 |
| 4 | Provenance and license records: post-split files and openQ4 donor commits in the manifest, SPDX lines, the minizip relabel (release gate 3) | any | none | Manifest covers every tracked file; game `check_spdx.py` passes |
| 5 | M4 gameplay-driven tests: classes, damage and kills, deployable delivery, stopwatch and campaign transitions | macOS arm64 | none | New meson tests pass on the 1.5 data |

## Inputs needed from the owner
- Optional: `Loaded pk4 ... with checksum` lines from other-language editions (`pak000`–`pak004`), so the retail-install check can accept them.
- For information (A7/C11 as built): `pak005`–`pak008` are required, where openQ4 leaves its patch paks optional, so an install without the 1.5 patch (including the Steam data package examined, which has no `pak005`–`pak008`) is refused until the patch paks are added; the retail dedicated-server download's server-only paks are refused too.
- Manual retail-data runs on Linux and Windows, once builds exist (C12).

## Questions for the owner
- None open.
