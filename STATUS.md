<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# OpenETQW status

What works today, the milestones and the release gate, for both repositories (engine and
game module). Each claim carries its evidence level (`CONTRIBUTING.md` § Records). The
project's long session record is kept outside this repository; this file is the public
summary. `docs/platform-matrix.md` has the latest full suite on each platform.

Last updated: 2026-10-29 (engine `main` after #349, game `main` after game #62).

## Capability matrix
Each cell holds an evidence level, `—` if not yet tried, or `blocked: <reason>`. Linux x86_64 engine evidence is from the Linux x64 host (Fedora 44, GCC 16.2.1, RX 6400 / RADV, debug, since 2026-09-26); Windows 11 x64 from the Windows host (Windows 11 26H2, VS 2026 MSVC 19.51, RX 6400 / Adrenalin 26.8.1, debug and debugoptimized, since 2026-09-28); Windows 11 arm64 from the Windows arm64 host (Windows 11 25H2 build 26200, VS 2026 MSVC 19.51.36256 for ARM64, Qualcomm Snapdragon X with Adreno X1-85 (driver 31.0.128.0), debugoptimized, since 2026-10-03); Linux arm64 from the Linux arm64 host (Fedora 44 Asahi, kernel 7.1.6 with 16k pages, GCC 16.2.1, Apple M1 / Honeykrisp (Mesa 26.2.3), 8 GB, debug, since 2026-10-04); the rest from the macOS host, macOS 27 arm64, Apple Clang 21, debug. "No engine build" means the engine has never been configured on that OS: there is no host, Actions minutes ran out 2026-09-24, and the engine CI legs skip while `openETQW-game` is private.

| Capability | Linux x86_64 | Windows 11 x64 | Windows 11 arm64 | macOS arm64 | macOS x86_64 (dropped 2026-09-30; earlier evidence kept) | Linux arm64 | Notes |
|---|---|---|---|---|---|---|---|
| idLib + game module build | compiled (local, Fedora 44, GCC 16.2.1) | compiled (local, VS 2026 MSVC 19.51, debug and debugoptimized, 2026-09-28; also hosted CI, windows-2025, MSVC 19.51) | compiled (local, VS 2026 MSVC 19.51.36256 for ARM64, debugoptimized and release, 2026-10-07) | compiled (local) | compiled (hosted CI) | compiled (local, the Linux arm64 host, GCC 16.2.1, 2026-10-04; also hosted CI, GCC cross from x64) | CI last green at game 9a929f6 (an ID that no longer resolves), before the last game commits. CI resolves `GetGameAPI` after loading the module (not the arm64 cross build) and runs no unit tests. The Windows runner is not Windows 11. The Linux runner is Ubuntu, not Arch, Fedora or Debian |
| Engine build | compiled (local, Fedora 44, GCC 16.2.1, 2026-09-26) | compiled (local, VS 2026 MSVC 19.51, debug and debugoptimized, 2026-09-28) | compiled (local, VS 2026 MSVC 19.51.36256 for ARM64, debugoptimized and release, 2026-10-07; the stack tracer's ARM64 branch since #59) | compiled | compiled (cross from arm64 with `-arch x86_64`, 2026-09-30; the dedicated smoke passes 3/3 under Rosetta) | compiled (local, the Linux arm64 host, GCC 16.2.1, 2026-10-04) | Linux ARM64 engine CI legs exist but skip (the macOS x64 legs were removed on 2026-09-30). The macOS x64 build used a hand-written cross file; the repository has none |
| Dedicated server launch (headless) | map-loaded | map-loaded | map-loaded | map-loaded | — | map-loaded | Linux: `SDL_VIDEO_DRIVER=offscreen`, stdin console, exit 0 |
| Client launch (graphics) | map-loaded | map-loaded | map-loaded (Adreno X1-85: Vulkan on the raster path, no ray-query images; the OpenGL fallback on Microsoft's GL-on-D3D12 layer, `render-api-fallback`) | map-loaded | map-loaded (under Rosetta: the universal2 app's x86_64 slice loaded valley, 2026-09-30) | map-loaded | SDL3 + Vulkan (RADV on Linux, MoltenVK on macOS). OpenGL fallback verified. `+devmap` sessions and network join/listen |
| Asset-free unit tests | unit-tested | unit-tested | unit-tested (13/13, 2026-10-07) | unit-tested | — | unit-tested | bitmsg, usercmd-delta, struct-layout, usercmd-history, strbuilder, install-licenses, zero-sdk, retail-install-check (on synthetic paks); with #25 also packaging-tools and release-tools: 10/10 on Linux (2026-09-29) and Windows (2026-09-30); with M5 step 12 also retired-cvars and with step 11 frame-cap-cvars (both on synthetic data, the game module staged): 12/12 on macOS (2026-09-30); with #55 also msgchannel (fragments sent through real channels; fails with the fatal error without the fix): 13/13 on Windows (2026-10-01), and msgchannel on Linux (GCC, also under ASan+UBSan, with the rest of the GCC unit suite passing) and macOS (Apple Clang) |
| Installed tree / release package | map-loaded (the `.install` tree, the packaged tar.xz and the AppImage, each run from outside the repository, 2026-09-29, #24/#25) | map-loaded (the `meson install` tree, the zip, the zip inside an ET:QW directory and the Inno Setup install, each run from outside the repository, 2026-09-30) | map-loaded (the release `meson install` tree from `openetqw_validate.py push`, and the zip run from outside the repository: valley loaded in game with the bundled `glslangValidator`, 2026-10-07; the Inno Setup installer not built) | map-loaded with game #17 and engine #30 (the installed tree and the ad-hoc-signed `.app`, each from outside the repository, 2026-09-30); before them the `debugoptimized` game module failed `game->Init` on the retail data and the `.app` could not be packaged | map-loaded (the universal2 `.app` from clean `main` 066dd25 / game 98756d8, each slice of the client and the dedicated server, x86_64 under Rosetta, from outside the repository, 2026-09-30) | — | Linux: the stripped packaged client, loader environment cleared, used its bundled SDL3 and glslangValidator, compiled its shaders into the per-user cache and loaded valley; the AppImage found the retail data from its start directory. Windows: the zip's and the installer's client used their bundled glslangValidator with the SDK and vcpkg off PATH and cached their shaders per user. macOS: the app loaded SDL3, the Vulkan loader, MoltenVK and the game module from `Contents/Frameworks` (0 images from Homebrew), used its bundled glslangValidator and loaded valley (client, dedicated server and `open -n`) |
| Stock map: server | map-loaded | map-loaded | map-loaded | map-loaded | — | map-loaded | 11 stock maps + Valley, warmup. One Valley match with forced objectives |
| Stock map: client | map-loaded | map-loaded | map-loaded (with the validation layer active) | map-loaded | — | map-loaded | valley, outskirts, ark, canyon, refinery. Draw counters only. On Linux, `campaign-client-maps` also passes with synchronization validation on |
| OpenETQW MP: dedicated | map-loaded | map-loaded | map-loaded | map-loaded | — | map-loaded | Loopback, one host. Up to 2 clients (headless, or the Vulkan client and a headless one) + 1 bot. Movement checked both ways; reconnect, client restart, map restart, 10-minute lossy session |
| OpenETQW MP: listen server | map-loaded | map-loaded | map-loaded | map-loaded | — | map-loaded | The Vulkan client with `+spawnServer` and one headless peer. Since M5 step 7 the host's camera and viewmodel change on every draw, and since step 9 its remote players too (`frame-poses-listen` A4–A7) |
| Cross-OS client/server pair | map-loaded (both ways, 2026-10-08: the dedicated server on the Linux x64 host with Windows x64 clients; the Linux x64 host's Vulkan client (offscreen) and headless peer on the Windows server; both ways with the Mac, 2026-10-16) | map-loaded (both ways, 2026-10-08: the Vulkan client and a headless peer on the Linux x64 server; the dedicated server hosting the Linux x64 host's clients; both ways with the Mac, 2026-10-16) | — | map-loaded (both ways with Linux x64 and with Windows x64, 2026-10-16; the Mac side run from a Terminal.app window, not tmux) | — | — | M3, meson `cross-os` (`tests/test_cross_os.py`, the server started by hand on its host; rcon is a stub). Windows x64 → Linux x64 and Linux x64 → Windows x64: X1–X4 pass each way (join, walks both ways agreeing to 0.00 u and matching the server's `PLAYER_POS`, reconnect, the map change to outskirts). macOS ↔ Linux x64 and macOS ↔ Windows x64: X1–X4 pass each way (2026-10-16, engine 33d2018). Every pair of the three desktop OSes has passed both ways |
| High refresh: on foot | map-loaded | map-loaded | map-loaded (uncapped ceiling about 155 fps, so cap 240 is reported, not paced) | map-loaded | — | map-loaded | Offline replay at caps 60/120 (held exactly) and 144/240 (display-bound). `high-refresh-determinism` passes. On Linux x86_64 (ceiling ~370 fps, so caps 60/120/240 are all paced) `frame-poses-*` pass; A8 at cap 240 used to fail on a few prediction-clock corrections (a known gap), and since 2026-09-26 counts ticks (max(1, 5%)) and passes. On Linux arm64 (the Linux arm64 host, ceiling 205.5 and 205.1 fps) caps 60 and 120 are paced and pass A1; cap 240 is above 0.9 x the ceiling, so it is reported against the ceiling, not failed (D1; lowest phase 213.6 and 218.8 fps) |
| High refresh: vehicles | map-loaded for listed seats (opt-in, `g_unlock_vehicleSeats`; meson `seat-poses-*`) | map-loaded for listed seats (opt-in, `g_unlock_vehicleSeats`; meson `seat-poses-*`) | map-loaded for listed seats (opt-in, `g_unlock_vehicleSeats`; meson `seat-poses-*`) (in its full suite) | map-loaded for listed seats (opt-in, `g_unlock_vehicleSeats`; meson `seat-poses-*`) | — | map-loaded for listed seats (opt-in, `g_unlock_vehicleSeats`; meson `seat-poses-*`) (in its full suite) | By default seats still draw at the tick rate (the built-in list is empty; D2). A listed seat is drawn on every frame from tick samples: first person, the chase views and, since 2026-10-07/08, the gunner views with blended joints and the jetpack (ledgers 062, 063, 070); mouse look shows on the next draw (064, 065); the hull can lead by up to a tick per class (067, off by default). Seats join the default list after the owner's review on a high-refresh display (vehicle-responsiveness §3.3) |
| Bots / navigation | map-loaded | map-loaded | map-loaded | map-loaded (DVD data) | — | map-loaded | Retail AAS on all 12 maps. Warmup traversal on foot (`bot_useVehicles 0` in `all-stock-maps`). Slipgate AAS flake (77.8%) observed on Linux |
| Retail 1.5 peer rejected at connect (clean break, D7) | launched | launched | launched | launched | — | launched | Simulated peer (id/Splash Damage majors 1, 2, 10–14, 22) gets "Server uses protocol 20293.5". No real retail client tried |

### Build and dependency scripts verified
| Script | Arch | Fedora | Debian | macOS | Windows 11 |
|---|---|---|---|---|---|
| `scripts/build-*` (2026-10-22) | — | — | — | verified (2026-10-22, the macOS host arm64: a fresh build with `--test`, then `--server --map valley` on retail 1.5 data) | — (the helper's dry run passes under pwsh on macOS; not run on Windows yet) |
| `tools/validation/openetqw_validate.py push` / `pr` (#25) | — | verified (2026-09-29, `--skip-game-build`) | — | verified (2026-09-30, `push` and `pr`; the smoke run separately on an assigned port, since the validator has no port option) | verified (2026-09-30, `pr`; #29); arm64: verified (2026-10-07, `push --buildtype release`, main e3244b2) |
| `tools/build/package_release.py` + `package_linux_appimage.py` + `build_glslang.py` (#25) | — | verified (2026-09-29: tar.xz, debug tar.xz, AppImage, each run) | — | arm64 `.app`: failed before engine #30 (the client did not link the Vulkan loader); verified with #30 (2026-09-30) | verified (2026-09-30: the zip and the Inno Setup installer, each run; #29); arm64: the zip verified (2026-10-07, run from outside the repository; `build_glslang.py` on arm64 too), the installer not built |
| `tools/build/prepare_macos_deps.py` (#25; `assemble_macos_universal2.py` removed 2026-09-30 with macOS x64) | — | — | — | verified (2026-09-30: deps for arm64 and universal2; universal2: thin arm64 and x64 builds (x64 cross-compiled with a local cross file; the repository has none), assembled and packaged from clean `main` 066dd25 / game 98756d8, 2026-09-30) | — |

The `scripts/build-*` row: `docs/build-scripts.md` has the evidence per host; Linux runs go in the Fedora and Debian columns. `scripts/deps/*` was never written: the helpers check dependencies themselves. (Before 2026-10-22 no `scripts/` directory existed, and earlier "verified" cells for it were wrong.)

## Milestones (CHARTER §8)
Charter milestone numbers. Older notes use the stock-maps phase's own M2–M5, which are different. Each track is reported separately; no percentages.

**M0 (bootstrap and baseline): partial.**
- Met on macOS arm64:
  - The import from `~/etqw` f2b655c is recorded in each repository's first own commit (engine 9043b91, game 9600d7a) and in `docs/provenance.md`, with history preserved.
  - Engine HEAD holds no SDK files (`zero-sdk` test).
  - The baseline JSON is committed.
  - The asset-free unit suite passes.
  - Engine and game compile.
- Gaps:
  - The engine configures and compiles on Linux (2026-09-26) and Windows (2026-09-28) as well; the M0 items below are still open.
  - At 84a224b, `docs/provenance.md` does not state the import method. The docs pass (PR #2) adds it.
  - SDK code in the engine build: registered (`docs/provenance.md` §4) and approved as exceptions on 2026-09-25 (`docs/decisions/0001-sdk-code-in-the-engine-build.md`).
  - Provenance labels are wrong at 84a224b for the compiledscript tree (SDK, labeled MIT), and two minizip entries are stale (`.cpp` → `.c`). The docs pass (PR #2) corrects both (Release gate 3).
  - openQ4-derived files (`URLPolicy.h`, `macosx_common.h`, `macosx_sys.h`, `macosx_local.h`, `macosx_compat.mm`, `macosx_misc.mm`): done 2026-10-07. The donor is `jm2/openQ4` b37b1456 (3 identical, 3 adapted); each file has an SPDX line and a donor note, and the manifest records it (`docs/provenance.md` §6).
  - At 84a224b the codec unit tests were not labeled as self-consistency tests; ledger rows 003/014 and this file now label them.
  - The charter asks for a `LicenseRef-ETQW-SDK` SPDX line on SDK-derived files; none has one (the game's `spdx_allowlist.txt` stands in).

**M1 (native execution): met for the build and the dedicated server on macOS arm64, Linux x86_64 and Windows 11 x64; gaps below.**
- macOS arm64:
  - Client, dedicated server and game module compile.
  - The dedicated server mounts retail data through `fs_basepath`, initializes decls and the game, loads maps, accepts stdin console commands and exits 0.
- Linux x86_64 (2026-09-26) and Windows 11 x64 (2026-09-28, MSVC): client, dedicated server and game module compile, and the dedicated server runs every stock map (the full retail suite passes on both; `docs/platform-matrix.md`).
- Gaps: the missing scripts under "Not done" and retail data discovery. (The `false` defaults of `build_engine` and `build_dedicated` became `true` on 2026-10-20.)

**M2 (stock map): server side met on macOS arm64; client side partial.**
- Server: all 11 stock maps and Valley load on the dedicated server with map scripts and retail AAS, and bots move (map-loaded, warmup only).
- Client: the Vulkan client renders 5 maps, backed by draw counters only.
- Gaps:
  - In the graphical client, spawn and movement are asserted only by the network tests (`client-network-*`, 2026-09-25), from the server's and the peer's side; collision is not asserted.
  - The skipped-chunk list was incomplete: portal data is discarded, the client portal API is stubbed, and the headless render world is stubbed. Ledger 013 records them since the 2026-09-24 docs pass; their gameplay effects are not measured.

**M3 (OpenETQW multiplayer): partial. The suites pass on every platform over loopback; cross-OS pairs pass both ways between Linux x86_64, Windows 11 x64 and macOS arm64 (Linux ↔ Windows on 2026-10-08, both Mac pairs on 2026-10-16).**
- Shown (map-loaded):
  - A dedicated server and two headless clients connect, spawn on opposite teams and replicate positions.
  - Movement replicates in both directions: each client sees the other walk, and its copy ends within 2 units of the server's position once the walker is at rest (2026-09-25).
  - Reconnect: a client's `reconnect` gets a new game init, it spawns again and both clients see each other; a killed client replaced by a new process gets a new slot while the dead one times out (`net_serverClientTimeout`); `serverMapRestart` with both clients connected respawns both, and they see each other (`reconnect-restart`, 2026-09-25).
  - A 10-minute session at 2% loss each way and 20–60 ms delay (40 ± 20 ms, order kept) completed without a disconnect: 33 replication checks within 0.04 units, 33 walks each seen by the other client, longest snapshot gap 0.24 s (`lossy-session`, final version, 2026-09-25).
  - The Vulkan client joined a dedicated server, with a headless peer: each client saw the other walk (`client-network-join`, 2026-09-25). It also hosted a listen server: the peer saw the host walk, and the host moved the peer's player from its usercmds (`client-network-listen`). The client presented frames and drew the world in both. Neither involved a human at the controls; movement came from `clientMove`.
  - Remote clients follow map changes, also at 10% loss with 60 ms + 0–40 ms latency and at 20% loss.
  - The two-client test also passes at 80 ms + 0–40 ms latency with 5% loss.
  - User info reaches both clients.
  - The server registers a client's attack and its release (asserted since 2026-09-24).
  - A dropped client is told and leaves its game: a kick, the server's timeout of the client, and a listen host's `disconnect` (which also ends the host's server) each reach the client as a disconnect with the reason; it unloads its map, reads no more snapshots and can `reconnect` or join another server. A client that times out the server unloads its map too (2026-10-16, #130: `client-server-kick`, `client-server-timeout`, `listen-host-disconnect`; Linux x64, Windows x64, macOS, Linux arm64; the timeout case skips on Windows, which has no SIGSTOP).
- Not tested:
  - peer-observed weapon fire (a headless peer cannot observe it);
  - cross-OS pairs that include a Linux arm64 or Windows arm64 host. Linux x86_64, Windows 11 x64 and macOS arm64 have each passed with the other two, both ways;
  - the Vulkan client with a human at the controls.
- Loss and jitter simulation (POSIX and, since 2026-09-28, Windows) keeps packet order. Since 2026-10-08 `net_forceReorder` (percent) and `net_forceReorderDelay` (ms) hold received packets back so later ones overtake them, and each process reports how many it released out of order (`NET_REORDER`). meson `remote-client-map-change-reorder` (20 ms link, 20% reordered by up to 60 ms, base check) and `multiclient-gameplay-reorder` (40 ms link, 20%, 60 ms, base check) pass on win-arm64, Windows x64 and Linux arm64; no netcode failure was found. The counts are per socket, so a server's covers all its clients. A packet that arrives after a later one is dropped by the channel (`idMsgChannel` drops a sequence at or below the last one it accepted), so on the wire a reordered packet acts as a loss that the snapshot stream and the reliable resend recover, as they do for `net_forceDrop`.

**M4 (full match): partial. The gameplay transitions, a time-limited match played by live bots, and matches on two maps beyond the campaign (quarry, island) are shown by tests; an objective played out by bots, or a match played by people, is not.**
- meson `m4-*` (2026-10-08/09, #104 and #107 with game #45/#47; valley unless noted; read from the server's `g_logGameEvents` lines, except `m4-deployable`, which reads `g_debugDeployables` and `deployStatus`):
  - `m4-classes`: each of the ten classes spawns with its retail 1.5 kit;
  - `m4-damage-kills`: a hit damages, hits kill (one KILL line with victim, attacker, teams and damage decl), and the dead player spawns again;
  - `m4-deployable`: a deployable is requested with the deploy checks on, carried in and landed, for each team;
  - `m4-match-end`: an objective match ends both ways, the defenders' time-limit win and the attackers' objective win;
  - `m4-stopwatch`: a stopwatch pair, round 1 setting the time to beat and round 2 played against it, with the teams swapped;
  - `m4-campaign`: a campaign plays its three maps (valley, outskirts, area22) with a remote client following, then an admin map change; the engine now runs the game's queued map changes (#107).
- `valley-match`: a started objective match with a Titan entered and a seat swap, ending in game review.
- meson `m4-bot-match` and `m4-offcampaign-match` (2026-10-23, #199; pass on Linux x64 and macOS arm64):
  - `m4-bot-match`: two live bots, one per team, play a time-limited objective match on valley. At least one moves within a single life, the defenders win on the time limit, game review follows, and no objective is completed.
  - `m4-offcampaign-match`: on quarry and island, with the bots paused, an objective match starts and runs to its time limit with no objective completed. The map's defenders win (GDF on quarry, Strogg on island) and game review agrees.
- Not shown: an objective completed by bots (they play no objectives), a started match on the other seven non-campaign maps, and a human at the controls.
- Bots / navigation row: partial. Retail AAS is on all 12 maps and bots traverse in warmup. Bots play no objectives.

**M5 (high-refresh multiplayer): not met.**
- **State (2026-10-19), by `docs/m5-plan.md` step:**
  - **Done:**
    - 1: the frame-path doc;
    - 2: frame timing;
    - 3: frame poses on foot and, since 2026-10-19, in a vehicle seat (`seat-poses-*`, with S5 for the cockpit);
    - 5 and 5a: the replay hash, in single player and on the listen host, and the vehicle physics guard;
    - 6: no presentation values in tick state;
    - 7: the listen host's per-frame view;
    - 8–10: remote entities interpolated everywhere, and tick frames drawn at their sub-tick fraction (ledgers 059–061);
    - 11–12: the unlock CVars;
    - 13: the seat gate (ledger 054);
    - 14a: aircraft re-prediction (ledger 055);
    - 15: mixed caps (A7/A7o pass since step 8);
    - 16: GUI updates;
    - 17: a cap change during play;
    - 18: the default cap of 240 (ledger 069).
  - **Step 14's seat presentation:** built for the `standard`, chase, `pivot`/`freepivot` and jetpack views. `pivot`/`freepivot` use the transport's joints blended per draw. Late look covers the `standard`, chase, rate-limited, `pivot` and `freepivot` seats and, since 2026-10-20, the damped first-person `clampDamped*` view (on valley, the husky driver). The `freepivot` and `clampDamped*` tier is map-loaded (valley, `seat-poses` devmap, join and listen) on the Windows x64 host (Windows 11 x64) at game b589449 and on the Linux x64 host (Fedora 44, Linux x64) at game 32fcae8, both with engine 58453f9's tests (ledger 064). Ledgers: 062, 063, 066 and 070 for the presentation, 064 and 065 for late look.
  - **Step 14b's hull lead:** built, off by default (ledger 067).
  - **Left:**
    - the default seat list and the hull-lead bounds (the owner, after a review on a high-refresh display).

    The default seat list is empty, so by default seats still draw at the tick rate. Step 4's ~120 fps ceiling is the display's, accepted as evidence (D3).
- The replay runs offline in single player (`high-refresh-determinism`) and, since 2026-10-01, on a listen host (`high-refresh-determinism-listen`: the Vulkan client with `+spawnServer`, the same schedules; plan step 5's listen variant): `SIM_HASH`, `SIM_VIEW` and `VEH_TRACE` identical to cap 30's and to `com_unlockFPS 0`'s on every tick (the Linux x64 host, 5 runs). Before plan step 7 the host drew between ticks but never called `ClientUpdateView` there, so none of those draws was an unlocked draw; since step 7 every one is (asserted), and both replays check that tick code never starts on a render frame's view (`TICK_VIEW`, owner decision E3). `SIM_HASH` leaves out view angles, other players and projectiles; since PR #14 the replay also compares `SIM_VIEW` each tick at full precision (view angles, the logged view origin and weapon pose, the vehicle's pose and its view's last returned axis). Other players and projectiles are still not compared.
- In network games (client or listen host) no GUI updated: the engine never called the per-frame GUI update outside single player (0 updates in 5 s of play). Fixed 2026-09-25; `client-network-*` require it (60 updates/s at cap 60).
- What the Vulkan client draws is now captured per frame (`g_logFramePoses`, meson `frame-poses-join`/`-listen`, 2026-09-25, on foot, caps 60/120/240): on a network client the camera changes on every draw, remote players only once per tick (the `Entity::Present` producer is off) and, since the `PresentUnlocked` fix, the viewmodel changes on every draw (before it, it repeated on about a fifth of draws: after a snapshot re-ran prediction, `isNewFrame` was false and the weapon's `Present()` returned early); on a listen host camera, viewmodel and remote players all changed only once per tick until plan step 7; since then the host's camera and viewmodel change on every draw (A4–A6 100%, A8 every draw between ticks unlocked, at caps 60/120/240), and since step 9 its remote players too (A7 100%).
- Frame rate is now measured during play (`com_logFrameTiming`, 2026-09-25): caps 60 and 120 are held exactly (16.67 and 8.33 ms frames) after a pacer fix; caps 144 and 240 run at about 120 because presentation waits for this host's 120 Hz display. Every replay schedule has one ~410–540 ms stall right after spawn: first-use material and render-program loading (with debug dumps), not pacing.
- The 2026-09-24 turn-rate fix removed one render-rate dependence of authoritative state (key turning). Since 2026-10-01 `turn-rate-listen` also checks it on the Vulkan listen host: 20.0 deg/s at cap 30 and 19.8 deg/s at cap 240 (7.7–7.9 frames a tick), both ways (the Linux x64 host, 2 runs). Before plan step 7 the host built a usercmd only at each tick (`LocalClientInput`); since step 7 it also builds one on each render-only frame, so `TurnTimeScale`'s scaling across them is exercised there too: 20.0 deg/s at caps 30 and 240, both ways, with 413–415 of 474–475 draws of each cap-240 hold unlocked (the Linux x64 host, 2 runs).
- `docs/frame-path.md` (2026-09-25) traces one host frame in each mode, from code; its pacing section (§7.1, §7.5, §7.6) also has the frame rates measured with `com_logFrameTiming`. `docs/m5-plan.md` orders the remaining work in 17 steps; the owner made its ten decisions (D1–D10) on 2026-09-25; D10's seat view is on by default.
- Open (details and file:line in `docs/frame-path.md` §9):
  - vehicle seats by default. Steps 13–14 replaced the SDK's proxy gate for listed seats, but the default seat list is empty until the owner picks seats;
  - the SDK channels from render frames into tick state that remain: the damped views' `lastReturnedAxis` (owner decision 7, kept), and on network clients the `renderView` that `ClientUpdateView` writes and scripts read before `CalculateView` (since plan step 7 it is put back at each tick's entry on the listen host and in single player, owner decision E3; OpenETQW's vehicle-view interpolation was removed in step 6);
  - in a mixed-cap live session (meson `mixed-caps`, M5 step 15, since 2026-10-01), what each client sees of the other client and of the peer changed once per tick until M5 step 8 restored the producer (2026-10-03, ledger 059: `mixed-caps` A7/A7o pass, their known gaps removed; #135 tracks a separate A1 flake on the Linux x64 host); the rest of that test passes on macOS and, since #54, on Windows and Linux. A cap change during play is tested since 2026-09-30 (M5 step 17, engine #39): map-loaded on macOS arm64, Apple Clang 21, debug, retail 1.5 data: a `high-refresh-determinism` schedule changes the cap from 60 to 90 (`com_unlock_maxFPS`) and to 45 (`com_maxFPS`) during play; the simulation matches every other schedule and each new cap is held (90.0 and 45.0 fps);

**M7 (public release): not met.** None of the six release-gate items is cleared yet. Item 2's SDK exceptions are approved (A6) and their condition, the SDK EULA in every engine package, is met (PR #19); the SDK files in engine history are covered by `EULA_SDK_ETQW.txt` at the repository root (2026-10-29). Also missing:
- a release made on hosted runners: the packaging scripts and the manual release workflow exist since #25 (`docs/releasing.md`), and the Windows zip and installer, the Linux tar.xz and AppImage and the macOS app were each built and run locally, but hosted CI has no Actions minutes;
- retail data discovery.

The app's game module is found: since #24 `FindDLL` tries the executable's directory, then the package's module root (`Contents/Frameworks` in the app; `FileSystem.cpp:4792-4799`), and the 2026-09-30 app runs loaded it from there.

## Release gate (CHARTER §11)
Both repositories are private, and no release has been made. The `screenshots-prNNN` prereleases were review material, not releases.
1. **jmarshall's reconstruction code has no licence: unresolved, blocking.**
   - Both histories start from jmarshall's three upstream commits (`docs/provenance.md` §1), which carry no licence file.
   - A file-by-file audit (2026-10-29) found 230 files, about 82k lines, that are his own text with no ETQW SDK, Doom 3 GPL or third-party counterpart. 180 of them reached shipped builds. 154 upstream files carry his own "GPL v3 or later" header; most of those are Doom 3-derived, and none is in the shipped core.
   - #349 and game #62 (2026-10-29) removed the files no build compiles. About 185 remain, nearly all shipped: the Vulkan backend and ray tracing, much of the framework and the renderer front end, the decl library and the Windows bootstrap.
   - 59 of the shipped files say in their own text that they were reconstructed from the retail PDB, Hex-Rays output or the retail executable. That is a separate risk from the licence.
   - The author did not respond; replacement or rewriting is undecided (owner, 2026-10-29). This gate stays blocking until it is resolved.
2. **SDK-licensed code linked into engine binaries: not cleared; exceptions approved by the owner (2026-09-25).**
   - The engine links the game repo's SDK idLib (`libidLib.a`, 72 objects) and compiles the staged SDK `DeclType.cpp`, 189 staged SDK headers and `BuildDefines.inc`; three unit tests also compile the SDK `BitMsg.cpp`. `docs/provenance.md` §4 lists them (E1–E4).
   - The owner approved them as tracked exceptions on openQ4's precedent (A6; `docs/decisions/0001-sdk-code-in-the-engine-build.md`). Condition: every engine package carries the SDK EULA as well as the GPL. Met since PR #19: `meson install` puts it under `licenses/ETQW-SDK`, checked by meson `install-licenses`. That clears the item for the engine binaries.
   - Engine history holds SDK files through jmarshall's root commits (the owner left them in place). SDK EULA §5 (the EULA must accompany each copy) is met for them by `EULA_SDK_ETQW.txt` at the repository root since 2026-10-29 (`docs/provenance.md` §6). No other condition of this item is recorded as open; declaring it cleared is the owner's call.
3. **`docs/provenance.md` coverage: not cleared.**
   - `docs/provenance-manifest.json` classifies 5,274 imported files.
   - Files added after the split: in it since 2026-10-07 (213 entries then, 230 at b318c87: 193 engine, 37 game; `addedAfterSplit`; `tools/build/provenance_post_split.py` reports and adds later ones).
   - At 84a224b / 24f58bf, the manifest and the game's `tools/ci/spdx_allowlist.txt` label the 204 files in `src/compiledscript/scripts/` (202 `.script`, 2 `.include`) and 13 compiledscript runtime files (217 in all) Reconstruction_original/MIT. They are SDK files (`LicenseRef-ETQW-SDK`).
   - The 2026-09-24 docs pass corrected this. The manifest relabels 217 compiledscript files as SDK, fixes the two minizip entries. The allowlist is updated to match (merged in PR #2).
   - jmarshall files are labeled GPL-3.0-or-later although their license is the open question.
   - Other defects are listed under M0.
4. **LICENSE and README explain the split and the retail-data requirement: not cleared.**
   - At the audit, the engine `readme.md` was jmarshall's two lines, the game repo had no README, and the engine `LICENSE` did not mention the split. The game `LICENSE` explains the SDK EULA/MIT split and the retail requirement.
   - The 2026-09-24 docs pass drafted an engine `readme.md` and a game `README.md` covering these points. They are merged in PR #2 and await owner review.
5. **No retail binaries, PDBs, raw RE output, captures or assets in either repo's history: not cleared.**
   - Both histories were scanned against the retail 1.5 data: the only matches are the SDK script text in `src/compiledscript/scripts/` (`docs/provenance.md` §7).
   - Before publishing, check that no ref on the host, including GitHub's pull-request refs, reaches retail content. The owner expects to recreate the repositories.
   - No `.exe`, `.pdb`, `.pk4` or capture files and no raw decompiler output were found.
   - Third-party code binaries: the owner approved the deletion on 2026-10-26 (all eight, including `src/sys/win32/eaxguid.lib`); deleted in #301. `src/openal/lib/openal32.lib`, `src/openal/lib/eaxguid.lib`, `src/sys/win32/eaxguid.lib`, zlib's four `contrib/masmx{64,86}/*.obj` and the PowerPC `src/openal/osx/OpenAL.framework` binary were deleted because no build target references them — OpenAL is compiled out everywhere (`ID_OPENAL` is defined nowhere) and zlib is the system dependency. What remains binary is documentation, vendored UI/media assets and SDL2's Gradle wrapper jar — no prebuilt code in any engine build target; see `docs/provenance.md` §6.
6. **SDK EULA with the game repo and every package; retail-install check enforced; nothing sold: not cleared (both safeguards are in place; no package exists yet to carry them).**
   - The owner accepted the EULA §2 risk as openQ4 does (A3, 2026-09-25), with two safeguards (`docs/decisions/0002-sdk-eula-risk-and-retail-install-check.md`):
     - the retail-install check, openQ4's design (A7): enforced since engine PR #19 (`fs_validateOfficialPaks`, meson `retail-install-check`);
     - the licence texts in every package: the engine and game install sets carry them since engine PR #19 (checked by meson `install-licenses`) and game PR #12.
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
  - 050: added in PR #1 (2026-09-24). Its "retail" signatures were the 1.0 DVD scripts'; retail 1.5 uses the SDK ones, which game PR #11 (775c102) restored.
  - 047: `validClasses` read as retail 1.5 reads it, `GetBool` (B10; game PR #11). 030: resolved by the 1.5 data. 040: B8. 052 (new): the data target is retail 1.5.
  - 009: dedicated pacing (2026-09-24: no busy-spin; the client margin is adaptive since 2026-09-25).
  - 036: clean break. The rejection is now checked before the challenge. D7 was deferred on 2026-09-24 and dropped on 2026-09-25.
  - 014: the 16-bit wire parity claim is withdrawn; the usercmd tests now compile the engine codec and are labeled self-consistency tests.
  - 051 (new): turn rate, connect check order, seat-swap finding.
  - 008: aim/camera; relevant to the turn-rate fix.
  - 028 and 029: the audit found overclaims (stale pacing description; vehicle smoothing that never ran). The 029 finding was corrected on 2026-09-25: the vehicle sub-tick code runs on the render frames of the tick in which a player enters a vehicle, and through `GetAORView` (AoR, antilag), where it writes into tick state (plan step 6). M5 step 6 (2026-09-30) widened 029 to the SDK behaviour restored (resolved, LOW), added 056 (the draw-time proxy guard) and 057 (the `originlog` reset at the pose log), and dropped 054's entry-leak exception.
- **Protocol version bumped (incompatible wire change)?** No. No change on 2026-09-24 or 2026-09-25 alters bytes on the wire. A connect message holding only the protocol field now gets a print instead of silence; that is server behavior, not format.
