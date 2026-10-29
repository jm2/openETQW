# Hull lead (M5 step 14b)

The plan for `docs/vehicle-responsiveness.md` stage 7: drawing your own vehicle's hull, and what it carries, up to one tick ahead of the last tick, per vehicle class.

Written 2026-10-06 by the Mac session against engine main 2f8698d and game main d9acb36. It refines stage 7's predictor after a first measurement (§2). Stage 7's `PredictNextPose`, the physics' own step on a copy, is essentially exact in free flight, but poor on the ground. Extrapolating the presentation's own tick samples is the better predictor on the ground and needs no physics code, but it amplifies a network client's decay noise. So the plan keeps both as candidates, and adds a hybrid, with the choice per class made by the measurement step. Everything else in stage 7 stands. Line numbers drift; re-check before relying on one.

**Evidence level.** §2's numbers were measured on this Mac (M1 Max, macOS 27.0.1) with `tools/analysis/veh_pred.py`. The rest is read from code *[inspected]* or reasoned from it *[derived]*, and is marked so where it matters.

## 1. Why

Seats that `sdPresentation` draws (ledger 062) blend the hull between ticks n−1 and n, so the hull is drawn a fixed 33 ms old. Today's tick-rate view draws it 0–33 ms old, 16.5 ms on average, so the blend costs 16.5 ms of mean age (`vehicle-responsiveness.md` §2). Angle-driven seats win that back with late mouse look (stage 5; ledger 064). Hull-driven camera motion can only win it back with a lead: drivers, pilots and passengers riding a turning hull.

That is why the owner kept driver seats on retail's tick-rate view "until the hull lead is measured for their class" (STATUS, 2026-09-29), and why pilot seats stay off until then (`vehicle-responsiveness.md` §3.3).

## 2. What was measured

**Method.**
- `g_determinismTest 2`, the scripted single-player replay on valley (game repo: `src/game/Game_local.cpp:11909`, `RunVehicleDeterminismSchedule`), drives a badger for 155 ticks (throttle, right and left turns, brake and reverse, forward), then flies an anansi for 124 ticks (lift-off, nose down, a yaw and roll ramp, collective down).
- With `g_simHash 1`, every tick prints a `VEH_TRACE` line per occupied transport: the physics origin, orientation, linear and angular velocity, and the last collision time.
- `tools/analysis/veh_pred.py` takes each tick n with n−2, n−1 and n+1 present and compares the pose at n+1 with each predictor's guess made at n. That error is what a lead of one tick (L = 1) shows at the end of tick n, before tick n+1 corrects it.
- In single player the render entity is the physics pose, because there is no prediction-error decay. On a network client the samples also carry the decay; see §6.

**Reproduce.**

```
openETQW-client_arm64 +set fs_basepath <retail> +set fs_game base +set si_rules sdGameRulesObjective \
  +set g_determinismTest 2 +set g_simHash 1 +devmap valley +clientTeam gdf +clientClass soldier +closeLimbo
python3 tools/analysis/veh_pred.py <console log> --worst 6
```

**Results** (2026-10-06; the simulation is identical at every cap, so one run covers all of them). Each figure is median / p95 / max of the error of a guess made at tick n against the pose at n+1:

| Predictor | badger position (u) | anansi position (u) | badger rotation (°) | anansi rotation (°) |
|---|---|---|---|---|
| Hold (no lead: the motion in one tick) | 9.91 / 21.72 / 26.58 | 9.08 / 21.33 / 21.52 | 0.174 / 3.196 / 3.91 | 0.582 / 1.927 / 2.11 |
| Physics step: v·dt about the centre of mass | 0.674 / 3.226 / 33.71 | 1.131 / 1.309 / 3.02 | | |
| Physics step: (v + g·dt)·dt about the centre of mass (stage 7's gravity add-back) | 1.332 / 3.783 / 33.73 | **0.005 / 0.023** / 4.18 | | |
| Physics step: ω·dt | | | 0.064 / 0.358 / 4.55 | **0.000 / 0.046** / 0.22 |
| Samples, constant velocity: p(n) + Δ(n) | 0.708 / 1.551 / 33.21 | 0.271 / 0.722 / 4.68 | | |
| Samples, constant acceleration: p(n) + Δ(n) + (Δ(n) − Δ(n−1)) | **0.095** / 1.842 / 33.45 | 0.011 / 0.087 / 4.29 | | |
| Samples, constant acceleration with §3.3's spike fallback | 0.100 / **1.749** / 33.45 | 0.011 / 0.087 / 4.29 | | |
| Samples, constant rotation: the last tick's rotation applied again | | | **0.058** / 0.380 / 4.56 | 0.000 / 0.126 / 0.26 |

`VEH_TRACE` does not print the body's centre of mass (`mainCenterOfMass`), so the tool fits it per class by least squares. Two fits: the badger's, (7, −12, −163) u, absorbs ground contact effects and is not physical; the anansi's, (−32, 0, 45) u with gravity, is plausible. Both fits are in-sample, made on the same data the errors are reported from, with the impact included, so the anansi's 0.005 u is slightly optimistic. The game implementation reads the real value, and step 2 logs it to confirm the fit.

**What it shows.**
- **What the physics' own step on a copy contains.**
  - At `sdTransport::PostThink`, after `Evaluate`, the external force and torque are already zeroed (game repo: `src/game/physics/Physics_RigidBodyMultiple.cpp:2209-2210`, inside the repetition loop).
  - The next tick's control forces, and its contact, friction and motor constraint impulses, are not known yet. `ContactFriction` (`:1448`) adds those impulses to the momentum before `Integrate` (`:1572-1577`), so they do move the next step's position.
  - With no external force, one `idODE_Euler` step of `RigidBodyDerivatives_Multi` (`:265-293`) moves the centre of mass by dt·v; its friction term changes only the momentum. `Integrate` then re-derives the origin from the centre of mass and the new orientation (`:658-680`).
  - So the copy is (v [+ g·dt])·dt about the centre of mass, plus the rotation ω·dt. *[inspected]*
- **In free flight the physics step is exact.** For the anansi, with the gravity add-back and the centre-of-mass lever arm: median 0.005 u, p95 0.023 u, rotation p95 0.046°. That is better than the samples (0.011 u / 0.087 u / 0.126°).
- **On the ground the physics step misses the contacts.**
  - The badger's physics-step error is 0.67 u median and 3.2 u p95; the samples' is 0.095 u and 1.8 u.
  - Adding gravity back makes the badger's median worse, 0.67 → 1.33 u: the contact constraint would have removed that momentum again, so adding it back sinks the predicted hull by about 1.16 u. Stage 7's "about 1.16 u per tick" is right only in the air. *[measured]*
- **Extrapolating the samples carries the persistent forces**, ground contacts included, with no physics reads. On rotation, both families are close to exact.
- **Impacts dominate the worst case.**
  - All three badger errors over 10 u are one collision at tick 83 (2739 ms). The badger's speed jumps from 569 to 870 u/s: it was thrown, not stopped.
    - The guess made at tick 82 is 33.45 u off: nothing before the collision foresees it.
    - The guesses made at ticks 83 and 84 are 27.6 and 10.2 u off: the constant-acceleration term then extrapolates the impact itself. §3.3's fallback, applied from the tick that shows the spike, brings tick 83 down to about 9 u.
  - Landings: the badger dropping onto the ground at spawn gives 2.3–2.9 u, and the anansi settling after it is entered gives 4.2–4.3 u.
  - Every other tick in the run is under 2.1 u.

### 2.1 Every class (step 2, 2026-10-06)

**Method.** `g_determinismTest 4` (game repo: `src/game/Game_local.cpp`, `RunLeadMeasureSchedule`) and meson `lead-measure`:
- Each rigid-body class gets a 200-tick segment. Every other vehicle on the map is removed on every tick, then the class is spawned on schedule 2's badger spot and entered by name.
- Ground classes are driven: throttle, a right and a left turn at speed, brake and reverse, forward again. Aircraft are flown: lift-off, nose down, a yaw and roll ramp, level, descent.
- In this schedule only, a `VEH_BODY` line per tick gives the real `mainCenterOfMass` and `HasGroundContacts()`, so `veh_pred.py` uses the logged centre of mass instead of a fit, and adds the hybrid.
- The replay is identical at com_maxFPS 60 and 144.
- Valley's construction module respawns a badger on that spot about 20 s after the map's badger is gone, and the other spawners respawn their vehicles, which is why foreign vehicles are removed on every tick. The goliath (a walker, not a rigid body) and the platypus (it needs water) are not in it.

**Position error, median / p95 / max (u), one tick ahead** (165–179 moving ticks per class: a tick whose hull moves under 0.5 u is parked and left out, as in `seat-poses`; samples with §3.3's spike fallback):

| Class | on the ground | samples, accel + fallback | physics, gravity added back off the ground | hybrid (H) |
|---|---|---|---|---|
| titan | 98% | 0.174 / 4.17 / 29.3 | 0.808 / 3.91 / 28.9 | 0.174 / 3.32 / 29.3 |
| hog | 93% | 0.268 / 6.40 / 28.0 | 0.690 / 3.92 / 27.4 | 0.251 / 4.73 / 28.0 |
| desecrator (hover) | 1% | **0.035 / 1.27** / 13.5 | 1.245 / 1.81 / 13.2 | 1.245 / 1.88 / 13.2 |
| husky | 97% | 0.131 / 3.68 / 42.8 | 0.707 / 2.18 / 42.7 | 0.131 / 3.00 / 42.8 |
| badger | 97% | 0.208 / 3.57 / 35.9 | 0.723 / 2.75 / 35.7 | 0.208 / 3.03 / 35.9 |
| trojan | 98% | 0.072 / 1.61 / 13.4 | 0.540 / 1.66 / 8.9 | 0.072 / 1.52 / 9.0 |
| anansi | 3% | 0.019 / 0.050 / 1.5 | 0.001 / 0.015 / 1.2 | **0.001 / 0.014** / 0.09 |
| bumblebee | 5% | 0.006 / 0.047 / 1.4 | 0.001 / 0.180 / 1.5 | **0.001 / 0.003** / 1.5 |
| hornet | 1% | 0.015 / 0.109 / 1.5 | 0.004 / 0.056 / 1.2 | **0.004 / 0.050** / 1.0 |

Valley's vehicle spawners respawn the map's vehicles while the drive runs, so the schedule removes every vehicle but its own on every tick, and logs each one as `LEAD_FOREIGN`: 75 in a run. The numbers above are with that removal. They match a run that removed foreign vehicles only between segments to within a few hundredths at the median.

Rotation: the physics step's ω·dt and the samples' constant rotation agree within a few hundredths of a degree at the median for every class. The p95 is 0.04–0.6° for the aircraft, the trojan, the titan and the desecrator, and 2–4° for the hog, husky and badger.

**What it shows.**
- **Aircraft: the hybrid**, which is the physics step in the air, is near-exact (p95 0.007–0.051 u).
- **The desecrator is a hover tank.** Its pads hold it up without ground contacts, so `HasGroundContacts()` is false 99% of the time, and the hybrid wrongly adds gravity back (median 1.24 u). For this class S is the only good predictor. The hybrid's rule should therefore be per class, not per contact state alone.
- **Wheeled and tracked vehicles.**
  - The samples, the same as H on the ground, have the best median, 0.07–0.27 u.
  - The physics step has the best p95 for the hog, husky and badger. Their worst ticks are bumps and collisions, which the samples' acceleration term extrapolates.
  - Collisions that launch the vehicle, at speed jumps of up to 500→900 u/s, give 28–43 u maxima that no predictor foresees. The cap and L of §3.3 are what bound them.

**What it does not show yet.**
- The network client's decay noise (§6). That needs a client-side drive, because the server overriding a remote client's commands would make the client mispredict.
- Rough ground and long turns at speed. The drive stays on valley's open ground near one spot.
- A real human driver.

## 3. Design

### 3.1 The predictor

Three candidates are built behind one switch, and step 2 (§5) picks per class:

- **S, the samples.**
  - `sdPresentation` keeps three tick samples instead of two (`samples[ 3 ]`, by `framenum % 3`).
  - Position: `lead(n) = Δ(n) + k·(Δ(n) − Δ(n−1))`, with `Δ(n) = hull(n) − hull(n−1)`. k = 1 is constant acceleration and k = 0 is constant velocity.
  - Orientation: `rot(n) = hull(n−1)ᵀ · hull(n)`, the last tick's rotation in id's row-axis convention (`axis(n) = axis(n−1) · rot`), applied again: `axis(n) · rot(n)`.
  - It needs no physics reads and no new tick-side code, so `vehicle-responsiveness.md` §3.1 rules 1–2 hold as they are: samples only on new ticks, from the post-decay render entity.
- **P, the physics step (stage 7's `PredictNextPose`, corrected).**
  - It is computed once per tick in `SampleTick`, from the transport's physics state after `Evaluate` and `Present`:
    - the centre of mass moves by (v + g·dt)·dt, with the gravity add-back only when `HasGroundContacts()` is false;
    - the orientation rotates by ω·dt;
    - the origin is re-derived through `mainCenterOfMass`.
  - The result is stored as a delta (predicted − physics(n)), which the draw applies to the post-decay render pose. So the decay is never extrapolated.
  - It is a closed form in `SampleTick`, not stage 7's `idODE_Euler` step on a copy called from `PostThink`. Without external force the two are the same step (§2).
  - **P is an exception to `vehicle-responsiveness.md` §3.1 rule 2** ("never the physics pose"):
    - it reads the physics pose, velocities and contact state at tick time;
    - it never draws them: only the delta (predicted − physics(n)) reaches the draw, on top of the post-decay render pose, so the decay is still what the player sees;
    - rule 1 holds, since it writes nothing.

    The ledger row for step 3 records the exception.
- **H, the hybrid.** P while `HasGroundContacts()` is false (airborne), and S while it is true.
  - The contacts come from the tick's `EvaluateContacts`, before `Integrate` (game repo: `Physics_RigidBodyMultiple.cpp:2129`). They include the wheel, track and hover-pad contacts that `sdTransport::EvaluateContacts` adds (`TransportComponents.cpp:1870, 2305, 3274`).
  - So a driving badger counts as grounded, and a hovering pad vehicle may never count as airborne. Step 2 records the contact state per tick to check this per class.

Common to all three:
- **Continuity.** The samples must pass the existing continuity guards (`SamplesContinue`: one seat, one proxy, no jump) pairwise. When they don't, the lead is 0 for that draw, which is today's blend.
- **The switch.** `g_vehicleLeadPredictor` (S, P or H) is a CVar for measurement; the built-in per-class list (§3.4) carries each class's choice.

### 3.2 Drawing

- **The drawn hull.** It is at α = f + L − 1 ticks from n, where f is the frame's fraction and L ∈ [0, 1] is the class's lead:
  - α < 0: the existing lerp and slerp from n−1 to n;
  - α ≥ 0: `hull(n) + α·lead(n)`, with the orientation `axis(n) · slerp(identity, rot(n), α)`.
- **Choosing L.**
  - L = 0 is today's blend, exactly.
  - L = 1 draws the predicted pose: a mean age of about 0 ms plus the prediction error, 16.5 ms better than the tick-rate view.
  - L = 0.5 keeps the tick-rate view's mean age and halves the worst overshoot.
- **The correction at a new tick.** At the new tick's first draw, `hull(n+1)` and the last drawn lead pose differ by the prediction error: a median of 0.1 u (badger) and 0.01 u (anansi), and up to 33 u at an impact.
  - That difference is faded out over `g_vehicleLeadFadeMsec` (default 25 ms, under one tick), as an offset that decays linearly.
  - The correction is measured from the last tick's pose as it would be drawn now, its own remaining fade offset included, so the new fade starts exactly where the hull was. Nothing is counted twice: the old offset is replaced, not added.
  - A fade still running when the next tick arrives is not restarted: the new correction is added to what is left of the old offset, and the sum decays over a fresh fade time.
  - While the fade (25 ms) is shorter than the tick spacing (33 ms), the offset is bounded by the sum of the last two corrections. A longer fade, or ticks bunched after a hitch, lets residuals pile up, so `g_vehicleLeadFadeMsec` is clamped to 30 ms.
  - An error over a jump bound, the same 64 u as the hull guard, is not faded: the draw snaps, as a respawn does today.
- **The camera.**
  - **First person:** the camera rides the drawn hull, through its offset in the hull's frame.
    - The offset (and its axis) is blended at the frame's own fraction f, as before the lead: the damped view's own motion in the hull is neither extrapolated nor moved to the hull's time. Blending it at t = f + L, as first built, stepped it by L × its per-tick change at every tick (review of #42); `seat-poses` H2v checks the drawn view's steps.
    - Late look (row 064) applies unchanged on top.
  - **Chase views** (world cameras, row 062): no lead in the first tier. The chase camera is already damped behind the hull, and leading it would extrapolate the damping. A second tier can move the world camera by the hull's lead translation, after the stage 5 chase-view orbit work has landed.
- **Riders, the cockpit and carried client entities** use the drawn hull through the carrier rule, as today, so they lead with it.
- **Remote entities stay one tick behind** (stage 4; D5). A vehicle driving alongside therefore appears up to a tick of its own motion behind. That is the "mixed clocks" risk of `vehicle-responsiveness.md` §5.3, and the human review (§5, step 4) has to judge it.

### 3.3 The impact guard

Nothing in the samples or the physics state foresees a collision, so a lead overshoots before the tick that collides. Three measures, applied in order (§2 numbers):

1. **The acceleration falls back after a spike** (S and H). When |Δ(n) − Δ(n−1)| exceeds a per-class bound, the predictor drops k to 0, constant velocity, from that tick for 3 more ticks.
   - The starting bound is 6 u per tick², about 5500 u/s². The largest non-impact value in §2's run is 4.68 (the anansi being entered), so the margin is narrow, and step 2 sets the bound per class.
   - This targets the error that follows an impact: tick 83's 27.6 u becomes about 9 u.
2. **The lead's length is capped** (`g_vehicleLeadMaxLength`, 48 u: a 16 u start clipped the hornet's and anansi's normal 16–30 u per tick, so the cap now sits above every class's normal move; built 2026-10-07). This bounds how far the drawn hull can lead. It does not bound the correction at the next tick, which can still be as large as the impact's error.
3. **L itself.** L = 0.5 halves whatever is left.

A hull trace in `SampleTick` against the world is **not** proposed. It would bound the overshoot exactly, but it goes beyond §3.1 rule 2 of `vehicle-responsiveness.md` ("sampling reads fields directly"), and `ClipView`, the nearest existing call, is on rule 4's list because it relinks clip models. A read-only `gameLocal.clip.Translation` at tick time would need its own review of what the clip system caches and counts. That review is an owner question, so it is listed in §4 rather than designed here.

### 3.4 Configuration

- **`g_unlock_vehicleLead`** (CVAR_GAME, archive, profile). A comma-separated list of `vehicleClass:L` items, with `*` and `?` wildcards in the class, the same parser style as `g_unlock_vehicleSeats`.
  - Default: `"default"`, a built-in list that starts empty.
  - The class is the transport's vehicle script name, the first field of a seat key (`badger`, `anansi`).
  - L is clamped to [0, 1].
  - The lead applies only in a seat the seat gate presents (ledger 062). Being listed here does not turn a seat on.
- **`g_vehicleLeadFadeMsec`** (default 25), the correction fade of §3.2.
- **`g_vehicleLeadPredictor`** (S, P or H; empty uses the class's built-in choice), §3.1.
- **Logging (a cheat).** `g_logVehiclePred 1` prints a `VEH_PRED` line from `SampleTick` on each new tick. Its fields:
  - the class, tick, L, the predictor and k;
  - the error of the prediction made at n−1 against the new sample;
  - whether the impact guard fired;
  - the last collision time.

  It is the in-game counterpart of `veh_pred.py`, and on a network client it measures what the client actually draws from, after the decay.

### 3.5 What does not change

- **Tick state.** Every tick value, `SIM_HASH`, `SIM_VIEW` and `VEH_TRACE` are unchanged. S reads only the presentation's samples. P reads physics fields at tick time through existing const accessors: `GetMainCenterOfMass` (game repo: `Physics_RigidBodyMultiple.h:229`; `sdTransport::GetCenterOfMass`, `Transport.h:683`) and `HasGroundContacts` (`Physics_Base.h:153`). It writes nothing.
- **Other players' views.** The lead is local presentation of the local view player's own transport.
- **Seats the seat gate doesn't present.** They keep today's tick-rate view.

## 4. Owner decisions this plan needs

The choice between S, P and H is technical: step 2's numbers decide it. The owner should still see that stage 7's physics step stays a candidate, and why (§2).


**Decided (owner, 2026-10-07, relayed by the Linux session):**
- **Question 2, the impact overshoot:** accept the soft guard (the spike fallback, the length cap, L per class). No tick-side clip trace.
- **Question 4, the decay on your own vehicle:** keep retail's prediction-error decay. Measure its effect on the lead from a network client's `VEH_PRED` first (§6), and revisit only if it shows as wobble.
- **Related:** turret and IK joint blending for gunner views will be built in presentation, beyond D2, with its own ledger row (the Linux session). The default frame cap is 240 (m5-plan step 18, the win-arm64 session).

**Still open:** questions 1 and 3, the per-class p95 bound and L, and which driver and pilot seats join the default list. They need the measurements below and the owner's review on a high-refresh display.

The questions as asked:

1. **The per-class bound for enabling L.** Stage 7 said "p95 stays under a bound the owner sets by eye". The measurement step (§5) will offer p95 and max per class and state, plus the human review; the owner picks the bound and each class's L.
2. **The impact overshoot.** Accept §3.3's three measures, which still allow a small overshoot into a wall for up to a tick, or ask for a review of a tick-side clip trace that would bound it exactly?
3. **Driver and pilot seats.** Which classes' driver seats, then pilot seats, join `g_unlock_vehicleSeats`'s default list once their class has a lead. The 2026-09-29 decision already ties this to the measurement.
4. **Prediction-error decay on your own vehicle.** This is `vehicle-responsiveness.md` §5.1 question 7, still open. It matters here because, on a network client, S extrapolates the decay along with the motion (§6). P does not.

## 5. Steps

1. **This plan** (docs only): `docs/hull-lead.md`, `tools/analysis/veh_pred.py`, and `vehicle-responsiveness.md` stage 7, the m5-plan and STATUS pointing here.
2. **Measure every class (engine and game, test only).**
   - A scripted `g_determinismTest 4` (2 is the vehicle replay, 3 the seat-gate schedule) that spawns and drives each valley vehicle class in turn on open ground: straight, turning at speed, braking, one deliberate wall hit, and, for aircraft, lift-off, a turn and a descent.
   - Reuse the anansi's `SpawnEntityDef` pattern from mode 2, and keep it out of mode 2 so that mode 2's golden stays valid.
   - The output is a `veh_pred.py` table per class. **Done 2026-10-06 (§2.1; meson `lead-measure`).**
   - Then repeat on a join client with `g_logVehiclePred`, to see the decay noise, and on a listen host. That needs a client-side drive (`clientMove`), as `seat-poses` does. **Done 2026-10-20 (§6; meson `lead-measure-join` and `lead-measure-listen`, with the game's `g_leadMeasureDrive 0`).**
3. **Implement (game). Done 2026-10-07 (ledger 067).**
   - The three-sample predictor, the α draw, the fade and the impact guard in `sdPresentation`, plus `g_unlock_vehicleLead` and `VEH_PRED`.
   - It touches `Presentation.{h,cpp}`, which the Linux session is changing for the stage 5 chase-view look (branch `m5/14-stage5-chase-look`). The merge order is agreed with it before this step starts, and this step is rebased on its result.
   - Ledger row (next free number): "Your own vehicle's hull drawn up to one tick ahead, per class (beyond retail; owner 2026-09-29)".
4. **Test (engine). Done 2026-10-07: `seat-poses` H0–H3 and `seat-gate` run B (H4).** `seat-poses` gains a lead phase at caps 60 and 120, with the badger listed at L = 1. Its checks:
   - **H0:** the lead was on: `SEAT_PRESENT` counts blended draws, and `FRAME_POSE` carries `lead=`.
   - **H1, age:** with `g_vehicleLeadFadeMsec 0`, each draw's hull equals `hull(n) + α·lead(n)` (the `lead=` that `FRAME_POSE` logs per tick) at α = f + L − 1, to 0.05 u. With L = 0 it lies on the n−1 → n segment, the existing S3.
   - **H2, continuity:** away from collisions, the hull step across a tick is at most 2.5× the median step within a tick, the late-look L3 rule.
   - **H3, L = 0 is the blend:** listed at L = 0, the hull poses equal the interpolation path to 0.05 u, as S3 does today.
   - **H4, the simulation is untouched:** `SIM_HASH` with the lead on equals the lead off over the same scripted drive (`seat-gate` already compares `SIM_HASH`, `VEH_TRACE` and `SIM_VIEW` across the gate; the lead CVar joins that comparison).
   - **A negative control:** an unlisted class draws at L = 0.
   - The A/B latency check from stage 7: one throttle step at a known tick, and the first draw whose hull differs from a run without it, for hold, the blend and L = 1. It is reported, not failed, because it depends on the display.
5. **Human review (D3)** on a high-refresh display, per class, in driver and pilot seats: smoothness, the feel at walls, and vehicles alongside. Then the owner's §4 decisions, and the classes that pass go into `g_unlock_vehicleLead`'s default list.

**Effort** *[inferred]*: step 2 is about 2 days, step 3 about 3 days, and step 4 about 2 days, plus the review.

## 7. As built (2026-10-07)

- **Game:** `sdPresentation` keeps three samples and computes `leadS`/`rotS`, `leadP`/`rotP` and the ground state on every presented tick (`SampleLead`). `HullAt` draws t = f + L ticks past n−1, and `ApplyLeadCorrection` fades the new tick's correction. `sdSeatGate::LeadForClass` parses `g_unlock_vehicleLead`. The CVars are `g_vehicleLeadPredictor`, `g_vehicleLeadFadeMsec`, `g_vehicleLeadSpike`, `g_vehicleLeadMaxLength` and `g_logVehiclePred`, and `FRAME_POSE` carries `lead=`.
- **Rotation sign:** the physics step's rotation is about −ω·dt in id's row-axis convention. The first build used +ω·dt; `VEH_PRED` showed rotation errors of 1–3°, and the sign was flipped. With the flip the in-game predictors match `veh_pred.py`'s numbers.
- **What the player sees** (single player, `g_determinismTest 4`, cap 120, every class at L = 1, the drawn hull against the true path at the draw's moment):

  | Class | L = 0 (the blend), median / p95 (u) | L = 1, median / p95 (u) |
  |---|---|---|
  | hog | 9.31 / 19.70 | 0.26 / 6.87 |
  | desecrator | 8.66 / 10.99 | 0.03 / 1.36 |
  | husky | 13.46 / 24.66 | 0.17 / 3.32 |
  | badger | 13.44 / 22.17 | 0.22 / 4.72 |
  | trojan | 10.04 / 13.85 | 0.08 / 1.38 |
  | anansi | 11.89 / 28.31 | 0.01 / 0.11 |
  | bumblebee | 7.05 / 10.20 | 0.01 / 0.06 |
  | hornet | 16.41 / 29.53 | 0.02 / 0.12 |

  The titan is not drawn by the presentation: its driver's first view is `pivot`.

## 6. Network clients: the decay and sample noise

On a network client the render entity is the predicted pose after prediction-error decay (game repo: `src/game/ScriptEntity.cpp:2244-2255`). The decay is a correction towards each new snapshot, spread over several ticks. To the predictor it is motion that the vehicle did not make.

- **S amplifies it.** With independent per-sample noise of size σ, constant acceleration is p(n) + Δ(n) + (Δ(n) − Δ(n−1)) = 3p(n) − 3p(n−1) + p(n−2). Its coefficients (3, −3, 1) give √19 ≈ 4.4σ RMS in the lead. Constant velocity (2, −1) gives √5 ≈ 2.2σ, and hold gives σ. *[derived]* The decay is correlated over ticks rather than independent, so the real factor is measured, not assumed.
- **P does not.** It predicts from the physics state and applies only the delta to the render pose, so the decay offset is carried along but never extrapolated.
- **Step 2 measures both on a join client**, with `VEH_PRED` against the client's own next sample, and picks the predictor and k per class on the client's numbers, not single player's. If S's client error is several times its single-player error, the class takes H or P, or k = 0.
- **Measured (2026-10-07, macOS, loopback).** `seat-poses` was run with `g_logVehiclePred 1` on the observer, in the badger's driver seat, over its drive phases at caps 60–240 (about 1140 moving ticks per mode). Error of the one-tick guess, median / p95 (u):

  | Mode | S (samples) | P (physics step) | H (hybrid) |
  |---|---|---|---|
  | devmap (single player) | 0.019 / 0.95 | 0.325 / 1.01 | 0.019 / 0.95 |
  | listen host | 0.003 / 0.83 | 0.120 / 0.93 | 0.003 / 0.82 |
  | join (network client, with re-prediction and decay) | 0.062 / 4.66 | 0.423 / 2.77 | 0.062 / 3.91 |

  - On the client, S's p95 is about 5× its single-player value; this is the decay §6 predicted, magnified.
  - P, which applies only the physics delta and never extrapolates the decay, holds its p95 at 2.77 u. Its median is the worse of the two, as on the ground in §2.1.
  - The errors over 5 u (54 of 1146 ticks) are almost all at collisions in the drive phases, in every mode.
  - Loopback has little network jitter, so a real network adds more decay. A badger's driver on a network client is the case question 1's bound must cover: S at k = 1 is the best median, and P the best p95.
- **Measured per class (2026-10-20, Windows x64, loopback).** Meson `lead-measure-join` and `lead-measure-listen`, and `tests/test_lead_measure_client.py --mode devmap` for single player with the same drive.
  - **The setup.**
    - Host: the Windows x64 host, Windows 11 x64, RX 6400, MSVC 19.51 debugoptimized, retail 1.5 data.
    - Builds: engine 995d4b6 (the logs' `g995d4b6d`) with game a5f49bd (`g_leadMeasureDrive`).
    - The player's game: the Vulkan client at `com_maxFPS 120`, every seat presented (`g_unlock_vehicleSeats *`), `g_logVehiclePred 1`.
  - **The drive.**
    - The server runs schedule 4 with `g_leadMeasureDrive 0`: it spawns each class on the same open spot, enters the player, then takes them out and removes the vehicle.
    - The player drives with their own commands: `clientMove` for the throttle, steering, collective and pedals, and `+lookdown`/`+left`/`+lookup` (`in_pitchSpeed 12`, `in_yawSpeed 24`) for an aircraft's nose and roll, timed by the client.
    - The ground legs are the schedule's (§2.1). The aircraft legs only approximate them, so the aircraft rows compare across the three modes here, not with §2.1:
      - lift-off comes about 30 ticks later than in the scripted run (not diagnosed);
      - the nose comes back with a sustained `+lookup`, where the schedule snaps it back in one tick;
      - the roll isn't returned;
      - nothing checks that the look keys took effect.
    - The titan isn't presented (its driver's first view is a third-person `pivot`), so it logs no `VEH_PRED`.
  - **The numbers** are the error of the one-tick guess against the pose the player's game then draws from, over the ticks that moved at least 0.5 u (154–180 per class and run).
    - Each cell is the range, over four runs per mode, of the per-run median / p95 (u).
    - Every mode varies from run to run, because the client times the drive. Single player varies least (the hog repeats exactly; the badger's S p95 is 1.70–2.17), and the network client most.

    | Class | Devmap S | Devmap P | Devmap H | Listen S | Listen P | Listen H | Join S | Join P | Join H |
    |---|---|---|---|---|---|---|---|---|---|
    | hog | 0.27 / 5.77 | 0.67 / 2.90 | 0.23 / 4.74 | 0.07–0.13 / 1.88–5.34 | 0.65 / 1.60–2.20 | 0.07–0.13 / 1.75–2.32 | 0.13–0.54 / 4.44–9.02 | 0.65–0.67 / 2.81–8.66 | 0.13–0.54 / 3.08–8.95 |
    | desecrator | 0.04 / 1.44 | 1.27–1.28 / 1.82–1.87 | 1.27–1.28 / 1.82–1.87 | 0.03–0.04 / 1.26–1.87 | 1.24–1.25 / 1.80–1.83 | 1.24–1.25 / 1.82–1.83 | 0.04–0.05 / 2.08–3.64 | 1.24–1.25 / 1.83–1.96 | 1.25 / 1.90–1.96 |
    | husky | 0.06 / 1.61 | 0.55 / 1.23 | 0.06 / 1.29 | 0.02 / 1.22 | 0.54 / 1.10 | 0.02 / 1.02 | 0.05–0.09 / 2.66–4.45 | 0.52–0.54 / 1.19–3.16 | 0.05–0.09 / 2.63–4.03 |
    | badger | 0.08 / 1.70–2.17 | 0.65 / 1.57–1.81 | 0.07–0.08 / 1.58–1.83 | 0.03–0.04 / 1.05 | 0.64–0.65 / 1.18–1.20 | 0.03 / 0.56–0.57 | 0.06–0.12 / 1.36–4.11 | 0.64–0.65 / 1.58–2.42 | 0.06–0.12 / 0.95–4.03 |
    | trojan | 0.04 / 1.28 | 0.48 / 1.13 | 0.03 / 1.20 | 0.04 / 1.41 | 0.33–0.39 / 1.32 | 0.03–0.04 / 1.32 | 0.06 / 2.05–3.36 | 0.34–0.53 / 1.46–2.50 | 0.06 / 1.91–3.23 |
    | anansi | 0.005 / 0.03 | 0.000 / 1.13 | 0.000 / 0.01 | 0.005 / 0.03 | 0.000 / 1.13 | 0.000 / 0.01 | 0.02–0.03 / 0.06–0.07 | 0.005–0.007 / 0.03–0.05 | 0.005–0.007 / 0.03 |
    | bumblebee | 0.005 / 0.41–0.44 | 0.000 / 1.16 | 0.000 / 0.01–0.09 | 0.004–0.005 / 0.42 | 0.000 / 1.16 | 0.000 / 0.06 | 0.02–0.03 / 0.32–0.39 | 0.007–0.010 / 1.16 | 0.007–0.009 / 0.12–0.63 |
    | hornet | 0.01–0.02 / 0.10–0.12 | 0.006–0.007 / 1.16 | 0.006 / 0.06 | 0.01–0.02 / 0.11 | 0.006 / 1.16 | 0.006 / 0.06 | 0.03 / 0.14–0.27 | 0.01 / 1.16 | 0.01 / 0.06–0.08 |

  - **Ground classes on the network client.**
    - S's p95 is 0.6–2.8× its single-player value.
      - The husky and the trojan are 1.6–2.8× in every run.
      - The hog and the badger are 0.6–2.4×; some runs come in below single player.
      - An earlier run of the same drive gave the hog 14.86 u: that drive launched or flipped it, and it was grounded on only 59 of 171 ticks.
    - This is less than the 5× the badger showed on macOS above. That run used `seat-poses`' drive phases, with their collisions, at caps 60–240, on another display, so the two compare per class, not number for number.
    - P has the best p95 on the client in 15 of the 16 run × class cells (H in the other), and in 14 of 16 in single player. Its median is 1.2–11× S's on the client: 0.34–0.67 u against 0.05–0.54.
  - **The listen host** is an authoritative game: there H has the best p95 in 12 of the 16 cells.
  - **The desecrator** takes S in every mode (median 0.03–0.05 u). H uses P there, with a median of 1.24–1.28 u, because the hover tank is airborne to its contacts (§2.1's finding again).
  - **Aircraft: H is near-exact.**
    - Its median is 0.012 u or less in every mode.
    - Its p95 is 0.09 u or less in single player and on the listen host, and 0.63 u or less on the client.
    - P alone has a p95 of 1.13–1.16 u everywhere except the anansi on the client (0.03–0.05).
  - **Logs:** on the Windows x64 host in `C:\Users\<name>\openetqw-evidence\lead-client-2026-10-20\final\` (`devmap-N/`, `listen-N/`, `join-N/`, `aggregate.txt`).
- Whether to keep the decay on your own vehicle at all: kept, by the owner's 2026-10-07 decision (§4).
- **An option for question 1 (suggested by the Linux session):** choose the predictor by role.
  - On a network client (`gameLocal.isClient`), use P, or H with P on the ground too, for every class, since the decay inflates S there.
  - In authoritative games (single player, the listen host), keep each class's own choice (S on the ground).
  - It is a small change in `sdSeatGate::LeadForClass`, and the class's L is unchanged. It needs the same review on a high-refresh display.


