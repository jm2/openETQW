# Hull lead (M5 step 14b)

The plan for `docs/vehicle-responsiveness.md` stage 7: drawing your own vehicle's hull, and what it carries, up to one tick ahead of the last tick, per vehicle class.

Written 2026-10-06 by the Mac session against engine main 2f8698d and game main d9acb36. It replaces stage 7's predictor (`PredictNextPose`, the physics' own step on a copy) with one built from the presentation's own tick samples, because a first measurement (§2) found that one about ten times more accurate. Everything else in stage 7 stands. Line numbers drift; re-check before relying on one.

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

**Results** (2026-10-06; the simulation is identical at every cap, so one run covers all of them):

| Predictor | badger position, median / p95 / max (u) | anansi position, median / p95 / max (u) | badger rotation, median / p95 (°) | anansi rotation, median / p95 (°) |
|---|---|---|---|---|
| Hold (no lead; the motion in one tick) | 9.91 / 21.72 / 26.58 | 9.08 / 21.33 / 21.52 | 0.174 / 3.196 | 0.582 / 1.927 |
| Physics v·dt (stored momentum) | 0.714 / 2.060 / 33.38 | 1.358 / 1.692 / 3.02 | — | — |
| Physics v·dt + g·dt² (stage 7's gravity add-back) | 1.362 / 2.349 / 33.40 | 0.456 / 1.478 / 4.18 | — | — |
| Physics ω·dt | — | — | 0.227 / 6.145 | 0.970 / 3.670 |
| Samples, constant velocity: p(n) + (p(n) − p(n−1)) | 0.708 / 1.551 / 33.21 | 0.271 / 0.722 / 4.68 | — | — |
| **Samples, constant acceleration**: p(n) + Δ(n) + (Δ(n) − Δ(n−1)) | **0.095 / 1.842 / 33.45** | **0.011 / 0.087 / 4.29** | — | — |
| **Samples, constant rotation**: q(n)·q(n−1)⁻¹·q(n) | — | — | **0.058 / 0.380** | **0.000 / 0.126** |

**What it shows.**
- **The physics' own step on a copy is the wrong predictor.**
  - At `sdTransport::PostThink`, after `Evaluate`, the external force and torque are already zeroed (game repo: `src/game/physics/Physics_RigidBodyMultiple.cpp:2209-2210`, inside the repetition loop). The next tick's control forces and contact constraints are not known yet (`ContactFriction`, `:1448`).
  - With no external force, one `idODE_Euler` step of `RigidBodyDerivatives_Multi` (`:265-293`) moves the position by dt·v. Friction acts only on momentum, so it does not change this step's position.
  - So the copy reduces to v·dt about the centre of mass, plus stage 7's gravity add-back. *[inspected]*
- **The gravity add-back helps aircraft but hurts grounded vehicles.** Airborne, the stored momentum is missing a tick of gravity, so the add-back helps (anansi median 1.36 → 0.46 u). On the ground the contact constraint removes that momentum again, so adding it back sinks the predicted hull (badger median 0.71 → 1.36 u). Stage 7's "about 1.16 u per tick" bias is real only in the air. *[measured]*
- **Extrapolating the samples captures the forces.** A constant acceleration taken from the last three samples carries the persistent control, hover and gravity forces. It is about 10× better than the physics step at the median, and needs no physics code at all.
- **The physics angular velocity is worse than holding.** ω·dt has a p95 of 6.1° against the hold's 3.2° (badger). Extrapolating the sample rotation is close to exact. Why ω·dt does so badly (its frame, or an inertia term) was not determined.
- **Impacts dominate the worst case.**
  - All three badger errors over 10 u are one wall hit. The collision lands at tick 83 (2739 ms):
    - the guess made at tick 82 is 33.45 u off, because nothing in the samples foresees the wall;
    - the guesses made at ticks 83 and 84 are 27.6 and 10.2 u off, because the constant-acceleration term then extrapolates the impact's deceleration.
  - Landings: the badger dropping onto the ground at spawn gives 2.3–2.9 u, and the anansi settling after it is entered gives 4.2–4.3 u.
  - Every other tick in the run is under 2.1 u.

**What it does not show yet.**
- The other classes: titan, trojan, husky, hog, platypus, bumblebee, goliath, desecrator and the strogg aircraft.
- The network client's decay noise.
- Rough ground and long turns at speed.
- A real human driver.

§5's measurement step covers these.

## 3. Design

### 3.1 The predictor

- **Where.** In `sdPresentation`, from its own tick samples. `SampleTick` keeps three samples instead of two (`samples[ 3 ]`, by `framenum % 3`). There are no physics reads and no new tick-side code, so §3.1 rules 1–2 of `vehicle-responsiveness.md` hold as they are: samples are taken only on new ticks, from the post-decay render entity.
- **Position.** `lead(n) = Δ(n) + k·(Δ(n) − Δ(n−1))`, with `Δ(n) = hull(n) − hull(n−1)`. Here k = 1 is constant acceleration and k = 0 is constant velocity.
- **Orientation.** `rot(n) = hull(n) · hull(n−1)ᵀ`, the last tick's rotation, applied again.
- **Continuity.** The three samples must pass the existing continuity guards (`SamplesContinue`: one seat, one proxy, no jump) pairwise. When they don't, the lead is 0 for that draw, which is today's blend.

### 3.2 Drawing

- **The drawn hull.** It is at α = f + L − 1 ticks from n, where f is the frame's fraction and L ∈ [0, 1] is the class's lead:
  - α < 0: the existing lerp and slerp from n−1 to n;
  - α ≥ 0: `hull(n) + α·lead(n)`, with the orientation slerped from the identity towards `rot(n)` by α and applied to `hull(n)`.
- **Choosing L.**
  - L = 0 is today's blend, exactly.
  - L = 1 draws the predicted pose: a mean age of about 0 ms plus the prediction error, 16.5 ms better than the tick-rate view.
  - L = 0.5 keeps the tick-rate view's mean age and halves the worst overshoot.
- **The correction at a new tick.** At the new tick's first draw, `hull(n+1)` and the last drawn lead pose differ by the prediction error: a median of 0.1 u (badger) and 0.01 u (anansi), and up to 33 u at an impact.
  - That difference is faded out over `g_vehicleLeadFadeMsec` (default 50 ms, under two ticks), as an offset that decays linearly.
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

Nothing in the samples foresees a wall, so a lead overshoots into it before the tick that collides. Three measures, applied in order (§2 numbers):

1. **The acceleration falls back after a spike.** When |Δ(n) − Δ(n−1)| exceeds a per-class bound (start: 6 u per tick², about 5500 u/s², above anything the controls produce in §2's run), the predictor falls back to constant velocity (k = 0) for the next 3 ticks. This targets the 27.6 and 10.2 u that follow an impact.
2. **The lead's length is capped per class** (start: 0.5 × the class's top speed × 33 ms). This bounds the overshoot before the impact.
3. **L itself.** L = 0.5 halves whatever is left.

A hull trace in `SampleTick` against the world is **not** proposed. It would bound the overshoot exactly, but it goes beyond §3.1 rule 2 of `vehicle-responsiveness.md` ("sampling reads fields directly"), and `ClipView`, the nearest existing call, is on rule 4's list because it relinks clip models. A read-only `gameLocal.clip.Translation` at tick time would need its own review of what the clip system caches and counts. That review is an owner question, so it is listed in §4 rather than designed here.

### 3.4 Configuration

- **`g_unlock_vehicleLead`** (CVAR_GAME, archive, profile). A comma-separated list of `vehicleClass:L` items, with `*` and `?` wildcards in the class, the same parser style as `g_unlock_vehicleSeats`.
  - Default: `"default"`, a built-in list that starts empty.
  - The class is the transport's vehicle script name, the first field of a seat key (`badger`, `anansi`).
  - L is clamped to [0, 1].
  - The lead applies only in a seat the seat gate presents (ledger 062). Being listed here does not turn a seat on.
- **`g_vehicleLeadFadeMsec`** (default 50), the correction fade of §3.2.
- **Logging (a cheat).** `g_logVehiclePred 1` prints a `VEH_PRED` line from `SampleTick` on each new tick. Its fields:
  - the class, tick, L and k;
  - the error of the prediction made at n−1 against the new sample;
  - whether the impact guard fired;
  - the last collision time.

  It is the in-game counterpart of `veh_pred.py`, and on a network client it measures what the client actually draws from, after the decay.

### 3.5 What does not change

- **Tick state.** Every tick value, `SIM_HASH`, `SIM_VIEW` and `VEH_TRACE` are unchanged. The predictor reads only the presentation's samples.
- **Other players' views.** The lead is local presentation of the local view player's own transport.
- **Seats the seat gate doesn't present.** They keep today's tick-rate view.

## 4. Owner decisions this plan needs

The PR flags these for the Linux session to raise with the owner:

1. **The per-class bound for enabling L.** Stage 7 said "p95 stays under a bound the owner sets by eye". The measurement step (§5) will offer p95 and max per class and state, plus the human review; the owner picks the bound and each class's L.
2. **The impact overshoot.** Accept §3.3's three measures, which still allow a small overshoot into a wall for up to a tick, or ask for a review of a tick-side clip trace that would bound it exactly?
3. **Driver and pilot seats.** Which classes' driver seats, then pilot seats, join `g_unlock_vehicleSeats`'s default list once their class has a lead. The 2026-09-29 decision already ties this to the measurement.
4. **Prediction-error decay on your own vehicle.** This is `vehicle-responsiveness.md` §5.1 question 7, still open. It matters here because the decay is now part of what the predictor extrapolates on a network client (§6).

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
   - **H1, age:** each draw's hull lies on the forward extension of the last two knots, at α ≈ f within 0.1 tick (median). With L = 0 it lies on the n−1 → n segment, the existing S3.
   - **H2, continuity:** away from collisions, the hull step across a tick is at most 2.5× the median step within a tick, the late-look L3 rule.
   - **H3, L = 0 is the blend:** listed at L = 0, the hull poses equal the interpolation path to 0.05 u, as S3 does today.
   - **H4, the simulation is untouched:** `SIM_HASH` with the lead on equals the lead off over the same scripted drive (`seat-gate` already compares `SIM_HASH`, `VEH_TRACE` and `SIM_VIEW` across the gate; the lead CVar joins that comparison).
   - **A negative control:** an unlisted class draws at L = 0.
   - The A/B latency check from stage 7: one throttle step at a known tick, and the first draw whose hull differs from a run without it, for hold, the blend and L = 1. It is reported, not failed, because it depends on the display.
5. **Human review (D3)** on a high-refresh display, per class, in driver and pilot seats: smoothness, the feel at walls, and vehicles alongside. Then the owner's §4 decisions, and the classes that pass go into `g_unlock_vehicleLead`'s default list.

**Effort** *[inferred]*: step 2 is about 2 days, step 3 about 3 days, and step 4 about 2 days, plus the review.
