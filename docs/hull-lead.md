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

**What it does not show yet.**
- The other classes: titan, trojan, husky, hog, platypus, bumblebee, goliath, desecrator and the strogg aircraft.
- The network client's decay noise (§6).
- Rough ground and long turns at speed.
- A real human driver.

§5's measurement step covers these.

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
  - A correction is measured against the predicted pose without the fade offset, so a residual is never counted twice.
  - A fade still running when the next tick arrives is not restarted: the new correction is added to what is left of the old offset, and the sum decays over a fresh fade time.
  - While the fade (25 ms) is shorter than the tick spacing (33 ms), the offset is bounded by the sum of the last two corrections. A longer fade, or ticks bunched after a hitch, lets residuals pile up, so `g_vehicleLeadFadeMsec` is clamped to 30 ms.
  - An error over a jump bound, the same 64 u as the hull guard, is not faded: the draw snaps, as a respawn does today.
- **The camera.**
  - **First person:** the camera rides the drawn hull, through its offset in the hull's frame.
    - Below α = 0 the offset is blended as today.
    - At α ≥ 0 it is held at tick n's offset: the damped view's own motion is not extrapolated.
    - Late look (row 064) applies unchanged on top.
  - **Chase views** (world cameras, row 062): no lead in the first tier. The chase camera is already damped behind the hull, and leading it would extrapolate the damping. A second tier can move the world camera by the hull's lead translation, after the stage 5 chase-view orbit work has landed.
- **Riders, the cockpit and carried client entities** use the drawn hull through the carrier rule, as today, so they lead with it.
- **Remote entities stay one tick behind** (stage 4; D5). A vehicle driving alongside therefore appears up to a tick of its own motion behind. That is the "mixed clocks" risk of `vehicle-responsiveness.md` §5.3, and the human review (§5, step 4) has to judge it.

### 3.3 The impact guard

Nothing in the samples or the physics state foresees a collision, so a lead overshoots before the tick that collides. Three measures, applied in order (§2 numbers):

1. **The acceleration falls back after a spike** (S and H). When |Δ(n) − Δ(n−1)| exceeds a per-class bound, the predictor drops k to 0, constant velocity, from that tick for 3 more ticks.
   - The starting bound is 6 u per tick², about 5500 u/s². The largest non-impact value in §2's run is 4.68 (the anansi being entered), so the margin is narrow, and step 2 sets the bound per class.
   - This targets the error that follows an impact: tick 83's 27.6 u becomes about 9 u.
2. **The lead's length is capped per class** (start: 0.5 × the class's top speed × 33 ms). This bounds how far the drawn hull can lead. It does not bound the correction at the next tick, which can still be as large as the impact's error.
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


The PR flags these for the Linux session to raise with the owner:

1. **The per-class bound for enabling L.** Stage 7 said "p95 stays under a bound the owner sets by eye". The measurement step (§5) will offer p95 and max per class and state, plus the human review; the owner picks the bound and each class's L.
2. **The impact overshoot.** Accept §3.3's three measures, which still allow a small overshoot into a wall for up to a tick, or ask for a review of a tick-side clip trace that would bound it exactly?
3. **Driver and pilot seats.** Which classes' driver seats, then pilot seats, join `g_unlock_vehicleSeats`'s default list once their class has a lead. The 2026-09-29 decision already ties this to the measurement.
4. **Prediction-error decay on your own vehicle.** This is `vehicle-responsiveness.md` §5.1 question 7, still open. It matters here because, on a network client, S extrapolates the decay along with the motion (§6). P does not.

## 5. Steps

1. **This plan** (docs only): `docs/hull-lead.md`, `tools/analysis/veh_pred.py`, and `vehicle-responsiveness.md` stage 7, the m5-plan and STATUS pointing here.
2. **Measure every class (engine and game, test only).**
   - A scripted `g_determinismTest 4` (2 is the vehicle replay, 3 the seat-gate schedule) that spawns and drives each valley vehicle class in turn on open ground: straight, turning at speed, braking, one deliberate wall hit, and, for aircraft, lift-off, a turn and a descent.
   - Reuse the anansi's `SpawnEntityDef` pattern from mode 2, and keep it out of mode 2 so that mode 2's golden stays valid.
   - The output is a `veh_pred.py` table per class.
   - Then repeat on a join client with `g_logVehiclePred`, to see the decay noise, and on a listen host.
3. **Implement (game).**
   - The three-sample predictor, the α draw, the fade and the impact guard in `sdPresentation`, plus `g_unlock_vehicleLead` and `VEH_PRED`.
   - It touches `Presentation.{h,cpp}`, which the Linux session is changing for the stage 5 chase-view look (branch `m5/14-stage5-chase-look`). The merge order is agreed with it before this step starts, and this step is rebased on its result.
   - Ledger row (next free number): "Your own vehicle's hull drawn up to one tick ahead, per class (beyond retail; owner 2026-09-29)".
4. **Test (engine).** `seat-poses` gains a lead phase at caps 60 and 120, with the badger listed at L = 1. Its checks:
   - **H0:** the lead was on: `SEAT_PRESENT` counts blended draws, and `FRAME_POSE` carries `lead=`.
   - **H1, age:** with `g_vehicleLeadFadeMsec 0`, each draw's hull equals `hull(n) + α·lead(n)` (the `lead=` that `FRAME_POSE` logs per tick) at α = f + L − 1, to 0.05 u. With L = 0 it lies on the n−1 → n segment, the existing S3.
   - **H2, continuity:** away from collisions, the hull step across a tick is at most 2.5× the median step within a tick, the late-look L3 rule.
   - **H3, L = 0 is the blend:** listed at L = 0, the hull poses equal the interpolation path to 0.05 u, as S3 does today.
   - **H4, the simulation is untouched:** `SIM_HASH` with the lead on equals the lead off over the same scripted drive (`seat-gate` already compares `SIM_HASH`, `VEH_TRACE` and `SIM_VIEW` across the gate; the lead CVar joins that comparison).
   - **A negative control:** an unlisted class draws at L = 0.
   - The A/B latency check from stage 7: one throttle step at a known tick, and the first draw whose hull differs from a run without it, for hold, the blend and L = 1. It is reported, not failed, because it depends on the display.
5. **Human review (D3)** on a high-refresh display, per class, in driver and pilot seats: smoothness, the feel at walls, and vehicles alongside. Then the owner's §4 decisions, and the classes that pass go into `g_unlock_vehicleLead`'s default list.

**Effort** *[inferred]*: step 2 is about 2 days, step 3 about 3 days, and step 4 about 2 days, plus the review.

## 6. Network clients: the decay and sample noise

On a network client the render entity is the predicted pose after prediction-error decay (game repo: `src/game/ScriptEntity.cpp:2244-2255`). The decay is a correction towards each new snapshot, spread over several ticks. To the predictor it is motion that the vehicle did not make.

- **S amplifies it.** With independent per-sample noise of size σ, constant acceleration is p(n) + Δ(n) + (Δ(n) − Δ(n−1)) = 3p(n) − 3p(n−1) + p(n−2). Its coefficients (3, −3, 1) give √19 ≈ 4.4σ RMS in the lead. Constant velocity (2, −1) gives √5 ≈ 2.2σ, and hold gives σ. *[derived]* The decay is correlated over ticks rather than independent, so the real factor is measured, not assumed.
- **P does not.** It predicts from the physics state and applies only the delta to the render pose, so the decay offset is carried along but never extrapolated.
- **Step 2 measures both on a join client**, with `VEH_PRED` against the client's own next sample, and picks the predictor and k per class on the client's numbers, not single player's. If S's client error is several times its single-player error, the class takes H or P, or k = 0.
- Whether to keep the decay on your own vehicle at all is the open owner question 4 (§4).

