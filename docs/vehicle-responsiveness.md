# Vehicle responsiveness

This is the plan for decision D10 of [`m5-plan.md`](m5-plan.md), written 2026-09-25 from a design study (three readers, three designs from different angles, two adversarial judges). It is a proposal for review; the owner's questions are in §5.1. The owner's goal (D10): "let's get vehicles feeling as modern/responsive as possible without breaking game physics. If we can move vehicles entirely out of tick rate and modernize them safely, we should explore that."

*Update 2026-10-18:* most of this plan has been built since. Stages 0–4 and 6 have landed, most of stage 5 has, and so have stage 7 (the hull lead, off by default; `hull-lead.md`) and §3.6's joint blending. Each stage's status line says what. What is left: the owner's default seat list (the open owner questions). Late look for `freepivot` views and the one presented `clampDamped*` seat, the husky driver, landed 2026-10-20 (ledger 064). The text below was written before the build: nothing in it was built or run then, except the stage 6 spike (2026-09-30), which is marked *[measured]*. The analysis comes from reading the code. Every millisecond, degree and distance below is arithmetic from the 33 ms tick, unless it says "measured".

**How to read the references**
- `game` = openETQW-game at `f00f4d3`.
- `engine` = openETQW at `228a5c8`.
- §1.3 and stage 6 were rewritten on 2026-09-30, and their references are to game `cd18aee` and engine `7368e68`.
- `SDK` = the 1.5 SDK copy in `.agents/upstreams/etqw_sdk_comparison/sdk/source`.
- `openQ4-game` at `9889d9e` and `openq4` at `b37b1456`. These are used for patterns only.

**Tags**
- *[inspected]*: read in the code.
- *[inferred]*: reasoned from the code, not observed.
- *[derived]*: arithmetic.
- *[measured]*: taken from an existing capture in [`frame-path.md`](frame-path.md), or from the stage 6 spike.

## Summary

- **Vehicle physics stays on the 30 Hz tick.** Moving it off the tick would change handling, the server's results and client prediction. It also would not remove the delay the driver feels most: keys and the pilot's mouse reach physics once per tick whatever rate the physics runs at.
- **What the player sees can leave the tick.** So can mouse look, in seats where the mouse only aims the view. This needs no protocol change and no change to the simulation.
- **The plan has eight stages.** Stages 0–5 make every allowed seat smooth and give angle-driven seats on-foot mouse latency. Stage 6 fixes a client-prediction bug in network pilots' cyclic and collective (measured and fixed 2026-09-30). Stage 7 is optional: it removes the 33 ms that interpolation costs, for each vehicle class where the physics' own next pose can be predicted.
- **On by default, seat by seat.** The owner decided D10 on 2026-09-25: vehicles should feel as modern and responsive as possible out of the box, without breaking game physics. A seat joins the default list once it passes the prerequisites in §3.3 (the captures, the replays and a human review); until then it keeps retail's tick-rate view.

## 1. What a player feels in vehicles today, and why

### 1.1 The cause

The game ticks at 30 Hz, one tick every 33 ms (game `src/framework/UsercmdGen.h:15-16`).

On foot, frames between ticks are "unlocked". The camera is interpolated between tick poses, and the view angles come from a late input sample.

In any vehicle seat it is different:
- `idPlayer::IsFPSUnlock` returns false, because the player has a proxy entity (game `src/game/Player.cpp:8794-8796`). Retail 1.5 does the same (the retail 1.5 reference notes §3). The result is latched when each tick starts (game `src/game/Game_local.cpp:5252-5261`).
- `idGameLocal::ClientUpdateView` then returns before doing any per-frame work (game `src/game/Game_local.cpp:5983-5987`).
- The seat camera is computed once per tick, in `sdTransport::PostThink` → `UpdatePlayerViews` (game `src/game/vehicles/Transport.cpp:4434`).
- Every frame until the next tick draws that camera unchanged. *[inspected]*

The vehicle simulation and control code is the 1.5 SDK code, unchanged. VehicleControl, TransportComponents, VehicleIK and JetPack show no diff against the SDK. So retail physics feel is today's baseline. *[inspected]*

### 1.2 What it feels like

| Cap (fps) | Frames each 33 ms pose is held |
|---|---|
| 60 | about 2 |
| 120 | about 4 |
| 144 | about 4.75 |
| 240 | about 8 |

*[derived]*

1. **Everything you see steps at 30 Hz.** This covers the camera, the hull, wheels, rotors and turret, the other riders, the cockpit and camera shake.
   - At 90°/s of yaw each step is 3°. At 800 u/s each step is 26 units. *[derived]*
   - The frame pacer is not aligned to ticks, so poses are held for an uneven number of frames (4 or 5 at 144 Hz). That adds judder on top of the stepping (engine `src/framework/Common.cpp:1086-1139`; frame-path §7.1). *[inferred]*
2. **Other vehicles, players and projectiles also step at 30 Hz.** The remote-entity producer was compiled out (game `src/game/Entity.cpp:1736-1759`) until M5 step 8 restored it on network clients. Even retail switches it off while you are seated, because it needs `canUnlockFrames`. *[inspected]* On foot, remote players change on only 50.5% and 25.2% of draw pairs at caps 60 and 120 (frame-path §9). *[measured]*
3. **Mouse look in a seat waits for the next tick.**
   - The engine does sample input on every render-only frame (engine `src/framework/Session.cpp:332-345`, `src/framework/async/AsyncClient.cpp:351-355`), but in a seat the game discards that sample.
   - The turn builds up in the usercmd generator's view angles (engine `src/framework/UsercmdGen.cpp:486-489`) and appears at the next tick as one jump.
   - That is 0–33 ms of extra delay compared with on foot, about 16.5 ms on average. The jump is the turn rate × 33 ms, for example 6° at 180°/s. *[derived]*
   - Gunners and free look feel this most.
4. **Driving keys reach the physics once per tick.** Throttle, steering, brake and up enter only through the tick's usercmd (game `src/game/vehicles/TransportExtras.cpp:35-53`). Steering then ramps by rate × 33 ms (game `src/game/vehicles/VehicleControl.cpp:710-763`). That is 0–33 ms of quantization, 16.5 ms mean. This is the simulation rate itself, and on foot is no different. *[inspected]*
5. **Pilots feel two ticks of delay.**
   - The mouse is control input. Pitch and roll input is the change in the command's angles over one tick (game `src/game/vehicles/VehicleControl.cpp:1984-1990`), clamped to ±2 per tick at the rotor (game `src/game/vehicles/TransportComponents.cpp:2775-2776`).
   - The integrator is explicit Euler, so the aircraft's attitude only moves in the *next* tick's integration (game `src/game/physics/Physics_RigidBodyMultiple.cpp:658-678`). The response is tick quantization plus one physics tick, and then it is shown as a single 30 Hz step. *[inspected code; felt effect inferred]*
   - After a hitch, all of the mouse motion lands in the first catch-up tick (engine `src/framework/UsercmdGen.cpp:451-452`), which jolts the aircraft. *[inferred]*
6. **On a network client, your own vehicle is predicted and corrected at each snapshot.**
   - Snapshots arrive about every 50–67 ms (frame-path §4.5).
   - Prediction-error decay smooths corrections, but it advances only on new ticks (game `src/game/PredictionErrorDecay.cpp:357-360`).
   - Re-predicted frames do not move the drawn vehicle until the next new tick (game `src/game/Game_network.cpp:3937-3941`). *[inspected]*
7. **On a client, remote vehicles are extrapolated, never interpolated.** They are simulated forward with the driver's relayed inputs, and then with the last input repeated, and corrected at each snapshot. Far vehicles get updates as rarely as every 0.5 s (game `src/game/decls/DeclAOR.cpp:262-287`). *[inspected; felt effect inferred]*
8. **On a listen host, the mouse was read once per tick, on foot too, until M5 step 7.** The host built a usercmd only at ticks and never called `ClientUpdateView` (engine `src/framework/async/AsyncServer.cpp:448-456, 821-833` before step 7), so every frame it drew was a base draw. *[inspected]* Since step 7 (2026-10-01) it builds a render-only usercmd and calls `ClientUpdateView` on each frame that ran no tick (engine `src/framework/async/AsyncServer.cpp:492-500`), as single player and the client do; in a seat `IsFPSUnlock` still refuses, so seats draw at the tick rate there as everywhere until stage 3. *[measured: `frame-poses-listen`]*
9. **The SDK call order adds one-tick lags in a few places.** These are retail behaviour, and this plan leaves them alone. *[inferred]*
   - The chase camera follows the hull's rotation from `lastPushedAxis`, which the previous tick's `Present` wrote (game `src/game/vehicles/VehicleView.cpp:1205-1206`; `Transport.cpp:4434` runs before `:4451`).
   - The view lean and the IK are updated after the camera (game `Transport.cpp:4434-4435`).
   - The Desecrator's strafe and boost read the driver's usercmd from the previous tick (game `src/game/vehicles/VehicleControl.cpp:353-356, 378`). So does the jetpack's heading (game `src/game/vehicles/JetPack.cpp:278-286`).
10. **Entering and exiting were not clean: fixed in M5 step 6.**
    - The render frames of the tick you entered on still ran the on-foot path and the OpenETQW proxy path, because the gate was latched when the tick started. That was the source of `KNOWN_VIEW_LEAK` in the replay (traced in step 6). Since step 6 those frames hold the tick's vehicle view: the proxy path is gone and the on-foot blocks run only without a proxy at draw time (ledger 056).
    - After an exit between ticks (the console, script events, every network client's exit), nothing reset `originlog`, so the first on-foot tick's base draws rewound to the vehicle camera (`vehicle-view-net` measured 270 u on a listen host). Since step 6 the origin log is reset at the pose log when the view player's seat changes (ledger 057).
11. **Input is late or lossy in every mode.** Input waits up to one frame before any usercmd sees it. SDL's fractional mouse motion was truncated for each event; since #141 (2026-10-19) the remainder is carried between events (`src/sys/sdl/MouseDelta.h`, meson `mouse-delta`). *[inspected]* This is not specific to vehicles.

### 1.3 A client-prediction bug that probably hurts network pilots

`sdAirVehicleControl` keeps `oldCmdAngles` and `collective` between ticks. The air control is not networked (game `src/game/vehicles/VehicleControl.h:68`), and a snapshot restores only networked controls (game `src/game/vehicles/Transport.cpp:3923-3940`). *[inspected]*

A snapshot holds the state after server tick S, and by then the client has predicted ahead to some tick P > S. So on S+1, the first tick it replays:
- **Cyclic.** The pitch/roll delta is taken against tick P's command instead of tick S's: the input is cmd(S+1) − cmd(P), not cmd(S+1) − cmd(S) (game `VehicleControl.cpp:1984-1990`; mouse Y is pitch, mouse X is roll). With the mouse moving at a steady r degrees per tick, the client applies −(P−S−1)·r instead of r. One tick ahead the input is lost; two ticks ahead it is reversed; three or more, it is reversed and amplified, up to the ±2° clamp (game `TransportComponents.cpp:2775-2776`). *[inspected; the effect is derived]*
- **Collective.** `collective` is the forward/back keys' integral, and it is re-integrated from its newest value (game `VehicleControl.cpp:2113-2139`). In the air with a collective key held, each snapshot therefore adds P−S extra ticks of ramp until the value saturates. With no key held, it decays toward its default P−S ticks early.
- **Ground state.** While the landing gear is down, `isGrounded` is recomputed every tick from the restored pose; with the gear up it is false (game `VehicleControl.cpp:1902-1909`). What stays stale is the landing-gear state (toggled inside `SetupInput` by `UpdateLandingGear`), the landing timers and `lastDriverTime` (game `VehicleControl.h:528-549`).
- **Remote aircraft.** Aircraft that the client predicts for other players are hit differently. Beyond the commands the server relays, the client repeats the last relayed one (engine `AsyncClient.cpp:400-405, 425-432`), so their first-time deltas are zero. On the replay tick they then get the whole accumulated change at once, rather than a reversal. *[inferred]*

How far ahead the client runs (P−S) depends on latency. On loopback it was one or two ticks, mostly two, and with 50 ms of forced latency on each side it was four to six, mostly five (stage 6). *[measured]*

The server, the listen host and single player never re-predict, so they are unaffected. Retail has the same code. Stage 6 measured the mismatch on loopback and fixed it (2026-09-30). How much a pilot feels it has not been judged.

### 1.4 OpenETQW code that leaked (removed in M5 step 6)

OpenETQW added vehicle interpolation that wrote values from render frames into state that tick code reads:
- the damped, free-pivot and smooth view paths (game `src/game/vehicles/VehicleView.cpp:556-568, 832-844, 1168-1187`);
- `sdTransport::ClientUpdateInterpolation` (game `Transport.cpp:5222-5247`);
- the chassis logs, which record the physics pose (`Transport.cpp:4404-4415`);
- the `UpdateModelTransform` early return (`Transport.cpp:5194-5196`);
- the proxy branch of `ClientUpdateView` (game `Game_local.cpp:6058-6079`).

These paths wrote `lastReturnedAxis`, which the free-pivot clamp reads (`VehicleView.cpp:727-730`). They also wrote `lastPushed*` and `thirdPersonView*`, which the server's AoR and antilag views reach through `GetAORView` (game `src/game/Player.cpp:15170-15187`). *[inspected]* None of this gave any seat a per-frame view. **Removed in M5 step 6** (ledger 029): the views are the SDK's again, and the transport code, the early return and the proxy branch are deleted. The line numbers above are those before step 6.

## 2. Can vehicles move entirely off the tick rate safely?

**Partly.**

| Part | Off the tick? | Why |
|---|---|---|
| What is drawn: camera, hull, riders, cockpit, other entities | **Yes** | It can be drawn every frame from copies built from the last two tick samples. Nothing is written back to tick state. |
| Mouse look, in seats where the mouse only aims the view | **Yes** | The late input sample already exists. The next tick's command already contains every bit of motion the frames showed. |
| Vehicle physics, controls, camera damping, turret limits | **No** | All of it is written for one 33 ms step. Changing the step changes handling. |
| Driving keys and the pilot's mouse reaching physics | **No** | Only a higher usercmd rate could shorten this, and that changes everything (§4). |

**Why the simulation cannot move** *[inspected unless marked]*
- Vehicle physics takes one explicit-Euler step per tick (game `Physics_RigidBodyMultiple.cpp:658-678, 2007-2312`).
- Each step first adds back a tick of gravity momentum and removes it again afterwards (`:2085-2087`, `:2267-2268`).
- Drive constraints act only in the step's first repetition (`:1560-1579`, `:2143`), and forces are zeroed after each repetition (`:2209-2210`).
- The controllers solve for dt = 33 ms. One comment reads "force needed to make it in one frame" (game `TransportComponents.cpp:4971`); see also `:1803`, `:5028`, `:5635-5639`, `:5800-5804`.
- Camera damping and the turret rate limits are rate × `gameLocal.msec` (game `VehicleView.cpp:420`; `TransportExtras.cpp:525-527`; `VehicleIK.cpp:826-952`).
- Splitting one step into two moves the position by an extra a·dt²/4 per tick. At the pseudo-hover's 4000 u/s² limit that is about 1.1 units per tick. *[derived]*
- The server simulates at 30 Hz, and client prediction must match it.

**Today's tests would not catch a change in handling.** `SIM_HASH` hashes only the vehicle's origin and linear velocity, to 0.01 (game `Game_local.cpp:11636-11642, 11682-11700`). The replay compares one build with itself across caps, and a change inside the tick would still be identical across caps. Stage 0 adds the guard that would catch it. *[inspected]*

**Why presentation can move.** Everything the player sees is available locally at every tick:
- the post-decay render pose of every entity;
- the camera the tick computed;
- the newest input sample.

Drawing from copies of these touches no tick state. openQ4 did the same (openq4 `docs/dev/high-framerate-rendering-plan.md:7-19`). It also rejected raising the usercmd rate (`:371-382`). *[inspected]*

**The cost to keep in view.** Interpolating between ticks n−1 and n draws poses a fixed 33 ms old. Today's sample-and-hold draws them 0–33 ms old, 16.5 ms on average. So interpolation alone adds about 16.5 ms on average. openQ4's blend costs 16.7 ms at its 60 Hz. At ETQW's 30 Hz it costs twice that, which is why this plan pairs the blend with late mouse look (stage 5) and an optional lead (stage 7). *[derived]*

**Latency relative to today** (mean; *[derived]*):

| What moves | Today | Stages 3–4 (blend) | + stage 5 (late look) | + stage 7 (lead L = 1, where the prediction is accurate) |
|---|---|---|---|---|
| Mouse look, angle-driven seat | 0–33 ms, jumps of 30 Hz | +16.5 ms, smooth | **−16.5 ms** (on-foot latency), smooth | same as stage 5 |
| Camera motion caused by the hull (driver, passenger) | 0–33 ms, stepped | +16.5 ms, smooth | +16.5 ms, smooth | **−16.5 ms** plus prediction error, smooth |
| Pilot attitude from the mouse | shown in one step at tick n+1 | +16.5 ms (a regression) | +16.5 ms (no late look for pilots) | starts one tick earlier than today; one tick ahead of the blend |
| Driving keys into physics | 0–33 ms | unchanged | unchanged | unchanged |
| Remote entities while seated | 0–33 ms, stepped | fixed 33 ms, smooth (the retail D5 shape) | same | same |

## 3. Recommended plan

### 3.1 Rules every stage keeps

1. **No simulation code changes.** The one exception is stage 6's fix, which runs only when `gameLocal.isClient` is set, so the server, the listen host and single player take byte-identical code paths. Its probe (`AIR_CTL`) is a read-only print on both sides, behind a cheat CVar.
2. **Samples are taken only on new ticks.** They read the post-decay `renderEntity` and the tick camera, never the physics pose (openQ4's lesson, openQ4-game `src/game/Entity.cpp:1898-1904`). Sampling reads fields directly. It never calls `GetWorldOrigin`/`GetWorldAxis`, which write `renderEntity` through `UpdateModelTransform` (game `Entity.cpp:3614-3651`).
3. **Render frames write only three things:**
   - the presentation's own members;
   - `idPlayerView::currentView`;
   - the renderer's entity defs, through copies that are restored afterwards.
4. **Render frames never call anything that changes game state:**
   - `Present`, which writes `lastPushed*` and relinks combat models (game `Entity.cpp:1726-1791`);
   - `BecomeActive`, which can add an entity to the next tick's think set (game `Entity.cpp:1314-1352`);
   - `UpdateVisuals`, `UpdateModelTransform`, `GetWorldOrigin`/`GetWorldAxis` and `LinkCombat`;
   - `ClipView`, which relinks clip models (game `VehicleView.cpp:194-226`);
   - the tick's `ClampViewAngles`, which clears the mutable `freshEntry` (game `VehicleView.cpp:174-181`);
   - `CalculateViewPos`, prediction-error `Decay`, `Think`, `RunPhysics` and `gameLocal.random`.
5. **The field of view is never blended.** The tick's `ClampFinalAxis` reads the FOV of the last drawn view (game `VehicleView.cpp:300`).
6. **Latched gates are re-checked at draw time.** At every draw, compare the live proxy, seat and view mode with the latch. On any mismatch (entry, exit, a seat swap, a camera cycle, a teleport, a clock reset), draw the tick-n pose with no blend and no late look.
7. **Restore protocol.**
   - Before submitting a copy, save the renderer's current def (`GetRenderEntity`).
   - After `RenderScene`, submit the saved def again. Skip it if the entity's `spawnId` or `modelDefHandle` has changed.
   - As a second line of defence, re-submit the tick defs at every tick entry: `RunFrame`, `ClientPrediction`, `ClientReadSnapshot` and the reliable-message path.
   - Why: `UpdateEntityDef` is a plain struct copy, and a freed handle is reused by the next entity (engine `src/renderer/RenderWorld.cpp:99-120, 222-224`). Snapshots and reliable messages are handled on frames that ran no tick (engine `AsyncClient.cpp:319, 348, 703`). So restoring a stale tick-end sample could overwrite a newer def, or land on a different entity. *[mechanism inspected; whether it happens in play is inferred]*

### 3.2 Stages at a glance

| Stage | What | m5-plan step | Default seat list |
|---|---|---|---|
| 0 | Physics guard: per-tick vehicle trace, vehicle replay, cross-build golden | extends 5 | test only |
| 1 | Remove the leaks | 6 | — |
| 2 | Seat predicate, CVar, `listSeatViews`, guard test | 13 | empty (no seat qualifies yet) |
| 3 | Presentation core and generic seat blend | 14 (blend, render copy); shares its core with 9 | empty |
| 4 | Remote entities while seated | 8, 9 | follows stage 2 |
| 5 | Late mouse look in angle-driven seats | 14 (sub-tick mouse turn) | angle-driven seats join as they pass §3.3 |
| 6 | Aircraft re-prediction fix (client only) | 14a (done 2026-09-30) | on (`g_airControlHistory 1`) |
| 7 | Optional hull lead, using the physics' own next pose | beyond 14 | L = 0 |

Engine step 10 (tick frames drawn at their real fraction) is needed for full coverage; step 7 (the listen host) is done; see §3.4.

### Stage 0: physics guard

**Status: done 2026-09-30** (M5 step 5a; details in `docs/m5-plan.md`). `VEH_TRACE`, `g_determinismTest 2`, `high-refresh-determinism` comparing `VEH_TRACE` across caps, and `vehicle-physics-golden` with a per-toolchain golden. The aircraft is spawned by the schedule: valley has none at warmup.

**What changes**
- **Game.** Behind `g_simHash`, print a `VEH_TRACE` line each tick for each occupied rigid-body transport. Print full precision (`%.9g`, as `SIM_VIEW` does). Contents:
  - origin, quaternion, linear and angular velocity;
  - `atRest` and the time of the last collision;
  - the control state: the wheeled control's `desiredDirection`, and the air control's `oldCmdAngles` and `collective`.
- **Game.** A second replay schedule (`g_determinismTest 2`):
  - a ground vehicle for about 150 ticks with throttle, steering, brake, a reverse and one wall contact;
  - then an aircraft, with `usercmd.angles` injected in the block at game `Game_local.cpp:5343-5376`. Today's replay injects no angles and no steering.
  - It is kept separate so that schedule 1's `SIM_HASH` values and the `KNOWN_VIEW_LEAK` ranges (then allowed; gone since step 6) did not move.
- **Engine tests.**
  - `high-refresh-determinism` also compares `VEH_TRACE` across caps.
  - A new `vehicle-physics-golden` test compares `VEH_TRACE` with a file recorded from main before any M5 vehicle PR lands. It is bit-exact within one toolchain and fixed-precision across toolchains. It is regenerated only with a flag and a stated reason.

**What stays authoritative:** everything. The prints are behind test CVars, and the injection runs only under `g_determinismTest`.

**Player-visible gain:** none. It is the evidence that no later stage changes handling.

**Measurement and safety proof:** the golden runs in the local merge gate on every PR that touches `src/game/vehicles` or `src/game/physics`.

**Effort:** 2–3 days. *[inferred]*

### Stage 1: remove the leaks (m5-plan step 6)

**Status: done 2026-09-30** (M5 step 6: the network test first, game #22 / engine #43; then game #24 / engine #46; ledger 029 widened, 056 and 057; details and numbers in `docs/m5-plan.md`). As below, with the owner's seven decisions of 2026-09-30 (STATUS): the view code is deleted, not kept as helpers (decision 1), and `unlockedDraw` keeps retail's lifetime (decision 3).

**What changes.** Everything in step 6: revert the three view paths to SDK code, and delete `ClientUpdateInterpolation`, the chassis logs, the `UpdateModelTransform` early return and the proxy branch. Plus two additions:
- Run the on-foot blocks of `ClientUpdateView` only when `p->GetProxyEntity() == NULL` at draw time. The latch is taken before entity Think, and the replay enters the vehicle inside the tick with `CMD_EXEC_NOW` (game `Game_local.cpp:5362-5363`). Ledger 056.
- Reset `originlog` at the pose log of the first tick whose view player shows a different proxy, seat or view mode from the last log's: slot n-1 takes slot n, so that tick's base draw does not rewind and its render-only frames hold. Not on a change of view player, a respawn or a teleport (decision 5). It replaces OpenETQW's event-time resets, which read the previous view's camera on a camera cycle and did not run on an exit. Ledger 057.
- `unlock.unlockedDraw` keeps retail's lifetime, from a render-only frame to the next tick (decision 3), rather than being cleared after `RenderPlayerView` as first planned: once the views are reverted and the `UpdateModelTransform` early return is gone, only SDK readers (game `Entity.cpp:1783`, `Parachute.cpp:238`, `JetPack.cpp:412`, `PlayerView.cpp:959`; the one at `Entity.cpp:1741` is in the compiled-out producer) and the test logs (`FRAME_POSE`, `g_logVehicleViews`) read it, and clearing it would change frames that draw more than once.

**What stays authoritative:** the physics.
- One authoritative change: the server's AoR and antilag view for smooth-view seats goes back from the previous tick's camera to the current one. Compare SDK `game/vehicles/VehicleView.cpp:1077-1081` with game `VehicleView.cpp:1168-1187` before step 6. With it, smooth-seat camera damping, the `smooth_locked` view angles and third-person turret aim are the SDK's again (decision 2).
- D4 approved this change, and it matches retail.

**Player-visible gain:** none directly. It removes chase-camera wobble at snapshot rate on clients, where it existed (measured: client snapshot reads already returned the tick's view; client moveables' in-tick calls did not), and the two interleaved chains the server's smooth camera split into after a camera cycle. After an exit between ticks the first on-foot tick no longer draws from the vehicle camera.

**Measurement and safety proof:**
- `high-refresh-determinism` passes without its `KNOWN_VIEW_LEAK` allowance: `SIM_VIEW` is identical to cap 30's on every tick at every cap.
- `seat-gate` passes without its entry-leak allowance.
- `vehicle-view-net` (owner decision 4, added before the revert with the old behaviour as known gaps) passes with none: every AoR call returns the tick's view origin, on the server and the client; in the smooth view it also returns the tick's angles and changes nothing (the standard view's SDK path recalculates its axis, decision 7); and the exit draws no rewind.
- The stage-0 golden is unchanged.

**Effort:** estimated 3–5 days, including tracing which write caused the weapon-log leak *[inferred]*. The trace was done in step 6 (the proxy branch's `PresentWeapon`).

### Stage 2: seat predicate and switch (m5-plan step 13)

**Status: done 2026-09-30** (M5 step 13; [`decisions/0003-vehicle-seat-gate.md`](decisions/0003-vehicle-seat-gate.md), ledger row 054). The predicate, `g_unlock_vehicleSeats` (default `default`, the built-in list), recipes keyed by (proxy kind, view class), `listSeatViews`, the latch and draw-time re-check, a per-tick `SEAT_GATE` line, a seat tour (`g_determinismTest 3`) and `seat-gate`. The recipe table and the built-in list are empty, so no seat is allowed yet and nothing drawn or simulated changes. Retail seat names are localisation paths, so keys read `badger:guis_vehicles_badger_driver:standard`. §5.2 has the per-seat answers.

**What changes**
- Add `idPlayer::IsSeatPresentationAllowed()` next to `IsFPSUnlock` (game `Player.cpp:8773-8829`). `IsFPSUnlock` stays byte-identical.
- The new predicate makes the same checks except the proxy check. In its place:
  - the proxy must be an `sdTransport` that is not teleporting;
  - its current view class must have a recipe;
  - the seat's key must be allowed by `g_unlock_vehicleSeats` (the name is a proposal).
- Keys take the form `vehicleScript:positionName:viewType`, and wildcards are allowed. The default list starts empty (retail behaviour) and grows as seats pass §3.3 (D10: on by default). Wildcards never match mounted guns (their proxy is set at game `src/game/ScriptEntity.cpp:652`) or the jetpack until those have a recipe.
- The predicate is latched next to `canUnlockFrames` (game `Game_local.cpp:5252-5261`, `Game_network.cpp:3795-3803`) and re-checked at draw time.
- A read-only cheat command, `listSeatViews`, prints for each seat:
  - position name and view type;
  - `thirdPerson`, `tophatRequired`, `autoCenter` and the clamps;
  - the eye joint, and whether it is parented to a joint the IK drives.

  This answers the per-seat question that the binary vehicle decls hide.
- A decision record in `docs/decisions/`, a ledger row, and a guard test that an excluded proxy never unlocks.

**What stays authoritative:** everything.

**Player-visible gain:** none by itself. It gives the owner a switch for each seat.

**Effort:** 2–3 days. *[inferred]*

### Stage 3: presentation core and generic seat blend (m5-plan step 14, first part)

*Status (2026-10-05; since 2026-10-07 `pivot` and `freepivot` are presentable too, with the turret joints blended per draw, ledger 070): built for the first-person `standard` view and the chase views (`smooth`, `smooth_free`, `smooth_locked`; `pivot` and `freepivot` then followed turret joints that were not blended); ledger row 062, meson `seat-poses`. `SIM_VIEW` carries the proxy's def at tick entry (`vdef=`, compared across runs by `seat-gate`). Chase views are built too (their own tick cameras blended in the world). The jetpack, gunner views and the narrow audit CVar are not built yet.*

**What changes (game)**
- **A new presentation object.** `sdPresentation` (proposed as `src/game/Presentation.{h,cpp}`), owned by `idGameLocal`.
- **Tick sample, on new ticks only.**
  - On single player and the host, it runs after the pose log and before `EmitSimView` (game `Game_local.cpp:5466-5488`). On a client, it runs inside `if ( isNewFrame )` (game `Game_network.cpp:3937-3990`).
  - It records the renderer def of the local player's transport, its riders and the cockpit, with the frame number and the seat and view key.
  - It records the tick camera, and `Crel`: the camera expressed in the hull's render frame.
- **Draw, at one fraction `f` for everything:** the same fraction the on-foot view uses (frame-path §2).
  - The hull copy is lerped and slerped from n−1 to n.
  - Riders and the cockpit are drawn relative to their carrier: `presentedMaster × (master(n)⁻¹ × child(n))`.
  - The camera is `hullCopy × lerp(Crel(n−1), Crel(n), f)`, which stays continuous at tick boundaries.
  - Chase (smooth) views blend their own tick outputs.
  - The FOV is the tick's.
- **Submit and restore.** These go around `RenderScene` in `idPlayerView::SingleView` (game `src/game/PlayerView.cpp:416-422`), following rule 7.
- **Drawn view.** `RenderPlayerView` draws the presented view, not `viewPlayer->renderView` (game `PlayerView.cpp:952`).
- **Base draws.** Frames that ran a tick use the same recipe at `f = 0` until engine step 10, so nothing steps back at a tick boundary.
- **Continuity guards.** Draw the tick-n pose with no blend when:
  - the frames are not consecutive;
  - the seat, view or proxy changed;
  - the player teleported, `freshEntry` is pending, or the game is paused;
  - the pose jumped more than max(64 u, 2·|v|·33 ms) or 45°.

  openQ4's fixed 32-unit camera guard (openQ4-game `src/game/Player.cpp:13249-13251`) would snap above about 960 u/s at 30 Hz. *[derived]*

**What stays authoritative:** everything. Samples read tick state, and draws write only what rule 3 allows.

**Player-visible gain**
- The camera, hull, riders and cockpit move on every frame.
- Camera motion caused by the hull is drawn at a fixed age of 33 ms, which is +16.5 ms on average against today. That is the trade on-foot drawing already makes.
- Mouse look also gets +16.5 ms until stage 5 lands. Angle-driven seats must therefore not be switched on before stage 5.

**Measurement.** This is the seat variant of the `frame-poses` capture, and since 2026-10-19 it closes m5-plan step 3's seat part (with S5, the cockpit): devmap, and join with a headless peer. It checks:
- the view origin, view yaw, the `veh=` hull pose and the rider poses change on at least 99% of draw pairs while driving;
- nothing steps backwards along the velocity;
- the step across a tick is at most 2.5× the median step within a tick;
- within a tick, the eye stays fixed in the drawn hull's frame (< 0.01 u);
- negative control: with the CVar off, the stepped shape returns (about 50% of pairs at 60, 25% at 120);
- guard: an excluded proxy is never unlocked.

**Safety proof**
- An A/B replay, CVar off against on, gives identical `SIM_HASH`, `SIM_VIEW` and `VEH_TRACE` on every tick.
- A replay across caps with the CVar on is identical.
- `SIM_VIEW` gains the proxy's renderer def at tick start, which proves the restore worked.
- A static contract test, in the style of openQ4's `tools/tests/presentation_interpolation_contract.py`, fails if the presentation files:
  - call anything on rule 4's list;
  - assign `lastPushed*`, `renderEntity.`, `viewAngles`, `clientViewAngles`, `vehicleEye*`, `firstPersonView*` or `p->renderView`;
  - or if `sdPresentation` is referenced anywhere outside the listed hooks.
- A narrow audit CVar checksums the fields the frame-path §6.9 leaks used, before and after each draw's presentation work. Its self-test must trip. It is kept narrow on purpose.

**Effort:** 1.5–2 weeks, including tests. *[inferred]*

### Stage 4: remote entities while seated (m5-plan steps 8, 9)

*Status (2026-10-05): built (ledger row 063): the producer and both consumers run while a seat's presentation is allowed, the local seat's vehicle and riders are left to `sdPresentation`, and riders of interpolated vehicles use the carrier rule. Meson `seat-poses-join`/`-listen` measure a remote player; remote vehicles are not measured yet.*

**What changes.** Steps 8 and 9 restore the producer as render copies: origin only and one tick behind (D5), with nothing written back (D6). This stage makes two further changes:
- It changes their gate from `canUnlockFrames` to `canUnlockFrames || seatActive`, and keeps retail's `AOR_INHIBIT_IK` exclusion (retail `idEntity::Present`).
- Riders of remote vehicles use the carrier rule. Otherwise a rider would slide against the seat, because origins are lerped independently while axes snap. *[inferred]*

**What stays authoritative:** everything. Remote targets are drawn 33 ms behind, and vehicles have no antilag (game `src/game/AntiLag.cpp:763-766, 1061-1064`). So a drawn remote vehicle trails the pose the server tests by one tick of its own motion. This is retail's shape.

**Player-visible gain:** other vehicles, players and projectiles move on every frame while you sit in an allowed seat.

This stage must ship in the same release as stage 3. Otherwise targets stepping at 30 Hz under a smooth camera would look worse than today's uniform stepping. *[inferred]*

**Measurement:** a remote-vehicle pose detector in the join seat capture (at least 99% of pairs change), with its negative control.

**Effort:** 2–4 days on top of steps 8 and 9. *[inferred]*

### Stage 5: late mouse look in angle-driven seats (m5-plan step 14, second part)

**Status (2026-10-05; updated 2026-10-18: the chase views, the jetpack, the rate-limited classes and `pivot` have late look too, 2026-10-06 to 10-08, ledgers 064 and 065; `freepivot` and the `clampDamped*` final clamp 2026-10-20, ledger 064).** First tier built for the `standard` class (ledger 064, `g_unlock_vehicleLook`), in seats whose clamp is the base class's with extents only and that do not need the tophat: the drawn angles, r_n (with no input counted on a tick that ignored its command's turn), the pure clamp and the zero-Δ cases below, plus `InhibitTurning`; a pending fresh entry draws the tick view (row 062's guard). Measured by the first changed draw after `+left` (meson `seat-poses`: 1.00 draws on, 2.89 off at cap 120) and a held turn that never turns back (L3). Chase views (`smooth`, `smooth_free`) built 2026-10-06 as an orbit about the focus while the tick's camera lies on the ray back from it (ledger 065; L4 checks the orbit). `smooth_locked` in tophat mode built 2026-10-07 (ledger 065); the jetpack, rate-limited classes and the `pivot` view (its eye arm swung with the drawn angles) built 2026-10-08 (ledgers 062, 064). Free-pivot and the `clampDamped*` final clamp were built 2026-10-20 (ledger 064). Free-pivot's screen-space turn and owner-space clamps, and the final clamp, are shared with the tick (`LateLookTurn`, `ClampInOwnerSpace`, `ClampFinalAxisPure`). Not built yet: the scripted Δ for replay. The per-tick angle-error check is approximated by `seat-poses` L3/L4 under a rate-bound held turn (a draw that ended off the next tick's angles would step at the tick).

**What changes (game)**
- **The late turn.** Use the render-only usercmd the engine already passes: Δ = `SHORT2ANGLE((short)(cmd.angles − p->usercmd.angles))`, normalized to the shortest arc. This is the same form as the on-foot path (game `Game_local.cpp:6087`).
- **The drawn angles.** A_k = A_n − (1−f)·r_n + clampPure(A_n, Δ, f·33 ms).
  - r_n = wrap(A_n − A_{n−1}) − wrap(SHORT2ANGLE((short)(cmd_n − cmd_{n−1}))) is the angle change that input did not cause, such as auto-centre and clamps.
  - Every angle difference here is a shortest-arc delta: `wrap` is `idMath::AngleNormalize180`, and the usercmd difference is taken in 16 bits (`(short)`) before `SHORT2ANGLE`. Yaw wraps at ±180° and usercmd angles wrap at 16 bits, so a direct subtraction would turn a small turn across the wrap (179° to −179°) into about −358°, and spreading that residual over the tick would swing the camera the long way round or snap it. Δ is computed the same way: `SHORT2ANGLE( (short)( cmd.angles − p->usercmd.angles ) )`, then normalized.
  - Spreading r_n over the tick stops it showing as a step at the boundary.
- **`clampPure`** is a new const method for each view class. It follows the class's `ClampViewAngles`, with two differences:
  - it skips `DoFreshEntryAngles`;
  - it uses a `ClampAngle` overload that takes the elapsed time. The tick overload keeps `gameLocal.msec` (game `TransportExtras.cpp:525-527`).
- **Δ is zero when:**
  - the seat requires the tophat and it is not held (game `Transport.cpp:4264`);
  - the player is teleporting;
  - the seat's weapon is EMPed (`Transport.cpp:4311`);
  - `freshEntry` is pending;
  - the class locks its angles.
- **Composition per class:**
  - damped and pivot;
  - free-pivot, using the drawn hull's up axis;
  - Icarus;
  - smooth and smooth_free, as an orbit, only while the tick's world clamp did not shorten the boom;
  - smooth_locked, only while the tophat is held (game `VehicleView.cpp:1503-1539`);
  - never for `_Player` views or pilots.
- **Order.** The first tier is seats whose clamps are only extents. Rate-limited seats follow once their `clampPure` passes the angle-error check below.
- **Replay.** Under `g_determinismTest`, Δ is a scripted function of (framenum + f), and the tick `cmd.angles` get the matching ramp.

**What stays authoritative:** everything.
- The next tick's command already contains every delta the frames showed (engine `UsercmdGen.cpp:486-489`).
- `sdTransport::UpdateViewAngles` and `ClampViewAngles` still compute the aim at the tick, unchanged (game `Transport.cpp:4258-4321`).
- The turret's rate and filter lag stays on the tick, because it decides where shots come from.

**Player-visible gain**
- Removes the 0–33 ms of extra look delay (16.5 ms mean). This also cancels stage 3's +16.5 ms.
- A 6° jump per tick at 180°/s becomes 1.5°, 1.25° or 0.75° per frame at 120, 144 or 240 fps. *[derived]*
- The crosshair you see matches the next tick's fired aim more closely. Today up to 33 ms of mouse motion is fired before it is ever shown. *[inferred]*

**Measurement**
- In the gunner seat, after `+left` executes, the first draw shows the yaw change. With the CVar off, the first changed draw is the next tick's draw.
- View yaw changes on at least 99% of draw pairs while turning.
- Each tick, log the error between the angles drawn at the last draw and the tick's actual angles. The p95 bound is set by the first run, for example 0.05°. This catches a pure clamp that has drifted from the real one.

**Safety proof:** the stage-3 A/B and cross-cap replays, with the scripted Δ running, plus the contract test.

**Effort:** about 1 week for the first tier, and about 1 more week for all the rate-limited classes. *[inferred]*

### Stage 6: aircraft re-prediction fix (client only; m5-plan step 14a)

Done 2026-09-30: the fix, `AIR_CTL` and meson `aircraft-reprediction` are built as below, and the results are at the end of this stage (ledger row 055). The game line references in this stage stay those of game `cd18aee`, before the change.

**How the frames line up** *[inspected]*
- A snapshot carries the state after server tick S. `ClientReadSnapshot` sets `framenum = S` (game `Game_network.cpp:2450`). The engine then replays the usercmds from S up to its newest tick through `ClientPrediction` (engine `src/framework/async/AsyncClient.cpp:345-349`, after the snapshot read at `:613-638`).
- `ClientPrediction` increments `framenum` before any entity thinks (game `Game_network.cpp:3819`). So the first replayed tick is S+1, and it consumes usercmd S. A server tick F likewise consumes usercmd F−1 (engine `AsyncServer.cpp:452-457`; game `Game_local.cpp:5192`). Both counters restart at map load.
- So `framenum` pairs a client's prediction of a tick with the server's run of the same tick. The driver's usercmd carries `gameFrame = F−1` on both sides, and that confirms `framenum` and the usercmd index move in lockstep.
- **What that check cannot see.** It cannot see a command the server duplicated because it arrived late. A duplicate keeps `gameFrame = F−1` and only increments `duplicateCount`, and a late arrival overwrites the slot with `duplicateCount = 0` (engine `UsercmdCodec.cpp:90-97`, `AsyncServer.cpp:627-639, 1255-1259`). So the server's count must be logged when the tick runs.
- `isNewFrame` is false on every replayed tick (game `Game_network.cpp:3836-3849`). A replayed tick thinks only the entities the snapshot made visible (`:3913-3920`), so the local pilot's aircraft does replay; an aircraft outside the PVS does not. The control's `Update`, which calls `SetupInput`, runs before the tick's physics (`Transport.cpp:3366-3391`).

**What changes (game)**
- **A history ring in `sdAirVehicleControl`.**
  - The ring has 64 slots, each holding `{ frame, oldCmdAngles, collective, collectiveDefault }`. Every slot starts with `frame = -1`.
  - On a client, `SetupInput` records slot `framenum & 63` at its end (after `VehicleControl.cpp:2245`).
  - At its start, `SetupInput` restores the slot for `framenum − 1` when `gameLocal.isClient` is set, `g_airControlHistory` is on and that slot exists.
- **Why that is enough.**
  - On a first-time tick, the slot for `framenum − 1` holds the values already in place, so the restore changes something only on the first tick replayed after a snapshot.
  - On that tick, it brings back tick S's command angles and the client's own collective after tick S. No rewind detection is needed.
  - The ring records whatever the CVar says, so a test can switch the CVar at run time.
  - 64 ticks cover `net_clientMaxPrediction` (1000 ms, 30 ticks). After a prediction restart (engine `AsyncClient.cpp:621-631`) the slot may be missing, and the tick takes today's path.
- **`g_airControlHistory`** (`CVAR_GAME | CVAR_BOOL`, default 1). It exists for the A/B measurement. It is not a cheat, because it changes only the client's own prediction.
- **What the ring cannot make exact on a client:**
  - commands the server duplicated. It reports only a count, and the difference stays in `collective` until the value next saturates;
  - `collective`, which comes back from the client's own prediction of tick S. An earlier misprediction persists until the value saturates, decays to its default or the aircraft lands;
  - the landing gear, its timers and `lastDriverTime`;
  - the entry tick, because `oldCmdAngles` is zero without a driver;
  - with simple flight controls (`ui_advancedFlightControls 0`, the default), the input itself. Auto-levelling reads the predicted pose (`VehicleControl.cpp:2000-2106`), and that pose carries the snapshot's quantization.
- **Remote aircraft** that the client predicts take the same path. For them the ring restores the client's own guess of the earlier command, which is exact only if the previous snapshot relayed it (engine `AsyncServer.cpp:1681-1691`). The test does not cover them. *[inferred]*
- **Networking only if a residual remains.** `collective` and `oldCmdAngles` are networked only if loopback shows a residual (owner, 2026-09-29). That bumps the protocol's minor version (engine `src/framework/async/AsyncNetwork.h:49`), justified because the snapshot is the only exact source.

**Tooling (game, written before the fix)**
- **An `AIR_CTL` line.** It is behind `g_logAirControl` (`CVAR_GAME | CVAR_BOOL | CVAR_CHEAT`, a cheat like `g_logVehicleViews`, because it logs other players' input). At the end of `sdAirVehicleControl::SetupInput`, it prints one line per tick for each air vehicle with a driver: on the server, and on a client for every predicted tick, first-time or replayed. Fields:
  - `side=srv|cl`; `tick=` (`framenum`); `ent=`; `uf=` (the driver's usercmd `gameFrame`); `dup=` (its `duplicateCount` as the tick runs); `adv=` (the driver's `ui_advancedFlightControls`, which reaches the server as userinfo and can lag);
  - `pred=` (1 when `isNewFrame` is false); `since=` (`framenum` minus the last snapshot's frame, kept in a new member set by `ClientReadSnapshot`); `ahead=` on a client (the newest frame it predicted before the snapshot was read, a new member, minus the snapshot's frame, which is P−S; on the first snapshot after a map load it is meaningless, and those ticks are never paired). After a restart from too far ahead, the engine resets its frame to the snapshot's (engine `AsyncClient.cpp:621-631`), so the next first-time ticks also print `pred=1`, with `ahead=` above 30;
  - `cmd=` (the tick's command angles, as the usercmd's shorts);
  - `base=` (the `oldCmdAngles` the delta used, after any restore, also as shorts);
  - `in=` (the pitch, roll and yaw inputs);
  - `coll=` and `collDef=` (after the update);
  - `fwd=`, `grounded=` and `gear=`;
  - the pose and velocities the tick starts from, at full precision, as `VEH_TRACE` prints them.
- **Why a new line.**
  - `VEH_TRACE` is compared verbatim by `vehicle-physics-golden`, `high-refresh-determinism` and `seat-gate`, and it prints only on the server.
  - Its `cmdang` is the post-tick `oldCmdAngles`, which equals the tick's command on both sides, so it cannot show the bug.
- **Why in `SetupInput`.**
  - It runs on every tick a vehicle thinks, before the tick's physics, so no hook is needed in `ClientPrediction`, where step 6 adds its probes.
  - The pose it prints is the state after the previous tick, so an input error on tick S+1 shows in the pose at `since=2`.
- **No engine change.**
  - The test steers with `+left`/`+right` (roll) and `clientMove` (collective).
  - Pitch is not driven. Pitch and roll come from one subtraction (`VehicleControl.cpp:1984-1986`) and the fix restores the whole angle, so the roll axis tests both.
  - The engine has no command that drives pitch: `UsercmdGen.cpp:384-385` reads `UB_LOOKUP`/`UB_LOOKDOWN`, but nothing sets them.

**What stays authoritative:** everything.
- The server, the listen host and single player never re-predict, and they take the unchanged path.
- The ring is written and read only when `gameLocal.isClient` is set.

**Player-visible gain:** mouse-flown Hornet, Anansi and hover-copter pilots on a network client stop losing or reversing one tick of cyclic with every snapshot, which arrives about every 50–67 ms, and their collective stops running ahead. The control-state mismatch is measured (results below); how much a pilot feels it is not.

**Measurement** (`tests/test_aircraft_reprediction.py`, meson `aircraft-reprediction`, needs retail data)
- **Setup.** As in `vehicle-view-net`: a Vulkan listen host on valley (cheats allowed) and a Vulkan client as the pilot.
  - Why not a dedicated server:
    - The headless dedicated binary crashes as a client in a vehicle (`tests/test_vehicle_view_net.py`).
    - A dedicated server has no local player to `spawn` from.
    - `networkSpawn` from the client puts the vehicle 80 u in front of the player, which at valley's GDF spawn is under an overhang.
  - The host runs `spawn vehicle_anansi origin "9100 -10100 1700" angle 67`, about 280 u above open ground beside the badger's start spot, and then `enterVehicle <pilot> vehicle_anansi` at once. With a driver seated at once, the rotor starts before the aircraft falls far, as schedule 2 of `g_determinismTest` does. On its gear the anansi does not reliably leave the ground on collective alone: spawned on the ground, it stayed down for 30 s in two of four tries. *[measured]*
  - The pilot sets `ui_advancedFlightControls 1`, `in_yawSpeed 30` and `in_angleSpeedKey 1`. It holds `clientMove 127 0 0` until the server's trace shows the aircraft 100 u above its lowest point.
  - Its collective moved about 0.24 a second at `clientMove 30`, between limits of ±0.3. *[measured]*
- **Phases**, 9 s each. On loopback only about half the snapshots start a replay: in one roll phase, 121 snapshots arrived and 62 started one, about 10 pairs a second against 20 with latency. So 6 s phases left 52 kept pairs, under the minimum. *[measured]*
  - *still*: collective held at full, so it is saturated, and the aircraft hovers where the flight ceiling fades its lift. This gives the floor that snapshot quantization sets.
  - *roll*: collective still at full; `+left` and `+right`, alternating each second (about 1° a tick, under the clamp).
  - *collective*: `clientMove -30` for 1 s, then `clientMove 30` for 1.5 s, repeated. The value moves for most of each cycle, then saturates at the top for about half a second. That realigns client and server after any duplicated command.
- **Legs.**
  - The phases run with the fix off and with it on, switching `g_airControlHistory` on the pilot with a 1 s gap.
  - Each runs at loopback latency, and again with `net_forceLatency 50` on both processes. With that latency the client runs at least two ticks ahead, so the input is reversed rather than lost.
  - The loopback legs run first, so latency only ever goes up. When it goes up, the client's prediction takes about 3 s to absorb the change: the prediction moves 1 ms per snapshot (engine `AsyncClient.cpp:648`), and until it catches up the server duplicates commands. The next phase waits until the server logs no `dup>0` for 2 s. After 15 s the test fails as inconclusive.
  - One more leg with simple controls is reported only.
  - About 4 minutes in total.
- **Pairing.**
  - Each client line with `pred=1 since=1` is paired with the server line of the same `tick` and `ent`. Their `uf` must match, which confirms the lockstep.
  - A pair is dropped on the entry tick; when the server's line for tick S or S+1 has `dup>0`; or when `ahead=` is above 30 (`net_clientMaxPrediction`), which marks a prediction restart.
  - For check 3, the pairs from a server duplicate until the next saturation are also dropped. The next saturation is the first tick at which both sides are at the limit.
  - Drops are counted and reported.
  - Each leg and phase needs at least 60 kept pairs, and at most a quarter may be dropped. In 6 s, 90–120 snapshots arrive, one every 50–67 ms. Otherwise the test fails as inconclusive, so a check cannot pass on no data.
- **Checks:**
  1. **Fix on.** In every kept pair, the client's `base` equals the server's (exact shorts). Where both lines have `adv=1`, `in` matches too.
  2. **Fix off.** This is the negative control, showing that the test sees the bug:
     - `base` differs in at least 80% of the roll phase's kept pairs;
     - `coll` differs in at least 80% of the collective phase's kept pairs where the server's value is between its limits.

     The run reports the distribution of `ahead=`.
  3. **Collective.** With the fix on, `coll` equals the server's in every kept pair of the collective phase.
  4. **Physics, at `since=2`.** Reported, not judged: for each leg, the median and p95 of the pose and angular-velocity errors, against the still phase's floor. A bound is set once there are runs to set it from.
  5. **Elsewhere.** `vehicle-physics-golden`, `high-refresh-determinism`, `seat-gate` and the frame-poses tests are unchanged.
- **The residual rule.** A fix-on pair that fails check 1 or 3 is a residual, unless a duplicated command or a prediction restart explains it. If one remains, `oldCmdAngles` and `collective` are networked, as the owner decided.

**Results (2026-09-30, the macOS host, game `m5/14a-air-control`; the range over three runs)** *[measured]*

| Leg (advanced controls) | Client ahead (P−S) | Roll pairs with `base` differing | Moving-collective pairs with `coll` differing | Angular-velocity error at `since=2` (roll and collective phases), p95 |
|---|---|---|---|---|
| loopback, fix off | 1–2 (mostly 2) | 83 of 83 | 76–77 of 77–78 | 0.057 rad/s |
| loopback, fix on | 1–2 | 0 of 83–84 | 0 of 77–78 | 1.4–1.7×10⁻⁵ rad/s |
| 50 ms, fix off | 4–6 (mostly 5) | 161 of 161 | 147–151 of 147–151 | 0.066–0.067 rad/s |
| 50 ms, fix on | 4–6 | 0 of 160–161 | 0 of 147–150 | 1.4–1.5×10⁻⁵ rad/s |

- No command was duplicated and no prediction restarted, so no pair was dropped.
- With the fix on, `in=` also matched in every pair.
- In the simple-controls leg, `base` and collective were equal in every pair. `in=` differed wherever auto-levelling acted: in every pair of the still and collective phases, but only 2 of 161 roll pairs, because it acts when the mouse is idle. It reads the predicted pose, which carries the snapshot's quantization, as anticipated above. The leg's angular-velocity error in the roll and collective phases stayed at the fix-on level (p95 1.4–1.5×10⁻⁵ rad/s).
- At a direction reversal one tick ahead, the fix-off collective can match, hence 76–77 of 77–78 at loopback.
- The origin error at `since=2` (p95 about 0.04 u) and the orientation error (p95 under 5×10⁻⁵°) are the same with the fix on and off. The snapshot's quantization sets them; one tick of wrong cyclic shows first in the angular velocity. The still phase's floor in the advanced-controls legs: origin p95 0.013–0.038 u, angular velocity p95 at most 1.1×10⁻⁷ rad/s.
- There is no residual, so `oldCmdAngles` and `collective` stay un-networked.

**Effort:** about 1 day for the tooling and the fix, plus 2–3 days for the test. It needs a ledger row, because this client behaviour differs from retail. *[inferred]*

### Stage 7 (optional): hull lead using the physics' own next pose

> **Predictor refined (2026-10-06): see [`hull-lead.md`](hull-lead.md), the step 14b plan.** A measurement on the scripted badger and anansi replay found that the physics' own step on a copy is (v + g·dt)·dt about the centre of mass plus ω·dt, because the external forces are already zeroed after `Evaluate`. It is essentially exact in free flight (anansi median 0.005 u), but misses the ground contacts (badger median 0.67 u). There, extrapolating the presentation's last three samples is better (0.095 u), and needs no physics code, though it amplifies a network client's decay noise. The plan keeps the physics step, the samples and a hybrid as candidates, chosen per class by measurement. The physics step there is a closed form in `SampleTick`, not an `idODE_Euler` step on a copy in `PostThink`. It reads the physics pose at tick time, an explicit exception to §3.1 rule 2: only its delta reaches the draw. Impacts overshoot by up to 33 u. The rest of this stage (the α draw, L per class, `VEH_PRED`, the measurement and the owner's bound) stands, as refined there.

**What changes (game)**
- **`PredictNextPose`.** A const `sdPhysics_RigidBodyMultiple::PredictNextPose( msec, origin, axis )` works on a copy of `current->i`. It:
  - adds back the gravity momentum exactly as `Evaluate` does (`:2085-2087`);
  - makes the same centre-of-mass shift;
  - runs its own `idODE_Euler` step;
  - re-orthonormalizes the result;
  - copies `FixDenormals` (`:2010-2013`).

  It returns the current pose when the body is at rest, bound, frozen or paused.
- **Where it is called.** At the end of `sdTransport::PostThink`, after `Present`, so the pose it starts from has already had prediction-error decay applied. It stores `delta = predicted − physics(n)` in the presentation record. Walkers (`idPhysics_Monster`) and the jetpack use interpolation.
- **`VEH_PRED`.** Each tick, log the error between `physics(n)` and the prediction made at n−1. Log it with the number of custom constraints and impulses applied that tick, so that errors can be attributed instead of treated as bugs.
- **Drawing.** The hull is drawn at α = f + L − 1 ticks from n:
  - α < 0: lerp from n−1 to n;
  - α ≥ 0: pose(n) + α·delta.

  When a new tick arrives, the correction is faded out over a few milliseconds. L is set per vehicle class and defaults to 0.
- **Why not plain velocity extrapolation.** Stored momentum leaves out one tick of gravity (game `Physics_RigidBodyMultiple.cpp:2085-2087, 2267-2268`; `GetLinearVelocity` = momentum/mass at `:2762-2765`). So v·dt misses g·dt² ≈ 1.16 u per tick at g = 1066 (game `src/game/Common.h:32-33`). A hovering aircraft logs about +35 u/s upward and would show a 30 Hz vertical sawtooth. *[inspected; magnitude derived]*

**What stays authoritative:** everything. The prediction runs on a copy, and only the presentation reads it.

**Player-visible gain, where L = 1 is enabled**
- The hull's drawn age is about 0 ms plus the prediction error: −16.5 ms on average against today, and −33 ms against the blend.
- A pilot's attitude change starts to show one tick earlier than today's hold, and one tick ahead of the blend. *[derived]*

**Expected accuracy** *[derived]*
- Grounded vehicles carry an error every tick, because the wheel constraints are solved before the integration step. In a hard turn at 800 u/s and 90°/s it is about 1.4 u.
- Free flight is exact only while the anti-roll, anti-pitch and pseudo-hover constraints are inactive, which is not determined.

**Measurement**
- Collect the `VEH_PRED` distribution per vehicle and per state: airborne, grounded, colliding, air brake on.
- Run an A/B replay with one input step at a known tick. Find the first draw that differs from the reference run, for today's hold, the blend and the lead, at caps 60 and 120.
- Check the step ratio across ticks.
- Hold a human review on a real display (D3).
- Enable the lead only for classes where p95 stays under a bound the owner sets by eye.

**Effort:** about 1 week plus the measurement. It needs a ledger row; the owner approved going beyond retail on 2026-09-29, and L per class is set after the measurement. *[inferred]*

### 3.3 When to turn the seat view on

- **Prerequisites:**
  - stages 0–5 have landed;
  - the seat capture, the A/B replay and the cross-cap replay pass;
  - a person has reviewed it on a high-refresh display (D1, D3).
- **First candidates:** angle-driven seats (gunners, free look), as `listSeatViews` identifies them.
- **Ground-vehicle driver seats: wait for stage 7** (owner, 2026-09-29). They get smoothness, but camera motion caused by the hull becomes 16.5 ms older on average, unless stage 7 enables a lead for that class. If the driver's view is angle-driven, their mouse look gains 16.5 ms.
- **Pilot seats stay off until stage 7 is measured.** Without a lead they regress by 16.5 ms. A pilot holding the tophat could later get late look for the free-look part.
- **D10 (2026-09-25): on by default.** Each seat joins the default list when it meets the prerequisites above.

### 3.4 Engine steps this depends on

- **Step 7 (listen host): done 2026-10-01.** The host builds a usercmd and calls `ClientUpdateView` on each frame that ran no tick (engine `AsyncServer.cpp:492-500`), so stages 3–5 can reach it, and step 13's latch can have its first reader there. The presentation is read-only, so it can run on an authoritative host as D6 requires. The SDK `renderView` write that `ClientUpdateView` brings to the host is put back at each tick's entry (owner decision E3; compat-ledger 058), so the host's tick code never reads a render frame's view.
- **Step 10 (tick frames at their real fraction):** removes the judder at tick frames, up to 0.5, 0.25, 0.21 or 0.13 of a tick at caps 60, 120, 144 and 240 (frame-path §9). Until then, stage 3 draws tick frames at `f = 0`.

### 3.5 Effort total

Stages 0–7 come to about 5–7 weeks of focused work for one engineer. That does not include m5-plan steps 7, 8, 9 and 10 themselves. Stages 0–6 alone deliver most of the gain. *[inferred]*

### 3.6 Deferred (not rejected)

*Status (2026-10-07): the transport's drawn joints are built (owner-approved; ledger 070): captured in tick code with `CreateFrame` and blended into scratch with the copy's `callback = NULL`, joints over 90° per tick held; `pivot`/`freepivot` presentable. Riders' IK arms, the exact wheel-angle lerp and the rest of this list stay deferred.*

- **Drawn joints: turret and IK arms, wheels, suspension, rotors.**
  - Capture them in tick code, not in a draw. A draw-time `CreateFrame` would add animator writes to entities the renderer culls (engine `src/renderer/tr_light.cpp:2505-2519`).
  - Blend into scratch, with `callback = NULL`.
  - Hold any joint that turns more than 90° per tick.
  - The wheel angle is a scalar accumulator, so it can be lerped exactly (game `TransportComponents.cpp:1932-1935`).
  - Exclude AF, ragdoll and angular-velocity joints, as openQ4 does (openQ4-game `src/game/anim/Anim_Blend.cpp:5292-5303`).
  - It is cheaper than the designs feared: the renderer already calls the model callback and re-skins every visible dynamic model on every draw (engine `tr_light.cpp:2505-2533`; `src/renderer/Model.cpp:359-372`). *[inspected]*
  - It goes beyond D2, so it needs the owner and a ledger row.
- **Vehicle lights and bound effects.** First check that moving an effect def and back on every draw does not emit trail particles. *[inferred risk]*
- **A late input latch after `BeginFrame`** (engine `Session.cpp:218-232`). Only worth it if `r_showmetrics` shows `BeginFrame` blocking; m5-plan step 4 still has that open.
- **Fractional SDL mouse deltas.** A separate engine fix for all modes.
- **Remote axes and joints, and a lead for remote transports.** Owner decisions that go beyond D5.
- **HUD target markers reading drawn poses.** Today they read `lastPushedOrigin` (game `src/game/PlayerProperties.cpp:1085-1090`).

## 4. What is rejected, and why

1. **Substeps inside the 33 ms tick.**
   - Per-tick results change: an extra a·dt²/4, about 1.1 u per tick at 4000 u/s². *[derived]*
   - The drive constraints and forces would act only in the first substep (`Physics_RigidBodyMultiple.cpp:2143, 1560, 2209-2210`), or every controller would need re-deriving.
   - Handling changes, and the physics costs N times as much on the server and on clients.
   - Input still arrives once per tick, so nothing gets more responsive.
2. **A higher simulation rate for vehicles only.**
   - It needs a new usercmd format, codec and relay.
   - It changes interactions with players and projectiles still at 33 ms.
   - Prediction-error decay and AoR are timed in ticks.
   - It would gain only about 8 ms of mean key quantization. *[derived]*
3. **Raising `USERCMD_HZ` globally.** It breaks assumptions everywhere, for example:
   - `getTicsPerSecond` is documented "always returns 30" (game `src/game/script/Script_SysCall.cpp:135`);
   - the staggered ground checks need an odd msec (game `src/game/physics/Physics_Player.cpp:2396-2403`);
   - the aircraft cyclic is a per-tick delta clamped to ±2;
   - the usercmd backup and duplication limits are sized in ticks (engine `AsyncNetwork.h:55`, `UsercmdCodec.h:19`).

   openQ4 rejected the same thing (openq4 `docs/dev/high-framerate-rendering-plan.md:371-382`).
4. **A higher rate on the client only.** Every tick would mis-predict, and the handling felt would differ from the server's, which resolves hits.
5. **Speculative per-frame physics on a copy, or a shadow simulation.**
   - `Evaluate` has side effects: damage, impulses on other entities, clip links and script events (game `Physics_RigidBodyMultiple.cpp:1148-1332, 2194-2233`; `Transport.cpp:2372-2396`).
   - A partial step does not lie on the path to the next tick, so the drawn vehicle would snap.
6. **Running camera damping, turret filters or auto-centre every frame with scaled constants.** They are small nonlinear simulations tuned per tick. The turret lag decides where shots come from. Their outputs are drawn instead.
7. **Calling `CalculateViewPos` again on every frame.** It writes damping members, relinks clip models through `ClipView` and would apply the damping twice.
8. **Lifting the proxy gate in `IsFPSUnlock`.** It contradicts retail (D2, D10). It would also run the on-foot blocks in seats: the on-foot clamps, the `originlog` rewind and the weapon block, which write `p->renderView`.
9. **Writing back from render frames.** This rules out:
   - the SDK and retail way, `renderEntity.origin` = lerp and then `Present`;
   - openQ4's write-then-restore on game fields.

   Both expose drawn values to tick code: `LinkCombat` (game `Player.cpp:10765-10766`), `lastPushed*` (read by `VehicleView.cpp:1205-1206`) and the hull's `renderEntity` axis (read by `VehicleView.cpp:650-652, 750, 785`). This violates D4 and D6.
10. **Restoring the tick-end sample after a draw.** It can overwrite newer defs or land on a reused handle (rule 7).
11. **Plain velocity extrapolation of the hull** (latency-first's v·dt). It has a gravity bias of about 1.16 u per tick in the air. Stage 7's predictors replace it: the physics step about the centre of mass, the presentation's own samples, or a hybrid ([`hull-lead.md`](hull-lead.md) §2–3).
12. **Making L = 1, or any extrapolation, the default.** At impacts it carries the camera up to L·v·33 ms into walls, 26 u at 800 u/s. openQ4 also refused to extrapolate (plan `:256`).
13. **Late look for pilots and `_Player` views.** A pilot's mouse is control input, so a camera turned early would show a turn the aircraft has not made.
14. **Blending the FOV.** It would feed a value that depends on the cap into `ClampFinalAxis` (`VehicleView.cpp:300`).
15. **Reusing the const `ClampViewAngles` in a draw.** It clears the mutable `freshEntry`.
16. **Running prediction-error decay on every frame.** It writes its spline and `lastReturned` state (game `PredictionErrorDecay.cpp:625-629, 1090-1094`).
17. **Buffered snapshot interpolation of remote vehicles.** There is no vehicle antilag, so it would need server-side rewind of vehicles, which changes hit detection.
18. **Changing think order to remove the Desecrator and jetpack one-tick lags.** It changes the authoritative per-tick results against retail.
19. **A tick-aligned frame scheduler.** D7 rejected it.
20. **A second `GetDirectUsercmd` per frame.** Its callbacks change game state (frame-path §5).
21. **Slerping fast-spinning joints.** Wheels and rotors that turn more than 180° per tick would appear to spin backwards.
22. **openQ4's fixed 32 u camera guard.** At 30 Hz it would cut the blend above about 960 u/s.

## 5. Open questions and risks

### 5.1 For the owner

1. **The split.** Does it meet D10? Everything drawn and mouse look in angle-driven seats leave the tick. Driving keys and a pilot's mouse keep reaching physics once per 33 ms, which only a simulation change could shorten.
2. **Which seats first.** D10 answers "on by default": seats join the default list as they qualify (§3.3). A person's review of the first seat group on a high-refresh display is still wanted (D3).
3. **Driver seats.** Accept 16.5 ms more mean age on camera motion caused by the hull in exchange for smoothness, or wait for stage 7? *Answered 2026-09-29: wait for stage 7; driver seats keep the tick-rate view until the lead is measured for their class.*
4. **Stage 7.** Adopt the lead per class after measurement? L = 0.5 keeps today's mean age; L = 1 removes it, with a risk of overshoot.
5. **Remote vehicles.** They are drawn one tick behind (retail, D5), and there is no vehicle antilag. Do you want vehicles to depart from retail, using the lead or remote axes and joints? That needs a ledger row. *Answered 2026-09-29: yes, each departure with a ledger row, enabled per class under a measured bound.*
6. **Aircraft state.** If loopback shows a residual, network the air-control state (a protocol minor bump)? *Answered 2026-09-29: yes.*
7. **Prediction-error decay on your own vehicle.** It has no exclusion for the local player (game `src/game/ScriptEntity.cpp:2244-2255`). Keep it, or replace it with a faster fade in presentation? *Answered 2026-10-07: keep retail's decay, and revisit only if a network client's `VEH_PRED` shows it as wobble ([`hull-lead.md`](hull-lead.md) §6 has the first client numbers).*

### 5.2 Not determined

- **Which view class and flags each retail seat uses.** *Answered 2026-09-30 by `listSeatViews` on valley (retail 1.5 data; `seat-gate`'s run A).* Every seat has one or two views. First views are `standard` for most drivers and passengers, and `pivot` or `freepivot` for gunners and for the titan's and the goliath's drivers; second views are the third-person `smooth`, `smooth_free` or `smooth_locked`, or `pivot`. No seat needs the tophat. The icarus (a jetpack) has the `icarus` view and a third-person `player` view, and uses no eye joint. Valley's badger, titan and icarus:

  | Vehicle | Seat | Mode | Class | Key | Third person | Auto-center | Eye (pivot) | Driven parent of the eye |
  |---|---|---|---|---|---|---|---|---|
  | badger | 0 `guis/vehicles/badger_driver` | 0 | standard | `badger:guis_vehicles_badger_driver:standard` | 0 | 1 | cam_7 (base) | none |
  | | | 1 | smooth_locked | `badger:guis_vehicles_badger_driver:smooth_locked` | 1 | 0 | driver_gun (base) | none |
  | | 1 `guis/vehicles/badger_gunner` | 0 | freepivot | `badger:guis_vehicles_badger_gunner:freepivot` | 0 | 0 | gun_cam (gun2) | gun2: seat 1's minigun aimer |
  | | | 1 | smooth_free | `badger:guis_vehicles_badger_gunner:smooth_free` | 1 | 0 | gun2 (gun) | gun2: seat 1's minigun aimer |
  | | 2 `guis/vehicles/badger_passenger` | 0 | standard | `badger:guis_vehicles_badger_passenger:standard` | 0 | 1 | cam_8 (base) | none |
  | | | 1 | pivot | `badger:guis_vehicles_badger_passenger:pivot` | 1 | 0 | gun_cam (gun2) | gun2: seat 1's minigun aimer |
  | | 3 `guis/vehicles/badger_rear_left` | 0 | standard | `badger:guis_vehicles_badger_rear_left:standard` | 0 | 0 | rear1_position_cam (base) | none |
  | | 4 `guis/vehicles/badger_rear_right` | 0 | standard | `badger:guis_vehicles_badger_rear_right:standard` | 0 | 0 | rear2_position_cam (base) | none |
  | titan | 0 `guis/vehicles/titan_driver` | 0 | pivot | `titan:guis_vehicles_titan_driver:pivot` | 1 | 0 | gunner_cam (turret) | gun: seat 0's minigun aimer |
  | | | 1 | smooth_free | `titan:guis_vehicles_titan_driver:smooth_free` | 1 | 0 | turret (hull) | turret: seat 0's minigun aimer |
  | | 1 `guis/vehicles/titan_passenger` | 0 | freepivot | `titan:guis_vehicles_titan_passenger:freepivot` | 0 | 0 | gpmg_cam (aft_gun_mount) | gpmg_mount: seat 1's minigun aimer |
  | | | 1 | smooth_free | `titan:guis_vehicles_titan_passenger:smooth_free` | 1 | 0 | gpmg_cam (aft_gun_mount) | gpmg_mount: seat 1's minigun aimer |
  | icarus | 0 `guis/vehicles/icarus_driver` | 0 | icarus | `icarus:guis_vehicles_icarus_driver:icarus` | 0 | 0 | none | none |
  | | | 1 | player | `icarus:guis_vehicles_icarus_driver:player` | 1 | 0 | none | none |

  The eye is what the damped views (`standard`, `pivot`, `freepivot`) follow; the smooth views use it only as the orbit origin. Clamps: the titan's views limit their turn rate (yaw and pitch at 110 degrees a second in the driver's `pivot` view, 240 in the others), while the badger's only bound the angles (the driver's first view to -65..80 yaw and -20..12 pitch, the gunner's pitch to -50..17). `listSeatViews` also lists valley's trojan, husky, platypus, goliath, hog and desecrator, with the same pattern; the titan driver's first view is the only first view that is third person. Hull-relative `clampDamped*` clamps of the final axis (re-read 2026-10-20) are on two views only: the titan driver's `pivot` view (pitch -360..46, third person, never presented) and the husky driver's `standard` view (yaw -75..75, pitch -180..64). The desecrator driver's first view is rate-limited (110 degrees a second), not damped.
- **Whether the anti-roll, anti-pitch and pseudo-hover constraints are active on aircraft in flight.** This decides whether stage 7 is exact there; `VEH_PRED` answers it.
- **Whether the aircraft mis-prediction is visible in play.** Stage 6 now traces how the frame numbers line up.
- **Other vehicle state that is not networked and not rolled back** during re-prediction. Only the air control was traced.
- **Think order of a transport and its seated player** (inferred from game `Game_local.cpp:5091-5130`).
- **Whether any seat's eye joint is parented to a joint the IK drives,** which would put that camera one tick behind the turret. *Answered 2026-09-30 by `listSeatViews` on valley: yes, for most gunner seats and several third-person views.* The joints are written less by the position IK systems than by the vehicle weapons' aimers (`sdVehicleWeaponFixedMinigun`'s yaw and pitch joints), which the listing includes. On the badger, the gunner's two views and the passenger's second view sit under `gun2`, which the gunner's minigun aimer turns; on the titan, both of the driver's views sit under the main gun's aimer joints (`gun`, `turret`) and both of the passenger's under `gpmg_mount`. The drivers' and passengers' first-person `standard` views and the icarus have none. The other valley vehicles show the same (the platypus gunner under its `sdVehicleIKArms`, the goliath driver under its `sdVehicleSwivel`, the hog, trojan and desecrator gun views under their aimers). Aimers and IK update after the seat cameras in `sdTransport::PostThink` (`UpdatePlayerViews`, then `positionManager.Think`, then each weapon's `Update`), so these cameras read the turret pose of the previous tick *[inferred from the call order; not measured]*. Joints moved by scripts, animation or wheels are not in the listing.
- **Whether vehicles carry `sdGuiSurface`s.** `DrawWorldGuis` and the occlusion queries run before the copies are submitted (game `PlayerView.cpp:411-415`), so such GUIs would be drawn at the tick pose. `sdTransport::PostThink` does update a vehicle GUI (`Transport.cpp:4430-4431`).
- **Whether HUD reticles project tick-time positions** through the drawn view: `GetCurrentView` readers such as `UICrosshairInfo.cpp`, and `PlayerProperties.cpp:1085`.
- **Whether the Vulkan path issues occlusion queries.** Their results feed client-side collision merging for remote players (game `Physics_Player.cpp:2387`). This is a pre-existing channel from the SDK, on clients only.
- **Whether the GPU reads joint data after `RenderScene` returns.** The CPU side is synchronous *[inspected]*; the GPU upload timing is inferred.
- **Every magnitude above.** Nothing was measured, apart from the on-foot figures quoted from frame-path §9.

### 5.3 Risks

- **The restore must be complete.** Every draw path, including early returns and `SkipWorldRender`, must restore the renderer defs. `ModelTrace` is a stub today (engine `RenderWorld.cpp:785-788`), so render-model hit traces (game `src/game/physics/Clip.cpp:596-632`) never hit. Once it is implemented, the restore becomes critical to hit detection.
- **A pure clamp can drift from the tick clamp.** That shows as a small snap at the tick. The per-tick angle-error metric catches it.
- **The SDK has a channel of its own: kept (owner decision 7).** The damped view's non-full path writes `lastReturnedAxis` between ticks (SDK `game/vehicles/VehicleView.cpp:524`), and the free-pivot clamp reads it. So aim state can depend on when snapshots are written. That is retail behaviour; changing it would be a deviation with its own ledger row. *[inferred]*
- **Mixed clocks under stage 7.** With a lead on your own vehicle while remote entities stay one tick behind, a vehicle driving alongside appears one tick of its own motion behind.
- **Cockpit carrier detection** comes from script modes (retail `script/vehicles/cockpits/basic.script:92-102, 122-198`). If it is wrong, the cockpit swims. The seat capture must confirm it for each cockpit script.
- **Bit-exact goldens break when the toolchain or FP flags change.** Regenerate only with a stated reason.
- **The fraction has 1 ms resolution** (frame-path §2). Above about 500 fps, consecutive draws can repeat a fraction.
- **Transitions.** Entry, exit and seat swaps rely on the draw-time re-check (rule 6). One tick of held pose is expected and acceptable.