# M5 plan: high-refresh multiplayer

This is the order of work for charter milestone M5, based on the trace in [`frame-path.md`](frame-path.md). Each step is one reviewable PR, or a game/engine pair, with a test that can fail. The owner decided D1–D10 on 2026-09-25; each **decision** below records the answer. Where the owner asked to match retail 1.5 (D2, D4), the facts come from the retail 1.5 Mac game module (`game001.pk4` of the official Linux 1.5 client installer; it keeps its symbols), with addresses. Written 2026-09-25; revise it as steps land.

M5 is done when:
- replaying one command stream at caps 60/120/144/240 gives the same authoritative state per tick;
- clients at mixed caps show smooth motion;
- captured frame data shows the camera, the viewmodel and remote entities changing every rendered frame, on foot and in one vehicle seat (the seat view is an OpenETQW extension: retail 1.5 draws every vehicle seat at the tick rate; see D2 and D10);
- the listen host is covered;
- changing the cap during play works.

## Measure first

1. **Frame-path doc** (`docs/frame-path.md`). Documentation only.

2. **Frame timing (engine).** Done (2026-09-25):
   - `com_frameTicks` counts the ticks run in each host frame (single player, network client, server).
   - `com_logFrameTiming 1` prints one `FRAME_TIMING` line a second: frames, ticks, the cap, and the percentiles of frame intervals and frame work. Per-frame lines with `timeLeft` are deferred to step 3's capture.
   - `high-refresh-determinism` measures the rate during play from the median frame interval, per cap, with an uncapped run for this host's ceiling (also compared tick by tick). It fails above 1.03 × cap and below 0.98 × cap where the cap is well below the ceiling. It also still fails below 0.9 × min(cap, ceiling); per D1 that bound, and frame-poses A1's 0.85 × ceiling bound, are to become printed reports (not done yet; until then the test's docstring still calls D1 pending).
   - It also fixed the pacer, which ran a 60 cap at 58–59 fps because macOS wakes sleeps about 2 ms late.
   - **Decision D1** (decided 2026-09-25): when the host cannot reach a cap (this Mac tops out at about 120 fps in a window, the display's rate), the test reports it and does not fail. The owner: high-refresh testing is needed only as far as "the engine is configured to support it and doesn't regress"; no high-refresh hardware goes into testing. openQ4 does the same: its CI never measures the frame rate. So the tests keep the checks that need no fast display (the replay and pose comparisons across caps, the cap never exceeded by more than 3%, the pacer holding a cap well below the host's ceiling to 98%, each schedule reporting its own cap) and only report whether a cap was reached (the 0.9 × min(cap, ceiling) bound and frame-poses A1's 0.85 × ceiling bound are to become reports; not done yet).

3. **Per-frame pose capture (game + engine tests).** On foot done (2026-09-25): `g_logFramePoses` and `frame-poses-join`/`-listen` at caps 60/120/240, with a negative control. The first run confirmed every predicted gap and found one more, since fixed: the network client's viewmodel repeated on about a fifth of draws, because `Present()` returned early while `isNewFrame` was false after a snapshot re-ran prediction (`docs/frame-path.md` §9, criterion 3; `PresentUnlocked`, 2026-09-25). Each known gap is checked against its predicted shape. Still to do: vehicle seats (D2) and a devmap variant.
   - Also found, and fixed (2026-09-25): right after a snapshot the client's prediction-clock correction stepped the presented time back by a millisecond or two (about 1 draw in 500–700). The game now holds the fraction within a tick; A8 requires no backward step.
   - Done (2026-09-25): `ClientUpdateView` presents the weapon and the `interpolateEntities` consumer through `PresentUnlocked` even when `isNewFrame` is false. `frame-poses-join` A6 passes (100% at caps 60/120/240).
   - Done: the CVar `g_logFramePoses` (game; a cheat, since it logs other players' positions) prints one `FRAME_POSE` line per main-view draw, just before `RenderScene` in `idPlayerView::SingleView`: the view, the viewmodel, the seat and up to 8 other players' render-world poses, and the unlock state and fraction.
   - Done: `frame-poses-join` and `frame-poses-listen` (the Vulkan client with a headless peer that walks). Known gaps were written from the code before the first run (plus the one it found), each with the shape its failure must keep. A negative control makes the camera detectors fail.
   - To do: a devmap variant with a bot, vehicle seats (D2), and a negative control for the remote-player detector once the `Entity::Present` producer is restored.
   - **Decision D2** (decided 2026-09-25: match retail 1.5). Retail 1.5 has no per-frame view in any vehicle seat: `idPlayer::IsFPSUnlock` (0x3dd320) returns false whenever the viewed player has a proxy entity, which covers every seat of every vehicle, the jetpack included, with no seat or vehicle-type exception; it matches the SDK check for check. Retail interpolates remote entities by `renderEntity.origin` only, one tick behind (`idEntity::Present`, 0x108224): no axes and no skeletal joints, and animation is evaluated at the tick's time. The viewmodel gets a rigid origin and axis. So "pose" means entity origins (and the viewmodel's origin and axis), not joints. The one-seat capture uses the seat view from D10 (on by default).

4. **The ~120 fps ceiling.**
   - Measured (2026-09-25): presentation waits for the 120 Hz display. Uncapped and at caps 144/240 each frame's work takes the whole 8.3 ms, while the same frames take about 7.7 ms at cap 120 (`docs/frame-path.md` §7.5). Still open: which call blocks (fence or acquire; `r_showmetrics 1`), and whether fullscreen or a 60 Hz panel setting changes it.
   - The ~410–540 ms stall in every replay is first-use material and render-program loading right after spawn (with stage dumps printed and shader dumps written to disk). A precache pass at map load and quieter debug output would remove it (not scheduled).
   - A test-only mode that skips presenting would also have to skip acquiring the swapchain image and render offscreen; otherwise the swapchain runs dry and the run hangs. Not to be built (D3).
   - **Decision D3** (decided 2026-09-25): yes, a run whose display cannot reach the cap is acceptable evidence for the determinism and pose criteria. Following D1, no offscreen or no-present mode will be built for it: the replay already runs every cap whatever the display reaches. "Smooth motion" at those caps is judged by people on real displays, not by a test.

## Keep presentation out of simulation

5. **The replay hash covers the state render frames could leak into (game + tests).** Single-player part done (2026-09-25): the game prints `SIM_VIEW` each tick (view angles after `UpdateViewAngles`, the tick's logged view origin and weapon offset and orientation, the vehicle's last pushed pose and the axis its view last returned) and `high-refresh-determinism` compares it across caps, at full precision since game PR #10: identical on all 150 ticks at every cap except for one leak it found: after the replay enters the vehicle, the logged weapon pose can differ between caps, because the entering tick's render frames still run the on-foot weapon presentation and the vehicle ticks log the result (the test allows exactly that until step 6 fixes it; at full precision it covers 55–56 of the ticks 92–150 when it appears, 7 of 10 runs on the DVD and 1.5 data). Still to do: the listen-host and network-client variants, where the other §6.9 leaks live; AoR results are not in the line yet.
   - At the end of each tick, add to `SIM_HASH` values that tick code produces and later reads:
     - the local player's view angles after `UpdateViewAngles`;
     - the tick pose logs (`originlog` and the weapon logs);
     - the vehicle's `lastPushedOrigin`/`lastPushedAxis` and `lastReturnedAxis`;
     - the AoR view results.
   - Do not hash presentation buffers (`renderView`, the first-person view, the weapon's render pose). Between ticks they are legitimately at a different sub-tick fraction at each cap, so comparing them would fail even with no leak.
   - Add a listen-host replay variant. The test compares these fields across caps.

6. **Game: no presentation values in tick, snapshot or AoR state.**
   - Revert the OpenETQW `!fullUpdate && unlockedDraw` interpolation in the vehicle views to SDK code: `sdSmoothVehicleView`, and the base and free-pivot `sdDampedVehicleView`. Move the interpolation into read-only helpers for step 14.
   - In `sdTransport::ClientUpdateInterpolation`, stop writing `lastPushedOrigin`/`lastPushedAxis`, both directly and through the `Present()` call that follows (`idEntity::Present`, and `sdJetPack::Present` for the jetpack).
   - Deal with `unlockedDraw` staying set between a render-only frame and the next snapshot or tick. `SetupClientAoR` runs in that window.
   - Stop the render frames of the tick in which the player enters a vehicle from writing the weapon render pose that the vehicle ticks then log (`canUnlockFrames` is latched when the tick starts). This is the likely cause *(inferred, not traced)* of the weapon-log divergence that step 5's `SIM_VIEW` replay found (engine PR #14). Before fixing it, trace which write it is: with a transport proxy, `ClientUpdateView` takes the proxy branch and returns before the on-foot weapon block, and that branch presents the weapon through `sdTransport::ClientUpdateInterpolation` → `PresentPlayers` → `idWeapon::PresentWeapon`. Once the fix is confirmed, `high-refresh-determinism` drops its `KNOWN_VIEW_LEAK` allowance.
   - Remove the `unlockedDraw` early return in `sdTransport::UpdateModelTransform` (retail 1.5 has none; `docs/specs/retail-1.5-reference.md` §3).
   - Delete the proxy branch of `ClientUpdateView` and `sdTransport::ClientUpdateInterpolation`, or rebuild it on the step-13 predicate so that it never writes `lastPushedOrigin`/`lastPushedAxis` or presents the tick entity. It runs today on the entering tick's render frames.
   - **Decision D4** (approved 2026-09-25, on condition that it matches retail 1.5 or the spirit of its gameplay): tick code no longer sees the view drawn in the last render frame; ledger row. It does match the spirit: in retail 1.5 render frames reached tick state through side channels, and no traced path reaches the local player's movement or aim input (inferred: readers of the render view before `CalculateView` were not traced exhaustively, and a mounted gun fired in the tick after entry takes its direction from the render-frame view, `frame-path.md` §6.9). The unlocked frames overwrote `renderView` until `CalculateView` late in the next `idPlayer::Think` (`ClientUpdateView` 0x1518a8), `Present` relinked a player's combat model at the drawn origin (`OnUpdateVisuals` → `idActor::LinkCombat` 0x3b64f8) and set `lastPushedOrigin`. None of retail's vehicle views interpolate. OpenETQW's additions reach aim on every tick, not only after an entry: the damped and free-pivot views write the interpolated axis into `lastReturnedAxis`, which the free-pivot `ClampViewAngles` reads on the next tick.

7. **Engine: the listen host updates its view every rendered frame.**
   - Add `idAsyncServer::GetGameTimeLeft()`. In `RunFrame`, on frames where no tick ran, call `GetDirectUsercmd` and then `ClientUpdateView` for the local client.
   - Today the host reads mouse input only once per tick, so its camera turns at 30 Hz even when adding the view update.
   - This step depends on step 6. Also fix the four stale comments about an input thread.

## Smooth remote entities

8. **Game: restore the `Entity::Present` interpolation on network clients.**
   - Remove jmarshall's `#if 0` (SDK behaviour), limited to network clients until step 9.
   - `Present` returns early while `isNewFrame` is false, so after a re-prediction the lerped origin never reaches the renderer. The consumer must present interpolated entities on such frames.
   - **Decision D5** (approved 2026-09-25): restore it. Retail 1.5 runs this producer (`idEntity::Present` 0x108224, origin only, one tick behind), so remote players, vehicles, items and projectiles drew at the frame rate in retail and draw at 30 Hz in OpenETQW today. Keep retail's origin-only, one-tick-behind shape; step 9 keeps its writes out of tick state (D4).

9. **Game: presentation-only interpolation on single player and the listen host.**
   - Compute the drawn origin into a render copy, and never write it back to `renderEntity.origin` or `lastPushedOrigin` during a tick.
   - Bound client entities and attachments read the presentation origin.
   - **Decision D6** (decided 2026-09-25): presentation-only writes on authoritative hosts. The SDK way leaks render-frame values into server state (see step 6): in retail a listen host relinked hit models at drawn positions. Ledger row.

10. **Engine: draw tick frames at their real sub-tick fraction.**
    - Frames that ran a tick are currently drawn as "base" frames at the tick boundary. That produces a 30 Hz judder.
    - The fix is to call `ClientUpdateView` on every drawn frame, with `timeLeft` from the residual left after the tick.
    - **Decision D7** (decided 2026-09-25): this, not a tick-aligned frame scheduler (retail timing methods 1 and 2). It is simpler, works at any cap and adds no latency.

## CVars and vehicles

11. **`com_unlockFPS` and `com_unlock_maxFPS`.**
    - When `com_unlockFPS` is 0, draw once per tick. This also covers the tick frames from step 10.
    - Make `com_unlock_maxFPS` a last-write-wins alias of `com_maxFPS`, using the CVar change callbacks. Define what happens on the first frame, when both CVars start out modified.
    - **Decision D8** (decided 2026-09-25): yes, `com_unlockFPS 0` draws once per tick. Retail's default is 1 (engine `etqw.x86`), as in OpenETQW.

12. **The dead `com_unlock_avgFrames`, `com_unlock_safetyMargin` and `com_unlock_timingMethod`.**
    - **Decision D9** (decided 2026-09-25): remove them for now, with a ledger row, since step 10 replaces what they did. Test that retail configs which set them still load cleanly.

13. **Vehicle proxy gate.** `idPlayer::IsFPSUnlock` turns the per-frame view update off in any vehicle (SDK behaviour), so vehicles draw at 30 Hz.
    - **Decision D10** (decided 2026-09-25): **on by default**. The owner: "let's get vehicles feeling as modern/responsive as possible without breaking game physics. If we can move vehicles entirely out of tick rate and modernize them safely, we should explore that." So: allowed seats get a presentation-only view, and the allow-list grows to every seat as each is made safe; `IsFPSUnlock` stays as retail 1.5 has it, and a separate predicate and CVar (default on) select the seats. The authoritative simulation (vehicle physics, hits, the tick rate) does not change. Ledger row: an OpenETQW deviation from retail 1.5's look (retail draws every seat at the tick rate). How much further vehicles can move off the tick rate safely (input sampling, remote vehicles, physics) is being explored before steps 13–14.
    - A decision record goes in `docs/decisions/`, plus a guard test that an excluded proxy never unlocks.

14. **Game: presentation-only view for allowed seats.** It blends the last two tick views, adds the sub-tick mouse turn and draws the vehicle from a render copy. It depends on steps 6 and 13.

## Remaining criteria

15. **Mixed-cap live session.** Two Vulkan clients at different caps (60 with 240, then 120 with 144) and a headless peer. Each client is checked against its own cap as in D1: the cap is never exceeded by more than 3%, the frame-pose checks of step 3 pass on every draw, and a cap this host cannot reach (144 and 240 on this 120 Hz display) is reported with the measured rate, not failed. Smooth motion at caps the display cannot reach is judged by people on real displays (D3).

16. **GUI updates in network sessions.** Done (2026-09-25): no GUI updated during a network game (0 updates in 5 s); a client or listen host now calls `GuiFrameEvents` once per frame, as single player and Doom 3 do (60 updates/s at cap 60), and `client-network-*` check it. Originally: call `GuiFrameEvents` once per frame in multiplayer, as single player and Doom 3 do. First check that the HUD is not already updated some other way; otherwise it would update twice per frame.

17. **Cap change during play.** A registered test changes `com_maxFPS` (and `com_unlock_maxFPS` after step 11) in the middle of a session and checks that the new cap is held and that `SIM_HASH`/`SIM_VIEW` still match. No test covers it today (`docs/frame-path.md` §9 criterion 5; `tests/test_frame_pacing_adversarial.py` is not registered).

All ten decisions are made (2026-09-25).
