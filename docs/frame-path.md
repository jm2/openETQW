# Frame path

This document traces what one host frame does in each run mode, from input sampling to the sleep before the next frame: input sampling, socket servicing, command deadlines, server steps, snapshot receipt, prediction, committed-state publication, the extra-view update (`ClientUpdateView`), render submission and pacing, with time units and the meaning of the `timeLeft` residual. It is the trace that CHARTER §7.5 ("Trace before editing") asks for before any high-refresh (M5) work, and it ends with the gaps against the M5 criteria.

Written 2026-09-25 against engine main 85879e3 and game main d8e5b3d; the engine citations were remapped for the frame-timing change the same day. Line numbers drift; re-check before relying on one.

**Evidence level.** Everything here was read from code, except the frame rates and pacing results in §7.1, §7.5 and §7.6, which were measured with `com_logFrameTiming` on 2026-09-25. Nothing else was built or run for this document. Statements marked *(inferred)* or *(derived)* are conclusions drawn from the code or from arithmetic that nobody has observed at runtime. Engine `src/` is identical at 84ad1bb, a6dbde0 and 85879e3; only tests, docs and the companion pin changed between them. Game `src/` differs between 7f0e275 and d8e5b3d only in `src/game/gamesys/SysCmds.cpp` (`playerPos`), which this document does not cite.

**Conventions.**
- Citations use one of these forms:
  - engine repo: `src/...:N`, `tests/...:N` or `tools/...:N`, relative to the engine repo root; a range is `:N-M`;
  - game repo: `game repo: src/...:N`, relative to the openETQW-game root;
  - pristine ETQW SDK 1.5 source: `SDK: <path>:N`, relative to `.agents/upstreams/etqw_sdk_comparison/sdk/source/` (a local, untracked reference);
  - Doom 3 GPL source: `Doom 3: neo/...:N`, at `HEAD` of `.agents/upstreams/id-Software_DOOM-3.git`;
  - retail data: `pakNNN: <path>:N`, the pak that supplies the file in the 1.5 data set (for example `pak008`, which overrides the DVD `pak000` copy of most scripts).
- `STATUS.md`, the charter, jmarshall's tree and build logs are named without line numbers.
- The SDK ships the game module and the engine headers only, so engine `.cpp` files have no SDK counterpart.
- "jmarshall" is the reconstruction both repos were split from (commit f2b655c). "OE" marks an OpenETQW change.
- For engine files, provenance was checked against jmarshall and Doom 3. For game files it was checked by diffing against the SDK.

## 1. Time units and clocks

| Quantity | Unit and type | Where |
|---|---|---|
| Tick length | `USERCMD_MSEC = 1000 / USERCMD_HZ` = 33 ms (integer division), `USERCMD_HZ` = 30 | `game repo: src/framework/UsercmdGen.h:15-16`, same as `SDK: framework/UsercmdGen.h:15-16` |
| Tick rate in wall time | 1000 / 33 ≈ 30.3 ticks per second *(derived)* | |
| Game time per tick | +33 ms per `game->RunFrame` or `ClientPrediction` | `src/framework/Session.cpp:560`, `src/framework/async/AsyncServer.cpp:451-453`, `src/framework/async/AsyncClient.cpp:336-337`, `src/framework/async/AsyncClient.cpp:344-348` |
| Single-player tick clock | `mainThreadTicResidual` (double, ms, scaled by `com_timescale`) and `com_ticNumber`; catch-up capped at 10 ticks | `src/framework/Common.cpp:901-937`, cap at `src/framework/Common.cpp:924-929` |
| Client tick clock | `gameTimeResidual + clientPredictTime` (int, ms) | `src/framework/async/AsyncClient.cpp:334`, `src/framework/async/AsyncClient.cpp:882-895` |
| Server tick clock | `gameTimeResidual` (int, ms) | `src/framework/async/AsyncServer.cpp:436`, `src/framework/async/AsyncServer.h:213` |
| Per-frame wall-time clamp | client and server add at most 100 ms per host frame (`UpdateTime( 100 )`) | `src/framework/async/AsyncClient.cpp:311`, `src/framework/async/AsyncClient.cpp:882-895`, `src/framework/async/AsyncServer.cpp:426`, `src/framework/async/AsyncServer.cpp:1646-1658` |
| Client prediction lead | `net_clientPrediction` 16 ms; `net_clientMaxPrediction` 1000 ms | `src/framework/async/AsyncNetwork.cpp:54-55` |
| Frame pacer | `std::chrono::steady_clock`, µs | `src/framework/Common.cpp:1086-1139` |
| Key and controller turn scale | `steady_clock` µs since the previous usercmd build, clamped to 0-4 ticks | `src/framework/UsercmdGen.cpp:362-372` |
| Snapshot interval | `net_serverSnapshotDelay`, default 50 ms, rounded up to the server's host-frame grid (§4.5) | `src/framework/async/AsyncNetwork.cpp:45`; checked at `src/framework/async/AsyncServer.cpp:1113` |

## 2. `timeLeft` and the sub-tick fraction

`idGame::ClientUpdateView( const usercmd_t &cmd, int timeLeft )` is the SDK's hook "to prepare for an extra draw (com_unlockFPS)" between ticks (`SDK: game/Game.h:173-174`; unchanged at `game repo: src/game/Game.h:173-174`). The engine passes `timeLeft`, the number of milliseconds left before the next tick boundary. The game turns it into the fraction of the tick that has already elapsed:

```
interpFraction = ( USERCMD_MSEC - timeLeft ) / USERCMD_MSEC
```

This is at `game repo: src/game/Game_local.cpp:6024` (`SDK: game/Game_local.cpp:4941`). The game aborts with a warning when `timeLeft == USERCMD_MSEC` (`game repo: src/game/Game_local.cpp:5977-5980`; `SDK: game/Game_local.cpp:4894-4897`).

Two engine functions produce `timeLeft`, and both clamp the elapsed part to [1, 32] ms:

| Mode | Function | Residual used |
|---|---|---|
| Single player | `Com_GetGameTimeLeft` (`src/framework/Common.cpp:939-955`) | `mainThreadTicResidual`, measured by `Com_UpdateGameTime` at frame start (`src/framework/Common.cpp:1040`) |
| Network client | `idAsyncClient::GetGameTimeLeft` (`src/framework/async/AsyncClient.cpp:295-303`) | `gameTimeResidual + clientPredictTime`, the same sum the tick loop tests (`src/framework/async/AsyncClient.cpp:334`) |
| Listen host | none | `idAsyncServer` keeps `gameTimeResidual` (`src/framework/async/AsyncServer.h:213`) and exposes it only as `GetDelay()` (`src/framework/async/AsyncServer.h:152`) |

Consequences:
- `interpFraction` takes values from 1/33 to 32/33 in steps of 1/33. The integer-ms `timeLeft` keeps successive fractions distinct only while frames are at least 1 ms apart *(derived)*.
- The residual is read at the start of the frame (`src/framework/Common.cpp:1040` in single player, `src/framework/async/AsyncClient.cpp:311` on a client), before the draw. A frame therefore shows the pose for the moment its frame began, not the moment it is presented *(inferred)*.
- On a client, every snapshot moves the prediction clock: `clientPredictTime -= delta / PREDICTION_FAST_ADJUST ± 1`, with `PREDICTION_FAST_ADJUST` = 4 (`src/framework/async/AsyncClient.cpp:19`, `src/framework/async/AsyncClient.cpp:648-649`). The out-of-range reset in §4.2 also moves both terms. Because `timeLeft` includes `clientPredictTime`, the fraction can jump slightly when a snapshot is processed *(inferred)*.
- The client branch of `Com_GetGameTimeLeft` (`src/framework/Common.cpp:940-942`) is unreachable. Its only caller (`src/framework/Session.cpp:344`) runs only when no async session is active (`src/framework/Session.cpp:318`).
- `Com_GetGameTimeLeft` must not be reused on a listen host. There the client is not active, so it would return the single-player accumulator, which advances every frame (`src/framework/Common.cpp:1040`) but has a different phase from the server's `gameTimeResidual`.

## 3. The host loop and `idCommonLocal::Frame`

### 3.1 Host loops

The meson build chooses the platform sources in `tools/build/meson_sources.py:198-228` and `tools/build/meson_sources.py:254-275`:
- Every client built with the default `sdl3` backend compiles `src/sys/sdl/sdl_glimp.cpp`, `src/sys/sdl/sdl_events.cpp` and `src/sys/sdl/sdl_sound.cpp`, on Windows too.
- macOS and Linux add `src/sys/posix/*` and `src/sys/sys_local.cpp`; their client adds `src/sys/osx/macosx_sdl3_main.cpp`. macOS also compiles `src/sys/osx/macosx_compat.mm` and `src/sys/osx/macosx_misc.mm` for every target, and its client adds `src/sys/osx/macosx_sdl3.cpp`.
- From `src/sys/win32`, Windows compiles only `src/sys/win32/SystemBootstrap.cpp`, so `src/sys/win32/win_main.cpp` and `src/sys/win32/win_inputthread.cpp` are not built.

| Build | Loop | Where |
|---|---|---|
| macOS/Linux client | `common->Frame()` with no sleep | `src/sys/osx/macosx_sdl3_main.cpp:23-26` |
| POSIX dedicated | `common->Frame(); Sys_Sleep( 1 );` | `src/sys/posix/posix_main.cpp:660-664` |
| Windows client and dedicated | `common->Frame(); Sys_Sleep( 1 );` with `Sys_Sleep` = `Sleep( msec )` | `src/sys/win32/SystemBootstrap.cpp:1405-1408`, `src/sys/win32/SystemBootstrap.cpp:1464-1467`, `src/sys/win32/SystemBootstrap.cpp:562-564` |

No meson build starts an input thread. During play the only input pump is `Sys_GenerateEvents` at the start of each frame; loads also pump it from `PacifierUpdate` (`src/framework/Session.cpp:248`).

### 3.2 `idCommonLocal::Frame` (`src/framework/Common.cpp:1031-1142`)

1. **Input pump.** `Sys_GenerateEvents` (`src/framework/Common.cpp:1039`). On POSIX it calls `Sys_ProcessSDLEvents`, then queues at most one stdin console line as `SE_CONSOLE` (`src/sys/posix/posix_main.cpp:625-641`). Mouse motion is queued as `SE_MOUSE`, and SDL's float `xrel`/`yrel` are truncated to int (`src/sys/sdl/sdl_events.cpp:233-235`).
2. **Single-player clock.** `Com_UpdateGameTime` (`src/framework/Common.cpp:1040`; body `src/framework/Common.cpp:901-937`) runs in every mode. Only single player uses `com_ticNumber` to run ticks, but every mode reads it:
   - `com_frameTime = com_ticNumber * USERCMD_MSEC` (`src/framework/Common.cpp:1052`) drives console timing (`src/framework/Console.cpp:730-759`, `src/framework/Console.cpp:854`, `src/framework/Console.cpp:952`, `src/framework/Console.cpp:1029`);
   - `com_ticNumber` drives the edit-field cursor blink (`src/framework/EditField.cpp:597`), the session wipe (`src/framework/Session.cpp:626-656`) and `UsercmdInterrupt`'s buffered slot (`src/framework/UsercmdGen.cpp:654`).
3. **Config write** every 2 s (`src/framework/Common.cpp:1041-1045`).
4. **Event loop.** `RunEventLoop( true )` (`src/framework/Common.cpp:1050`) runs the command buffer and dispatches events (`src/framework/EventLoop.cpp:95-129`): `idSessionLocal::ProcessEvent` → console → `game->HandleGuiEvent` → `usercmdGen->ProcessEvent` once a map is spawned (`src/framework/Session.cpp:201-216`). `ProcessEvent` adds mouse deltas to `mouseDx`/`mouseDy` and records key state (`src/framework/UsercmdGen.cpp:722-737`). The test harness relies on the engine executing one stdin line per frame (`tests/etqw_harness.py:464-465`).
5. `com_frameTime` is set (`src/framework/Common.cpp:1052`).
6. `networkService->RunFrame()` (`src/framework/Common.cpp:1053-1055`), then the per-host-frame `game->RunFrame()` (`src/framework/Common.cpp:1059`). That overload only services sdnet (`game repo: src/game/Game_local.cpp:9050-9060`; `SDK: game/Game_local.cpp:7898-7908`).
7. **Network.** `idAsyncNetwork::RunFrame()` (`src/framework/Common.cpp:1061`) runs `client.RunFrame()` and then `server.RunFrame()` (`src/framework/async/AsyncNetwork.cpp:180-183`).
8. **Session and draw.**
   - With no async session active, `session->Frame()` runs (`src/framework/Common.cpp:1063-1064`).
   - With one active on a dedicated binary, `renderSystem->SyncRenderSystem()` runs instead (`src/framework/Common.cpp:1065-1067`).
   - `session->UpdateScreen( false )` then always runs (`src/framework/Common.cpp:1068`).
   - `UpdateScreen` (`src/framework/Session.cpp:218-232`) returns at once when `!Sys_IsWindowVisible()`. That is always the case in `ID_DEDICATED` builds, and in a client whose window is minimised (`src/sys/posix/posix_main.cpp:425-432`). Otherwise it runs `BeginFrame`, `Draw`, `EndFrame`.
   - With a map spawned, `Draw` calls `game->Draw()` (`src/framework/Session.cpp:278`), then `Draw2D` and `DrawSystemUI`.
   - The call chain is `game->Draw()` → `idPlayerView::RenderPlayerView` (`game repo: src/game/Game_local.cpp:5939-5941`, `game repo: src/game/PlayerView.cpp:857`) → `SingleView` (`game repo: src/game/PlayerView.cpp:973`) → `gameRenderWorld->RenderScene( view )` (`game repo: src/game/PlayerView.cpp:350`).
9. `++com_frameNumber`. Then `com_maxFPS` and `com_unlock_maxFPS` are mirrored (`src/framework/Common.cpp:1073-1084`) and the frame is paced (`src/framework/Common.cpp:1086-1139`); see §7.1. Pacing runs after present and before the next input pump.

## 4. Per-mode call sequences

### 4.1 Single player and `+devmap` (no async session)

1. `Com_UpdateGameTime` adds wall-clock ms × `com_timescale` to `mainThreadTicResidual` and moves whole 33 ms steps into `com_ticNumber` (`src/framework/Common.cpp:901-937`).
2. `idSessionLocal::Frame` (`src/framework/Session.cpp:309-347`):
   1. `GuiFrameEvents( false )` (`src/framework/Session.cpp:310`) → `game->GuiFrameEvents` → `uiManager->Update` (`src/framework/Session_menu.cpp:30-34`; `game repo: src/game/Game_ui.cpp:1274-1275`; `SDK: game/Game_ui.cpp:1273-1275`).
   2. `tics = latchedTicNumber - lastGameTic`, bounded to 0..10 (`src/framework/Session.cpp:319-328`).
   3. **Server steps and command deadlines.** Each tic calls `RunGameTic` (`src/framework/Session.cpp:333-336`; body `src/framework/Session.cpp:552-562`). `RunGameTic` builds a fresh usercmd with `GetDirectUsercmd` (`src/framework/Session.cpp:555`), stores it in `mapSpawnUsercmd[0]` (`src/framework/Session.cpp:556`) and calls `game->RunFrame( cmds, USERCMD_MSEC )` (`src/framework/Session.cpp:560`).
   4. **Several tics in one frame.** Their commands are built back to back. `TurnTimeScale` returns the real time since the previous build, so the first command gets all key and controller turning since the last build and the others get almost none (`src/framework/UsercmdGen.cpp:362-372`, `src/framework/UsercmdGen.cpp:554-565`). The first command also takes all accumulated mouse motion, which `MouseMove` zeroes (`src/framework/UsercmdGen.cpp:451-452`), unless `m_smooth` is above its default of 1 (`src/framework/UsercmdGen.cpp:172`). The total turn is right, but it all lands in the first tic. A client or listen host that runs several ticks in one frame behaves the same way.
   5. **Extra-view update.** Only when no tic ran (`renderOnlyFrame`, `src/framework/Session.cpp:332`): `GetDirectUsercmd` (`src/framework/Session.cpp:343`), then `game->ClientUpdateView( cmd, Com_GetGameTimeLeft() )` (`src/framework/Session.cpp:344`).
3. `UpdateScreen` draws (`src/framework/Common.cpp:1068`). A frame that ran a tic is a **base draw**: `ClientUpdateView` is not called, so the game draws at sub-tick fraction 0 (§6.4).
4. Pacing (§7.1).

Provenance: `Session::Frame`, `RunGameTic` and the single-player `Com_GetGameTimeLeft` match the jmarshall baseline.

### 4.2 Network client (`idAsyncClient`)

`idAsyncClient::RunFrame` (`src/framework/async/AsyncClient.cpp:305-360`):

1. `UpdateTime( 100 )` adds wall-clock ms to `gameTimeResidual` (`src/framework/async/AsyncClient.cpp:311`; body `src/framework/async/AsyncClient.cpp:882-895`).
2. **Socket servicing.** The `clientPort.GetPacket` loop (`src/framework/async/AsyncClient.cpp:315-320`) passes every packet to `ProcessMessage` (`src/framework/async/AsyncClient.cpp:792-814`):
   - connectionless packets go to `ConnectionlessMessage` (`src/framework/async/AsyncClient.cpp:795-798`);
   - packets from another server id or address, and every packet during a map change, are dropped (`src/framework/async/AsyncClient.cpp:801-806`);
   - otherwise `channel.Process`, then `ProcessReliableServerMessages` (`src/framework/async/AsyncClient.cpp:811`), then `ProcessUnreliableServerMessage` (`src/framework/async/AsyncClient.cpp:812`; body `src/framework/async/AsyncClient.cpp:570-656`).
   
   After the loop come `CheckTimeout` (`src/framework/async/AsyncClient.cpp:322-326`), `SetupConnection` (`src/framework/async/AsyncClient.cpp:328`) and, when a `ui_*` CVar changed, `SendUserInfoToServer` (`src/framework/async/AsyncClient.cpp:329-331`).
3. **Snapshot receipt** (`src/framework/async/AsyncClient.cpp:577-655`):
   - **Out-of-range reset.** If the client's `gameTime` is behind the snapshot, or more than `net_clientMaxPrediction` ahead (`src/framework/async/AsyncClient.cpp:618-619`), the client clears its own usercmds for the skipped frames, sets `gameFrame` and `gameTime` to the snapshot's, and clamps `gameTimeResidual` and `clientPredictTime` to ±`net_clientMaxPrediction` (`src/framework/async/AsyncClient.cpp:620-628`). This moves the tick clock.
   - `game->ClientReadSnapshot` reads the snapshot (`src/framework/async/AsyncClient.cpp:633`). It becomes the delta base only when the server's `SERVER_RELIABLE_MESSAGE_APPLYSNAPSHOT` arrives (comment at `src/framework/async/AsyncClient.cpp:631-632`; handled at `src/framework/async/AsyncClient.cpp:693-701`).
   - In the game, `ClientReadSnapshot` resets `framenum` and `time` to the snapshot and sets `isNewFrame` (`game repo: src/game/Game_network.cpp:2449-2458`), then recomputes AoR flags against the viewer (`game repo: src/game/Game_network.cpp:2563-2577`; see §6.9).
   - On success, `predictedGameFrame = snapshotGameFrame` (`src/framework/async/AsyncClient.cpp:636`), so prediction replays from the snapshot frame. The first snapshot of a game moves the client from `CS_CONNECTED` to `CS_INGAME` and zeroes `gameTimeResidual` (`src/framework/async/AsyncClient.cpp:637-640`). The prediction clock is nudged (`src/framework/async/AsyncClient.cpp:646-649`).
4. **Command deadlines** (in `CS_INGAME`). `startFrame = gameFrame` (`src/framework/async/AsyncClient.cpp:333`). Then, while `gameTimeResidual + clientPredictTime >= USERCMD_MSEC`: `SendUsercmdsToServer`, followed by `++gameFrame` (`src/framework/async/AsyncClient.cpp:334-340`).
   - `SendUsercmdsToServer` builds one usercmd for the frame with `GetDirectUsercmd` (`src/framework/async/AsyncClient.cpp:502-506`).
   - It sends only when the channel's rate limit allows (`src/framework/async/AsyncClient.cpp:508-510`), and each message carries the newest `net_clientUsercmdBackup` commands, default 5 (`src/framework/async/AsyncClient.cpp:512-516`; `src/framework/async/AsyncNetwork.cpp:56`).
5. **Prediction.** `predictedGameFrame` is clamped to the usercmd backup (`src/framework/async/AsyncClient.cpp:341-343`), then `game->ClientPrediction` runs for every frame up to `gameFrame` (`src/framework/async/AsyncClient.cpp:344-348`).
6. `ProcessReliableServerMessages` (`src/framework/async/AsyncClient.cpp:349`).
7. **Extra-view update.** Only when no tick ran this frame (`gameFrame == startFrame`): `GetDirectUsercmd`, `GetGameTimeLeft`, then `game->ClientUpdateView` (`src/framework/async/AsyncClient.cpp:351-355`).
8. In `CS_CONNECTED` (not yet in game), `SendUsercmdsToServer` builds a usercmd every frame without advancing `gameFrame`, and sends it only when the channel is ready (`src/framework/async/AsyncClient.cpp:356-359`, `src/framework/async/AsyncClient.cpp:508-510`). Each build runs the game callbacks of §5.

`Session::Frame` is skipped (`src/framework/Common.cpp:1063`), and `UpdateScreen` draws (`src/framework/Common.cpp:1068`).

Provenance: the `ClientUpdateView` call, `GetGameTimeLeft` and the client branch of `Com_GetGameTimeLeft` were added by OE in 8191cae (2026-09-21). jmarshall's `AsyncClient` has none of them.

**What a client draws of remote players today.**
- Relayed usercmds are stored per frame, up to `MAX_USERCMD_RELAY` (`src/framework/async/AsyncNetwork.h:56`; `src/framework/async/AsyncClient.cpp:381-404`). The frames after the relayed ones are cleared, so prediction duplicates the last relayed command (`src/framework/async/AsyncClient.cpp:398-404`, `src/framework/async/AsyncClient.cpp:423-431`).
- Remote players think and move once per predicted tick (`game repo: src/game/Player.cpp:6691`, `game repo: src/game/Player.cpp:6743`). Prediction-error decay advances only on new frames (`game repo: src/game/PredictionErrorDecay.cpp:357-360`).
- They are presented once per tick (`game repo: src/game/Player.cpp:6862-6867`). There is no snapshot interpolation.
- Nothing updates their render entities between ticks, because the remote-entity producer is compiled out (§6.6).
- At caps 60/120/144/240 they therefore hold still for about 2/4/4.8/8 rendered frames *(derived)*.

### 4.3 Listen host (`+spawnServer` in the client binary)

- `localClientNum` is `net_serverDedicated ? -1 : 0` (`src/framework/async/AsyncServer.cpp:226`). The client binary defaults `net_serverDedicated` to 0 (`src/framework/async/AsyncNetwork.cpp:43`).
- An async session is active, so `Session::Frame` is skipped (`src/framework/Common.cpp:1063`).

`idAsyncServer::RunFrame` (`src/framework/async/AsyncServer.cpp:421-473`):

1. `UpdateTime( 100 )` (`src/framework/async/AsyncServer.cpp:426`; body `src/framework/async/AsyncServer.cpp:1646-1658`).
2. **Socket servicing.** `ProcessConnectionLessMessages` (`src/framework/async/AsyncServer.cpp:427`; body `src/framework/async/AsyncServer.cpp:475-485`) reads every packet on the server port and passes it to `ProcessMessage` (`src/framework/async/AsyncServer.cpp:1557-1594`):
   - connectionless packets go to `ConnectionlessMessage` (`src/framework/async/AsyncServer.cpp:1560-1562`);
   - other packets are matched to a client slot by client id and address (`src/framework/async/AsyncServer.cpp:1569-1585`), then `channel.Process`, `ProcessReliableClientMessages` (`src/framework/async/AsyncServer.cpp:1590`) and `ProcessUnreliableClientMessage` (`src/framework/async/AsyncServer.cpp:1591`; body `src/framework/async/AsyncServer.cpp:1176-1265`).
   
   A usercmd message:
   - applies the snapshot the client acknowledged, if it is newer than the last acknowledged one, and queues `SERVER_RELIABLE_MESSAGE_APPLYSNAPSHOT` for it (`src/framework/async/AsyncServer.cpp:1220-1228`, `src/framework/async/AsyncServer.cpp:921-929`);
   - is dropped when the client's frame is more than `MAX_USERCMD_BACKUP` from the server's (`src/framework/async/AsyncServer.cpp:1230-1233`);
   - otherwise stores each usercmd in `userCmds[frame][client]` (`src/framework/async/AsyncServer.cpp:1235-1257`) and records the client's frame and prediction lead (`src/framework/async/AsyncServer.cpp:1207-1209`, `src/framework/async/AsyncServer.cpp:1259-1262`).
   
   Packets are read before the tick loop, so a command that arrives this host frame is used by this frame's ticks if its frame has not run yet *(inferred from the order)*.
3. `CheckClientTimeouts` (`src/framework/async/AsyncServer.cpp:428`; body `src/framework/async/AsyncServer.cpp:835-846`) drops a remote client silent for `net_serverClientTimeout` (40 s) and clears a zombie slot after `net_serverZombieTimeout` (5 s) (`src/framework/async/AsyncNetwork.cpp:49-50`).
4. If no map is spawned, the residual is zeroed and the function returns (`src/framework/async/AsyncServer.cpp:432-435`). Otherwise `gameTimeResidual += delta` (`src/framework/async/AsyncServer.cpp:436`).
5. User-info changes made by the host are applied (`src/framework/async/AsyncServer.cpp:438-446`).
6. **Server steps.** While `gameTimeResidual >= USERCMD_MSEC` (`src/framework/async/AsyncServer.cpp:448-456`):
   - `DuplicateUsercmds` fills each client's slot whose command has not arrived with the previous command, and counts it (`src/framework/async/AsyncServer.cpp:623-637`; `src/framework/async/UsercmdCodec.cpp:90-109`);
   - `LocalClientInput` puts `GetDirectUsercmd` into the host's slot (`src/framework/async/AsyncServer.cpp:821-833`; call at `src/framework/async/AsyncServer.cpp:826`);
   - `game->RunFrame( userCmds[gameFrame], USERCMD_MSEC )`;
   - `++gameFrame`.
7. **Publication to remote clients** (`src/framework/async/AsyncServer.cpp:458-471`); see §4.5. The local client and bots are skipped (`src/framework/async/AsyncServer.cpp:460`).
8. `MasterHeartbeat` (`src/framework/async/AsyncServer.cpp:472`).

Nothing on the host calls `ClientUpdateView`, and nothing builds a usercmd between ticks. `UpdateScreen` draws every frame (`src/framework/Common.cpp:1068`). The pacer takes the client branch, because `IsDedicated()` reads `net_serverDedicated` (`src/framework/async/NetworkSystem.cpp:304-306`).

Consequences, from the code:
- **Every host frame is a base draw.**
  - `idGameLocal::RunFrame` clears `unlock.unlockedDraw` (`game repo: src/game/Game_local.cpp:5258`) and nothing sets it.
  - The base draw rewinds only the origin: the camera origin is drawn at `originlog[n-1]` while the axis stays at tick n (`game repo: src/game/PlayerView.cpp:880-889`). Both step at 30 Hz; the origin is one tick behind, the axis is not.
- **The host camera turns at 30 Hz.** Mouse deltas wait in `mouseDx`/`mouseDy` until `MakeCurrent`/`MouseMove` runs (`src/framework/UsercmdGen.cpp:416-490`, `src/framework/UsercmdGen.cpp:548-601`). On the host the only caller is `LocalClientInput`, once per tick.
  - Adding `ClientUpdateView` alone would not fix this. The host also needs a per-frame `GetDirectUsercmd`, as single player does (`src/framework/Session.cpp:343`).
- **The SDK expected unlocked frames on the authoritative side.** SDK `idGameLocal::RunFrame` sets `canUnlockFrames` from the local player and clears `unlockedDraw` (`SDK: game/Game_local.cpp:4222-4231`; unchanged at `game repo: src/game/Game_local.cpp:5250-5259`).

Provenance: jmarshall's `AsyncServer::RunFrame` is already non-blocking, has `LocalClientInput`, and has no `ClientUpdateView`.

### 4.4 Dedicated server, and a headless client in the dedicated binary

- In the dedicated binary `net_serverDedicated` is ROM 1 (`src/framework/async/AsyncNetwork.cpp:39-41`), so `localClientNum` is -1 and no usercmds are built.
- Ticks run as in §4.3 steps 1-8. Then `SyncRenderSystem` runs (`src/framework/Common.cpp:1065-1067`), and `UpdateScreen` returns without drawing (`src/framework/Session.cpp:219`; `src/sys/posix/posix_main.cpp:425-432`).
- **Pacing.**
  - The pacer sleeps the whole remainder of the frame at `com_maxFPS` (default 60) (`src/framework/Common.cpp:1098-1102`), and the host loop adds `Sys_Sleep( 1 )` (`src/sys/posix/posix_main.cpp:660-664`).
  - Ticks therefore run on a wall-clock grid of about 16.7 ms. Game time stays exact, but tick and snapshot send times jitter by up to one frame *(derived)*.
- `DoClientSideStuff()` is false in the dedicated binary (`game repo: src/game/Game_local.cpp:4829-4831`). With no local player, `canUnlockFrames` is false (`game repo: src/game/Game_local.cpp:5251-5256`).
- A headless client (dedicated binary with `+connect`) runs the §4.2 path, including `ClientUpdateView`, and draws nothing.

### 4.5 Committed-state publication (server)

Here "committed state" is the server's game state after the last tick a host frame ran. The server publishes it in snapshots written only after its tick loop, at most one per host frame and client (`src/framework/async/AsyncServer.cpp:458-471`). Reliable game messages sent during a tick are only queued on the client's channel (`src/framework/async/AsyncServer.cpp:494-513`, `src/framework/async/AsyncServer.cpp:1628-1644`) and leave with the next unreliable message. The listen host's own client and bots get nothing (`src/framework/async/AsyncServer.cpp:460`, `src/framework/async/AsyncServer.cpp:1632-1640`); the host's client shares the server's game.

For each remote client in the loop:
1. **`SendSnapshotToClient`** (`src/framework/async/AsyncServer.cpp:1096-1174`).
   - A client still in `SCS_CONNECTED` gets its game init once and no snapshot (`src/framework/async/AsyncServer.cpp:1101-1109`).
   - Otherwise a snapshot goes out only when all of these hold:
     - the client is in the game and not loading a map (`src/framework/async/AsyncServer.cpp:1110-1112`);
     - at least `net_serverSnapshotDelay` ms have passed since its last snapshot (`src/framework/async/AsyncServer.cpp:1113-1115`);
     - the channel's rate limit allows a send and no fragments are pending (`src/framework/async/AsyncServer.cpp:1116-1118`).
   - `game->ServerWriteSnapshot` (`src/framework/async/AsyncServer.cpp:1135`; `game repo: src/game/Game_network.cpp:1665`) writes entity state as of the last tick run, plus the relayed usercmds of other clients (`game repo: src/game/Game_network.cpp:1584` → `src/framework/async/AsyncServer.cpp:1672-1689`). At most `net_serverMaxUsercmdRelay` commands are relayed, default 5 (`src/framework/async/AsyncNetwork.cpp:48`).
   - The header carries `gameFrame` and `gameTime` as they are after the loop, that is the next frame to run, plus the duplicated-usercmd count and `aheadOfServer` (`src/framework/async/AsyncServer.cpp:1143-1157`). The client restarts prediction from that frame (§4.2).
   - `lastSnapshotTime` is set to this host frame's `realTime` (`src/framework/async/AsyncServer.cpp:1172`), even when the channel refused the message (`src/framework/async/AsyncServer.cpp:1165-1169`).
2. **`SendEmptyToClient( i, false )`** when no snapshot went out (`src/framework/async/AsyncServer.cpp:463-468`). It sends an empty unreliable message at most every 500 ms (`SERVER_EMPTY_RESEND_MSEC`, `src/framework/async/AsyncServer.cpp:17`; `src/framework/async/AsyncServer.cpp:952-968`). Queued reliable messages, `APPLYSNAPSHOT` included, travel only inside unreliable messages (comment at `src/framework/async/AsyncServer.cpp:464-466`).
3. `ProcessReliableClientMessages` and `ApplyPendingUserInfo` (`src/framework/async/AsyncServer.cpp:469-470`).

Timing *(derived from the code, not measured)*:
- The delay is checked against the host frame's `realTime`, so a snapshot goes out on the first host frame at least 50 ms after the previous one. The interval is the delay rounded up to the host-frame grid. On a dedicated server paced at 60 fps that is 3 or 4 frames, about 50-67 ms.
- Ticks (33 ms) and snapshots are not aligned, so consecutive snapshots are usually 1 or 2 ticks apart; 3 is possible (a residual of up to 32 ms plus a 67 ms interval), and a frame overrun widens it further *(derived)*.
- Snapshot content is always post-tick state. Rendering changes what is published only through state that `ServerWriteSnapshot` reads; `GetAORView` is one such path (`game repo: src/game/Game_network.cpp:1684`; see §6.9).

### 4.6 Summary

| | Single player | Network client | Listen host | Dedicated server |
|---|---|---|---|---|
| Tick driver | `Session::Frame` | `idAsyncClient::RunFrame` | `idAsyncServer::RunFrame` | `idAsyncServer::RunFrame` |
| Tick clock | `mainThreadTicResidual` | `gameTimeResidual + clientPredictTime` | `gameTimeResidual` | `gameTimeResidual` |
| Socket servicing | none | before the ticks | before the ticks | before the ticks |
| Usercmd built | every tick and every render-only frame | every tick and every render-only frame (every frame before the first snapshot) | every tick only | never |
| State published | none | none | after the ticks (§4.5) | after the ticks (§4.5) |
| `ClientUpdateView` | frames with no tick | frames with no tick | never | never |
| Frames that ran a tick | base draw | base draw | base draw (so is every other frame) | no draw |
| Pacer wait | sleep, then spin | sleep, then spin | sleep, then spin | sleep only |
| `GuiFrameEvents` per frame | yes (`src/framework/Session.cpp:310`) | yes, since 2026-09-25 (`src/framework/Common.cpp:1073`) | yes, since 2026-09-25 (`src/framework/Common.cpp:1073`) | no |

## 5. Input sampling, and the aim shown versus the aim fired

- **What a sample runs.** `GetDirectUsercmd` (`src/framework/UsercmdGen.cpp:657-666`) runs `InitCurrent` (`src/framework/UsercmdGen.cpp:536-546`) and `MakeCurrent` (`src/framework/UsercmdGen.cpp:548-601`). `MakeCurrent` runs, in order:
  - `TurnTimeScale` (`src/framework/UsercmdGen.cpp:362-372`);
  - `AdjustAngles`;
  - `CmdButtons`;
  - `KeyMove`, which calls `game->KeyMove` (`src/framework/UsercmdGen.cpp:409`);
  - `MouseMove` (`src/framework/UsercmdGen.cpp:416-490`), which calls `game->GetSensitivity` and `game->MouseMove` (`src/framework/UsercmdGen.cpp:470`, `src/framework/UsercmdGen.cpp:487`) and folds `mouseDx`/`mouseDy` into the persistent `viewangles`;
  - `ControllerMove` (`src/framework/UsercmdGen.cpp:563`), which calls `game->ControllerMove` (`src/framework/UsercmdGen.cpp:510`);
  - `game->UsercommandCallback( cmd )`, taking back any angles the game changed (`src/framework/UsercmdGen.cpp:581-597`).
  
  The game calls run on every sample, because `doGameCallback` defaults to true (`game repo: src/framework/UsercmdGen.h:149`) and no caller passes false.
- **Callers.**
  - Single player: each tick (`src/framework/Session.cpp:555`) and each render-only frame (`src/framework/Session.cpp:343`).
  - Client: each tick (`src/framework/async/AsyncClient.cpp:504`), each render-only frame (`src/framework/async/AsyncClient.cpp:352`), and every frame in `CS_CONNECTED` (`src/framework/async/AsyncClient.cpp:357`).
  - Listen host: each tick only (`src/framework/async/AsyncServer.cpp:826`).
- **Render-only samples are not side-effect-free.** The command itself is discarded, including mouse-strafe motion written into `cmd.rightmove`/`cmd.forwardmove` (`src/framework/UsercmdGen.cpp:477`, `src/framework/UsercmdGen.cpp:481`). What persists:
  - `viewangles`, so the angle change reaches the next tick's command;
  - the consumed `mouseDx`/`mouseDy` (`src/framework/UsercmdGen.cpp:451-452`) and the advanced `m_smooth` history (`src/framework/UsercmdGen.cpp:417-442`);
  - `TurnTimeScale`'s `lastTurnUsec` (`src/framework/UsercmdGen.cpp:370`);
  - a scheduled impulse, which is folded into the persistent `flags` and `impulse` (`src/framework/UsercmdGen.cpp:536-543`, `src/framework/UsercmdGen.cpp:577-579`), so the next tick's command still carries it *(inferred)*;
  - in the game, `idPlayer::UsercommandCallback` (`game repo: src/game/Player.cpp:2130-2186`):
    - updates `clientButtons` and `clientOldButtons` (`game repo: src/game/Player.cpp:2135-2139`);
    - enables or disables VOIP (`game repo: src/game/Player.cpp:2141-2151`);
    - runs the active and passive HUD modules' `UsercommandCallback` (`game repo: src/game/Player.cpp:2153-2161`);
    - clears `playerFlags.clientWeaponLock` when neither attack button is held (`game repo: src/game/Player.cpp:2163-2171`). A render-only sample taken while attack is briefly released can therefore clear the lock, and a later tick then keeps attack in its command. That is a frame-rate dependence of fired input *(inferred)*. CHARTER §7.5 requires buttons and impulses to stay tick-bound.
- **Other frame-rate dependence.**
  - The `m_smooth` history advances on every call, so its smoothing window is counted in samples, not time (`src/framework/UsercmdGen.cpp:417-442`).
  - `TurnTimeScale` makes key and controller turning a rate per second. This was an OE change of 2026-09-24; the comment at `src/framework/UsercmdGen.cpp:345-356` explains it.
- **Stale comments.** Four places still describe input or common-services workers that no meson build has:
  - the `com_asyncInput` help text, "input is sampled by the dedicated input thread" (ROM; `src/framework/Common.cpp:76`);
  - the comment in `idCommonLocal::Async`, "the platform owns the dedicated input and sound workers" (`src/framework/Common.cpp:896-898`);
  - `src/framework/Session.cpp:553-554`;
  - `src/framework/UsercmdGen.cpp:650-651`.
- **Aim shown on a render-only frame.** `ClientUpdateView` shows `refAngles + ( cmd.angles - p->usercmd.angles )`, where `cmd` is the newest direct usercmd (`game repo: src/game/Game_local.cpp:6050-6072`). The delta is passed through `p->MouseMove` and clamped to `unlock.minAngles`/`maxAngles`. The SDK comment there says "This doesn't take all clamps/rate limits into account" (`game repo: src/game/Game_local.cpp:6052`).
- **Aim fired.** The next tick uses the usercmd built at that tick. Because `viewangles` accumulates, its angles include every mouse delta consumed by render-only samples plus any input that arrived after the last draw. The fired aim is therefore the last shown aim plus newer input, subject to the clamps the SDK comment calls incomplete *(inferred)*.
- **Aim shown on a base draw.** The view axis is at tick n while the origin is at n-1 (`game repo: src/game/PlayerView.cpp:886-889`).
- **Scripts.** During a tick, scripts may read the view that the last render frame wrote (§6.9).

## 6. The frame-unlock system in the game module

### 6.1 State: `idGameLocal::unlock` and two neighbouring members (`game repo: src/game/Game_local.h:720-742`)

`com_unlockFPS` and `interpolateEntities` are members of `idGameLocal` itself; the rest are fields of `struct idUnlock unlock` (`game repo: src/game/Game_local.h:724-742`).

| Member | Line | Origin | Purpose |
|---|---|---|---|
| `idGameLocal::com_unlockFPS` (pointer) | `game repo: src/game/Game_local.h:721` | SDK | Set to NULL in the constructor (`game repo: src/game/Game_local.cpp:1666`) and found at init (`game repo: src/game/Game_local.cpp:2481`) |
| `idGameLocal::interpolateEntities` | `game repo: src/game/Game_local.h:722` | SDK | Entities to interpolate on render-only frames; used as `gameLocal.interpolateEntities` |
| `unlock.canUnlockFrames` | `game repo: src/game/Game_local.h:725` | SDK | Recomputed at the start of every tick |
| `unlock.unlockedDraw` | `game repo: src/game/Game_local.h:726` | SDK | Set by `ClientUpdateView`; cleared when the next tick starts |
| `unlock.interpFraction` | `game repo: src/game/Game_local.h:727` | OE (79a0ea3) | Last sub-tick fraction; read by the OE vehicle views |
| `unlock.originlog[2]` | `game repo: src/game/Game_local.h:728` | SDK | View origin at ticks n and n-1, indexed by `framenum & 1` |
| `unlock.weaponOffsetLog[2]`, `weaponAxisLog[2]`, `weaponPoseValid[2]` | `game repo: src/game/Game_local.h:729-731` | jmarshall | Viewmodel pose in view space for the last two ticks |
| `unlock.minAngles` … `weaponGUIAxis` | `game repo: src/game/Game_local.h:732-741` | SDK | Per-cycle reference data and the pitch clamp |

`idGameLocal::Clear` clears `interpolateEntities` (`game repo: src/game/Game_local.cpp:1702`), zeroes the struct and sets the angle limits to ±180 (`game repo: src/game/Game_local.cpp:1797-1799`). OE also sets `unlock.interpFraction = 0.0f` (`game repo: src/game/Game_local.cpp:1759`), which the later memset makes redundant.

### 6.2 Tick side

- **Server, listen host and single player.** `idGameLocal::RunFrame` sets `canUnlockFrames = localPlayer ? localPlayer->IsFPSUnlock() : false`, clears `unlockedDraw` and clears `interpolateEntities` (`game repo: src/game/Game_local.cpp:5250-5259`; `SDK: game/Game_local.cpp:4222-4231`).
- **Client.** `idGameLocal::ClientPrediction` does the same (`game repo: src/game/Game_network.cpp:3793-3803`; `SDK: game/Game_network.cpp:3628-3638`). It runs for every predicted frame, including re-predicted ones.
  - A re-predicted frame sets `isNewFrame = false` (`game repo: src/game/Game_network.cpp:3834-3848`), and `idEntity::Present` returns early when `!isNewFrame` (`game repo: src/game/Entity.cpp:1727-1729`).
- **Pose log.** After `CalculateShake` for the local view player (`game repo: src/game/Game_local.cpp:5464-5482`, `game repo: src/game/Game_network.cpp:3973-3991`):
  - `originlog[framenum & 1] = renderView->vieworg`. This is SDK (`SDK: game/Game_local.cpp:4404`; `SDK: game/Game_network.cpp:3814`).
  - The weapon's view-space offset and axis, plus a valid flag. This part is jmarshall's; the SDK logged only the origin.
  - On the server and single-player path the log is written every tick, including while `canUnlockFrames` is false.
  - On a client it sits inside `if ( isNewFrame )` (`game repo: src/game/Game_network.cpp:3945`), so re-predicted frames do not write `originlog` or the weapon logs. After a snapshot correction the logged poses therefore stay those of the first prediction until the next new tick *(inferred)*.

### 6.3 `idPlayer::IsFPSUnlock` (`game repo: src/game/Player.cpp:8773-8829`; `SDK: game/Player.cpp:8759-8815`)

This is SDK code. The only OE change is a NULL check on the deploy menu (`game repo: src/game/Player.cpp:8808`). It returns false, in order, when:
1. `this` is not the local player (`game repo: src/game/Player.cpp:8778`);
2. there is no local view player (`game repo: src/game/Player.cpp:8782`);
3. the active viewer is someone else (`game repo: src/game/Player.cpp:8786`);
4. a teleport is in progress (`game repo: src/game/Player.cpp:8790`);
5. **the view player has a proxy (`game repo: src/game/Player.cpp:8794-8796`; `SDK: game/Player.cpp:8780-8782`).** A proxy is any vehicle seat, the jetpack, or a scripted usable such as a mounted gun. This is the "vehicle proxy gate";
6. a remote camera or cinematic is active outside noclip (`game repo: src/game/Player.cpp:8798-8806`);
7. the deploy menu is open (`game repo: src/game/Player.cpp:8808`);
8. `pm_thirdPerson` is set (`game repo: src/game/Player.cpp:8812`);
9. the player is dead or in limbo (`game repo: src/game/Player.cpp:8816-8821`);
10. the player is prone (`game repo: src/game/Player.cpp:8824`).

The SDK gives no reason for the proxy gate. From the code, the unlock path can only produce an on-foot first-person view:
- it takes `refAngles` from the tick's `renderView`;
- it uses a single `originlog` lerp;
- it re-poses the viewmodel.

Vehicle views come from `sdVehicleView` subclasses, whose clamps live in `proxy->ClampViewAngles`, not in `unlock.minAngles`/`maxAngles`. The SDK producer comment also warns about "an entity we're bound to however (like riding the icarus)" (`SDK: game/Entity.cpp:1739`).

### 6.4 Base draw (`game repo: src/game/PlayerView.cpp:880-936`)

A base draw is a draw with `unlockedDraw` false: every frame that ran a tick, and every listen-host frame. The rewind runs when `com_unlockFPS && !unlockedDraw && canUnlockFrames && g_unlock_updateViewpos && g_unlock_viewStyle == 1` (`game repo: src/game/PlayerView.cpp:886-888`). The logic is SDK; jmarshall added the `com_unlockFPS != NULL` check.
- `currentView.vieworg = originlog[n-1]` (`game repo: src/game/PlayerView.cpp:889`). Only the origin moves back. The axis stays at tick n.
- **Weapon (jmarshall rewrite).**
  - With a valid previous log slot, the weapon is placed at `vieworg(n-1) + weaponOffsetLog[n-1] · viewaxis`. Otherwise it falls back to subtracting the origin delta.
  - It is then presented, the bound GUI is re-attached rigidly, and bound effects get `ClientUpdateView` (`game repo: src/game/PlayerView.cpp:890-931`).
  - The SDK instead subtracted `originlog[n] - originlog[n-1]` from the weapon and GUI origins (`SDK: game/PlayerView.cpp:886-910`).
- `ShakeAxis` is applied afterwards (`game repo: src/game/PlayerView.cpp:936`).

### 6.5 Render-only frame: `idGameLocal::ClientUpdateView` (`game repo: src/game/Game_local.cpp:5957-6195`; `SDK: game/Game_local.cpp:4874-5065`)

1. Sets `unlock.unlockedDraw = true` **before** any gate (`game repo: src/game/Game_local.cpp:5960`). The flag stays true until the next `RunFrame` or `ClientPrediction`, even when the function returns at the next step, for example in a vehicle.
2. Returns when:
   - `!canUnlockFrames` (`game repo: src/game/Game_local.cpp:5962-5964`);
   - there is no active viewer, or the viewer is being briefed (`game repo: src/game/Game_local.cpp:5966-5973`);
   - `timeLeft == USERCMD_MSEC` (`game repo: src/game/Game_local.cpp:5977-5980`).
3. **Cycle capture** on the first render-only frame after each tick (`framenum != lastFullDrawFrame`, `game repo: src/game/Game_local.cpp:5987-6022`):
   - `refAngles` from the tick's `renderView.viewaxis`;
   - `viewOrigin = originlog[n-1]` when `viewStyle` is 1;
   - the weapon's render pose, which the base draw has already moved to n-1 (comment at `game repo: src/game/Game_local.cpp:5998`);
   - the pose of the one GUI bound to the weapon.
4. Computes `interpFraction`. OE stores it in `unlock.interpFraction` (`game repo: src/game/Game_local.cpp:6024-6025`).
5. **OE proxy branch** (`game repo: src/game/Game_local.cpp:6027-6047`, commit 79a0ea3). It is reachable only on the render-only frames of the tick in which the local player gains a transport proxy: `canUnlockFrames` is latched at the start of each tick (`game repo: src/game/Game_local.cpp:5251-5258`), so the `game repo: src/game/Game_local.cpp:5962` gate still passes until the next tick; after that §6.3 condition 5 blocks it. See §6.8.
6. **Angles (SDK).** When `g_unlock_updateAngles` is set and the game is not paused (`game repo: src/game/Game_local.cpp:6050-6072`): extrapolates with the newest input, clamps, and writes `p->renderView.viewaxis` (`game repo: src/game/Game_local.cpp:6070`).
7. **Origin (SDK).** When `g_unlock_updateViewpos` is set (`game repo: src/game/Game_local.cpp:6075-6097`):
   - `viewStyle` 0 extrapolates from tick n (`game repo: src/game/Game_local.cpp:6083`);
   - `viewStyle` 1 (the default) interpolates from n-1 to n (`game repo: src/game/Game_local.cpp:6092`), one tick (33 ms) behind.
   - Either way the result is written to `p->renderView.vieworg`.
8. **Scope sight (SDK)** (`game repo: src/game/Game_local.cpp:6101-6113`).
9. **Weapon (jmarshall rewrite)** (`game repo: src/game/Game_local.cpp:6120-6178`):
   - lerps and slerps the view-space weapon log, or falls back to the SDK-style rigid transform;
   - re-attaches the GUI rigidly;
   - updates bound effects;
   - `BecomeActive( TH_UPDATEVISUALS )` and `Present()`.
   - The SDK rotated and translated the n-1 pose instead (`SDK: game/Game_local.cpp:5008-5048`).
10. **Remote entities (SDK, byte-identical)** (`game repo: src/game/Game_local.cpp:6180-6194`; `SDK: game/Game_local.cpp:5050-5064`):
    - for each member of `interpolateEntities` except the viewer, `renderEntity.origin = lerp( history[n-1], history[n], f )`, then `Present()`;
    - only the origin changes, not the axis;
    - the list is always empty today (§6.6).

The four `g_unlock_*` CVars used above default to 1 and match the SDK (`game repo: src/game/gamesys/SysCvar.cpp:510-513`).

### 6.6 Remote-entity interpolation: the `Entity::Present` producer and its consumer

**Producer (compiled out).** In `idEntity::Present` (`game repo: src/game/Entity.cpp:1726-1791`), the block at `game repo: src/game/Entity.cpp:1737-1758` sits inside `#if 0 // jmarshall - unlock fps` (`game repo: src/game/Entity.cpp:1736`) … `#endif` (`game repo: src/game/Entity.cpp:1759`). Those two lines, which replace blank lines of the SDK, are the only difference from `SDK: game/Entity.cpp:1737-1758` for this feature. jmarshall's tree has the same `#if 0`. His repository has three commits (6de0697 "Initial Commit.", 961cb21, f2b655c); `Entity.cpp`'s history is only the initial commit, which records no reason.

The SDK block runs when all of these hold:
- the frame is new and the entity has `TH_UPDATEVISUALS` (`game repo: src/game/Entity.cpp:1727-1733`);
- `com_unlockFPS` and `g_unlock_interpolateMoving` are set;
- `unlockedDraw` is false and `canUnlockFrames` is true;
- the entity has `fl.unlockInterpolate`;
- `AOR_INHIBIT_IK` is clear.

It then:
1. links the entity into `interpolateEntities`;
2. on the first consecutive frame, sets both history slots to the current origin;
3. otherwise, stores the current origin in `history[framenum & 1]` and draws at `history[n-1]`, one tick behind, matching `viewStyle` 1.

Two further details:
- The producer dereferences `gameLocal.com_unlockFPS` without the NULL check that the base draw has (`game repo: src/game/Entity.cpp:1737`; compare `game repo: src/game/PlayerView.cpp:886`).
- The block's comment (`game repo: src/game/Entity.cpp:1751`) says repeated calls in one tick work. A second call in the same tick has a frame difference of 0, which takes the reset branch (`game repo: src/game/Entity.cpp:1747-1749`) *(inferred)*.

**Who is flagged** (all SDK, unchanged):

| Flagged | Where |
|---|---|
| Players | `game repo: src/game/Player.cpp:971` |
| Items | `game repo: src/game/Item.cpp:92` |
| Projectiles | `game repo: src/game/Projectile.cpp:276` |
| Transports, including the jetpack/Icarus | `game repo: src/game/vehicles/Transport.cpp:3601` |
| Script entities with `option_unlock_interpolate` | `game repo: src/game/ScriptEntity.cpp:1680` |

Doors never interpolate, because `idDoor::Present` does not call `idEntity::Present` (`game repo: src/game/misc/Door.cpp:1580-1595`). Weapons are not flagged.

**AOR_INHIBIT_IK.** The flag is set beyond the IK cutoff (`game repo: src/game/decls/DeclAOR.cpp:305-307`).
- On a client it is recomputed against the viewer when each snapshot is read (`game repo: src/game/Game_network.cpp:2563-2577`).
- On a server it is recomputed per remote client while each snapshot is written (`game repo: src/game/Game_network.cpp:1457-1462`).

**Consumer.** The consumer is the loop in §6.5 step 10. It is compiled and runs, but the list never has members, so `IsInterpolated()` is always false.

**What interpolates if the producer returns.**
- Interpolated: remote players, remote vehicles, projectiles, items and opted-in script entities.
- Not interpolated:
  - the local player's own body, because the consumer skips `e == p` (`game repo: src/game/Game_local.cpp:6184`);
  - doors and weapons;
  - anything beyond the AOR IK cutoff;
  - everything while `canUnlockFrames` is false.
- Only the origin is interpolated. The axis steps at 30 Hz, and skeletal animation is sampled at the unchanged `gameLocal.time` (`game repo: src/game/Entity.cpp:1766`, `game repo: src/game/Entity.cpp:1810`).

### 6.7 Client entities

- `rvClientEntity::ClientUpdateView` calls `UpdateBind( true )`, then `Present()` (`game repo: src/game/client/ClientEntity.cpp:119-122`). `rvClientEffect::ClientUpdateView` calls `UpdateBind( true )` and `UpdateEffectDef` (`game repo: src/game/client/ClientEffect.cpp:199-206`).
- While `unlockedDraw` is set, `idEntity::Present` calls `ClientUpdateView` on every bound client entity (`game repo: src/game/Entity.cpp:1783-1788`, SDK). The weapon's GUI and effects are therefore updated twice on a render-only frame: once here, once in the explicit loop of §6.5 step 9.

### 6.8 OE vehicle code behind the proxy gate (79a0ea3, a63e2bb)

All of the following is OE code. None of it is in the SDK or jmarshall.

**`sdTransport`**
- Chassis logs are recorded in `PostThink` (`game repo: src/game/vehicles/Transport.cpp:4404-4415`).
- `ClientUpdateInterpolation` (`game repo: src/game/vehicles/Transport.cpp:5222-5247`) lerps and slerps the chassis. It writes `renderEntity` **and `lastPushedOrigin`/`lastPushedAxis`** (`game repo: src/game/vehicles/Transport.cpp:5237-5240`), then presents the chassis and its occupants (`game repo: src/game/vehicles/Transport.cpp:5244-5246`).
- For most transports that `Present()` is `idEntity::Present`, because neither `sdTransport` nor its bases `sdScriptEntity` and `idAnimatedEntity` override it (`Present` is virtual, `game repo: src/game/Entity.h:380`). The jetpack overrides it: `sdJetPack::Present` calls `sdTransport::Present` and then rewrites `renderEntity` and `lastPushedOrigin`/`lastPushedAxis` from its own visuals (`game repo: src/game/vehicles/JetPack.cpp:407-424`). `idEntity::Present` copies `renderEntity` into `lastPushedOrigin`/`lastPushedAxis` again whenever `isNewFrame` is true (`game repo: src/game/Entity.cpp:1780-1781`). Removing the explicit writes at `game repo: src/game/vehicles/Transport.cpp:5239-5240` alone would not keep interpolated values out of `lastPushed*`.
- `UpdateModelTransform` returns early while `unlockedDraw` is set (`game repo: src/game/vehicles/Transport.cpp:5194-5196`). Since `unlockedDraw` stays set after a render-only frame, this also affects other callers until the next tick. On a client that window includes snapshot processing, which runs before `ClientPrediction` (§4.2), and `unlockedDraw` is set in a vehicle too (§6.5 step 1) *(inferred)*.

**`sdDampedVehicleView`** (`game repo: src/game/vehicles/VehicleView.cpp`)
- It logs the eye pose on full updates (`game repo: src/game/vehicles/VehicleView.cpp:539-550`, `game repo: src/game/vehicles/VehicleView.cpp:815-826`).
- It interpolates when `!fullUpdate && unlockedDraw` (`game repo: src/game/vehicles/VehicleView.cpp:556-568`, `game repo: src/game/vehicles/VehicleView.cpp:832-844`).

**`sdSmoothVehicleView::CalculateViewPos( !fullUpdate )`** (`game repo: src/game/vehicles/VehicleView.cpp:1169-1186`)
- It interpolates with `unlock.interpFraction` **with no `unlockedDraw` guard**, and writes the result back into `thirdPersonViewOrigin`/`thirdPersonViewAxes` and into its output parameters.
- The SDK version is a pure read (`SDK: game/vehicles/VehicleView.cpp:1076-1081`).
- `_Locked` now delegates its `!fullUpdate` case to this function (`game repo: src/game/vehicles/VehicleView.cpp:1547-1550`). The SDK read the cached pose directly (`SDK: game/vehicles/VehicleView.cpp:1411-1415`).

**What is not covered**
- The Icarus view uses the raw physics origin (`game repo: src/game/vehicles/VehicleView.cpp:1795`).
- `sdJetPack::Present` replaces `renderEntity` with its visuals' pose (`game repo: src/game/vehicles/JetPack.cpp:417-423`).
- Non-transport proxies, such as mounted guns, would fall through to the on-foot path.

**Defects if the gate were lifted as-is** *(inferred)*
- There would be no sub-tick mouse look in a vehicle: the branch ignores `cmd`.
- The chassis and camera axis would step back one tick at every tick boundary, because base draws rewind only the origin (`game repo: src/game/PlayerView.cpp:889`).
- Remote entities would stop interpolating, because the branch returns (`game repo: src/game/Game_local.cpp:6046`) before the loop at `game repo: src/game/Game_local.cpp:6180`.
- Presentation would leak into the tick: `lastPushed*` feeds the next full update of the smooth view (`game repo: src/game/vehicles/VehicleView.cpp:1205-1206`), through both writes described above.

### 6.9 Where presentation reaches tick state

These paths let a render frame change what the next tick, a snapshot or AoR code reads. That conflicts with the M5 rule that presentation never changes authoritative tick behaviour.

- **Live today (OE): the `sdSmoothVehicleView` `!fullUpdate` branch** (§6.8). It is reached through `idPlayer::GetAORView` → `CalculateFirstPersonView( false )` (`game repo: src/game/Player.cpp:15184`) → `UpdateViewPos` (`game repo: src/game/Player.cpp:8682`; `game repo: src/game/vehicles/Transport.cpp:518`), from:
  - server snapshot writes (`game repo: src/game/AORManager.cpp:119` ← `game repo: src/game/Game_network.cpp:1684`);
  - client AoR setup (`game repo: src/game/Game_network.cpp:2563` → `game repo: src/game/Game_network.cpp:2625`; `game repo: src/game/client/ClientMoveable.cpp:155`);
  - the antilag system (`game repo: src/game/AntiLag.cpp:803`).

  What it feeds, and when:
  - The next full update takes the values it writes as `state.oldOrigin`/`oldAxis` (`game repo: src/game/vehicles/VehicleView.cpp:1197-1200`).
  - On a dedicated server `interpFraction` is always 0, because only `idGameLocal::Clear` and `ClientUpdateView` write it (`game repo: src/game/Game_local.cpp:1759`, `game repo: src/game/Game_local.cpp:1797`, `game repo: src/game/Game_local.cpp:6025`). When both log slots are valid, the branch therefore writes the previous tick's logged camera (`game repo: src/game/vehicles/VehicleView.cpp:1172-1175`).
  - On a client it uses the last on-foot fraction, which depends on the frame cap: in a vehicle `ClientUpdateView` returns before `game repo: src/game/Game_local.cpp:6025`, except on the render-only frames of the tick in which the player entered it (see §6.5 step 5).
- **Live today on clients: the damped view while `unlockedDraw` is still set.** The main client route is snapshot receipt. `ClientReadSnapshot` runs from the engine's packet loop (`src/framework/async/AsyncClient.cpp:315-320`, `src/framework/async/AsyncClient.cpp:633`), before the tick loop and before `ClientPrediction` clears `unlockedDraw`. It calls `SetupClientAoR` → `GetAORView` (`game repo: src/game/Game_network.cpp:2563`, `game repo: src/game/Game_network.cpp:2625`; `game repo: src/game/AORManager.cpp:119`). After a render-only frame `unlockedDraw` is still true, in a vehicle too, because `ClientUpdateView` sets it before its `canUnlockFrames` gate (`game repo: src/game/Game_local.cpp:5960-5964`).
  - `lastReturnedAxis` has one reader: `sdDampedVehicleView_FreePivot::ClampViewAngles` (`game repo: src/game/vehicles/VehicleView.cpp:727-730`), called during `UpdateViewAngles` (`game repo: src/game/Player.cpp:5642`). `sdDampedVehicleView_FreePivot` overrides `CalculateViewPos` (`game repo: src/game/vehicles/VehicleView.cpp:802-861`), so the value it reads is the one written at `game repo: src/game/vehicles/VehicleView.cpp:860` (SDK code, `SDK: game/vehicles/VehicleView.cpp:768`), after OE's interpolation of the eye axis when `!fullUpdate && unlockedDraw` (`game repo: src/game/vehicles/VehicleView.cpp:832-844`), with the stale fraction above. Predicted view angles in a free-pivot seat can therefore depend on render frames.
  - The base `sdDampedVehicleView::CalculateViewPos` has the same OE interpolation (`game repo: src/game/vehicles/VehicleView.cpp:553-568`) and also writes `lastReturnedAxis` (`game repo: src/game/vehicles/VehicleView.cpp:587`, SDK code at `SDK: game/vehicles/VehicleView.cpp:524`), but nothing reads that write.
  - Status: plausible, not run. The single-player replay cannot reach it: `SIM_VIEW` compares `lastReturnedAxis` (`vret`), but this route runs at snapshot time on a network client, so only a network-client replay would catch it.
- **SDK-original: `ClientUpdateView` writes `p->renderView`** (`game repo: src/game/Game_local.cpp:6070`, `game repo: src/game/Game_local.cpp:6083`, `game repo: src/game/Game_local.cpp:6092`).
  - In `idPlayer::Think`, `UpdateScript()` (`game repo: src/game/Player.cpp:6793`) runs before `CalculateView()` (`game repo: src/game/Player.cpp:6803-6804`, only without a proxy). The script events `getRenderViewAngles` and `getViewOrigin` read `renderView` (`game repo: src/game/Player.cpp:9165-9177`). A script that runs in `UpdateScript` therefore reads the last render frame's view.
  - Two retail uses that look relevant are not affected today: `weapon_mounted::Fire` (`pak008: script/deployables/mount.script:305-306`) and `vehicle_weapon_stroybomb::Fire` (`pak008: script/vehicles/weapons/stroybomb.script:44`). Both run while the player has a proxy.
    - A mount seats its user with `user.enter( self )` (`pak008: script/deployables/mount.script:556`), which ends in `SetProxyEntity` (`game repo: src/game/Player.cpp:11324-11328`; `game repo: src/game/ScriptEntity.cpp:652`).
    - The stroybomb's user rides a transport.
    - With a proxy, `IsFPSUnlock` is false (`game repo: src/game/Player.cpp:8794-8796`), so `ClientUpdateView` returns before it writes `p->renderView` (`game repo: src/game/Game_local.cpp:5962-5964`). These scripts read the tick view, in the SDK and today.
    - The exception is the render-only frames of the tick in which the player entered, because `canUnlockFrames` is latched at the start of each tick (`game repo: src/game/Game_local.cpp:5251-5256`) *(inferred)*.
    - They become relevant if the proxy gate is lifted for such a seat.
  - On-foot scripts read these events too, for example `pak008: script/weapons/lightningpistol.script:298`, `pak008: script/player/player_context.script:855` and `pak008: script/items/landmine.script:157`. Whether any of them runs before `CalculateView` in a tick was not checked. Weapon updates run after it (`game repo: src/game/Player.cpp:6842`).
- **If the producer returns on an authoritative host** *(inferred)*.
  - It would rewrite `renderEntity.origin` and `lastPushedOrigin` of server entities during the tick.
  - Server code reads `renderEntity.origin`: bot vehicle steering (`game repo: src/game/botai/Bot.cpp:1825` and others) and `idPlayer::CheckTargetCanLock` (`game repo: src/game/Player.cpp:2802`). The latter runs on the server through `UpdateTargetLock` (`game repo: src/game/Player.cpp:2852`, called at `game repo: src/game/Player.cpp:6807-6810`).
  - On a listen host its selection also depends on `aorFlags`, which snapshot writing overwrites for each client (`game repo: src/game/Game_network.cpp:1457-1462`).

### 6.10 Provenance summary

| Site | SDK → jmarshall | jmarshall → OE |
|---|---|---|
| `ClientUpdateView` | weapon and GUI block rewritten | stores `interpFraction`; proxy branch (`game repo: src/game/Game_local.cpp:6027-6047`) |
| Pose log in `RunFrame` and `ClientPrediction` | weapon view-space log added | unrelated guards and determinism additions |
| `unlock` struct | three weapon log fields | `interpFraction` |
| `PlayerView::RenderPlayerView` base draw | NULL check; weapon and GUI rewrite | none in the unlock path |
| `IsFPSUnlock` | none | deploy-menu NULL check |
| `Entity::Present` producer | `#if 0` / `#endif` added | none |
| `sdTransport`, `VehicleView`, `TransportExtras` | none | all vehicle sub-tick code (§6.8) |
| Engine `ClientUpdateView` callers | single player only (`src/framework/Session.cpp:342-345`) | client (8191cae); none on the listen host |

## 7. Pacing, the unlock CVars and render submission

### 7.1 The pacer (`src/framework/Common.cpp:1073-1139`, OE 8191cae; sleep-only dedicated branch OE 007233d)

- **Target.** `targetFrameUsec = 1000000 / maxFPS` (`src/framework/Common.cpp:1091`), measured from where the previous frame's pacing ended (`lastFrameUsec`, `src/framework/Common.cpp:1087`, `src/framework/Common.cpp:1129`). Work time therefore counts toward the target.
- **Wait.**
  - Client: sleep until a margin before the frame is due, then spin. The margin is the lateness measured on recent sleeps plus 0.5 ms: it rises to a late wake at once (the stored lateness is clamped to 5.5 ms) and decays by 1/64 on every paced frame, including frames too short to sleep, so one late wake cannot leave every later frame spinning. Until 2026-09-25 it was a fixed 1.5 ms.
  - Dedicated: sleep only (`src/framework/Common.cpp:1098-1102`).
- **No catch-up.** An overrunning frame is not compensated (`src/framework/Common.cpp:1094-1096`, `src/framework/Common.cpp:1129`).
- **The pacer can pull the average below the cap.** When frame times vary around the target, short frames are padded up to the target and long frames are not compensated. The average rate then falls below both the cap and the uncapped rate. For example, alternating 5 ms and 12 ms frames at cap 120 average about 98 fps, against about 117 fps uncapped *(derived)*.
- **Sleeps wake late.** macOS wakes `sleep_for` about 2 ms after the requested time (measured 2026-09-25). With the old fixed 1.5 ms margin every paced client frame ran late: cap 60 gave 17.19 ms frames (58–59 fps). With a 3 ms margin, and with the adaptive margin, cap 60 holds 16.67 ms and cap 120 holds 8.33 ms (`FRAME_TIMING`, §7.6). The dedicated branch only sleeps (`src/framework/Common.cpp:1101`); its 30 Hz ticks do not need sub-millisecond pacing.
- **When every frame takes longer than the target**, the pacer never waits and cannot be the limiter. That is the case at caps 144 and 240 on this host, where each frame's own work takes one display refresh (§7.5, measured).
- **Not tick-aware.** The pacer ignores tick phase.

### 7.2 The pacing CVars

| CVar | Default and flags | Engine reads | Game reads | Retail meaning (evidence) |
|---|---|---|---|---|
| `com_unlockFPS` | 1; BOOL, ARCHIVE, PROFILE (`src/framework/Common.cpp:78-79`) | **none** | found at `game repo: src/game/Game_local.cpp:2481`; read at `game repo: src/game/PlayerView.cpp:886` and at `game repo: src/game/Entity.cpp:1737` (compiled out) | Help text: "allow renderer to go faster than game ticks". `pak000: loffy.cfg:134`: "Set to "0" for fixed fps, i.e. 30 fps"; `pak000: visuals.cfg:26` sets 1 |
| `com_unlock_avgFrames` | 4, range 1-10, NOCHEAT (`src/framework/Common.cpp:80-81`) | **none** | none | Help text: "average out render times over n frames for extra renders management" |
| `com_unlock_safetyMargin` | 0.1, NOCHEAT (`src/framework/Common.cpp:82-83`) | **none** | none | Help text: "percentage safety margin under which not to trigger an extra render" |
| `com_unlock_timingMethod` | 2, NOCHEAT (`src/framework/Common.cpp:84-85`) | **none** | none | Help text: 0 = whenever they fit; 1 = on boundaries of framerate multiples of 30; 2 = like 1, estimating from total previous time. `pak000: berzerkr.cfg:10-19`: 0 max FPS, 1 smoother, 2 smoothest and lowest FPS. `pak000: visuals.cfg:28` sets 1 |
| `com_maxFPS` | 60; INTEGER, ARCHIVE (`src/framework/Common.cpp:86-87`) | `src/framework/Common.cpp:1074-1089` | none | Not retail (OE 8191cae) |
| `com_unlock_maxFPS` | 60, NOCHEAT (`src/framework/Common.cpp:88-89`) | `src/framework/Common.cpp:1074-1089` (mirrored) | none | Help text: "only works in timing methods 1 & 2". `pak000: visuals.cfg:27` sets 120; `pak000: gizm.cfg:81` sets 0 |

- **Provenance.** All five `com_unlock*` CVars and their help texts are in jmarshall's `src/framework/Common.cpp` (`/Users/jmulesa/etqw`).
- **Retail semantics** are known from these help strings, from the community and developer cfgs at the root of pak000, and (since 2026-09-25) from the retail 1.5 engine's defaults (`docs/specs/retail-1.5-reference.md` §4); how its timing methods scheduled frames was not traced. Whether the retail GUIs reference any of these CVars is unverified: the GUIs ship compiled as `.guib` files (under `generated/declb/guis/`), and a string search of them finds no CVar names at all, not even common ones such as `r_mode`. Among text files, only root-level `.cfg` files mention `com_unlock`.
- **Mirroring** (`src/framework/Common.cpp:1073-1084`). Each frame one value wins:
  1. If `com_maxFPS` was modified, it wins.
  2. Otherwise, a modified `com_unlock_maxFPS` wins.
  3. Otherwise `com_unlock_maxFPS` is forced to equal `com_maxFPS`.
- **Side effects of mirroring.**
  - The `com_unlock_maxFPS` fallback at `src/framework/Common.cpp:1089` is effectively dead.
  - A cfg that sets `com_unlock_maxFPS` in the same frame that the archived `com_maxFPS` loads is overwritten (`src/framework/Common.cpp:1074-1077`).
  - Both CVars start out modified (`src/framework/CVarSystem.cpp:99`, `src/framework/CVarSystem.cpp:122`), so on the first frame `com_maxFPS` wins over a `com_unlock_maxFPS` set by a startup cfg.

### 7.3 `com_unlockFPS 0` today

- The engine never reads the CVar. It still calls `ClientUpdateView` on render-only frames (`src/framework/Session.cpp:342-345`; `src/framework/async/AsyncClient.cpp:351-355`).
- The game's `ClientUpdateView` does not check the CVar either (`game repo: src/game/Game_local.cpp:5957-5965`; `SDK: game/Game_local.cpp:4874-4881`). The base-draw rewind does check it (`game repo: src/game/PlayerView.cpp:886`).
- Result *(inferred, not observed)*: base draws show origin n while render-only frames interpolate from n-1, so the view steps back once per tick.

### 7.4 Changing the cap during play

- `com_maxFPS` is read every frame (`src/framework/Common.cpp:1089`). Setting 0 resets the pacing timer (`src/framework/Common.cpp:1130-1131`).
- Changing `r_swapInterval` recreates the swapchain, with `DeviceWaitIdle`, on the next `BeginFrame` (`src/renderer/VulkanBackend.cpp:5190-5201`).
- Nothing in the engine blocks a cap change. No registered meson test covers one. `tests/test_frame_pacing_adversarial.py` sends `set com_maxFPS` over stdin during play (`tests/test_frame_pacing_adversarial.py:309-310`) and tries edge cases (1000, -60, 1, rapid 60/240 toggles; `tests/test_frame_pacing_adversarial.py:456-490`), but it is not in `tests/meson.build`.

### 7.5 Render submission, and the ~120 fps ceiling

**How submission works**
- Two frames are in flight (`NUM_VULKAN_FRAMES = 2`, `src/renderer/VulkanBackend.cpp:47`).
- `BeginFrame` waits on the frame fence with no timeout (`src/renderer/VulkanBackend.cpp:5205-5211`), then acquires a swapchain image, also with no timeout (`src/renderer/VulkanBackend.cpp:5231-5245`).
- `EndFrame( bool )` ignores its argument (`src/renderer/VulkanBackend.cpp:5381`). It presents, and counts `framesPresented` only when the present succeeds (`src/renderer/VulkanBackend.cpp:5480-5490`). The counter is printed at shutdown (`src/renderer/VulkanBackend.cpp:5106`).
- Present mode: with `r_swapInterval <= 0`, `ChoosePresentMode` prefers MAILBOX, then IMMEDIATE, and otherwise uses FIFO (`src/renderer/VulkanBackend.cpp:1479-1494`). The choice is logged at `src/renderer/VulkanBackend.cpp:1969-1971`.

**The earlier recorded result.** `STATUS.md` (the render-cap replay determinism row, before 2026-09-25) records 387/645/647/647 presented frames at caps 60/120/144/240. The paragraph below explains why that count could not say which rate gameplay reached; §7.6 replaces it.
- `framesPresented` counts the whole process lifetime: loading frames, pacifier frames at up to 30 Hz (`src/framework/Session.cpp:234-255`), `CompleteWipe` frames, and gameplay.
- The test then divided that count by 150 and only asserted that the 120 run presented more frames than the 60 run (`tests/test_high_refresh_determinism.py` before 2026-09-25; it now measures the rate during play, §7.6).
- Gameplay lasts about 4.95 s, since the replay quits at tick 150 (`game repo: src/game/Game_local.cpp:5370-5371`).
- If the 60 run met its cap, gameplay at caps of 120 and above ran at roughly (645 − 90) / 4.95 ≈ 112 fps *(derived arithmetic, not measured; the 60 run in fact ran at 58–59 fps, §7.1, and the measured median rate is about 120, §7.5)*. That also assumes gameplay spans the full 150 ticks of wall time and that the number of frames outside gameplay is the same at every cap. Neither is guaranteed:
  - `CompleteWipe` presents frames in a loop outside the pacer (`src/framework/Session.cpp:631-638`), so their count depends on uncapped speed, not on the cap;
  - the catch-up cap lets one frame run up to 10 ticks at once (`src/framework/Common.cpp:924-929`; `src/framework/Session.cpp:319-328`), which may shorten gameplay after the load *(inferred)*.

**Host facts** (build output and hardware, not code)
- The client-network test logs (build output under `build/meson-logs/client-network/`, not in the repo) show `Vulkan swapchain: 2560x1440, 3 images, format 44, present mode 0`, that is IMMEDIATE, for both the join and the listen-host runs. So FIFO vsync is not what limits the rate; the display still does (below).
- The window is 1280x720 points with `SDL_WINDOW_HIGH_PIXEL_DENSITY` (`src/sys/sdl/sdl_glimp.cpp:52`), and `r_fullscreen` defaults to 0 (`src/renderer/RenderSystem_init.cpp:69`).
- The host is an M1 Max whose built-in panel runs at up to 120 Hz. MoltenVK is 1.4.2.

**Cause: presentation waits for the display (measured 2026-09-25).** With `com_logFrameTiming 1` (§7.6), uncapped play and caps 144 and 240 all run at about 120 fps, and each frame's *work* (from the start of the frame to the pacer, which includes `BeginFrame`'s fence wait and image acquire and `EndFrame`'s present) takes the whole 8.3 ms. The pacer never waits. The same client at cap 120 does its work in about 7.7 ms and waits the rest, and at cap 60 in about 3.8 ms. So on this host a windowed IMMEDIATE-mode swapchain is paced by the 120 Hz display. The other explanation considered, the frame's own CPU or GPU cost, does not fit: at cap 120 the same frames finish their work in 7.7 ms with two frames in flight. Which call blocks (the fence wait or the acquire) and whether fullscreen, a 60 Hz panel setting or MoltenVK settings change it was not measured; `r_showmetrics 1` (`src/renderer/RendererMetrics.cpp:18`) times both scopes (`src/renderer/VulkanBackend.cpp:5233`, `src/renderer/VulkanBackend.cpp:5206`).

**Consequence for M5.** Caps 144 and 240 cannot be reached here in a window. `high-refresh-determinism` now reports them as unreachable rather than failing (M5 plan decision D1, 2026-09-25: reaching a cap is reported, not tested), and still requires every schedule's per-tick state to match.

**The ~0.4–0.5 s stall is first-use loading after spawn (measured 2026-09-25).** Every replay schedule has one frame of about 410–540 ms. With `com_logFrameTiming 1` and `SIM_HASH` ticks in one log, it falls between replay ticks 1 and 20, while the player parachutes in and the map comes into view: the client loads materials and render programs on first use, prints a multi-line stage dump for materials with defaulted textures, and writes shader dumps to `renderprogs/shaderdump/` under the save path. Whether it counts as 'play' in `high-refresh-determinism` depends on where the one-second windows fall. It is a load hitch, not pacing; precaching at map load and quieter debug output would remove it (not done).

**Windows.** The loop's `Sleep( 1 )` sits outside the pacer (`src/sys/win32/SystemBootstrap.cpp:1405-1408`). `timeBeginPeriod` appears only in engine sources that no meson build compiles: `src/sys/win32/win_main.cpp:973` and the vendored SDL2 timer (`src/sys/sdl2/src/timer/windows/SDL_systimer.c:54`). Whether SDL3 raises the timer resolution is unverified. If it does not, this sleep could cap Windows well below high refresh *(inferred; the Windows engine has never been built)*.

### 7.6 Measuring the frame rate: `com_logFrameTiming`

- `com_frameTicks` counts the game ticks run in the current host frame. `idCommonLocal::Frame` resets it (`src/framework/Common.cpp:1036`); single player (`src/framework/Session.cpp:335`), the network client (`src/framework/async/AsyncClient.cpp:339`) and the server (`src/framework/async/AsyncServer.cpp:455`) add to it.
- With `com_logFrameTiming 1`, every host frame adds its interval (end of the previous frame's pacing to the end of its own) and its work time (start of the frame to the pacer), and once a second the engine prints one `FRAME_TIMING` line: the window length, frames, ticks, the cap, and the p50/p95/p99/max of the intervals and the p50/p95/max of the work times (`src/framework/Common.cpp:975-1024`, called at `src/framework/Common.cpp:1135`).
- A window with about 30 ticks is play; fewer ticks means loading or menus. `high-refresh-determinism` takes the frame rate from the median frame interval over the play windows, so one hitch does not decide it, and reports the average and the longest frame as well.

## 8. Doom 3, and what retail ETQW added

**Doom 3**
- Ticks at 60 Hz (`Doom 3: neo/framework/UsercmdGen.h:40`), advanced by an async thread (`Doom 3: neo/framework/Common.cpp:2547-2577`, `Doom 3: neo/framework/Common.cpp:2584-2622`).
- Single player waits for the next tic before running or rendering (`Doom 3: neo/framework/Session.cpp:2586-2606`).
- The network client and server block in `GetPacketBlocking` until a tick is due (`Doom 3: neo/framework/async/AsyncClient.cpp:1794-1807`; `Doom 3: neo/framework/async/AsyncServer.cpp:2401-2416`). Every mode therefore renders once per tick.
- There is no `ClientUpdateView` and there are no unlock CVars. The multiplayer branch of `Frame` calls `session->GuiFrameEvents()` (`Doom 3: neo/framework/Common.cpp:2461`).

**Retail ETQW** (from the SDK headers and game code, and since 2026-09-25 the retail 1.5 game module: `docs/specs/retail-1.5-reference.md` §3)
- Ticks at 30 Hz (`SDK: framework/UsercmdGen.h:15-16`).
- `ClientUpdateView` with `timeLeft` (`SDK: game/Game.h:173-174`).
- Unlock logic on the authoritative side as well as on clients (`SDK: game/Game_local.cpp:4222-4231`; `SDK: game/Game_network.cpp:3628-3638`).
- An engine scheduler for extra renders, described only by the CVar help texts in §7.2.

OE replaced that scheduler with the pacer of §7.1, which does not know where tick boundaries fall.

## 9. Known gaps against the M5 criteria

M5 is done when:
1. one tick-indexed command stream replayed at caps 60/120/144/240 gives the same authoritative state per tick and the same event counts;
2. clients at mixed caps in a live session show smooth motion;
3. captured frame data shows camera, viewmodel and remote-entity poses changing every rendered frame, on foot and in at least one vehicle seat;
4. the listen-server host is covered;
5. changing the cap during play works.

Remaining stepped paths must be listed rather than claimed as interpolated, and presentation work never changes `USERCMD_HZ` (30).

**Criterion 1: replay determinism**
- Only the offline single-player replay exists (`tests/test_high_refresh_determinism.py`, meson `high-refresh-determinism`).
- `SIM_HASH` leaves out view angles, other players and projectiles (`game repo: src/game/Game_local.cpp:11662-11676`). Since PR #14 the replay also compares `SIM_VIEW` (view angles, the logged view origin and weapon pose, the vehicle's pose and its view's last returned axis) at full precision; other players and projectiles are still not compared.
- The replay still samples input through the engine: `RunGameTic` builds each tick's usercmd with `GetDirectUsercmd` (`src/framework/Session.cpp:555-560`), and render-only frames sample too (`src/framework/Session.cpp:343`). The injection block (`game repo: src/game/Game_local.cpp:5341-5374`) only overwrites `forwardmove`, `rightmove`, `upmove` and attack on chosen ticks. Angles and every other field come from engine sampling, and they stay constant only because nothing moves the mouse.
- Presentation reaches tick state through the paths in §6.9. Since 2026-09-25 the replay also prints and compares `SIM_VIEW` each tick (the view angles after `UpdateViewAngles`, the tick's logged view origin and weapon offset and orientation, the vehicle's last pushed pose and the axis its view last returned, `lastReturnedAxis`; `game repo: src/game/Game_local.cpp`, `EmitSimView`, printed where `RunFrame` logs the pose). In the single-player replay the view angles, the logged view origin and the vehicle's pose and view axis match on all 150 ticks at caps 60/120/144/240 and uncapped. **One leak was found:** in some runs the logged view-space weapon pose (`wlog`) differs between one schedule and the 60 Hz one, from the tick after the replay enters the vehicle (92) to the end of the replay (the weapon lowering in the vehicle and raising after `exitVehicle`). At full precision (game PR #10) it appeared in 7 of 10 runs on the DVD and 1.5 data, on 55–56 of the 59 ticks 92–150; the earlier two-decimal comparison showed only 31–40 of those ticks. The likely mechanism *(inferred, not traced)*: `canUnlockFrames` is latched at the start of a tick (§6.5 step 5), so the render-only frames after the entering tick still run the on-foot weapon block of `ClientUpdateView` and write an interpolated pose into the weapon's render entity; in the vehicle the ticks do not rewrite it, so the tick log (`game repo: src/game/Game_local.cpp`, the pose capture in `RunFrame`) records the render-frame value and the lowering carries it on. It is presentation history, not gameplay state; plan step 6 covers the fix. `high-refresh-determinism` allows exactly this divergence, bounded (the `wlog` field alone, a weapon pose on both sides, starting at tick 92, on 45–59 ticks per schedule), and fails on any other. The §6.9 paths that matter run on network clients (snapshot receipt while `unlockedDraw` is set, the free-pivot damped view), and no client replay exists yet.
- Render-only samples run the game's usercmd callbacks (§5), so the weapon lock and other client button state can depend on the frame rate.
- There is no listen-host replay.

**Criterion 2: smooth motion at mixed caps**
- Remote entities step at 30 Hz on every client, because the producer is compiled out (§6.6).
- **Tick-frame timing error** *(derived from code)*.
  - A frame that ran a tick is a base draw at fraction 0, but the real time since the tick boundary can be anything up to one frame period, because the pacer is not aligned to ticks.
  - The pose therefore lags once per tick by up to one frame period: about 0.5 tick at 60 fps, 0.25 at 120, 0.21 at 144 and 0.13 at 240. That gives a 30 Hz judder.
  - Retail timing methods 1 and 2 aligned frames to ticks (§7.2).
- Each snapshot's prediction-clock nudge shifts the fraction (§2).
- When a snapshot lands on a frame with no tick, re-prediction runs with `isNewFrame == false` and clears `interpolateEntities` (`game repo: src/game/Game_network.cpp:3803`, `game repo: src/game/Game_network.cpp:3834-3848`). With the producer enabled, interpolated entities would then freeze for the rest of that tick *(inferred)*.
  - Keeping the list would not be enough: the consumer's `Present()` also returns early while `isNewFrame` is false (`game repo: src/game/Entity.cpp:1727-1729`) *(inferred)*.
  - `idWeapon::Present` has the same gate (`game repo: src/game/Weapon.cpp:1640-1662`), so the viewmodel could lag the camera on those frames *(inferred)*.
  - Re-predicted frames also do not refresh the pose log (§6.2).
- No mixed-cap session has been run.

**Criterion 3: captured frame data**
- Measured 2026-09-25 with `g_logFramePoses` (game repo: `src/game/PlayerView.cpp`, one `FRAME_POSE` line per main-view draw) and `tests/test_frame_poses.py` (meson `frame-poses-join`, `frame-poses-listen`), on foot, at caps 60/120/240:
  - network client (join): the camera turns and moves on every draw (A4, A5: 99.8–100% of draw pairs) and every draw between ticks is unlocked (A8). The fraction rises within a tick. Until 2026-09-25 it stepped back on about 1 draw in 500–700: right after a snapshot arrives the client corrects its prediction clock (§4.2), and the presented time moved back by a millisecond or two (for example 0.24 → 0.18 of a tick). The game now holds the fraction at the tick's last presented value instead (`unlock.lastFraction`), so such a draw repeats the pose rather than moving back. Remote players change only once per tick (A7: 50.5% of draw pairs at 60 fps, 25.2% at 120 and 240), as predicted from §6.6. **Not predicted, and fixed the same day:** the viewmodel's drawn pose repeated on about a fifth of draw pairs while walking (A6: 79–82%). When a snapshot arrives on a draw between ticks, the client re-runs prediction with `isNewFrame` false, so the weapon's `Present()` in `ClientUpdateView` returned early (`game repo: src/game/Entity.cpp:1727-1729`) and the viewmodel stayed where it was until the next tick. `ClientUpdateView` now presents through `idGameLocal::PresentUnlocked`, which sets `isNewFrame` for that call only (the weapon and the `interpolateEntities` consumer); the viewmodel then changes on 100% of draw pairs at caps 60/120/240.
  - listen host: camera, viewmodel and remote players all change only once per tick (the changed fraction equals the fraction of draw pairs that cross a tick: 50–51% at 60, 25–28% at 120 and 240), and no draw between ticks is unlocked (A8), as predicted from §4.3.
  - with `g_unlock_updateAngles`, `g_unlock_updateViewpos` and `g_unlock_interpolateMoving` off, the network client's camera also changes on only 51% of draw pairs at 60 fps: the checks can fail.
  - Vehicle seats are not captured yet. Retail 1.5 has no per-frame seat view (M5 plan D2); the one-seat capture will use the opt-in seat view of plan step 13 (D10).
- Vehicle seats are blocked by the proxy gate (§6.3), and the OE vehicle branch has the defects listed in §6.8.
- Interpolation is origin-only. The axis and skeletal animation step at 30 Hz (§6.6).

**Criterion 4: listen host**
- No `ClientUpdateView` and no input sampling between ticks (§4.3).
- No time-left function uses the server residual (§2).
- The host publishes post-tick state (§4.5). Wiring the unlock path up naively would make the §6.9 paths authoritative on the host, and some of them feed `ServerWriteSnapshot`.

**Criterion 5: cap change**
- Untested by any registered test (§7.4).
- `com_unlockFPS` is ignored by the engine (§7.3).
- The mirroring can overwrite a cfg-set `com_unlock_maxFPS` (§7.2).

**Pacing and other gaps in the frame path**
- Caps above 120 cannot be reached on the development Mac in a window: presentation waits for its 120 Hz display (measured, §7.5). Every replay schedule also has one ~410–540 ms stall right after spawn: first-use material and render-program loading (§7.5).
- Three CVars are read nowhere: `com_unlock_avgFrames`, `com_unlock_safetyMargin` and `com_unlock_timingMethod` (§7.2). CHARTER §7.5 requires every exposed pacing CVar to do something.
- Fixed 2026-09-25: in client and listen-host sessions `GuiFrameEvents` → `uiManager->Update` did not run per frame, only out of sequence during loads and wipes (`src/framework/Session.cpp:224-226`), and no other route updated the GUIs: with `g_guiSpeeds 1` a joined Vulkan client made 0 GUI updates in 5 s of play, against 277 in single player. `idCommonLocal::Frame` now calls it once per frame for a client or listen host (`src/framework/Common.cpp:1073`), as Doom 3's multiplayer branch did; `client-network-join`/`-listen` require >= 40 updates a second (measured 60/s at cap 60).
- Render-only input samples have side effects (§5): the weapon lock can clear between ticks, the `m_smooth` window is counted in samples, and mouse-strafe motion from render-only samples is dropped.
- Windows `Sleep( 1 )` sits outside the pacer (§7.5).

**Paths that stay stepped even with the SDK design restored.** These must be listed rather than claimed as interpolated:
- Skeletal animation: frames are created at `gameLocal.time` (`game repo: src/game/Entity.cpp:1766`, `game repo: src/game/Entity.cpp:1810`).
- Entity orientation: only the origin is lerped (`game repo: src/game/Game_local.cpp:6190`).
- The local player's own body (`game repo: src/game/Game_local.cpp:6184`).
- Doors (`game repo: src/game/misc/Door.cpp:1580-1595`).
- Entities beyond the AOR IK cutoff (`game repo: src/game/Entity.cpp:1743`).
- The Icarus camera (`game repo: src/game/vehicles/VehicleView.cpp:1795`) and jetpack visuals (`game repo: src/game/vehicles/JetPack.cpp:417-423`).
- Every state in which `IsFPSUnlock` returns false (§6.3): vehicles, jetpack, mounted guns, remote cameras and cinematics, the deploy menu, third person, dead or in limbo, prone, teleporting, and following another player.

## 10. Open questions

- **Answered on 2026-09-25** (`docs/m5-plan.md`, `docs/specs/retail-1.5-reference.md`):
  - What counts as a pose: the entity origin, as retail 1.5 does, and the viewmodel's origin and axis; no skeletal joints (D2).
  - Tick frames: draw them at their real sub-tick fraction, not with a tick-aligned scheduler (D7).
  - `com_unlockFPS 0` draws once per tick (D8).
  - The `isNewFrame` gate did freeze the network client's viewmodel after a snapshot (on about a fifth of draws, measured by `frame-poses-join`); `PresentUnlocked` fixed it.
  - The remote-entity producer is to be restored (D5, plan step 8): retail 1.5 runs it.
- **Remote-entity producer.**
  - Why did jmarshall disable it? No reason is recorded; D5 restores it anyway.
  - Which retail entityDefs set `option_unlock_interpolate`? The defs are binary `.defb` files, and a string grep found nothing.
  - Does the parachute's list handling (`game repo: src/game/misc/Parachute.cpp:232-243`) interact badly with zeroed history?
- **Render-only input samples.** Should they skip the game callbacks (`GetDirectUsercmd( false )`), or take only the view angles, so that buttons and impulses stay tick-bound as CHARTER §7.5 requires? What does a render-only sample then need from `MouseMove`'s game clamps?
- **Client ordering.** `ClientPrediction` (`src/framework/async/AsyncClient.cpp:344-348`) runs before `ProcessReliableServerMessages` (`src/framework/async/AsyncClient.cpp:349`) in the same frame. Reliable messages in each packet are already processed before that packet's snapshot (`src/framework/async/AsyncClient.cpp:811`). Is the second call intended, and what can it still find?
- **Seat and camera switches.** The `originlog` resets on seat switch and camera cycle copy `player->renderView.vieworg`, which may still be the pre-switch view (`game repo: src/game/vehicles/Transport.cpp:2832-2837`, `game repo: src/game/vehicles/TransportExtras.cpp:442-445`, `game repo: src/game/vehicles/VehicleView.cpp:267-270`). Nothing resets `originlog` on vehicle exit, respawn or teleport, and that gap is SDK behaviour.
