# M5 plan: high-refresh multiplayer

This is the order of work for charter milestone M5, based on the trace in [`frame-path.md`](frame-path.md). Each step is one reviewable PR, or a game/engine pair, with a test that can fail. The steps marked **decision** need the owner, and the recommendation given there is only a proposal. Written 2026-09-25; revise it as steps land.

M5 is done when:
- replaying one command stream at caps 60/120/144/240 gives the same authoritative state per tick;
- clients at mixed caps show smooth motion;
- captured frame data shows the camera, the viewmodel and remote entities changing every rendered frame, on foot and in one vehicle seat;
- the listen host is covered;
- changing the cap during play works.

## Measure first

1. **Frame-path doc** (`docs/frame-path.md`). Documentation only.

2. **Frame timing (engine).**
   - Add a counter of ticks run in each host frame, set by `idSessionLocal::Frame`, `idAsyncClient::RunFrame` and `idAsyncServer::RunFrame`.
   - Add a CVar `com_logFrameTiming` (default 0). When set, print one `FRAME_TIMING` line per host frame: frame number, work time, pacing wait, ticks run and `timeLeft`.
   - `high-refresh-determinism` then reports the achieved gameplay frame rate per cap, and its frame-time distribution (median and 95th/99th percentile), instead of the renderer's lifetime present counter.
   - It fails when the rate is above 1.03 × cap, or when the per-tick hashes differ.
   - **Decision D1:** when the host cannot reach a cap (this Mac tops out at about 112–120 fps in a window), should the test fail or report the cap as unreachable? *Recommendation:* report it as unreachable with the measured ceiling, and fail only below 0.9 × min(cap, ceiling). A test that always fails on this Mac gates nothing.

3. **Per-frame pose capture (game + engine tests).**
   - Add a CVar `g_logFramePoses` that prints one `FRAME_POSE` line per main-view draw, just before `RenderScene` in `idPlayerView::SingleView`. It logs:
     - the view origin and angles;
     - the viewmodel origin;
     - the render origins of up to N remote players;
     - `unlockedDraw`, `canUnlockFrames` and `interpFraction`.
   - Add tests `frame-poses-join` and `frame-poses-listen` (the Vulkan client with a headless peer that walks), and a devmap variant with a bot.
   - The known-gap table (which assertions are expected to fail today) is written from the code before the first run and is not adjusted to fit that run.
   - A negative control must make each detector fail at least once.
   - This step depends on `test_client_network.py` (merged).
   - **Decision D2:** which vehicle and seat count as "one vehicle seat", and does "pose" include skeletal joints? *Recommendation:* the driver seat of a ground vehicle on valley, with entity origins and axes only (no joints).

4. **The ~120 fps ceiling.**
   - Use steps 2 and 3 to find out whether presentation (MoltenVK and the 120 Hz panel), the pacer or the frame work sets the ceiling. Try fullscreen, a smaller window and a 60 Hz panel.
   - A test-only mode that skips presenting must also skip acquiring the swapchain image and render offscreen; otherwise the swapchain runs dry and the run hangs.
   - **Decision D3:** is an offscreen or no-present run acceptable M5 evidence for caps 144 and 240 on a 120 Hz panel? *Recommendation:* yes for the determinism and pose criteria, with the limitation stated. "Smooth motion" still needs a display that reaches the cap.

## Keep presentation out of simulation

5. **The replay hash covers the state render frames could leak into (game + tests).**
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
   - **Decision D4:** tick code would no longer see the view drawn in the last render frame. That is a deviation from SDK single-player behaviour, needed for the "same state at every cap" criterion. Approve, with a ledger row? *Recommendation:* approve.

7. **Engine: the listen host updates its view every rendered frame.**
   - Add `idAsyncServer::GetGameTimeLeft()`. In `RunFrame`, on frames where no tick ran, call `GetDirectUsercmd` and then `ClientUpdateView` for the local client.
   - Today the host reads mouse input only once per tick, so its camera turns at 30 Hz even when adding the view update.
   - This step depends on step 6. Also fix the four stale comments about an input thread.

## Smooth remote entities

8. **Game: restore the `Entity::Present` interpolation on network clients.**
   - Remove jmarshall's `#if 0` (SDK behaviour), limited to network clients until step 9.
   - `Present` returns early while `isNewFrame` is false, so after a re-prediction the lerped origin never reaches the renderer. The consumer must present interpolated entities on such frames.
   - **Decision D5:** restore the SDK block without knowing why jmarshall disabled it? *Recommendation:* yes. It is SDK code, and step 3's capture shows its effect.

9. **Game: presentation-only interpolation on single player and the listen host.**
   - Compute the drawn origin into a render copy, and never write it back to `renderEntity.origin` or `lastPushedOrigin` during a tick.
   - Bound client entities and attachments read the presentation origin.
   - **Decision D6:** presentation-only writes on authoritative hosts (a deviation from the SDK) or SDK behaviour everywhere? *Recommendation:* presentation-only. The SDK way leaks render-frame values into server state (see step 6).

10. **Engine: draw tick frames at their real sub-tick fraction.**
    - Frames that ran a tick are currently drawn as "base" frames at the tick boundary. That produces a 30 Hz judder.
    - The fix is to call `ClientUpdateView` on every drawn frame, with `timeLeft` from the residual left after the tick.
    - **Decision D7:** this, or a tick-aligned frame scheduler (retail timing methods 1 and 2)? *Recommendation:* this. It is simpler, works at any cap and adds no latency.

## CVars and vehicles

11. **`com_unlockFPS` and `com_unlock_maxFPS`.**
    - When `com_unlockFPS` is 0, draw once per tick. This also covers the tick frames from step 10.
    - Make `com_unlock_maxFPS` a last-write-wins alias of `com_maxFPS`, using the CVar change callbacks. Define what happens on the first frame, when both CVars start out modified.
    - **Decision D8:** does `com_unlockFPS 0` mean "draw at the tick rate", as retail cfg comments suggest? *Recommendation:* yes.

12. **The dead `com_unlock_avgFrames`, `com_unlock_safetyMargin` and `com_unlock_timingMethod`.**
    - **Decision D9:** remove them, with a ledger row, or implement retail's tick-aligned timing? *Recommendation:* remove them, since step 10 replaces what they did. Test that retail configs which set them still load cleanly.

13. **Vehicle proxy gate.** `idPlayer::IsFPSUnlock` turns the per-frame view update off in any vehicle (SDK behaviour), so vehicles draw at 30 Hz.
    - **Decision D10:** keep the gate (the M5 vehicle criterion stays unmet), allow listed seats, or give vehicles a presentation-only view? Also: is there a CVar, and what is its default? *Recommendation:* allow-listed seats with a presentation-only view. Keep `IsFPSUnlock` unchanged, add a separate predicate for allowed seats, and default to on for the ground-vehicle driver seat (D2).
    - A decision record goes in `docs/decisions/`, plus a guard test that an excluded proxy never unlocks.

14. **Game: presentation-only view for allowed seats.** It blends the last two tick views, adds the sub-tick mouse turn and draws the vehicle from a render copy. It depends on steps 6 and 13.

## Remaining criteria

15. **Mixed-cap live session.** Two Vulkan clients at different caps (60 with 240, then 120 with 144) and a headless peer. Each client is judged against its own cap; the test depends on step 4 for the caps this host cannot reach.

16. **GUI updates in network sessions.** Call `GuiFrameEvents` once per frame in multiplayer, as single player and Doom 3 do. First check that the HUD is not already updated some other way; otherwise it would update twice per frame.

Steps 1, 2, 3, 5 and 16 need no decision.
