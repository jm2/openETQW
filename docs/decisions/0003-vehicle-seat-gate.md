# 0003: The vehicle seat gate

- **Date:** 2026-09-30
- **Status:** D10 decided by the owner (2026-09-25); the gate as built in M5 step 13. The `default` token and the list grammar are a **proposal the owner may revise** before any seat joins the default list.
- **Scope:** M5 plan step 13 (`docs/m5-plan.md`); `docs/vehicle-responsiveness.md` stage 2 and §3.3; compat-ledger row 054

## Context

Retail 1.5 draws every vehicle seat at the tick rate: `idPlayer::IsFPSUnlock` returns false whenever the viewed player has a proxy entity, for every seat of every vehicle, the jetpack included (D2; the retail 1.5 reference notes §3). D10 (2026-09-25) asks for vehicles that feel as modern and responsive as possible without breaking game physics, **on by default**: allowed seats get a presentation-only view (step 14), and the allow-list grows to every seat as each is made safe. `docs/vehicle-responsiveness.md` §3.3 sets when a seat qualifies.

Step 13 builds the switch and its evidence, and nothing that uses it: no consumer reads the latch until step 14.

## Decision

- **`IsFPSUnlock` stays as retail has it.** A separate predicate, `idPlayer::IsSeatPresentationAllowed()` (game `Player.cpp`), copies `IsFPSUnlock`'s checks in the same order and replaces only its proxy check with the seat gate, `sdSeatGate::AllowsSeat` (game `vehicles/SeatGate.{h,cpp}`). On foot it is always false. `canUnlockFrames` and everything it gates are unchanged.
- **The seat gate** allows a seat only when, in this order:
  1. the proxy is a transport (`SPK_TRANSPORT`) or the jetpack (`SPK_JETPACK`, an `sdJetPack`, tested first); a proxy that is not an `sdTransport`, such as a mounted gun (`sdScriptEntity`, game `ScriptEntity.cpp:652`) or a proxy a script set, is `SPK_OTHER` and never allowed;
  2. the transport is not teleporting;
  3. the player's position id is in range, the position holds the player, and it has a view (the checks whose failure is fatal in `PositionForPlayer` and `GetViewParms`);
  4. there is a recipe for (kind, current view class) (`FindRecipe`);
  5. the seat's key is listed in `g_unlock_vehicleSeats`.

  The structural checks come first, so no string is built while no recipe exists. The gate only reads state.
- **Recipes** are keyed by (proxy kind, view class): `standard`, `pivot`, `freepivot`, `player`, `smooth`, `smooth_free`, `smooth_locked`, `icarus` (`sdVehicleView::GetTypeName()`; an empty decl type is `standard`). A recipe is what step 14 needs to present that class; step 14 extends `seatRecipe_t` with its hooks. **The table is empty in step 13**, so no seat can be allowed, whatever the list says. The jetpack has no recipe, so no list item, a `*` included, can allow it; mounted guns cannot even be named.
- **Keys** are `script:position:viewType`:
  - `script` is the vehicle script decl (`vs_vehicleScript`: `badger`, not the entityDef `vehicle_badger`);
  - `position` is the seat's decl name; on retail 1.5 these are localisation paths, so the badger driver is `guis/vehicles/badger_driver`;
  - `viewType` is the current view's class.

  Every character outside `[A-Za-z0-9_]` in a field becomes `_`, and keys compare ignoring case: `badger:guis_vehicles_badger_driver:standard`. One key covers every view of that class in the seat; a `#mode` suffix can be added later if two views of one class in a seat need telling apart. `listSeatViews` prints every key.
- **`g_unlock_vehicleSeats`** (game; `CVAR_GAME | CVAR_PROFILE | CVAR_ARCHIVE`, so not a cheat; not serverinfo: it is the client's presentation only). All whitespace is removed first (the console lexer splits a typed `badger:*:x` into `badger: * : x`), then the value splits on `,` into items, each one of:
  - `default`: the built-in list (`seatDefaultList` in `SeatGate.cpp`), **empty in step 13**;
  - `*`: every key;
  - three `:`-separated globs, each non-empty and made of `[A-Za-z0-9_*?]`: `*` matches any run of characters, `?` one, against the whole field, ignoring case.

  Anything else is malformed: ignored, warned about when the changed value is next parsed (the first check that consults the list: with no recipe, only `SEAT_GATE` or `listSeatViews`), and counted as `bad=` by `listSeatViews`. `idStr::Filter` is not used: it accepts a text that only starts like the pattern, `?` can read past the end, and `*` does not backtrack.
- **The default value is the word `default`, not an empty list** (proposal). The CVar is archived, and the engine writes every archived CVar even at its default (engine `CVarSystem.cpp:1011-1017`); an archived empty string would keep a player off the built-in list after D10's list grows. With `default`, a player follows the built-in list unless they chose otherwise, and `default,<more>` adds to it. Seats join the built-in list as they meet §3.3.
- **Latch and re-check.** At every tick entry, next to `canUnlockFrames` (`RunFrame` and `ClientPrediction`), `idGameLocal::LatchSeatPresentation` stores the predicate in `unlock.seatAllowed` with the seat it was taken for (`seatProxySpawnId`, `seatPositionId`, `seatViewMode`; -1 each without a proxy). `idGameLocal::RecheckSeatPresentation()` is the draw-time re-check of `vehicle-responsiveness.md` §3.1 rule 6: true only while the latch is set, the live proxy, position and view mode equal the latched ones, and the predicate still holds. So an entry, exit, seat swap, camera cycle or teleport since the latch makes it false.
- **`listSeatViews [all|<filter>]`** prints, for the first vehicle of each vehicle script (`all`: every vehicle; a filter: every vehicle whose entity name, class or script matches), each seat's views: class, raw decl type, key, whether the list and a recipe cover it, `thirdPerson`, `tophatRequired`, `autoCenter`, the four clamps, the eye joint and pivot, and the first of the eye's parents that an IK system or weapon aimer named in the decl writes. It is a **cheat** (`CMD_FL_CHEAT`): it reads the positions and state of vehicles the player may not see. It only reads.
- **Since M5 step 14's first part (2026-10-05)** the table has built-in recipes for the transports' `standard` view and chase views (`smooth`, `smooth_free`, `smooth_locked`) (`presentable`, drawn by `sdPresentation`); `seat-gate`'s `BUILTIN_RECIPES` mirrors them.
- **The test recipe.** `g_seatGateTestRecipe` (game; `CVAR_GAME` only, so a cheat) lists view classes that get a transport recipe marked `testOnly`: it opens the gate for the seat-gate test's positive control and **is never presented**; step 14 must refuse a `testOnly` recipe. It never makes a jetpack recipe.
- **Evidence.** With `g_simHash`, a `SEAT_GATE` line each tick (after `VEH_TRACE`) gives `canUnlockFrames`, the latch, the predicate, the re-check, the seat and its key, and whether the list and a recipe cover it. `g_determinismTest 3` is a tick-indexed seat tour on valley (the badger and the titan in two seats each, with camera cycles; the icarus in its one seat). Engine meson `seat-gate` runs it four times; see `tests/test_seat_gate.py`.

## What step 14 reads

- On ticks: `gameLocal.unlock.seatAllowed` and the latched seat (`seatProxySpawnId`, `seatPositionId`, `seatViewMode`).
- At every draw: `gameLocal.RecheckSeatPresentation()`; a mismatch draws the tick pose with no blend and no late look. Since M5 step 6, `ClientUpdateView` runs its on-foot blocks only when the viewer has no proxy at draw time (ledger 056); step 14's seat branch goes next to that guard (`else if ( RecheckSeatPresentation() )`), and its latch return widens to `!canUnlockFrames && !seatAllowed`. Step 6 also resets `originlog` at the pose log when the view player's seat changes (ledger 057), so a seat view starts from a history that holds no other seat's camera.
- The recipe: `sdSeatGate::FindRecipe( kind, viewType )` returns a `seatRecipe_t`, which step 14 extends with its presentation hooks. A pointer to a test recipe is valid only until `g_seatGateTestRecipe` changes. `testOnly` recipes are never presentable.
- Seats join `seatDefaultList` as they meet §3.3; `seat-gate`'s `DEFAULT_LIST` mirrors it.

## Consequences

- Step 13 changes nothing drawn or simulated. `IsFPSUnlock` is byte-identical; `canUnlockFrames`, `ClientUpdateView` and schedules 1 and 2 are unchanged. `seat-gate` shows `SIM_HASH`, `VEH_TRACE`, `canUnlockFrames` and `SIM_VIEW` identical tick by tick with the gate shut, with every seat listed, and with the latch set by the test recipe. (Until M5 step 6 the weapon log alone could differ from tick 22, the entry leak; step 6 removed it and the allowance.)
- The copied `IsFPSUnlock` checks can drift from the original; the predicate's comment names `IsFPSUnlock`, and a change to either must change both.
- The IK column misses joints moved by scripts, animation and wheels.
- The network client runs the latch on re-predicted frames, but only the single-player replay is tested.
- Ledger row 054 records the deviation from retail 1.5 that D10 makes (retail draws every seat at the tick rate), which takes effect when step 14 presents the first seat.
